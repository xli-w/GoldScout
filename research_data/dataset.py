"""Strict, local-only GBP research snapshots; no journal or simulator integration."""

import csv
import errno
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SERIES = 256
MAX_ACTIONS = 10000
MAX_ROWS_PER_SERIES = 1_000_000
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.csv$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
UTC_OFFSET = re.compile(r"[+-](\d{2}):(\d{2})$")
DATASET_ID = re.compile(r"^[0-9a-f]{64}$")
PRODUCT_COLUMNS = ("timestamp", "available_at", "open", "high", "low", "close", "volume")
FX_COLUMNS = ("timestamp", "available_at", "gbp_per_unit")
CASH_COLUMNS = ("timestamp", "available_at", "annual_rate")
UNRESOLVED = [
    "Daily/weekly calendar, exchange-session, holiday, and missing-interval completeness are not assessed.",
    "FX coverage is an observation-timestamp envelope check, not proof of transaction-time conversion coverage.",
    "Declared price basis, product eligibility, costs, tax, cash accrual, total-return treatment, and portfolio/accounting feasibility are not verified.",
]


class CampaignDatasetError(ValueError):
    """Invalid, unsafe, unsupported, or tampered campaign dataset."""


def _canonical(value):
    return _json_dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_dumps(value, sort_keys=False, separators=None, ensure_ascii=True, indent=None):
    item_separator, key_separator = separators or ((", ", ": ") if indent is None else (",", ": "))

    def render(item, level):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("non-finite Decimal is not a JSON number")
            return str(item)
        if isinstance(item, dict):
            keys = sorted(item) if sort_keys else item.keys()
            entries = [
                json.dumps(key, ensure_ascii=ensure_ascii, allow_nan=False) + key_separator
                + render(item[key], level + 1)
                for key in keys
            ]
            if not entries:
                return "{}"
            if indent is None:
                return "{" + item_separator.join(entries) + "}"
            padding = " " * (indent * (level + 1))
            return "{\n" + padding + (",\n" + padding).join(entries) + "\n" + (
                " " * (indent * level)
            ) + "}"
        if isinstance(item, (list, tuple)):
            entries = [render(value, level + 1) for value in item]
            if not entries:
                return "[]"
            if indent is None:
                return "[" + item_separator.join(entries) + "]"
            padding = " " * (indent * (level + 1))
            return "[\n" + padding + (",\n" + padding).join(entries) + "\n" + (
                " " * (indent * level)
            ) + "]"
        return json.dumps(item, ensure_ascii=ensure_ascii, allow_nan=False)

    return render(value, 0)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_bounded_file(path, limit, where, size_error):
    path = Path(path)
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise CampaignDatasetError(f"{where} must be a regular, non-symlink file") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & reparse_flag):
        raise CampaignDatasetError(f"{where} must be a regular, non-symlink file")
    if before.st_size > limit:
        raise CampaignDatasetError(size_error)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or not before.st_ino or not opened.st_ino
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or getattr(opened, "st_file_attributes", 0) & reparse_flag):
            raise CampaignDatasetError(f"{where} changed or is unsafe")
        try:
            after = path.lstat()
        except FileNotFoundError as exc:
            raise CampaignDatasetError(f"{where} changed or is unsafe") from exc
        if (not stat.S_ISREG(after.st_mode) or getattr(after, "st_file_attributes", 0) & reparse_flag
                or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)):
            raise CampaignDatasetError(f"{where} changed or is unsafe")
        if opened.st_size > limit:
            raise CampaignDatasetError(size_error)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(limit + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > limit:
        raise CampaignDatasetError(size_error)
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CampaignDatasetError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise CampaignDatasetError(f"non-finite JSON value {value!r} is not allowed")


def _load_json_bytes(data):
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
            parse_float=Decimal,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CampaignDatasetError(f"invalid UTF-8 JSON: {exc}") from exc


def _keys(value, required, optional=(), where="object"):
    if not isinstance(value, dict):
        raise CampaignDatasetError(f"{where} must be an object")
    missing = set(required) - set(value)
    extra = set(value) - set(required) - set(optional)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(repr(key) for key in sorted(extra)))
        raise CampaignDatasetError(f"{where}: " + "; ".join(details))


def _text(value, where, max_length=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise CampaignDatasetError(f"{where} must be a non-empty string of at most {max_length} characters")
    return value


def _id(value, where):
    _text(value, where, 128)
    if not SAFE_ID.fullmatch(value):
        raise CampaignDatasetError(f"{where} is not a safe identifier")
    return value


def _currency(value, where):
    if not isinstance(value, str) or not CURRENCY.fullmatch(value):
        raise CampaignDatasetError(f"{where} must be a three-letter uppercase currency code")
    return value


def _timestamp(value, where):
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        raise CampaignDatasetError(f"{where} must be an RFC3339 timestamp with an explicit UTC offset")
    offset = UTC_OFFSET.search(value)
    if offset and (int(offset.group(1)) > 23 or int(offset.group(2)) > 59):
        raise CampaignDatasetError(f"{where} has an invalid UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignDatasetError(f"{where} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CampaignDatasetError(f"{where} must be timezone-aware")
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError as exc:
        raise CampaignDatasetError(f"{where} is outside the supported UTC range") from exc


def _timestamp_key(value, where):
    parsed = _timestamp(value, where)
    fractional = re.search(r"T\d{2}:\d{2}:\d{2}\.(\d+)", value)
    remainder = fractional.group(1)[6:].rstrip("0") if fractional else ""
    return parsed, remainder


def _metadata(value, where):
    if not isinstance(value, dict):
        raise CampaignDatasetError(f"{where} must be an object when supplied")
    try:
        _canonical(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CampaignDatasetError(f"{where} must contain finite JSON values") from exc


def _validate_provenance(value, where):
    _keys(value, ("provider", "license_terms", "retrieved_at"), ("metadata",), where)
    _text(value["provider"], where + ".provider", 200)
    _text(value["license_terms"], where + ".license_terms", 8192)
    retrieved_at = _timestamp(value["retrieved_at"], where + ".retrieved_at")
    if "metadata" in value:
        _metadata(value["metadata"], where + ".metadata")
    return retrieved_at


def _source_retrieved_at(item, default_provenance, where):
    return _timestamp_key(
        item.get("provenance", default_provenance)["retrieved_at"],
        where + ".provenance.retrieved_at",
    )


def _safe_file(value, where):
    _text(value, where, 128)
    if value in (".", "..") or not SAFE_FILE.fullmatch(value) or "/" in value or "\\" in value:
        raise CampaignDatasetError(f"{where} must be a safe CSV basename (no directories)")
    return value


def _finite_number(value, where, minimum=None, strict_minimum=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise CampaignDatasetError(f"{where} must be numeric")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise CampaignDatasetError(f"{where} must be finite") from exc
    if not math.isfinite(number):
        raise CampaignDatasetError(f"{where} must be finite")
    if minimum is not None and (number <= minimum if strict_minimum else number < minimum):
        relation = "greater than" if strict_minimum else "at least"
        raise CampaignDatasetError(f"{where} must be {relation} {minimum}")
    return number


def _load_source(manifest_path):
    path = Path(manifest_path)
    raw_manifest = _read_bounded_file(
        path, MAX_MANIFEST_BYTES, "manifest", f"manifest exceeds {MAX_MANIFEST_BYTES} bytes",
    )
    parsed = _load_json_bytes(raw_manifest)
    source_dir = path.parent
    _validate_manifest(parsed)
    references = _file_references(parsed)
    if len(references) > MAX_SERIES:
        raise CampaignDatasetError(f"manifest contains more than {MAX_SERIES} data series")
    total = len(raw_manifest)
    payloads = {}
    for filename in references:
        source = source_dir / filename
        if total > MAX_TOTAL_SOURCE_BYTES:
            raise CampaignDatasetError(f"source files exceed {MAX_TOTAL_SOURCE_BYTES} bytes in total")
        remaining = MAX_TOTAL_SOURCE_BYTES - total
        limit = min(MAX_CSV_BYTES, remaining)
        size_error = (f"source files exceed {MAX_TOTAL_SOURCE_BYTES} bytes in total"
                      if remaining < MAX_CSV_BYTES else f"{filename}: file exceeds {MAX_CSV_BYTES} bytes")
        payloads[filename] = _read_bounded_file(source, limit, f"{filename}: source", size_error)
        total += len(payloads[filename])
    return raw_manifest, parsed, payloads


def _file_references(manifest):
    references = []
    for group in ("products", "fx", "cash_rates"):
        references.extend(item["file"] for item in manifest[group])
    return references


def _validate_manifest(manifest):
    _keys(
        manifest,
        ("schema_version", "provenance", "products", "fx", "cash_rates", "corporate_actions"),
        where="manifest",
    )
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise CampaignDatasetError(f"unsupported campaign schema_version; expected integer {SCHEMA_VERSION}")
    provenance = manifest["provenance"]
    _validate_provenance(provenance, "provenance")

    for key in ("products", "fx", "cash_rates", "corporate_actions"):
        if not isinstance(manifest[key], list):
            raise CampaignDatasetError(f"{key} must be an array")
    if not manifest["products"]:
        raise CampaignDatasetError("at least one product series is required")
    if not manifest["cash_rates"]:
        raise CampaignDatasetError("at least one GBP cash-rate benchmark series is required")
    if len(manifest["products"]) + len(manifest["fx"]) + len(manifest["cash_rates"]) > MAX_SERIES:
        raise CampaignDatasetError(f"manifest contains more than {MAX_SERIES} data series")
    if len(manifest["corporate_actions"]) > MAX_ACTIONS:
        raise CampaignDatasetError(f"manifest contains more than {MAX_ACTIONS} corporate actions")

    identifiers = set()
    filenames = set()
    product_by_id = {}

    def unique_id(value, where):
        identifier = _id(value, where)
        if identifier in identifiers:
            raise CampaignDatasetError(f"duplicate id {identifier!r}")
        identifiers.add(identifier)
        return identifier

    def unique_file(value, where):
        filename = _safe_file(value, where)
        normalized = filename.casefold()
        if normalized in filenames:
            raise CampaignDatasetError(f"duplicate source filename {filename!r}")
        filenames.add(normalized)
        return filename

    for index, product in enumerate(manifest["products"]):
        where = f"products[{index}]"
        _keys(product, ("id", "identifier", "currency", "resolution", "timestamp_semantics", "price_basis", "file"),
              ("metadata", "provenance"), where)
        product_id = unique_id(product["id"], where + ".id")
        _text(product["identifier"], where + ".identifier", 256)
        currency = _currency(product["currency"], where + ".currency")
        if product["resolution"] not in ("daily", "weekly"):
            raise CampaignDatasetError(f"{where}.resolution must be daily or weekly")
        if product["timestamp_semantics"] != "period_end":
            raise CampaignDatasetError(f"{where}.timestamp_semantics must be period_end")
        if product["price_basis"] not in ("unadjusted_ohlcv", "split_adjusted_ohlcv", "total_return_adjusted_ohlcv"):
            raise CampaignDatasetError(f"{where}.price_basis is unsupported")
        unique_file(product["file"], where + ".file")
        if "metadata" in product:
            _metadata(product["metadata"], where + ".metadata")
        if "provenance" in product:
            _validate_provenance(product["provenance"], where + ".provenance")
        product_by_id[product_id] = currency

    fx_currencies = set()
    for index, fx in enumerate(manifest["fx"]):
        where = f"fx[{index}]"
        _keys(fx, ("id", "currency", "direction", "resolution", "timestamp_semantics", "file"),
              ("metadata", "provenance"), where)
        unique_id(fx["id"], where + ".id")
        currency = _currency(fx["currency"], where + ".currency")
        if currency == "GBP":
            raise CampaignDatasetError(f"{where}.currency must be non-GBP")
        if currency in fx_currencies:
            raise CampaignDatasetError(f"multiple FX series supplied for {currency}; selection is ambiguous")
        fx_currencies.add(currency)
        if fx["direction"] != "GBP_PER_UNIT":
            raise CampaignDatasetError(f"{where}.direction must explicitly be GBP_PER_UNIT")
        if fx["resolution"] not in ("daily", "weekly"):
            raise CampaignDatasetError(f"{where}.resolution must be daily or weekly")
        if fx["timestamp_semantics"] != "observation_time":
            raise CampaignDatasetError(f"{where}.timestamp_semantics must be observation_time")
        unique_file(fx["file"], where + ".file")
        if "metadata" in fx:
            _metadata(fx["metadata"], where + ".metadata")
        if "provenance" in fx:
            _validate_provenance(fx["provenance"], where + ".provenance")

    for index, cash in enumerate(manifest["cash_rates"]):
        where = f"cash_rates[{index}]"
        _keys(cash, ("id", "benchmark_identifier", "currency", "rate_unit", "resolution",
                     "timestamp_semantics", "file"), ("metadata", "provenance"), where)
        unique_id(cash["id"], where + ".id")
        _text(cash["benchmark_identifier"], where + ".benchmark_identifier", 256)
        if _currency(cash["currency"], where + ".currency") != "GBP":
            raise CampaignDatasetError(f"{where}.currency must be GBP")
        if cash["rate_unit"] != "annual_decimal":
            raise CampaignDatasetError(f"{where}.rate_unit must be annual_decimal")
        if cash["resolution"] not in ("daily", "weekly"):
            raise CampaignDatasetError(f"{where}.resolution must be daily or weekly")
        if cash["timestamp_semantics"] != "effective_time":
            raise CampaignDatasetError(f"{where}.timestamp_semantics must be effective_time")
        unique_file(cash["file"], where + ".file")
        if "metadata" in cash:
            _metadata(cash["metadata"], where + ".metadata")
        if "provenance" in cash:
            _validate_provenance(cash["provenance"], where + ".provenance")

    actions = manifest["corporate_actions"]
    action_keys = []
    conflicting = set()
    for index, action in enumerate(actions):
        where = f"corporate_actions[{index}]"
        _keys(action, ("id", "product_id", "type", "currency", "effective_at", "available_at", "terms"),
              ("source_metadata", "provenance"), where)
        action_id = unique_id(action["id"], where + ".id")
        product_id = _id(action["product_id"], where + ".product_id")
        if product_id not in product_by_id:
            raise CampaignDatasetError(f"{where}.product_id does not identify an imported product")
        if _currency(action["currency"], where + ".currency") != product_by_id[product_id]:
            raise CampaignDatasetError(f"{where}.currency must match the product currency")
        action_type = action["type"]
        if action_type not in ("split", "cash_dividend", "distribution"):
            raise CampaignDatasetError(f"{where}.type is unsupported")
        _timestamp(action["effective_at"], where + ".effective_at")
        _timestamp(action["available_at"], where + ".available_at")
        terms = action["terms"]
        if action_type == "split":
            _keys(terms, ("numerator", "denominator"), where=where + ".terms")
            _finite_number(terms["numerator"], where + ".terms.numerator", 0, True)
            _finite_number(terms["denominator"], where + ".terms.denominator", 0, True)
        else:
            _keys(terms, ("amount",), where=where + ".terms")
            _finite_number(terms["amount"], where + ".terms.amount", 0, True)
        if "source_metadata" in action:
            _metadata(action["source_metadata"], where + ".source_metadata")
        if "provenance" in action:
            _validate_provenance(action["provenance"], where + ".provenance")
        effective = _timestamp_key(action["effective_at"], where + ".effective_at")
        available = _timestamp_key(action["available_at"], where + ".available_at")
        if available > _source_retrieved_at(action, provenance, where):
            raise CampaignDatasetError(f"{where}.available_at exceeds source retrieved_at")
        ordering_key = (effective, available, product_id, action_id)
        if action_keys and ordering_key < action_keys[-1]:
            raise CampaignDatasetError("corporate_actions must already be ordered by effective_at, available_at, product_id, id")
        action_keys.append(ordering_key)
        conflict_key = (product_id, effective, action_type)
        if conflict_key in conflicting:
            raise CampaignDatasetError("conflicting duplicate corporate actions for product, effective time, and type")
        conflicting.add(conflict_key)


def _parse_csv(payload, filename, columns, kind, retrieved_at):
    try:
        return _parse_csv_checked(payload, filename, columns, kind, retrieved_at)
    except csv.Error as exc:
        raise CampaignDatasetError(f"{filename}: invalid CSV: {exc}") from exc


def _parse_csv_checked(payload, filename, columns, kind, retrieved_at):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CampaignDatasetError(f"{filename}: CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if tuple(reader.fieldnames or ()) != columns:
        raise CampaignDatasetError(f"{filename}: expected CSV columns {', '.join(columns)} in that order")
    rows = 0
    first = last = None
    previous_timestamp = None
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise CampaignDatasetError(f"{filename}:{line_number}: malformed CSV row")
        stamp = _timestamp_key(row["timestamp"], f"{filename}:{line_number}.timestamp")
        available = _timestamp_key(row["available_at"], f"{filename}:{line_number}.available_at")
        if available > retrieved_at:
            raise CampaignDatasetError(f"{filename}:{line_number}: available_at exceeds source retrieved_at")
        if kind in ("product", "fx") and available < stamp:
            raise CampaignDatasetError(
                f"{filename}:{line_number}: available_at must not precede the period-end/observation timestamp"
            )
        if previous_timestamp is not None and stamp <= previous_timestamp:
            raise CampaignDatasetError(f"{filename}:{line_number}: timestamps must be sorted and unique")
        previous_timestamp = stamp
        if kind == "product":
            values = {
                name: _csv_number(row[name], f"{filename}:{line_number}.{name}")
                for name in ("open", "high", "low", "close", "volume")
            }
            if any(values[name] <= 0 for name in ("open", "high", "low", "close")):
                raise CampaignDatasetError(f"{filename}:{line_number}: OHLC prices must be positive")
            if values["volume"] < 0:
                raise CampaignDatasetError(f"{filename}:{line_number}: volume must be non-negative")
            if values["high"] < max(values["open"], values["close"], values["low"]):
                raise CampaignDatasetError(f"{filename}:{line_number}: high violates OHLC invariants")
            if values["low"] > min(values["open"], values["close"], values["high"]):
                raise CampaignDatasetError(f"{filename}:{line_number}: low violates OHLC invariants")
        else:
            value_column = "gbp_per_unit" if kind == "fx" else "annual_rate"
            number = _csv_number(row[value_column], f"{filename}:{line_number}.{value_column}")
            if kind == "fx" and number <= 0:
                raise CampaignDatasetError(f"{filename}:{line_number}: GBP per unit FX must be positive")
            if kind == "cash" and number <= -1:
                raise CampaignDatasetError(f"{filename}:{line_number}: annual rate must be greater than -1")
        first = stamp if first is None else first
        last = stamp
        rows += 1
        if rows > MAX_ROWS_PER_SERIES:
            raise CampaignDatasetError(f"{filename}: exceeds {MAX_ROWS_PER_SERIES} rows")
    if rows == 0:
        raise CampaignDatasetError(f"{filename}: series must contain at least one observation")
    return {
        "rows": rows,
        "first_timestamp": first[0].isoformat().replace("+00:00", "Z"),
        "last_timestamp": last[0].isoformat().replace("+00:00", "Z"),
        "_first_key": first,
        "_last_key": last,
        "sha256": _sha256(payload),
        "bytes": len(payload),
    }


def _csv_number(value, where):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CampaignDatasetError(f"{where} must be numeric") from exc
    if not math.isfinite(number):
        raise CampaignDatasetError(f"{where} must be finite")
    return number


def _validate_payloads(source_manifest, payloads):
    stats = {}
    product_spans = {}
    for product in source_manifest["products"]:
        retrieved_at = _source_retrieved_at(product, source_manifest["provenance"], product["id"])
        info = _parse_csv(
            payloads[product["file"]], product["file"], PRODUCT_COLUMNS, "product", retrieved_at,
        )
        stats[product["file"]] = info
        product_spans[product["id"]] = (
            info["_first_key"],
            info["_last_key"],
            product["currency"],
        )
    fx_spans = {}
    for fx in source_manifest["fx"]:
        retrieved_at = _source_retrieved_at(fx, source_manifest["provenance"], fx["id"])
        info = _parse_csv(payloads[fx["file"]], fx["file"], FX_COLUMNS, "fx", retrieved_at)
        stats[fx["file"]] = info
        fx_spans[fx["currency"]] = (
            info["_first_key"],
            info["_last_key"],
        )
    for cash in source_manifest["cash_rates"]:
        retrieved_at = _source_retrieved_at(cash, source_manifest["provenance"], cash["id"])
        stats[cash["file"]] = _parse_csv(
            payloads[cash["file"]], cash["file"], CASH_COLUMNS, "cash", retrieved_at,
        )

    for product in source_manifest["products"]:
        first, last, currency = product_spans[product["id"]]
        if currency == "GBP":
            continue
        if currency not in fx_spans:
            raise CampaignDatasetError(f"{product['id']}: missing GBP-per-unit FX series for {currency}")
        fx_first, fx_last = fx_spans[currency]
        if fx_first > first or fx_last < last:
            raise CampaignDatasetError(
                f"{product['id']}: FX {currency} observation timestamps do not span product history "
                f"{first[0].isoformat()} through {last[0].isoformat()}"
            )
    return stats


def _build_output_manifest(raw_manifest, source_manifest, payloads):
    stats = _validate_payloads(source_manifest, payloads)
    source_checksums = {
        filename: stats[filename]["sha256"]
        for filename in sorted(stats)
    }
    source_manifest_sha256 = _sha256(raw_manifest)
    identity = _canonical({
        "schema_version": SCHEMA_VERSION,
        "source_manifest_sha256": source_manifest_sha256,
        "source_files": source_checksums,
    }).encode("utf-8")
    dataset_id = _sha256(identity)
    products = {}
    for item in source_manifest["products"]:
        products[item["id"]] = {
            "file": item["file"],
            "currency": item["currency"],
            "resolution": item["resolution"],
            "timestamp_semantics": item["timestamp_semantics"],
            "price_basis": item["price_basis"],
            "rows": stats[item["file"]]["rows"],
            "first_timestamp": stats[item["file"]]["first_timestamp"],
            "last_timestamp": stats[item["file"]]["last_timestamp"],
        }
    fx = {}
    for item in source_manifest["fx"]:
        fx[item["id"]] = {
            "file": item["file"],
            "currency": item["currency"],
            "direction": item["direction"],
            "resolution": item["resolution"],
            "rows": stats[item["file"]]["rows"],
            "first_timestamp": stats[item["file"]]["first_timestamp"],
            "last_timestamp": stats[item["file"]]["last_timestamp"],
        }
    cash_rates = {}
    for item in source_manifest["cash_rates"]:
        cash_rates[item["id"]] = {
            "file": item["file"],
            "currency": item["currency"],
            "rate_unit": item["rate_unit"],
            "resolution": item["resolution"],
            "rows": stats[item["file"]]["rows"],
            "first_timestamp": stats[item["file"]]["first_timestamp"],
            "last_timestamp": stats[item["file"]]["last_timestamp"],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "source_manifest_sha256": source_manifest_sha256,
        "source_checksums": source_checksums,
        "series": {"products": products, "fx": fx, "cash_rates": cash_rates},
        "source_manifest": source_manifest,
        "unresolved_checks": UNRESOLVED,
    }


def import_campaign_dataset(manifest_path, dataset_dir):
    """Validate local inputs and store raw files in a content-addressed immutable directory."""
    raw_manifest, source_manifest, payloads = _load_source(manifest_path)
    output_manifest = _build_output_manifest(raw_manifest, source_manifest, payloads)
    root = Path(dataset_dir)
    datasets = root / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    destination = datasets / output_manifest["dataset_id"]
    if destination.exists():
        CampaignDataset.open(root, output_manifest["dataset_id"])
        return output_manifest["dataset_id"]

    staging = Path(tempfile.mkdtemp(prefix=".campaign-staging-", dir=str(datasets)))
    try:
        source = staging / "source"
        source.mkdir()
        (source / "manifest.json").write_bytes(raw_manifest)
        for filename, payload in payloads.items():
            (source / filename).write_bytes(payload)
        serialized = (_canonical(output_manifest) + "\n").encode("utf-8")
        (staging / "dataset.json").write_bytes(serialized)
        try:
            staging.rename(destination)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            CampaignDataset.open(root, output_manifest["dataset_id"])
            shutil.rmtree(staging)
        return output_manifest["dataset_id"]
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


class CampaignDataset:
    """Opened immutable dataset whose source manifest and payload hashes were revalidated."""

    def __init__(self, directory, manifest):
        self.directory = Path(directory)
        self.manifest = manifest
        self.dataset_id = manifest["dataset_id"]

    @classmethod
    def open(cls, dataset_dir, dataset_id):
        if not isinstance(dataset_id, str) or not DATASET_ID.fullmatch(dataset_id):
            raise CampaignDatasetError("dataset id must be a 64-character lowercase SHA-256")
        directory = Path(dataset_dir) / "datasets" / dataset_id
        source_dir = directory / "source"
        manifest_path = directory / "dataset.json"
        if directory.is_symlink():
            raise CampaignDatasetError(f"campaign dataset {dataset_id} must not be a symlink")
        if not source_dir.is_dir() or source_dir.is_symlink():
            raise CampaignDatasetError(f"campaign dataset {dataset_id} is missing its source directory")
        output_manifest = _load_json_bytes(_read_bounded_file(
            manifest_path, MAX_OUTPUT_MANIFEST_BYTES, "generated dataset manifest",
            "generated dataset manifest exceeds the size limit",
        ))
        raw_manifest_path = source_dir / "manifest.json"
        raw_manifest = _read_bounded_file(
            raw_manifest_path, MAX_MANIFEST_BYTES, "preserved source manifest",
            "preserved source manifest exceeds the size limit",
        )
        source_manifest = _load_json_bytes(raw_manifest)
        _validate_manifest(source_manifest)
        payloads = {}
        references = _file_references(source_manifest)
        total_size = len(raw_manifest)
        for filename in references:
            path = source_dir / filename
            if total_size > MAX_TOTAL_SOURCE_BYTES:
                raise CampaignDatasetError("preserved source files exceed the aggregate size limit")
            remaining = MAX_TOTAL_SOURCE_BYTES - total_size
            limit = min(MAX_CSV_BYTES, remaining)
            size_error = ("preserved source files exceed the aggregate size limit"
                          if remaining < MAX_CSV_BYTES
                          else f"preserved source file {filename!r} exceeds size limit")
            payloads[filename] = _read_bounded_file(path, limit, f"preserved source file {filename!r}", size_error)
            total_size += len(payloads[filename])
        expected = _build_output_manifest(raw_manifest, source_manifest, payloads)
        try:
            actual_canonical = _canonical(output_manifest)
            expected_canonical = _canonical(expected)
        except (TypeError, ValueError, RecursionError) as exc:
            raise CampaignDatasetError("campaign dataset manifest contains invalid JSON values") from exc
        if actual_canonical != expected_canonical or output_manifest.get("dataset_id") != dataset_id:
            raise CampaignDatasetError("campaign dataset checksum/manifest mismatch (tampered or corrupt)")
        expected_names = {"dataset.json", "source"}
        if {path.name for path in directory.iterdir()} != expected_names:
            raise CampaignDatasetError("campaign dataset contains unexpected top-level files")
        if {path.name for path in source_dir.iterdir()} != {"manifest.json", *references}:
            raise CampaignDatasetError("campaign dataset source directory contains unexpected or missing files")
        return cls(directory, output_manifest)

    @property
    def source_manifest(self):
        return self.manifest["source_manifest"]

    def source_file(self, filename):
        if filename not in self.manifest["source_checksums"]:
            raise CampaignDatasetError(f"{filename!r} is not a source data file in this dataset")
        payload = _read_bounded_file(
            self.directory / "source" / filename, MAX_CSV_BYTES, f"{filename!r}: source",
            f"{filename!r}: source exceeds size limit",
        )
        if _sha256(payload) != self.manifest["source_checksums"][filename]:
            raise CampaignDatasetError(f"{filename!r}: source checksum mismatch")
        return payload

    def checksums(self):
        """Return a copy of the validated source-file checksums, excluding the manifest checksum."""
        return dict(self.manifest["source_checksums"])
