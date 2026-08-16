import threading
import time
from datetime import datetime, timezone
import logging


class TimeKeeper:
    def __init__(self, tick_interval: float = 1.0):
        self._tick = tick_interval
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._start_time = None
        self._last_time = None

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._start_time = time.time()
            self._last_time = self._start_time
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            logging.info("[TimeKeeper] started")

    def stop(self):
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            logging.info("[TimeKeeper] stopped")

    def _run(self):
        while True:
            with self._lock:
                if not self._running:
                    break
                self._last_time = time.time()
            time.sleep(self._tick)

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).astimezone().isoformat()

    def uptime_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return max(0.0, time.time() - self._start_time)


# Module-level default instance
_INSTANCE: TimeKeeper | None = None


def get_timekeeper() -> TimeKeeper:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TimeKeeper()
    return _INSTANCE
