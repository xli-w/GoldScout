"""Synthetic v2 investment snapshot used by the local dashboard demo."""

from datetime import timedelta

from investment.core import clock, iso, replay
from investment.service import report


def _demo_ledger(now):
    now = clock(now)
    opened_at = now - timedelta(days=120)
    role_at = opened_at + timedelta(hours=1)
    profile = {
        "schema_version": 2,
        "portfolio_id": "synthetic-demo",
        "currency": "GBP",
        "created_at": iso(opened_at),
        "accounts": [
            {"id": "bank", "name": "Synthetic savings account", "kind": "deposit",
             "binding": "synthetic-bank", "currency": "GBP"},
            {"id": "broker", "name": "Synthetic investment account", "kind": "broker",
             "binding": "synthetic-broker", "currency": "GBP"},
        ],
        "products": [
            {"id": "global-equity", "name": "Synthetic global equity fund",
             "sleeve": "equity", "currency": "GBP", "quantity_increment": "1",
             "minimum_notional_gbp": "1"},
            {"id": "bond-fund", "name": "Synthetic bond fund", "sleeve": "bond",
             "currency": "GBP", "quantity_increment": "1", "minimum_notional_gbp": "1"},
        ],
        "policy": None,
    }

    def evidence(identifier, kind, timestamp, description, **fields):
        return {
            "id": identifier,
            "type": kind,
            "timestamp": iso(timestamp),
            "available_at": iso(timestamp),
            "evidence": description,
            **fields,
        }

    events = [
        evidence("demo-opening", "opening", opened_at,
                 "Synthetic opening statement; demonstration only",
                 balances_gbp={"bank": "5000", "broker": "1000"},
                 holdings=[
                     {"account_id": "broker", "product_id": "global-equity",
                      "quantity": "20", "price_gbp": "100"},
                     {"account_id": "broker", "product_id": "bond-fund",
                      "quantity": "20", "price_gbp": "50"},
                 ]),
    ]
    for identifier, account_id, product_id, role, amount in (
        ("demo-core-cash", "bank", None, "core", "5000"),
        ("demo-dynamic-cash", "broker", None, "dynamic", "1000"),
        ("demo-core-equity", "broker", "global-equity", "core", "15"),
        ("demo-dynamic-equity", "broker", "global-equity", "dynamic", "5"),
        ("demo-core-bonds", "broker", "bond-fund", "core", "20"),
    ):
        events.append(evidence(
            identifier, "role_assignment", role_at,
            "Synthetic role attribution; not a target or recommendation",
            account_id=account_id, product_id=product_id, role=role, amount=amount,
        ))

    terms_at = now - timedelta(days=60)
    events.extend([
        evidence(
            "demo-savings-terms", "savings_terms", terms_at,
            "Synthetic savings terms; not provider-verified",
            account_id="bank", annual_rate_pct="4.00", rate_basis="AER",
            effective_from=terms_at.date().isoformat(), source="Synthetic statement",
            maturity_date=None, notice_days=None,
        ),
        evidence(
            "demo-equity-mark-1", "mark", now - timedelta(days=45),
            "Synthetic equity price observation", account_id="broker",
            product_id="global-equity", currency="GBP", price_gbp="108",
        ),
        evidence(
            "demo-bond-mark-1", "mark", now - timedelta(days=45),
            "Synthetic bond price observation", account_id="broker",
            product_id="bond-fund", currency="GBP", price_gbp="51",
        ),
        evidence(
            "demo-contribution", "contribution", now - timedelta(days=30),
            "Synthetic contribution receipt", account_id="bank", currency="GBP",
            amount_gbp="250", role="core",
        ),
        evidence(
            "demo-interest", "interest", now - timedelta(days=20),
            "Synthetic interest statement", account_id="bank", currency="GBP",
            amount_gbp="20", role="core",
            role_evidence="Synthetic core cash attribution",
        ),
        evidence(
            "demo-cash-observation", "cash_observation", now - timedelta(days=14),
            "Synthetic cash statement evidence", account_id="bank",
            balance_gbp="5260", source="Synthetic statement",
        ),
        evidence(
            "demo-equity-mark-2", "mark", now - timedelta(days=2),
            "Synthetic equity price observation", account_id="broker",
            product_id="global-equity", currency="GBP", price_gbp="112",
        ),
        evidence(
            "demo-bond-mark-2", "mark", now - timedelta(days=2),
            "Synthetic bond price observation", account_id="broker",
            product_id="bond-fund", currency="GBP", price_gbp="52",
        ),
    ])
    return replay(profile, events, now)


def demo_opening_snapshot(now=None):
    now = clock(now)
    return report(_demo_ledger(now), now, "demo")


def demo_monitoring_snapshot(now=None):
    from investment.monitoring import project
    from investment.store import canonical, digest

    now = clock(now)
    ledger = _demo_ledger(now)
    profile = {
        "schema_version": 1, "portfolio_id": ledger.spec["portfolio_id"],
        "investment_profile_sha256": digest(canonical(ledger.spec)),
        "observed_at": iso(now), "concentration_threshold_pct": "60",
        "accounts": [
            {"account_id": a["id"], "provider": "etoro" if a["kind"] == "broker" else "manual",
             "wrapper": "other", "denominator": a["kind"] == "broker",
             "max_age_seconds": 7 * 86400, "last_reviewed_at": None,
             "review_interval_days": 30, "evidence": "Synthetic monitoring configuration only"}
            for a in ledger.spec["accounts"]
        ],
    }
    return project(ledger, profile, now, source_mode="demo")
