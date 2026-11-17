"""Stable report contract for CLI and dashboard; pure demo, read-only recorded mode."""

from decimal import Decimal, localcontext
from datetime import timedelta

from . import InvestmentError
from .core import MAX_EVENTS, ROLES, ZERO, clock, fmt, iso, number, replay
from .store import canonical, digest, read, read_with_clock


LIMITATIONS = [
    "Manual source evidence is not broker truth, custody verification or execution ownership.",
    "Sleeves are human declarations, not proof of unleveraged UK access. Eligibility, "
    "custody/ownership, settlement and venue costs remain unverified.",
    "GBP only; FX, other currencies, corrections and actions other than splits are unsupported.",
    "Only evidenced interest/dividend payments are recorded; no accrual, entitlement or "
    "payment-coverage claim is made.",
    "No opening-position cost basis or inception is invented; empty evidenced inception required.",
    "Actual fill fees reduce cash/equity and gain; no forecast fees or automatic income adjustments.",
    "MWR is unavailable; inflation, tax, research, broker features and automatic execution deferred.",
    "Proposals are blocked pending approved and implemented role-aware policy, products, "
    "weights and loss/transfer rules.",
    "External deposit cash is not broker buying power; scheduled contributions are not spendable cash.",
    "Review acknowledgements are manual records, not trade approvals. Five-point drift "
    "remains a trigger.",
]


def _reported_timestamps(value, fields):
    if value is None:
        return None
    result = value.copy()
    for field in fields:
        result[field] = iso(clock(result[field]))
    return result


def report(ledger, now, source_mode="recorded"):
    if ledger.spec["schema_version"] == 2:
        return opening_report(ledger, now, source_mode)
    with localcontext() as context:
        context.prec = 100
        equity, as_of = ledger.valuation()
        products = {p["id"]: p for p in ledger.spec["products"]}
        holdings = []
        values = {"equity": ZERO, "bond": ZERO, "cash": sum(ledger.cash.values(), ZERO)}
        for (account, product_id), quantity in sorted(ledger.holdings.items()):
            if quantity == 0:
                continue
            product = products[product_id]
            mark = ledger.marks.get((account, product_id))
            value = quantity * mark[0] if mark else None
            if value is not None:
                values[product["sleeve"]] += value
            holdings.append({"account_id": account, "product_id": product_id,
                             "name": product["name"], "sleeve": product["sleeve"],
                             "quantity": fmt(quantity), "value_gbp": fmt(value)})
        allocation = []
        for sleeve in ("equity", "bond", "cash"):
            target = number(ledger.spec["policy"]["targets"][sleeve])
            actual = values[sleeve] / equity * 100 if equity is not None and equity > 0 else None
            allocation.append({"sleeve": sleeve, "target_pct": fmt(target),
                               "actual_pct": fmt(actual),
                               "drift_pp": fmt(actual - target) if actual is not None else None})
        warnings = []
        if equity is None:
            warnings.append("Incomplete valuation: all nonzero holdings need common-clock marks "
                            "at or after the latest event; equity/gain unavailable.")
        elif now - as_of >= timedelta(seconds=ledger.spec["policy"]["max_mark_age_seconds"]):
            warnings.append("Stale valuation shown explicitly as-of; proposal blocked.")
        performance = ledger.performance_valid and ledger.units > 0 and equity is not None
        if not performance:
            warnings.append("TWR/drawdown unavailable: no fully evidenced common-clock "
                            "valuation/flow chain from inception.")
        proposal = {
            "status": "blocked",
            "reason": "Role-aware policy, products, weights and loss/transfer rules are not approved or implemented.",
            "expires_at": None, "orders": [], "manual_transfers": [], "execution_enabled": False,
        }
        return {
            "schema_version": 1, "source_mode": source_mode,
            "portfolio_id": ledger.spec["portfolio_id"], "revision": ledger.revision,
            "generated_at": iso(now), "as_of": iso(as_of) if as_of else None,
            "currency": "GBP", "complete": equity is not None, "equity_gbp": fmt(equity),
            "net_contributions_gbp": fmt(ledger.net),
            "gain_gbp": fmt(equity - ledger.net) if equity is not None else None,
            "twr_pct": fmt((ledger.unit_price - 1) * 100) if performance else None,
            "max_drawdown_pct": fmt(-ledger.drawdown * 100) if performance else None,
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                          "cash_gbp": fmt(ledger.cash[a["id"]])} for a in ledger.spec["accounts"]],
            "holdings": holdings, "allocation": allocation, "proposal": proposal,
            "warnings": warnings, "limitations": LIMITATIONS.copy(),
        }


def opening_report(ledger, now, source_mode="recorded"):
    """Report version 2 opening evidence without inventing lifetime history."""
    with localcontext() as context:
        context.prec = 100
        equity, as_of = ledger.valuation()
        baseline = ledger.baseline_value
        gain = (equity - baseline - (ledger.net - ledger.baseline_net)
                if equity is not None and baseline is not None else None)
        recorded_interest = sum(
            (number(value["amount_gbp"]) for value in ledger.ids.values()
             if value.get("type") == "interest" and
             value["id"] not in ledger.superseded_ids), ZERO)
        recorded_dividends = sum(
            (number(value["amount_gbp"]) for value in ledger.ids.values()
             if value.get("type") == "dividend" and
             value["id"] not in ledger.superseded_ids), ZERO)
        recorded_fee_events = [
            _reported_timestamps({
                **{key: value[key] for key in (
                    "id", "type", "account_id", "product_id", "quantity",
                    "price_gbp", "fee_gbp", "timestamp", "available_at", "evidence")},
                "role": value.get("role", "unassigned"),
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values()
            if value.get("type") in ("buy", "sell") and
            value["id"] not in ledger.superseded_ids
        ]
        recorded_income_events = [
            _reported_timestamps({
                "id": value["id"], "type": value["type"],
                "account_id": value["account_id"],
                "product_id": value.get("product_id"),
                "role": value.get("role", "unassigned"),
                "role_evidence": value.get("role_evidence"),
                "amount_gbp": value["amount_gbp"],
                "timestamp": value["timestamp"],
                "available_at": value["available_at"],
                "evidence": value["evidence"],
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values()
            if value.get("type") in ("interest", "dividend") and
            value["id"] not in ledger.superseded_ids
        ]
        recorded_flow_events = [
            _reported_timestamps({
                "id": value["id"], "type": value["type"],
                "account_id": value["account_id"],
                "destination_account_id": value.get("destination_account_id"),
                "role": value.get("role", "unassigned"),
                "amount_gbp": value["amount_gbp"],
                "timestamp": value["timestamp"],
                "available_at": value["available_at"],
                "evidence": value["evidence"],
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values()
            if value.get("type") in ("contribution", "withdrawal", "transfer") and
            value["id"] not in ledger.superseded_ids
        ]
        recorded_corrections = [
            _reported_timestamps({
                "id": value["id"], "original_event_id": value["original_event_id"],
                "replacement_event_id": value["replacement_event"]["id"],
                "replaced_type": value["replacement_event"]["type"],
                "account_id": value["replacement_event"]["account_id"],
                "role": value["replacement_event"].get("role", "unassigned"),
                "role_evidence": value["replacement_event"].get("role_evidence"),
                "original_amount_gbp": ledger.ids[value["original_event_id"]]["amount_gbp"],
                "replacement_amount_gbp": value["replacement_event"]["amount_gbp"],
                "timestamp": value["timestamp"], "available_at": value["available_at"],
                "evidence": value["evidence"],
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values() if value.get("type") == "correction"
        ]
        products = {item["id"]: item for item in ledger.spec["products"]}
        holdings = []
        for (account, product_id), quantity in sorted(ledger.holdings.items()):
            if quantity <= 0:
                continue
            mark = ledger.marks.get((account, product_id))
            product = products[product_id]
            holdings.append({
                "account_id": account, "product_id": product_id,
                "name": product["name"], "sleeve": product["sleeve"],
                "quantity": fmt(quantity), "value_gbp": fmt(quantity * mark[0]) if mark else None,
                "mark_at": iso(mark[1]) if mark else None,
                "mark_available_at": iso(mark[2]) if mark else None,
            })
        performance = (ledger.performance_valid and ledger.units > 0
                       and equity is not None and ledger.baseline_at is not None)
        warnings = ([] if equity is not None else
                    ["A complete common-clock valuation is unavailable."])
        for account, observations in sorted(ledger.cash_observation_history.items()):
            discrepancies = [
                observation for observation in observations
                if Decimal(observation["difference_gbp"]) != 0
            ]
            if discrepancies:
                first, latest = discrepancies[0], discrepancies[-1]
                if first is latest:
                    warnings.append(
                        f"Recorded cash observation for account {account} differs by GBP "
                        f"{first['difference_gbp']} at {iso(clock(first['timestamp']))} (source: "
                        f"{first['source']}; evidence: {first['evidence']}); no correction "
                        "was applied, and later matching observations do not resolve it.")
                else:
                    warnings.append(
                        f"{len(discrepancies)} recorded cash observations for account {account} "
                        f"differ from recorded balances. Earliest: GBP {first['difference_gbp']} at "
                        f"{iso(clock(first['timestamp']))} (source: {first['source']}; evidence: "
                        f"{first['evidence']}). Latest: GBP {latest['difference_gbp']} at "
                        f"{iso(clock(latest['timestamp']))} (source: {latest['source']}; evidence: "
                        f"{latest['evidence']}); no correction was applied, and later matching "
                        "observations do not resolve prior differences.")
        cash_observations = sorted(
            ((account, observation)
             for account, observations in ledger.cash_observation_history.items()
             for observation in observations),
            key=lambda item: (clock(item[1]["timestamp"]), item[0]))
        role_cash = (ledger.role_cash if ledger.role_active else
                     {(account, "unassigned"): amount for account, amount in ledger.cash.items()})
        role_holdings = (ledger.role_holdings if ledger.role_active else
                         {(*key, "unassigned"): amount for key, amount in ledger.holdings.items()})
        role_values = []
        for role in ROLES:
            cash = sum((amount for (account, owner), amount in role_cash.items()
                        if owner == role), ZERO)
            role_value = None
            if equity is not None and (not ledger.role_active or ledger.role_last_time <= as_of):
                role_value = cash + sum(
                    (quantity * ledger.marks[(account, product)][0]
                     for (account, product, owner), quantity in role_holdings.items()
                     if owner == role and quantity > 0), ZERO)
            role_values.append({
                "role": role, "cash_gbp": fmt(cash) if ledger.opened else None,
                "value_gbp": fmt(role_value),
                "accounts": [
                    {"account_id": account["id"],
                     "cash_gbp": fmt(role_cash.get((account["id"], role), ZERO))}
                    for account in ledger.spec["accounts"]
                ] if ledger.opened else [],
                "holdings": [
                    {"account_id": account, "product_id": product, "quantity": fmt(quantity)}
                    for (account, product, owner), quantity in sorted(role_holdings.items())
                    if owner == role and quantity > 0
                ],
            })
        role_assignments = [
            _reported_timestamps({
                key: value[key] for key in (
                    "id", "account_id", "product_id", "role", "amount",
                    "timestamp", "available_at", "evidence")
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values() if value.get("type") == "role_assignment"
        ]
        return {
            "schema_version": 2, "source_mode": source_mode,
            "portfolio_id": ledger.spec["portfolio_id"], "revision": ledger.revision,
            "generated_at": iso(now), "as_of": iso(as_of) if as_of else None,
            "baseline_as_of": iso(ledger.baseline_at) if ledger.baseline_at else None,
            "currency": "GBP", "complete": equity is not None,
            "equity_gbp": fmt(equity), "net_contributions_gbp": fmt(ledger.net),
            "since_baseline_gain_gbp": fmt(gain), "lifetime_gain_gbp": None,
            "recorded_fees_gbp": fmt(ledger.fees),
            "recorded_interest_gbp": fmt(recorded_interest),
            "recorded_dividends_gbp": fmt(recorded_dividends),
            "recorded_income_gbp": fmt(recorded_interest + recorded_dividends),
            "recorded_fee_events": recorded_fee_events,
            "recorded_income_events": recorded_income_events,
            "recorded_flow_events": recorded_flow_events,
            "recorded_corrections": recorded_corrections,
            "twr_pct": fmt((ledger.unit_price - 1) * 100) if performance else None,
            "max_drawdown_pct": fmt(-ledger.drawdown * 100) if performance else None,
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                          "cash_gbp": fmt(ledger.cash[a["id"]]) if ledger.opened else None,
                          "terms": _reported_timestamps(
                              ledger.account_terms[a["id"]],
                              ("observed_at", "available_at")),
                          "cash_observation": _reported_timestamps(
                              ledger.cash_observations.get(a["id"]),
                              ("timestamp", "available_at"))}
                         for a in ledger.spec["accounts"]],
            "cash_observations": [
                {"account_id": account, **_reported_timestamps(
                    observation, ("timestamp", "available_at"))}
                for account, observation in cash_observations
            ],
            "valuation_history": ledger.valuation_history,
            "holdings": holdings,
            "role_accounting": role_values,
            "role_accounting_at": iso(ledger.role_last_time) if ledger.role_active else None,
            "role_assignments": role_assignments,
            "proposal": {"status": "blocked", "reason": "Allocation and role policy are not implemented",
                         "expires_at": None, "orders": [], "manual_transfers": [],
                         "execution_enabled": False},
            "warnings": warnings,
            "limitations": [
                "Opening holdings and cash are operator-recorded evidence, not broker verification.",
                "Lifetime cost basis and pre-baseline gain are unavailable.",
                "Returns are since the first complete, contemporaneously evidenced baseline.",
                "No valuation freshness threshold is configured; check the recorded dates.",
                "Cash observations are evidence only; discrepancies do not update recorded balances.",
                "Fee and income totals include only explicitly recorded events; completeness, tax and entitlement are not verified.",
                "Role allocations are operator evidence, not independent custody verification or risk budgets.",
                "Allocation policy, proposals and execution are not implemented for this store.",
            ],
        }


def snapshot(database, now=None):
    now = clock(now)
    return report(read(database, now), now)


def review_schedule(ledger, now, allocation):
    created = clock(ledger.spec["created_at"])
    monthly_anchor = ledger.monthly_review_at or created
    annual_anchor = ledger.annual_review_at or created
    monthly = (now.year, now.month) > (monthly_anchor.year, monthly_anchor.month)
    if annual_anchor.year == 9999:
        annual = False
    else:
        try:
            anniversary = annual_anchor.replace(year=annual_anchor.year + 1)
        except ValueError:
            anniversary = annual_anchor.replace(year=annual_anchor.year + 1, day=28)
        annual = now >= anniversary
    drifts = [Decimal(a["drift_pp"]) for a in allocation if a["drift_pp"] is not None]
    return {"monthly_due": monthly, "annual_due": annual,
            "drift_due": any(drift.copy_abs() >= 5 for drift in drifts) if drifts else None}


def review_status(database, now=None):
    ledger, now = read_with_clock(database, now)
    if ledger.spec["schema_version"] == 2:
        raise InvestmentError("Review status requires an implemented allocation policy")
    result = report(ledger, now)
    schedule = review_schedule(ledger, now, result["allocation"])
    valuation_status = ledger.valuation_status(now)
    if valuation_status != "fresh":
        schedule["drift_due"] = None
    blocked = None
    if valuation_status != "fresh":
        blocked = "A complete, fresh recorded valuation is required."
    elif ledger.revision >= MAX_EVENTS:
        blocked = "Investment event count has reached its limit."
    return {
        "schema_version": 1, "portfolio_id": result["portfolio_id"],
        "revision": ledger.revision, "generated_at": iso(now),
        "valuation_as_of": result["as_of"], "valuation_status": valuation_status,
        "last_monthly_review_at": iso(ledger.monthly_review_at) if ledger.monthly_review_at else None,
        "last_annual_review_at": iso(ledger.annual_review_at) if ledger.annual_review_at else None,
        **schedule, "acknowledgement_available": blocked is None,
        "acknowledgement_blocked_reason": blocked,
    }


def demo_profile(now):
    return {
        "schema_version": 1, "portfolio_id": "synthetic-demo", "currency": "GBP",
        "created_at": iso(now),
        "accounts": [
            {"id": "broker", "name": "Synthetic broker", "kind": "broker",
             "binding": "synthetic-broker", "currency": "GBP"},
            {"id": "savings", "name": "Synthetic external bank deposit", "kind": "deposit",
             "binding": "synthetic-deposit", "currency": "GBP"}],
        "products": [{"id": "equity", "name": "Synthetic GBP equity fund", "sleeve": "equity",
                      "currency": "GBP", "quantity_increment": "1",
                      "minimum_notional_gbp": "10"}],
        "policy": {"id": "human-demo-80-20", "targets": {"equity": "80", "bond": "0", "cash": "20"},
                   "max_mark_age_seconds": 86400, "proposal_ttl_seconds": 3600,
                   "monthly_contribution_gbp": "100"},
    }


def demo_events(now, contribution=False):
    common = {"timestamp": iso(now), "available_at": iso(now), "currency": "GBP",
              "evidence": "synthetic demonstration only"}
    values = [
        dict(common, id="broker-funding", type="contribution", account_id="broker", amount_gbp="800"),
        dict(common, id="deposit-funding", type="contribution", account_id="savings", amount_gbp="200"),
        dict(common, id="reference", type="mark", account_id="broker", product_id="equity",
             price_gbp="10"),
        dict(common, id="buy", type="buy", account_id="broker", product_id="equity",
             quantity="80", price_gbp="10", fee_gbp="0"),
    ]
    if contribution:
        values.append(dict(common, id="optional-100", type="contribution", account_id="broker",
                           amount_gbp="100"))
    return values


def demo_snapshot(now=None):
    now = clock(now)
    return report(replay(demo_profile(now), demo_events(now), now), now, "demo")


def proposal_export(database, now=None):
    """Binding envelope; never an executable instruction."""
    now = clock(now)
    ledger = read(database, now)
    if ledger.spec["schema_version"] == 2:
        raise InvestmentError("Proposal export requires an implemented allocation policy")
    result = report(ledger, now)
    return {"schema_version": 1, "portfolio_id": result["portfolio_id"],
            "revision": result["revision"], "policy_id": ledger.spec["policy"]["id"],
            "policy_hash": digest(canonical(ledger.spec["policy"])),
            "generated_at": result["generated_at"], "expires_at": result["proposal"]["expires_at"],
            "proposal": result["proposal"], "execution_enabled": False}
