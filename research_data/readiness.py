"""Read-only, scoped readiness checks; never a campaign or trading authorization."""

import csv
import io
import re
from bisect import bisect_left
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import exchange_calendars

from research_data.dataset import (
    CampaignDataset,
    CampaignDatasetError,
    DATASET_ID,
    MAX_MANIFEST_BYTES,
    MAX_SERIES,
    _keys,
    _load_json_bytes,
    _read_bounded_file,
    _sha256,
    _text,
    _timestamp,
)

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUBMICROSECOND = re.compile(r"\.\d{6}(\d+)(?:Z|[+-]\d{2}:\d{2})$")
MAX_AUDIT_DAYS = 366 * 50
MAX_EXAMPLES = 5
MAX_FX_CLOCKS = 50000
MAX_NORMALIZED_VALUE_LENGTH = 128
PRICE_UNIT_EXPONENTS = {"GBP_PER_UNIT": 0, "GBX_PER_UNIT": -2}
OHLC_FIELDS = ("open", "high", "low", "close")
PRODUCT_EVIDENCE = ("product_access", "license", "normalization", "price_basis", "actions")
CASH_EVIDENCE = ("eligibility", "terms", "license")
REMAINING_GATES = [
    "External product/account eligibility, source rights and comparator terms are not independently verified.",
    "Declared units, price-basis truth and corporate-action completeness require source evidence.",
    "Distribution entitlement/payment and adjusted-price reconciliation are not implemented.",
    "Cash accrual, costs, taxes and continuous GBP portfolio accounting are not implemented.",
    "Weekly features, simulation, campaign selection and broker recovery remain separate gates.",
    "This audit does not authorize data acquisition, performance runs or trading.",
]
FX_REMAINING_GATE = "Declared FX availability does not verify historical vintages, executable conversion prices, or costs."


def _date(value, where):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise CampaignDatasetError(f"{where} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CampaignDatasetError(f"{where} is not a valid date") from exc


def _seconds(value, where):
    if type(value) is not int or not 0 <= value <= MAX_AUDIT_DAYS * 86400:
        raise CampaignDatasetError(f"{where} must be a non-negative integer of at most {MAX_AUDIT_DAYS * 86400}")


def _evidence(value, names, where):
    _keys(value, names, where=where)
    for name in names:
        item = value[name]
        location = f"{where}.{name}"
        _keys(item, ("status", "reference", "as_of"), where=location)
        if item["status"] not in ("recorded", "unresolved"):
            raise CampaignDatasetError(f"{location}.status must be recorded or unresolved")
        if not isinstance(item["reference"], str) or len(item["reference"]) > 4096:
            raise CampaignDatasetError(f"{location}.reference must be a string of at most 4096 characters")
        if item["status"] == "recorded":
            _text(item["reference"], location + ".reference")
            _date(item["as_of"], location + ".as_of")
        elif item["as_of"] is not None:
            _date(item["as_of"], location + ".as_of")


def _profile_bytes(path, dataset, name):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CampaignDatasetError(f"{name} must be a regular, non-symlink local file")
    resolved = path.resolve()
    if resolved.is_relative_to(dataset.directory.resolve()):
        raise CampaignDatasetError(f"{name} must be outside the immutable dataset directory")
    raw = _read_bounded_file(path, MAX_MANIFEST_BYTES, name, f"{name} exceeds the manifest size limit")
    if path.resolve() != resolved:
        raise CampaignDatasetError(f"{name} changed while it was read")
    return raw


def _load_profile(path, dataset):
    raw = _profile_bytes(path, dataset, "readiness profile")
    profile = _load_json_bytes(raw)
    _keys(profile, ("schema_version", "dataset_id", "synthetic", "products", "cash"), where="readiness profile")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise CampaignDatasetError("readiness schema_version must be integer 1")
    if (not isinstance(profile["dataset_id"], str) or not DATASET_ID.fullmatch(profile["dataset_id"])
            or profile["dataset_id"] != dataset.dataset_id):
        raise CampaignDatasetError("readiness profile dataset_id must match the opened snapshot")
    if type(profile["synthetic"]) is not bool:
        raise CampaignDatasetError("readiness synthetic must be an explicit boolean")
    products = profile["products"]
    if not isinstance(products, list) or not 1 <= len(products) <= MAX_SERIES:
        raise CampaignDatasetError(f"readiness products must contain 1 to {MAX_SERIES} entries")
    source_ids = {item["id"] for item in dataset.source_manifest["products"]}
    selected = set()
    for index, product in enumerate(products):
        where = f"readiness products[{index}]"
        _keys(product, (
            "product_id", "identifier", "share_class", "isin", "venue", "calendar", "timezone",
            "currency", "price_unit", "price_basis", "start_date", "end_date", "inception_date",
            "audit_clock", "max_availability_delay_seconds", "evidence",
        ), where=where)
        for field in ("product_id", "identifier", "share_class", "isin", "venue", "calendar", "timezone",
                      "currency", "price_unit", "price_basis", "audit_clock"):
            _text(product[field], f"{where}.{field}", 256)
        if product["product_id"] not in source_ids or product["product_id"] in selected:
            raise CampaignDatasetError(f"{where}.product_id must identify a unique imported product")
        selected.add(product["product_id"])
        start = _date(product["start_date"], where + ".start_date")
        end = _date(product["end_date"], where + ".end_date")
        if end < start or (end - start).days > MAX_AUDIT_DAYS:
            raise CampaignDatasetError(f"{where} interval must be ordered and span at most {MAX_AUDIT_DAYS} days")
        _date(product["inception_date"], where + ".inception_date")
        _seconds(product["max_availability_delay_seconds"], where + ".max_availability_delay_seconds")
        _evidence(product["evidence"], PRODUCT_EVIDENCE, where + ".evidence")
    cash = profile["cash"]
    _keys(cash, (
        "series_id", "benchmark_identifier", "rate_unit", "max_rate_age_seconds",
        "day_count", "compounding", "evidence",
    ), where="readiness cash")
    for field in ("series_id", "benchmark_identifier", "rate_unit", "day_count", "compounding"):
        _text(cash[field], f"readiness cash.{field}", 256)
    if cash["series_id"] not in {item["id"] for item in dataset.source_manifest["cash_rates"]}:
        raise CampaignDatasetError("readiness cash.series_id must identify an imported cash series")
    _seconds(cash["max_rate_age_seconds"], "readiness cash.max_rate_age_seconds")
    _evidence(cash["evidence"], CASH_EVIDENCE, "readiness cash.evidence")
    return profile, _sha256(raw)


def _check(subject, name, status, reason, issues=0, examples=()):
    return {
        "subject": subject, "name": name, "status": status, "reason": reason,
        "issues": issues, "examples": list(examples)[:MAX_EXAMPLES],
    }


def _issue(result, example):
    result["issues"] += 1
    if len(result["examples"]) < MAX_EXAMPLES:
        result["examples"].append(example)


def _recording_check(subject, evidence):
    unresolved = [name for name, item in evidence.items() if item["status"] == "unresolved"]
    return _check(
        subject, "evidence_recording", "unresolved" if unresolved else "pass",
        "Only declaration completeness is checked; external facts are not independently verified.",
        len(unresolved), unresolved,
    )


def _readiness_timestamp(value, where):
    try:
        parsed = _timestamp(value, where)
    except OverflowError as exc:
        raise CampaignDatasetError(f"{where} is outside the supported UTC range") from exc
    fractional = SUBMICROSECOND.search(value)
    if fractional and fractional.group(1).strip("0"):
        raise CampaignDatasetError(f"{where}: non-zero sub-microsecond precision is unsupported")
    return parsed


def _timestamped_rows(dataset, item):
    # Reopen validates CSV semantics; source_file verifies these exact bytes again.
    payload = dataset.source_file(item["file"])
    provenance = item.get("provenance", dataset.source_manifest["provenance"])
    _readiness_timestamp(provenance["retrieved_at"], item["id"] + ".provenance.retrieved_at")
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8"), newline="")):
        yield (row, _readiness_timestamp(row["timestamp"], item["id"] + ".timestamp"),
               _readiness_timestamp(row["available_at"], item["id"] + ".available_at"))


def _load_fx_profile(path, dataset):
    raw = _profile_bytes(path, dataset, "FX readiness profile")
    profile = _load_json_bytes(raw)
    _keys(profile, ("schema_version", "dataset_id", "series_id", "synthetic", "max_age_seconds", "clocks"),
          where="FX readiness profile")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise CampaignDatasetError("FX readiness schema_version must be integer 1")
    if (not isinstance(profile["dataset_id"], str) or not DATASET_ID.fullmatch(profile["dataset_id"])
            or profile["dataset_id"] != dataset.dataset_id):
        raise CampaignDatasetError("FX readiness dataset_id must match the opened snapshot")
    if type(profile["synthetic"]) is not bool:
        raise CampaignDatasetError("FX readiness synthetic must be an explicit boolean")
    _text(profile["series_id"], "FX readiness series_id", 128)
    source = next((item for item in dataset.source_manifest["fx"] if item["id"] == profile["series_id"]), None)
    if source is None:
        raise CampaignDatasetError("FX readiness series_id must identify an imported FX series")
    _seconds(profile["max_age_seconds"], "FX readiness max_age_seconds")
    if not isinstance(profile["clocks"], list) or not 1 <= len(profile["clocks"]) <= MAX_FX_CLOCKS:
        raise CampaignDatasetError(f"FX readiness clocks must contain 1 to {MAX_FX_CLOCKS} timestamps")
    clocks = []
    for index, value in enumerate(profile["clocks"]):
        clock = _readiness_timestamp(value, f"FX readiness clocks[{index}]")
        if clocks and clock <= clocks[-1]:
            raise CampaignDatasetError("FX readiness clocks must be strictly increasing and unique")
        clocks.append(clock)
    return profile, _sha256(raw), source, clocks


def _fx_clock_observations(dataset, source, clocks):
    arrivals = [None] * len(clocks)
    for row, stamp, available in _timestamped_rows(dataset, source):
        index = bisect_left(clocks, available)
        if index < len(clocks) and (arrivals[index] is None or stamp > arrivals[index][0]):
            arrivals[index] = (stamp, available, row["gbp_per_unit"])
    current = None
    for clock, arrival in zip(clocks, arrivals):
        if arrival is not None and (current is None or arrival[0] > current[0]):
            current = arrival
        yield clock, current


def _fx_clock_problem(clock, current, max_age):
    if current is None:
        return "no available observation"
    if clock - current[0] > max_age:
        return "latest available observation is stale"
    return None


def audit_fx_readiness(dataset_dir, dataset_id, profile_path):
    """Audit declared FX availability at explicit clocks without conversion or simulation."""
    dataset = CampaignDataset.open(dataset_dir, dataset_id)
    profile, profile_hash, source, clocks = _load_fx_profile(profile_path, dataset)

    coverage = _check(
        source["id"], "fx_availability", "pass",
        "The latest observation published by each supplied clock must be within the declared observation-time age.",
    )
    coverage["passed_clocks"] = 0
    coverage["sample_observations"] = []
    max_age = timedelta(seconds=profile["max_age_seconds"])
    for clock, current in _fx_clock_observations(dataset, source, clocks):
        problem = _fx_clock_problem(clock, current, max_age)
        if problem:
            _issue(coverage, f"{clock.isoformat()}: {problem}")
        else:
            coverage["passed_clocks"] += 1
            if len(coverage["sample_observations"]) < MAX_EXAMPLES:
                coverage["sample_observations"].append({
                    "clock": clock.isoformat().replace("+00:00", "Z"),
                    "observation_at": current[0].isoformat().replace("+00:00", "Z"),
                    "available_at": current[1].isoformat().replace("+00:00", "Z"),
                })
    if coverage["issues"]:
        coverage["status"] = "fail"
    status = "fail" if coverage["issues"] else "unresolved" if profile["synthetic"] else "pass"
    return {
        "schema_version": 1, "scope": "fx_availability_at_supplied_clocks", "status": status,
        "dataset_id": dataset.dataset_id, "profile_sha256": profile_hash, "synthetic": profile["synthetic"],
        "series_id": source["id"], "currency": source["currency"], "direction": source["direction"],
        "source_file": source["file"], "source_sha256": dataset.checksums()[source["file"]],
        "source_provenance": source.get("provenance", dataset.source_manifest["provenance"]),
        "clock_count": len(clocks), "max_age_seconds": profile["max_age_seconds"], "coverage": coverage,
        "external_facts_verified": False, "clock_set_complete_verified": False,
        "point_in_time_vintage_verified": False, "executable_fx_verified": False, "supports_performance": False,
        "remaining_campaign_gates": list(REMAINING_GATES) + [FX_REMAINING_GATE],
    }


def fx_observation_view(dataset_dir, dataset_id, profile_path):
    """Return all selected FX observations without conversion or verified historical vintages."""
    dataset = CampaignDataset.open(dataset_dir, dataset_id)
    profile, profile_hash, source, clocks = _load_fx_profile(profile_path, dataset)
    max_age = timedelta(seconds=profile["max_age_seconds"])
    observations = []
    for clock, current in _fx_clock_observations(dataset, source, clocks):
        problem = _fx_clock_problem(clock, current, max_age)
        if problem:
            raise CampaignDatasetError(f"{source['id']}: {clock.isoformat()}: {problem}")
        rate = current[2]
        if len(rate) > MAX_NORMALIZED_VALUE_LENGTH:
            raise CampaignDatasetError(
                f"{source['id']}: source FX rate exceeds {MAX_NORMALIZED_VALUE_LENGTH} characters"
            )
        try:
            with localcontext() as context:
                context.traps[InvalidOperation] = True
                decimal_rate = Decimal(rate)
        except InvalidOperation as exc:
            raise CampaignDatasetError(f"{source['id']}: exact FX rate cannot be represented") from exc
        if not decimal_rate.is_finite() or decimal_rate <= 0:
            raise CampaignDatasetError(f"{source['id']}: invalid exact FX rate at {current[0].isoformat()}")
        observations.append({
            "clock": clock.isoformat().replace("+00:00", "Z"),
            "observation_at": current[0].isoformat().replace("+00:00", "Z"),
            "available_at": current[1].isoformat().replace("+00:00", "Z"),
            "gbp_per_unit": rate,
        })
    provenance = source.get("provenance", dataset.source_manifest["provenance"])
    retrieved_at = _readiness_timestamp(provenance["retrieved_at"], source["id"] + ".provenance.retrieved_at")
    return {
        "schema_version": 1, "scope": "offline_fx_observations_at_supplied_clocks",
        "dataset_id": dataset.dataset_id, "profile_sha256": profile_hash, "synthetic": profile["synthetic"],
        "series_id": source["id"], "currency": source["currency"], "direction": source["direction"],
        "source_file": source["file"], "source_sha256": dataset.checksums()[source["file"]],
        "source_provenance": provenance, "source_retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "clock_count": len(clocks), "max_age_seconds": profile["max_age_seconds"],
        "readiness_status": "unresolved" if profile["synthetic"] else "pass", "observations": observations,
        "external_facts_verified": False, "clock_set_complete_verified": False,
        "point_in_time_vintage_verified": False, "executable_fx_verified": False, "supports_performance": False,
        "remaining_campaign_gates": list(REMAINING_GATES) + [FX_REMAINING_GATE],
    }


def _normalized_row(row, stamp, available, exponent):
    if any(len(row[name]) > MAX_NORMALIZED_VALUE_LENGTH for name in (*OHLC_FIELDS, "volume")):
        return None
    result = {
        "timestamp": stamp.isoformat().replace("+00:00", "Z"),
        "available_at": available.isoformat().replace("+00:00", "Z"),
    }
    for name in OHLC_FIELDS:
        sign, digits, source_exponent = Decimal(row[name]).as_tuple()
        # Shift the decimal exponent directly to avoid context-dependent rounding.
        result[name] = str(Decimal((sign, digits, source_exponent + exponent))).replace("e", "E")
    result["volume"] = row["volume"]
    return result


def _product_audit(dataset, product, calendar):
    subject = product["product_id"]
    source = next(item for item in dataset.source_manifest["products"] if item["id"] == subject)
    checks = []
    matches = all(product[name] == source[name] for name in ("identifier", "currency", "price_basis"))
    checks.append(_check(subject, "product_identity", "pass" if matches else "fail",
                         "Profile identifier, currency and price basis must match the immutable source declaration."))
    exponent = PRICE_UNIT_EXPONENTS.get(product["price_unit"])
    gbp = product["currency"] == source["currency"] == "GBP" and exponent is not None
    checks.append(_check(subject, "gbp_units", "pass" if gbp else "unsupported",
                         "Only explicitly documented GBP_PER_UNIT or GBX_PER_UNIT quotes of GBP instruments "
                         "are supported; quote units and evidence are not inferred."))
    daily = source["resolution"] == "daily"
    checks.append(_check(subject, "daily_execution", "pass" if daily else "unsupported",
                         "Daily execution history is required; weekly-only execution is not supported."))
    checks.append(_recording_check(subject, product["evidence"]))
    has_actions = any(item["product_id"] == subject for item in dataset.source_manifest["corporate_actions"])
    simple_prices = source["price_basis"] == "unadjusted_ohlcv" and not has_actions
    checks.append(_check(subject, "action_semantics", "pass" if simple_prices else "unsupported",
                         "Only unadjusted OHLC without recorded actions is scoped here; source completeness is not verified."))
    start, end = date.fromisoformat(product["start_date"]), date.fromisoformat(product["end_date"])
    checks.append(_check(subject, "history_start",
                         "pass" if date.fromisoformat(product["inception_date"]) <= start else "fail",
                         "The declared interval must not precede the declared product inception; it is never trimmed."))
    summary = {name: product[name] for name in
               ("product_id", "venue", "calendar", "timezone", "currency", "price_unit", "price_basis",
                "start_date", "end_date", "audit_clock")}
    summary.update(
        expected_sessions=0, observed_rows=0, outside_interval_rows=0,
        price_scale_to_gbp=str(Decimal((0, (1,), exponent))) if gbp else None,
        normalized_price_examples=[], omitted_normalized_examples=0,
    )
    supported = (product["calendar"] == product["venue"] == "XLON"
                 and product["timezone"] == "Europe/London" and product["audit_clock"] == "next_session_open")
    if not supported:
        checks.append(_check(subject, "session_calendar", "unsupported",
                             "Only explicit XLON / Europe/London with next_session_open is supported."))
        return summary, checks, set()
    if start < calendar.first_session.date() or end > calendar.last_session.date():
        checks.append(_check(subject, "session_calendar", "unsupported",
                             f"Requested interval is outside calendar coverage {calendar.first_session.date()} "
                             f"through {calendar.last_session.date()}."))
        return summary, checks, set()
    schedule = calendar.schedule.loc[start.isoformat():end.isoformat()]
    if schedule.empty:
        checks.append(_check(subject, "session_calendar", "fail", "Declared interval contains no XLON sessions."))
        return summary, checks, set()
    last_index = calendar.schedule.index.get_loc(schedule.index[-1])
    if last_index + 1 >= len(calendar.schedule):
        checks.append(_check(subject, "session_calendar", "unsupported",
                             "Calendar coverage lacks the next executable open after the final requested session."))
        return summary, checks, set()
    checks.append(_check(subject, "session_calendar", "pass",
                         "Regular-session closes and following opens come from the declared XLON calendar."))
    if not daily:
        checks.append(_check(subject, "session_completeness", "unsupported",
                             "Weekly bars cannot establish daily execution completeness."))
        return summary, checks, set()
    expected = {}
    for label, row in schedule.iterrows():
        index = calendar.schedule.index.get_loc(label)
        close = row["close"].to_pydatetime()
        next_open = calendar.schedule.iloc[index + 1]["open"].to_pydatetime()
        expected[close] = next_open
    summary["expected_sessions"] = len(expected)
    grid = _check(subject, "session_grid", "pass", "All in-scope observations must equal expected regular-session closes.")
    availability = _check(subject, "bar_availability", "pass",
                          "Each bar must be published within the declared delay and by its next executable open.")
    observed = set()
    max_delay = timedelta(seconds=product["max_availability_delay_seconds"])
    first_sample = last_sample = None
    for row, stamp, available in _timestamped_rows(dataset, source):
        local_date = stamp.astimezone(calendar.tz).date()
        if not start <= local_date <= end:
            summary["outside_interval_rows"] += 1
            continue
        summary["observed_rows"] += 1
        if stamp not in expected:
            _issue(grid, stamp.isoformat())
            continue
        observed.add(stamp)
        if gbp:
            if first_sample is None:
                first_sample = (row, stamp, available)
            last_sample = (row, stamp, available)
        reasons = []
        if available - stamp > max_delay:
            reasons.append("exceeds declared publication delay")
        if available > expected[stamp]:
            reasons.append("later than next session open")
        if reasons:
            _issue(availability, f"{stamp.isoformat()}: {', '.join(reasons)}")
    if first_sample is not None:
        samples = [first_sample]
        if last_sample[1] != first_sample[1]:
            samples.append(last_sample)
        for row, stamp, available in samples:
            example = _normalized_row(row, stamp, available, exponent)
            if example is None:
                summary["omitted_normalized_examples"] += 1
            else:
                summary["normalized_price_examples"].append(example)
    missing = sorted(set(expected) - observed)
    for result in (grid, availability):
        if result["issues"]:
            result["status"] = "fail"
    checks.extend([
        grid,
        _check(subject, "session_completeness", "fail" if missing else "pass",
               "Every expected session in the full declared interval is required.",
               len(missing), [stamp.isoformat() for stamp in missing[:MAX_EXAMPLES]]),
        availability,
    ])
    return summary, checks, set(expected.values())


def _cash_audit(dataset, cash, clocks):
    subject = cash["series_id"]
    source = next(item for item in dataset.source_manifest["cash_rates"] if item["id"] == subject)
    identity = (cash["benchmark_identifier"] == source["benchmark_identifier"]
                and cash["rate_unit"] == source["rate_unit"] == "annual_decimal")
    checks = [
        _check(subject, "cash_identity", "pass" if identity else "fail",
               "Cash identity and annual_decimal units must match the source declaration."),
        _recording_check(subject, cash["evidence"]),
    ]
    result = _check(subject, "rate_availability", "pass",
                    "The latest effective rate must be available and within the declared effective-time age at each open; "
                    "no fallback to an older rate or use of a future effective rate.")
    rows = ((stamp, available) for _, stamp, available in _timestamped_rows(dataset, source))
    upcoming = next(rows, None)
    current = None
    max_age = timedelta(seconds=cash["max_rate_age_seconds"])
    for clock in sorted(clocks):
        while upcoming is not None and upcoming[0] <= clock:
            current = upcoming
            upcoming = next(rows, None)
        if current is None:
            _issue(result, f"{clock.isoformat()}: no effective rate")
        elif current[1] > clock:
            _issue(result, f"{clock.isoformat()}: latest effective rate unavailable")
        elif clock - current[0] > max_age:
            _issue(result, f"{clock.isoformat()}: latest effective rate stale")
    # Validate later source timestamps without applying their rates to earlier clocks.
    for _ in rows:
        pass
    if not clocks:
        result.update(status="unresolved", reason="No supported daily execution clocks exist for cash coverage checks.")
    elif result["issues"]:
        result["status"] = "fail"
    return checks + [result]


def _audit_dataset(dataset, profile, profile_hash):
    calendar = exchange_calendars.get_calendar("XLON")
    checks = [_check(
        "scope", "synthetic_inputs", "unresolved" if profile["synthetic"] else "pass",
        "Synthetic engineering fixtures cannot establish real product/data feasibility; this flag is operator-declared.",
    )]
    products = []
    clocks = set()
    for product in profile["products"]:
        summary, product_checks, product_clocks = _product_audit(dataset, product, calendar)
        products.append(summary)
        checks.extend(product_checks)
        clocks.update(product_clocks)
    checks.extend(_cash_audit(dataset, profile["cash"], clocks))
    counts = {status: sum(check["status"] == status for check in checks)
              for status in ("pass", "fail", "unsupported", "unresolved")}
    status = next((status for status in ("fail", "unsupported", "unresolved") if counts[status]), "pass")
    return {
        "schema_version": 1, "scope": "gbp_daily_data_readiness", "status": status,
        "dataset_id": dataset.dataset_id, "profile_sha256": profile_hash, "synthetic": profile["synthetic"],
        "calendar": {
            "name": "XLON", "timezone": "Europe/London", "package_version": exchange_calendars.__version__,
            "first_session": calendar.first_session.date().isoformat(),
            "last_session": calendar.last_session.date().isoformat(),
        },
        "products": products, "cash_audit_clock_count": len(clocks), "check_counts": counts, "checks": checks,
        "external_evidence": {
            "products": [{name: product[name] for name in ("product_id", "share_class", "isin", "evidence")}
                         for product in profile["products"]],
            "cash": profile["cash"],
        },
        "remaining_campaign_gates": list(REMAINING_GATES),
    }


def audit_readiness(dataset_dir, dataset_id, profile_path):
    """Return a bounded JSON-compatible audit; no writes, accrual, simulation or broker access."""
    dataset = CampaignDataset.open(dataset_dir, dataset_id)
    profile, profile_hash = _load_profile(profile_path, dataset)
    return _audit_dataset(dataset, profile, profile_hash)


def normalized_product_view(dataset_dir, dataset_id, profile_path, product_id, as_of):
    """Return a complete offline view using declared availability, not verified historical vintages."""
    cutoff = _readiness_timestamp(as_of, "as_of")
    dataset = CampaignDataset.open(dataset_dir, dataset_id)
    profile, profile_hash = _load_profile(profile_path, dataset)
    product = next((item for item in profile["products"] if item["product_id"] == product_id), None)
    if product is None:
        raise CampaignDatasetError("normalized product must be selected by the readiness profile")
    report = _audit_dataset(dataset, profile, profile_hash)
    blocking = [f"{item['subject']}.{item['name']} ({item['status']})" for item in report["checks"]
                if item["status"] in ("fail", "unsupported")]
    if blocking:
        raise CampaignDatasetError("cannot normalize with failing or unsupported readiness checks: "
                                   + ", ".join(blocking))
    source = next(item for item in dataset.source_manifest["products"] if item["id"] == product_id)
    provenance = source.get("provenance", dataset.source_manifest["provenance"])
    retrieved_at = _readiness_timestamp(provenance["retrieved_at"], product_id + ".provenance.retrieved_at")
    start, end = date.fromisoformat(product["start_date"]), date.fromisoformat(product["end_date"])
    calendar = exchange_calendars.get_calendar("XLON")
    exponent = PRICE_UNIT_EXPONENTS[product["price_unit"]]
    bars = []
    for row, stamp, available in _timestamped_rows(dataset, source):
        if not start <= stamp.astimezone(calendar.tz).date() <= end:
            continue
        if available > cutoff:
            raise CampaignDatasetError(f"{product_id}: bar {stamp.isoformat()} is not available at as_of")
        try:
            with localcontext() as context:
                context.traps[InvalidOperation] = True
                normalized = _normalized_row(row, stamp, available, exponent)
                if normalized is None:
                    raise CampaignDatasetError(
                        f"{product_id}: source OHLC or volume exceeds {MAX_NORMALIZED_VALUE_LENGTH} characters"
                    )
                prices = {name: Decimal(normalized[name]) for name in OHLC_FIELDS}
                volume = Decimal(normalized["volume"])
        except InvalidOperation as exc:
            raise CampaignDatasetError(f"{product_id}: exact decimal cannot be represented at {stamp.isoformat()}") from exc
        if (any(not value.is_finite() or value <= 0 for value in prices.values())
                or not volume.is_finite() or volume < 0
                or prices["high"] < max(prices.values())
                or prices["low"] > min(prices.values())):
            raise CampaignDatasetError(f"{product_id}: invalid exact OHLC or volume at {stamp.isoformat()}")
        bars.append(normalized)
        if len(bars) > MAX_AUDIT_DAYS + 1:
            raise CampaignDatasetError("normalized view exceeds the maximum interval row count")
    expected = next(item["expected_sessions"] for item in report["products"] if item["product_id"] == product_id)
    if len(bars) != expected:
        raise CampaignDatasetError(f"{product_id}: normalized view does not cover every requested session")
    return {
        "schema_version": 1, "scope": "offline_gbp_normalized_daily_view",
        "dataset_id": dataset.dataset_id, "profile_sha256": profile_hash, "product_id": product_id,
        "source_file": source["file"], "source_sha256": dataset.checksums()[source["file"]],
        "source_provenance": provenance, "source_retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "source_price_unit": product["price_unit"], "currency": "GBP", "price_unit": "GBP_PER_UNIT",
        "start_date": product["start_date"], "end_date": product["end_date"],
        "as_of": cutoff.isoformat().replace("+00:00", "Z"), "calendar": report["calendar"],
        "readiness_status": report["status"], "synthetic": profile["synthetic"],
        "external_facts_verified": False, "point_in_time_vintage_verified": False, "supports_performance": False,
        "remaining_campaign_gates": report["remaining_campaign_gates"], "bar_count": len(bars), "bars": bars,
    }
