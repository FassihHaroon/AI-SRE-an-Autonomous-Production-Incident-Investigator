"""Seeded, counter-based randomness.

Fault effects (which request fails, how much jitter) must be repeatable. ``Sampler`` draws the
n-th value for a key from ``blake2b(seed:key:n)``, so the same seed and the same request order
give the same decisions, independent of wall-clock time and of other keys.
"""

from __future__ import annotations

import hashlib


class Sampler:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def uniform(self, seed: int, key: str) -> float:
        """Next value in [0, 1) for ``key``."""
        n = self._counters.get(key, 0)
        self._counters[key] = n + 1
        digest = hashlib.blake2b(f"{seed}:{key}:{n}".encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big") / 2**64

    def chance(self, seed: int, key: str, probability: float) -> bool:
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        return self.uniform(seed, key) < probability

    def jitter(self, seed: int, key: str, amount: float) -> float:
        """Symmetric jitter in [-amount, +amount]."""
        if amount <= 0:
            return 0.0
        return (self.uniform(seed, key) * 2 - 1) * amount

    def forget(self, key: str) -> None:
        """Drop the counters of ``key`` and its sub-keys (``key:...``)."""
        for k in [k for k in self._counters if k == key or k.startswith(f"{key}:")]:
            del self._counters[k]

    def reset(self) -> None:
        self._counters.clear()


sampler = Sampler()
"""Process-wide sampler used by fault hooks."""
