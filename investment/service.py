"""Stable report contract for CLI and dashboard; pure demo, read-only recorded mode."""

from decimal import Decimal, localcontext

from .core import ROLES, ZERO, clock, fmt, iso, number, replay
from .store import read_with_clock


def _reported_timestamps(value, fields):
    if value is None:
        return None
    result = value.copy()
    for field in fields:
        result[field] = iso(clock(result[field]))
    return result


def report(ledger, now, source_mode="recorded"):
    """Report opening evidence without inventing lifetime history."""
    with localcontext() as context:
        context.prec = 100
        equity, as_of = ledger.valuation()
        baseline = ledger.baseline_value
        gain = (equity - baseline - (ledger.net - ledger.baseline_net) -
                (ledger.scope_additions - ledger.baseline_scope_additions)
                if equity is not None and baseline is not None and
                not ledger.historical_correction else None)
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
        recorded_account_fees = [
            _reported_timestamps({
                "id": value["id"], "type": "account_fee",
                "account_id": value["account_id"],
                "role": value.get("role", "unassigned"),
                "amount_gbp": value["amount_gbp"],
                "timestamp": value["timestamp"],
                "available_at": value["available_at"],
                "evidence": value["evidence"],
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values()
            if value.get("type") == "account_fee" and
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
                "account_id": value["replacement_event"].get("account_id"),
                "role": value["replacement_event"].get("role", "unassigned"),
                "role_evidence": value["replacement_event"].get("role_evidence"),
                "original_amount_gbp": ledger.ids[value["original_event_id"]].get("amount_gbp"),
                "replacement_amount_gbp": value["replacement_event"].get("amount_gbp"),
                "timestamp": value["timestamp"], "available_at": value["available_at"],
                "evidence": value["evidence"],
            }, ("timestamp", "available_at"))
            for value in ledger.ids.values() if value.get("type") == "correction"
        ]
        def reported_profile_change(value):
            if value["type"] == "scope_addition":
                target, entity = "account", value["account"]
                action = "scope_addition"
            elif value["action"] == "add_product":
                target, entity = "product", value["product"]
                action = "add_product"
            elif value["action"] == "rename":
                target, entity = value["target"], {
                    "id": value["entity_id"], "name": value["name"],
                }
                action = "rename"
            else:
                target, entity = value["target"], {"id": value["entity_id"]}
                action = "retire"
            return _reported_timestamps({
                "id": value["id"], "action": action, "target": target,
                "entity_id": entity["id"], "name": entity.get("name"),
                "timestamp": value["timestamp"],
                "available_at": value["available_at"], "evidence": value["evidence"],
            }, ("timestamp", "available_at"))

        recorded_profile_changes = [
            reported_profile_change(value)
            for value in ledger.profile_history
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
        if ledger.historical_correction:
            warnings.append(
                "Historical corrections preserve original evidence but invalidate historical performance; "
                "no new baseline was established.")
            if ledger.cash_observation_history:
                warnings.append(
                    "Historical correction replay may have changed past cash-observation differences. "
                    "Preserve prior reports and review the original observations and correction evidence.")
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
            "recorded_account_fees": recorded_account_fees,
            "recorded_income_events": recorded_income_events,
            "recorded_flow_events": recorded_flow_events,
            "recorded_corrections": recorded_corrections,
            "recorded_profile_changes": recorded_profile_changes,
            "recorded_scope_additions_gbp": fmt(ledger.scope_additions),
            "recorded_scope_additions": [
                _reported_timestamps(value, ("timestamp", "available_at"))
                for value in ledger.scope_addition_history
            ],
            "twr_pct": fmt((ledger.unit_price - 1) * 100) if performance else None,
            "max_drawdown_pct": fmt(-ledger.drawdown * 100) if performance else None,
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                          "status": "active" if a["id"] in ledger.active_accounts else "retired",
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
                "Profile changes preserve identity and prior evidence; retired definitions remain in the journal.",
            ],
        }


def snapshot(database, now=None, *, clock_source=None):
    ledger, observed = read_with_clock(database, now, clock_source=clock_source)
    return report(ledger, observed)


def demo_profile(now):
    return {
        "schema_version": 2, "portfolio_id": "synthetic-demo", "currency": "GBP",
        "created_at": iso(now),
        "accounts": [
            {"id": "broker", "name": "Synthetic broker", "kind": "broker",
             "binding": "synthetic-broker", "currency": "GBP"},
            {"id": "savings", "name": "Synthetic external bank deposit", "kind": "deposit",
             "binding": "synthetic-deposit", "currency": "GBP"}],
        "products": [{"id": "equity", "name": "Synthetic GBP equity fund", "sleeve": "equity",
                      "currency": "GBP", "quantity_increment": "1",
                      "minimum_notional_gbp": "10"}],
        "policy": None,
    }


def demo_events(now):
    return [{
        "id": "opening", "type": "opening", "timestamp": iso(now),
        "available_at": iso(now), "evidence": "synthetic demonstration only",
        "balances_gbp": {"broker": "0", "savings": "200"},
        "holdings": [{"account_id": "broker", "product_id": "equity",
                      "quantity": "80", "price_gbp": "10"}],
    }]


def demo_snapshot(now=None):
    now = clock(now)
    return report(replay(demo_profile(now), demo_events(now), now), now, "demo")


# Existing opening/setup callers use this semantic name; it is the same sole contract.
opening_report = report
