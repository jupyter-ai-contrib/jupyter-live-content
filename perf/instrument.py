# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Lightweight resource instrumentation for the perf benchmarks.

Provides:
- ``ResourceSampler``: a background thread that polls the current process for
  thread count and CPU% at a fixed interval, capturing the peak while a block of
  code runs. Used for "threads used" and "max CPU" metrics.
- ``measure``: a context manager that combines wall time, process CPU time,
  ``tracemalloc`` peak, and RSS delta for the enclosed block.
"""
from __future__ import annotations

import threading
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, List

import psutil


class ResourceSampler:
    """Polls process thread-count and CPU% on a background thread.

    Start it, run the code you care about, stop it, then read ``max_threads``
    and ``max_cpu_percent``. CPU% is normalized to a single core by psutil
    (can exceed 100 on multi-core work).
    """

    def __init__(self, interval_s: float = 0.005) -> None:
        self.interval_s = interval_s
        self._proc = psutil.Process()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.thread_samples: List[int] = []
        self.cpu_samples: List[float] = []

    def _run(self) -> None:
        # Prime cpu_percent so the first real sample is meaningful.
        self._proc.cpu_percent(None)
        while not self._stop.is_set():
            try:
                self.thread_samples.append(self._proc.num_threads())
                self.cpu_samples.append(self._proc.cpu_percent(None))
            except psutil.Error:
                pass
            time.sleep(self.interval_s)

    def __enter__(self) -> "ResourceSampler":
        self._stop.clear()
        self.thread_samples.clear()
        self.cpu_samples.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def max_threads(self) -> int:
        return max(self.thread_samples, default=self._proc.num_threads())

    @property
    def max_cpu_percent(self) -> float:
        # Drop the priming zero-ish first sample if we have several.
        samples = self.cpu_samples[1:] if len(self.cpu_samples) > 2 else self.cpu_samples
        return max(samples, default=0.0)


@dataclass
class TimeMeasurement:
    wall_s: float = 0.0
    cpu_s: float = 0.0
    rss_after_bytes: int = 0


@dataclass
class MemMeasurement:
    tracemalloc_peak_bytes: int = 0
    rss_delta_bytes: int = 0
    rss_after_bytes: int = 0


@contextmanager
def measure_time() -> Iterator[TimeMeasurement]:
    """Measure wall + CPU time for a block. Does NOT enable tracemalloc.

    tracemalloc traces every allocation and inflates wall time for
    allocation-heavy code (e.g. nbformat parse) by 10x+, so it must never be
    active while timing. Memory is captured in a separate pass by
    :func:`measure_memory`.
    """
    proc = psutil.Process()
    m = TimeMeasurement()
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    try:
        yield m
    finally:
        m.cpu_s = time.process_time() - cpu0
        m.wall_s = time.perf_counter() - wall0
        m.rss_after_bytes = proc.memory_info().rss


@contextmanager
def measure_memory() -> Iterator[MemMeasurement]:
    """Measure tracemalloc peak + RSS delta for a block. Timing is meaningless
    here (tracemalloc overhead dominates); run this in a separate pass."""
    proc = psutil.Process()
    m = MemMeasurement()
    tracemalloc.start()
    rss_before = proc.memory_info().rss
    try:
        yield m
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        m.tracemalloc_peak_bytes = peak
        m.rss_after_bytes = proc.memory_info().rss
        m.rss_delta_bytes = m.rss_after_bytes - rss_before
