# Performance benchmarks

Stress tests for the `jupyterlab-live-content` server, isolating each
performance surface so a regression can be attributed to a specific stage.

Run with the workspace venv (has `blake3`, `jupyter_server`, `psutil`, `plotly`):

```bash
../.venv/bin/python bench_resolve_single_edit.py --sizes 1000 2000 3000 --repeats 25
```

## Stage 1: `bench_resolve_single_edit.py`

Measures the cost to **resolve one random out-of-band single-cell edit** as a
function of notebook size. It mirrors `LiveContentManager._on_disk_change`
exactly (read -> `build_manifest` -> `diff_manifests` -> `update_message`),
driven through a real async `ContentsManager`, minus the WebSocket fan-out.

Metrics per size: time (median + p95), a per-stage time breakdown, tracemalloc
peak + RSS, max backend threads, and max backend CPU. Outputs a table, CSV,
JSON, and an interactive Plotly HTML chart under `results/`.

### Measurement notes

- **Time and memory are measured in separate passes.** `tracemalloc` traces
  every allocation and inflates wall time ~15x for parse/hash-heavy work, so it
  is never active while timing (see `instrument.py`).
- **CPU utilization** (`cpu_s / wall_s`) is the reliable per-op CPU signal; the
  psutil `max CPU %` sampled across the batch window is noisier.
- Reads go through `AsyncLargeFileManager` (production default) so the nbformat
  read/validate cost lands on the event loop exactly as in the server.

## Stage 3: `bench_loop_blocking.py`

Answers a sharper question than wall time: **how much does resolving one edit
actually block the event loop, and what fraction of that blocking is the
`ContentsManager` read** (file read + `nbformat` parse/validate) versus our own
hashing/diff code.

A heartbeat coroutine `await`s a fixed-interval `asyncio.sleep(tick)` and records
how late each wakeup arrives; any lateness means the loop could not run a ready
callback = it was blocked. Each busy interval is attributed to whichever resolve
stage was running, by time-overlap. This correctly counts both on-loop
computation (`build_manifest`, diff) and GIL contention from the worker thread
that runs the offloaded read.

```bash
../.venv/bin/python bench_loop_blocking.py --sizes 1000 2000 4000 8000 10000 --repeats 9 --tick-us 2000
```

### Measurement notes

- The sub-millisecond `asyncio.sleep` probe has a noise floor from OS timer
  granularity: ~57% at a 500us tick, ~4% at 2ms, ~2% at 5ms (measured against an
  idle loop). Use `--tick-us 2000` and subtract the floor when reading the
  absolute "blocked % of wall"; the _relative_ stage attribution is tick-stable.

## Findings

See the write-up in [`FINDINGS.md`](./FINDINGS.md) and the tracking issue
[#8](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/issues/8).
Headline: ~80% of event-loop-blocked time on a resolve is the `ContentsManager`
read, and thread offload does **not** protect the loop because `nbformat.reads`
holds the GIL. Everything is linear in cell count (~40ms/1000 cells) with no
cliff in the 1k-10k range.

## Planned

- Stage 2: multi-edit fan-out (200/400/600/800/1000 edits per size).
- WebSocket fan-out across K clients.
