"""Offline investment accounting; no execution or broker integration."""


class InvestmentError(ValueError):
    """Invalid, unsupported, or unverified investment evidence."""


__all__ = ["InvestmentError"]
