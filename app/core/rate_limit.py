"""Failure-counting rate limiter for the unauthenticated auth endpoints.

Only *failed* attempts are counted, so a busy shop whose cashiers log in all
day is never throttled — the counter for a key is cleared the moment a
credential checks out. That matters here because a PIN is 4-6 digits: without
a cap, `pin-login` is 10,000 guesses away from any shop's till.

State is per process and lost on restart. With one backend container (see
infra/docker-compose.prod.yml) that is the whole picture; if this is ever
scaled to several replicas, the counters need to move to Postgres or Redis or
each replica will grant an attacker its own budget.
"""

import time
from collections import deque
from dataclasses import dataclass, field

# Generous enough to absorb genuine fat-fingering at the register, small
# enough that exhausting a 4-digit PIN space takes ~10 days rather than
# minutes.
MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60

# Stops a long-running process from accumulating a key per attacker-chosen
# tenant_id forever. Well above any real shop's traffic; when it's hit, the
# fully-expired keys are dropped first.
_MAX_TRACKED_KEYS = 10_000


@dataclass
class FailureLimiter:
    max_failures: int = MAX_FAILURES
    window_seconds: int = WINDOW_SECONDS
    _failures: dict[str, deque[float]] = field(default_factory=dict)

    def retry_after(self, key: str) -> int | None:
        """Seconds the caller must wait, or None if the attempt may proceed."""
        attempts = self._live_attempts(key)
        if attempts is None or len(attempts) < self.max_failures:
            return None
        # The window slides, so the block lifts when the oldest failure ages out.
        return max(1, int(attempts[0] + self.window_seconds - time.monotonic()) + 1)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        attempts = self._failures.get(key)
        if attempts is None:
            self._evict_if_full()
            attempts = self._failures.setdefault(key, deque())
        self._drop_expired(attempts, now)
        attempts.append(now)

    def clear(self, key: str) -> None:
        """Called on a successful login — one good credential wipes the slate."""
        self._failures.pop(key, None)

    def reset(self) -> None:
        self._failures.clear()

    def _live_attempts(self, key: str) -> deque[float] | None:
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        self._drop_expired(attempts, time.monotonic())
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def _drop_expired(self, attempts: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

    def _evict_if_full(self) -> None:
        if len(self._failures) < _MAX_TRACKED_KEYS:
            return
        now = time.monotonic()
        for key in [k for k, v in self._failures.items() if not v or v[-1] <= now - self.window_seconds]:
            del self._failures[key]
        if len(self._failures) >= _MAX_TRACKED_KEYS:
            # Nothing has expired yet — drop the oldest-touched key so a flood
            # of live attempts can't grow this map without bound.
            del self._failures[min(self._failures, key=lambda k: self._failures[k][-1])]


login_limiter = FailureLimiter()
pin_login_limiter = FailureLimiter()


def reset_all() -> None:
    """Test helper — the limiters are module-level singletons, so without this
    one test's failed logins would count against the next one's."""
    login_limiter.reset()
    pin_login_limiter.reset()
