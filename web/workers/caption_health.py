"""Failure circuit for caption inference that cannot fit in GPU memory."""

from __future__ import annotations


class CaptionOomCircuit:
    """Open after repeated minimum-batch OOMs instead of retrying forever."""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = max(1, int(threshold))
        self.consecutive_failures = 0

    def reset(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> bool:
        self.consecutive_failures += 1
        return self.consecutive_failures >= self.threshold
