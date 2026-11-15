import math

import pandas as pd


def effective_time(action):
    return pd.Timestamp(action["ex_date"], tz="America/New_York").tz_convert("UTC")


def action_key(action):
    return effective_time(action), action["kind"] != "split", action["id"]


def ordered_actions(actions):
    unique = {}
    for action in actions:
        identity = action.get("id")
        if not isinstance(identity, str) or not identity or action.get("kind") not in ("split", "dividend"):
            raise ValueError("Corporate action requires a nonempty ID and supported kind")
        if identity in unique and unique[identity] != action:
            raise ValueError(f"Conflicting corporate action ID: {identity}")
        unique[identity] = dict(action)
    return sorted(unique.values(), key=action_key)


def normalize_actions(payload):
    actions = []
    supported = {"forward_splits", "reverse_splits", "cash_dividends"}
    for kind, records in payload.items():
        if records and kind not in supported:
            raise ValueError(f"Unsupported corporate action type: {kind}")
        for record in records:
            action = dict(record)
            if not action.get("ex_date") or action.get("currency", "USD") not in ("", "USD"):
                raise ValueError("Corporate action has missing date or unsupported currency")
            if kind == "cash_dividends":
                if not action.get("payable_date") or action.get("due_bill_on_date"):
                    raise ValueError("Dividend requires payment date and ordinary ex-date entitlement")
                action["kind"] = "dividend"
                value = float(action["rate"])
                if value < 0 or not math.isfinite(value):
                    raise ValueError("Invalid dividend rate")
                action["rate"] = value
            else:
                action["kind"] = "split"
                action["ratio"] = float(action["new_rate"]) / float(action["old_rate"])
                if action["ratio"] <= 0 or not math.isfinite(action["ratio"]):
                    raise ValueError("Invalid split ratio")
                if action.get("new_symbol") not in (None, "", action["symbol"]):
                    raise ValueError("Ticker-changing split requires explicit symbol mapping")
            actions.append(action)
    return ordered_actions(actions)


def adjusted_window(frame, actions, asof):
    adjusted = frame.copy()
    for action in ordered_actions(actions):
        timestamp = effective_time(action)
        if timestamp > asof:
            continue
        prior = adjusted.index < timestamp
        columns = ["open", "high", "low", "close"]
        if action["kind"] == "split":
            adjusted.loc[prior, columns] /= action["ratio"]
            adjusted.loc[prior, "volume"] *= action["ratio"]
        else:
            adjusted.loc[prior, columns] -= action["rate"]
    return adjusted


def continuous_prices(frame, actions):
    factor = pd.Series(1.0, index=frame.index)
    offset = pd.Series(0.0, index=frame.index)
    for action in ordered_actions(actions):
        after = frame.index >= effective_time(action)
        if action["kind"] == "split":
            factor.loc[after] *= action["ratio"]
        else:
            offset.loc[after] += action["rate"] * factor.loc[after]
    adjusted = frame.copy()
    for column in ("open", "high", "low", "close"):
        adjusted[column] = frame[column] * factor + offset
    return adjusted, factor