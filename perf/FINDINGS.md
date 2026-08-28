## Summary

While benchmarking the incremental-notebook-update resolve path added in #6, we investigated how much a single out-of-band edit **blocks the server event loop**, and what fraction of that blocking is attributable to the `ContentsManager` read (file read + `nbformat` parse/validate) versus our own hashing/diff code.

**Headline result: ~80% of the event-loop-blocked time during a resolve is the `ContentsManager` read.** Our BLAKE3 `build_manifest` accounts for ~18% and diff + message-build for ~2%. The loop is blocked for the large majority (~70-85%) of the resolve's wall time — and, surprisingly, this happens **even though `jupyter_server` already offloads the read to a worker thread**, because `nbformat.reads` is pure-Python and holds the GIL.

## Background

The resolve hot path is `LiveContentManager._on_disk_change`:

1. `read_notebook` → `ContentsManager.get(content=True)` (file read + `nbformat` parse/validate + optional whole-file hash)
2. `build_manifest` → BLAKE3 over **every** cell's source + filtered metadata
3. `diff_manifests` → O(n) id/hash comparison
4. `update_message` → inline the changed cells

A structurally important fact: even a single-cell edit triggers a **full re-read and full re-hash**, so cost scales with notebook size N, not edit size.

`jupyter_server`'s `AsyncLargeFileManager` runs the file read and `nbformat.reads` via `anyio.to_thread.run_sync` (a worker thread), while steps 2-4 run directly on the event loop. The open question was: since the read is "off-loop", does it actually stop blocking the loop?

## Methodology

All measurements use a realistic synthetic notebook generator (mixed code/markdown cells with plausible content, `nbformat` 4.5 cell ids, varied length, occasional tags/metadata), sweeping notebook size in 1000-cell increments. One random single-cell out-of-band edit per repeat; 9 repeats per size; medians reported.

**Event-loop lag probe.** A heartbeat coroutine `await`s a fixed-interval `asyncio.sleep(tick)` and records how late each wakeup arrives. Any lateness means the loop could not run a ready callback on time = the loop was blocked (whether by on-loop computation or by GIL contention from a worker thread). Each busy interval `[expected_wake, actual_wake]` is attributed to whichever resolve stage was running, by time-overlap. This correctly separates:

- offloaded stages (read): many small gaps from GIL contention while the worker thread holds the GIL, and
- on-loop stages (`build_manifest`, diff): one long gap covering the whole computation.

**Two-pass timing.** Wall time and memory are measured in separate passes. Running `tracemalloc` inside the timed block inflated wall time ~15x (per-allocation tracing over `nbformat` parse + manifest build), which produced a bogus early number; timing runs with `tracemalloc` off.

**Noise-floor characterization.** The sub-millisecond `asyncio.sleep` probe has a measurable noise floor from OS timer/scheduler granularity. Measured against a fully idle loop:

|   tick | idle-loop "blocked" (noise floor) |
| -----: | --------------------------------: |
|  500us |                              ~57% |
| 2000us |                               ~4% |
| 5000us |                               ~2% |

All results below use a **2ms tick** (≈4% noise floor). The noise floor barely affects the _relative_ attribution (there are almost no idle gaps mid-resolve to accumulate noise), but it does inflate the absolute "blocked % of wall", so it must be subtracted when interpreting that column.

## Findings

### 1. Loop-blocking attribution (2ms tick)

| cells | wall ms | loop-blocked ms | blk/wall % |  read | build_manifest | diff+msg | **read % of blocking** |
| ----: | ------: | --------------: | ---------: | ----: | -------------: | -------: | ---------------------: |
|  1000 |    39.1 |            29.1 |        74% |  22.6 |            5.5 |      0.7 |              **78.9%** |
|  2000 |    77.9 |            61.9 |        79% |  47.3 |           12.8 |      1.4 |              **77.0%** |
|  4000 |   157.2 |           127.2 |        81% |  96.0 |           27.5 |      3.2 |              **75.9%** |
|  8000 |   380.0 |           323.7 |        85% | 258.2 |           57.6 |      7.1 |              **80.0%** |
| 10000 |   467.4 |           397.4 |        85% | 315.4 |           71.4 |      9.1 |              **79.7%** |

The read's ~80% share is stable across probe resolutions (500us tick → 79-82%; 2ms tick → 76-80%), which gives confidence in the relative number.

### 2. Everything is linear in cell count — no cliff

~40ms of wall time and ~3MB of `tracemalloc` peak per 1000 cells across the 1k-10k range. Backend thread count stays flat at 3; CPU utilization ≈ 1.0 (one core). A single edit to a 10k-cell notebook stalls the loop ~400ms; extrapolating, ~20k cells would cross ~1s. This is the same event-loop-blocking failure class as jupyter/jupyter_ydoc#401, made concrete — but here it is the read+hash, not a diff.

### 3. Thread offload does NOT protect the loop, because of the GIL

The most important structural finding. `nbformat.reads` runs on a worker thread but holds the GIL the entire time, starving the loop thread. Control experiment (worker-thread task, 2ms tick, 3.8% noise floor):

```
IDLE loop 0.3s            blocked  3.8%   <- noise floor
GIL-freed sleep 0.3s      blocked  4.0%   <- releases GIL: loop truly free
GIL-held nbformat.reads   blocked 73.8%   <- holds GIL: loop starved despite being "off-loop"
```

The IDLE and GIL-freed baselines are identical at every tick, proving the "blocking" during GIL-freed work is pure probe artifact; GIL-held work sits far above it. That is real blocking.

## Implications

- Optimizing our own hashing (~18% of blocking) is not the lever. The `ContentsManager` read/parse (~80%) is.
- The usual "offload the read to a thread" mitigation does **not** help here — `jupyter_server` already offloads it, and the GIL defeats it. Reducing loop blocking requires a **GIL-releasing** read path: e.g. an `orjson`-based parse instead of `nbformat.reads`, or a process pool for the read.
- Because a single-cell edit forces a full re-read + full re-hash, the cost is inherently O(N) in notebook size regardless of edit size. Incremental server-side reads are a possible longer-term optimization but are out of scope for #6.

## Caveats

- These are single-notebook, tmpfs/page-cache-warm reads. A cold-disk read would move more time into GIL-releasing file I/O (which does not block the loop), lowering the read's blocking share somewhat.
- The `psutil` "max CPU %" sampler is noisy under batch-window sampling; the reliable signal is CPU utilization ≈ 1.0 (single core).
- Absolute "blocked % of wall" figures are tick-sensitive (see noise floor); relative attribution is not.

## Reproduction

Benchmark suite lives under `perf/` (added in the context of #6):

- `perf/notebook_factory.py` — realistic notebook generator
- `perf/instrument.py` — resource sampler + two-pass time/memory measurement
- `perf/bench_resolve_single_edit.py` — Stage 1: resolve cost vs notebook size
- `perf/bench_loop_blocking.py` — event-loop lag probe + per-stage blocking attribution

```
.venv/bin/python perf/bench_loop_blocking.py --sizes 1000 2000 4000 8000 10000 --repeats 9 --tick-us 2000
```

Related: PR #6 (the implementation being benchmarked), design issue #5.
