"""Shared KPI achievement calculation used by API and report surfaces."""
from __future__ import annotations


def calculate_achievement(actual: float | None, target: float | None, direction: str | None) -> tuple[float | None, bool | None]:
    if actual is None or target is None:
        return None, None
    lower_is_better = any(token in (direction or "").casefold() for token in
                          ("lower", "decrease", "maximum", "max ", "at most", "reduce"))
    if lower_is_better:
        return (100.0 if actual <= target else round(target / actual * 100, 1) if actual else 100.0,
                actual > target)
    if target <= 0:
        return None, None
    return round(actual / target * 100, 1), actual < target
