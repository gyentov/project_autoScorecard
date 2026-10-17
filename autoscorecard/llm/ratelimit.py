"""Sliding-window requests-per-minute limiter (one per provider)."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class RpmLimiter:
    def __init__(self, rpm: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rpm = rpm
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def wait_time(self) -> float:
        """Seconds until a request may be sent (0 if allowed now)."""
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= 60:
            self._stamps.popleft()
        if len(self._stamps) < self.rpm:
            return 0.0
        return 60 - (now - self._stamps[0])

    def acquire(self, on_wait: Callable[[float], None] | None = None) -> None:
        with self._lock:
            delay = self.wait_time()
            if delay > 0:
                if on_wait:
                    on_wait(delay)
                self._sleep(delay)
                self.wait_time()   # drop the expired stamp
            self._stamps.append(self._clock())
