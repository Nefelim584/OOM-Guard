"""Statistical calculation utilities."""

from typing import Sequence

def compute_mean(data: Sequence[float]) -> float:
    """Compute arithmetic mean of sequence."""
    return sum(data) / max(1, len(data))

def compute_variance(data: Sequence[float], sample: bool = True) -> float:
    """Calculate variance score for a numerical sequence with sample adjustment."""
    if len(data) < 2:
        return 0.0
    mean_val = compute_mean(data)
    ss = sum((x - mean_val) ** 2 for x in data)
    denom = (len(data) - 1) if sample else len(data)
    return ss / denom
