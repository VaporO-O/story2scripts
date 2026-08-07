"""Small dependency-free statistics helpers for repeated evaluation runs."""

from __future__ import annotations

import math
import statistics

# Two-sided 95% Student-t critical values, indexed by degrees of freedom.
_T95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
}


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
    return ordered[index]


def summarize_values(
    values: list[float | int | bool],
    *,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
) -> dict[str, float | int]:
    numeric = [float(value) for value in values]
    count = len(numeric)
    if not numeric:
        return {
            "count": 0,
            "mean": 0.0,
            "stddev": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "p50": 0.0,
            "p95": 0.0,
        }

    mean = statistics.fmean(numeric)
    stddev = statistics.stdev(numeric) if count > 1 else 0.0
    critical = _T95.get(count - 1, 1.96)
    margin = critical * stddev / math.sqrt(count) if count > 1 else 0.0
    low = mean - margin
    high = mean + margin
    if lower_bound is not None:
        low = max(lower_bound, low)
        high = max(lower_bound, high)
    if upper_bound is not None:
        low = min(upper_bound, low)
        high = min(upper_bound, high)
    return {
        "count": count,
        "mean": round(mean, 4),
        "stddev": round(stddev, 4),
        "ci95_low": round(low, 4),
        "ci95_high": round(high, 4),
        "p50": round(_percentile(numeric, 0.5), 4),
        "p95": round(_percentile(numeric, 0.95), 4),
    }
