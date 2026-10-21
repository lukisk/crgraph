"""A one-line progress display on stderr, redrawn in place while crgraph works. Only on a terminal: piped
output and CI logs never see it."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from typing import TextIO

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TICK = 0.1  # seconds between redraws, so a long step (the AI review) still shows it is alive
CLEAR = "\r\033[K"


class Progress:
    def __init__(self, stream: TextIO | None = None, *, enabled: bool | None = None, tick: float = TICK):
        self.stream = stream or sys.stderr
        if enabled is None:
            enabled = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self.enabled = enabled
        self.label, self.done, self.total = "", 0, None
        self._start = time.monotonic()
        self._frame = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(tick,), daemon=True) if enabled else None
        if self._thread is not None:
            self._thread.start()

    def stage(self, label: str, total: int | None = None) -> None:
        with self._lock:
            self.label, self.done, self.total = label, 0, total
        self._draw()

    def advance(self, n: int = 1) -> None:
        """Drawn on the next tick: thousands of files must not mean thousands of terminal writes."""
        with self._lock:
            self.done += n

    def close(self) -> None:
        """Clears the line, so whatever is printed next starts on a clean one."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None
        with self._lock:
            self.stream.write(CLEAR)
            self.stream.flush()

    def __enter__(self) -> Progress:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def line(self) -> str:
        count = f" {self.done}/{self.total}" if self.total else ""
        return f"{FRAMES[self._frame % len(FRAMES)]} crgraph: {self.label}{count} · {time.monotonic() - self._start:.1f}s"

    def _draw(self) -> None:
        if not self.enabled or self._stop.is_set():
            return
        with self._lock:
            width = max(20, shutil.get_terminal_size().columns - 1)  # a wrapped line could not be redrawn
            self.stream.write(CLEAR + self.line()[:width])
            self.stream.flush()

    def _run(self, tick: float) -> None:
        while not self._stop.wait(tick):
            self._frame += 1
            self._draw()


QUIET = Progress(enabled=False)
