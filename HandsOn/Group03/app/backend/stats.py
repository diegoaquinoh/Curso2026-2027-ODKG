"""Small statistics helpers (no NumPy needed for 31 stations)."""
from __future__ import annotations

import math
from collections.abc import Sequence


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation coefficient, or None when it is undefined."""
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    """Least-squares line y = slope * x + intercept, or None when undefined."""
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def describe_strength(r: float | None) -> str | None:
    """Conventional verbal label for |r|."""
    if r is None:
        return None
    a = abs(r)
    if a < 0.1:
        label = "no meaningful"
    elif a < 0.3:
        label = "weak"
    elif a < 0.5:
        label = "moderate"
    else:
        label = "strong"
    if label == "no meaningful":
        return "no meaningful linear association"
    return f"{label} {'positive' if r > 0 else 'negative'} association"
