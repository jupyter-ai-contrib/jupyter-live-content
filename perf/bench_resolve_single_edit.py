# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Stage 1 benchmark: cost to resolve ONE random out-of-band single-cell edit,
as a function of notebook size.

For each notebook size N we:
  1. Write a realistic N-cell notebook to a temp dir and read it once through a
     real (async) ContentsManager to seed the "currently open" manifest, exactly
     as ``LiveContentManager`` does on subscribe.
  2. Repeatedly: make one random single-cell source edit *on disk* (an
     out-of-band write), then run the server resolve pipeline
     (read -> build_manifest -> diff -> update_message) -- the exact work
     ``LiveContentManager._on_disk_change`` performs, minus the WS fan-out.

We report, per size:
  - Time to process   (median + p95 wall time of the resolve)
  - Memory            (tracemalloc peak of the resolve; RSS after)
  - Threads used      (max process threads during the batch)
  - Max CPU usage     (max process cpu% during the batch; + cpu utilization)

Run:
  .venv/bin/python perf/bench_resolve_single_edit.py --sizes 1000 2000 3000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

# Make sibling perf modules importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nbformat  # noqa: E402
from jupyter_server.services.contents.largefilemanager import (  # noqa: E402
    AsyncLargeFileManager,
)

from jupyterlab_live_content import nb_hash, nb_service  # noqa: E402

from instrument import ResourceSampler, measure_memory, measure_time  # noqa: E402
from notebook_factory import make_notebook  # noqa: E402


@dataclass
class SizeResult:
    n_cells: int
    file_bytes: int
    repeats: int
    # time
    wall_ms_median: float
    wall_ms_p95: float
    # time breakdown (median ms) -- attributes where the resolve time goes
    read_ms: float          # ContentsManager get(content=True) incl. nbformat validate
    hash_ms: float          # require_hash whole-file get
    manifest_ms: float      # BLAKE3 build_manifest over all cells
    diffmsg_ms: float       # diff_manifests + update_message
    # memory
    tracemalloc_peak_mb_median: float
    rss_after_mb: float
    # threads / cpu
    max_threads: int
    max_cpu_percent: float
    cpu_utilization: float  # cpu_s / wall_s (a value > 1.0 means multi-threaded)


def _write_notebook(cm_root: str, path: str, nb: Dict[str, Any]) -> int:
    """Write a notebook to disk out-of-band (bypassing the ContentsManager).
    Returns the file size in bytes."""
    abspath = os.path.join(cm_root, path)
    text = nbformat.writes(nbformat.from_dict(nb))
    with open(abspath, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.getsize(abspath)


def _edit_one_cell_on_disk(cm_root: str, path: str, rng: random.Random) -> None:
    """Read the on-disk notebook, mutate one random cell's source, write back."""
    abspath = os.path.join(cm_root, path)
    with open(abspath, "r", encoding="utf-8") as f:
        nb = json.load(f)
    cells = nb.get("cells", [])
    idx = rng.randrange(len(cells))
    src = cells[idx].get("source", "")
    if isinstance(src, list):
        src = "".join(src)
    cells[idx]["source"] = src + f"\n# out-of-band edit {rng.randint(0, 10**9)}\n"
    with open(abspath, "w", encoding="utf-8") as f:
        json.dump(nb, f)


async def _resolve(cm: Any, path: str, baseline: nb_hash.NbManifest):
    """Exactly mirrors LiveContentManager._on_disk_change for a notebook."""
    nbcontent, file_meta = await nb_service.read_notebook(cm, path)
    manifest = nb_service.build_manifest(nbcontent, file_meta)
    diff = nb_hash.diff_manifests(baseline, manifest)
    msg = nb_service.update_message(path, nbcontent, manifest, diff.changed)
    return manifest, msg, diff


async def _staged_resolve(cm: Any, path: str, baseline: nb_hash.NbManifest):
    """Same pipeline, timing each stage. Returns (new_manifest, stage_ms)."""
    from jupyter_server.utils import ensure_async

    stage: Dict[str, float] = {}
    s = time.perf_counter()
    model = await ensure_async(cm.get(path, content=True, type="notebook"))
    nbcontent = model["content"]
    stage["read_ms"] = (time.perf_counter() - s) * 1000.0

    s = time.perf_counter()
    file_meta = {"last_modified": _iso(model.get("last_modified")),
                 "hash": None, "hash_algorithm": None}
    try:
        meta_model = await ensure_async(
            cm.get(path, content=False, require_hash=True))
        file_meta["hash"] = meta_model.get("hash")
        file_meta["hash_algorithm"] = meta_model.get("hash_algorithm")
    except TypeError:
        pass
    stage["hash_ms"] = (time.perf_counter() - s) * 1000.0

    s = time.perf_counter()
    manifest = nb_service.build_manifest(nbcontent, file_meta)
    stage["manifest_ms"] = (time.perf_counter() - s) * 1000.0

    s = time.perf_counter()
    diff = nb_hash.diff_manifests(baseline, manifest)
    nb_service.update_message(path, nbcontent, manifest, diff.changed)
    stage["diffmsg_ms"] = (time.perf_counter() - s) * 1000.0
    return manifest, stage


def _iso(value: Any):
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


async def bench_size(n_cells: int, repeats: int, seed: int) -> SizeResult:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as root:
        cm = AsyncLargeFileManager(root_dir=root)
        path = "bench.ipynb"
        nb = make_notebook(n_cells, seed=seed)
        file_bytes = _write_notebook(root, path, nb)

        # Seed baseline manifest as the manager does on subscribe.
        nbcontent, file_meta = await nb_service.read_notebook(cm, path)
        baseline = nb_service.build_manifest(nbcontent, file_meta)

        wall_ms: List[float] = []
        cpu_utils: List[float] = []
        stages: Dict[str, List[float]] = {
            "read_ms": [], "hash_ms": [], "manifest_ms": [], "diffmsg_ms": []}
        rss_after = 0.0

        # -- Pass 1: timing + threads/CPU (NO tracemalloc) --------------------
        with ResourceSampler(interval_s=0.004) as sampler:
            for _ in range(repeats):
                _edit_one_cell_on_disk(root, path, rng)
                with measure_time() as m:
                    manifest, _msg, diff = await _resolve(cm, path, baseline)
                assert len(diff.changed) == 1, f"expected 1 changed, got {diff.changed}"
                baseline = manifest
                wall_ms.append(m.wall_s * 1000.0)
                cpu_utils.append((m.cpu_s / m.wall_s) if m.wall_s else 0.0)
                rss_after = m.rss_after_bytes / 1e6
                await asyncio.sleep(0.002)
            max_threads = sampler.max_threads
            max_cpu = sampler.max_cpu_percent

        # -- Pass 2: per-stage timing breakdown (attribution) ----------------
        for _ in range(max(3, repeats // 3)):
            _edit_one_cell_on_disk(root, path, rng)
            manifest, stage = await _staged_resolve(cm, path, baseline)
            baseline = manifest
            for k, v in stage.items():
                stages[k].append(v)

        # -- Pass 3: memory peak (tracemalloc; timing meaningless here) -------
        peak_mb: List[float] = []
        for _ in range(max(3, repeats // 5)):
            _edit_one_cell_on_disk(root, path, rng)
            with measure_memory() as mem:
                manifest, _msg, _diff = await _resolve(cm, path, baseline)
            baseline = manifest
            peak_mb.append(mem.tracemalloc_peak_bytes / 1e6)

    wall_sorted = sorted(wall_ms)
    p95 = wall_sorted[min(len(wall_sorted) - 1, int(0.95 * len(wall_sorted)))]
    med = statistics.median
    return SizeResult(
        n_cells=n_cells,
        file_bytes=file_bytes,
        repeats=repeats,
        wall_ms_median=round(med(wall_ms), 3),
        wall_ms_p95=round(p95, 3),
        read_ms=round(med(stages["read_ms"]), 3),
        hash_ms=round(med(stages["hash_ms"]), 3),
        manifest_ms=round(med(stages["manifest_ms"]), 3),
        diffmsg_ms=round(med(stages["diffmsg_ms"]), 3),
        tracemalloc_peak_mb_median=round(med(peak_mb), 3),
        rss_after_mb=round(rss_after, 1),
        max_threads=max_threads,
        max_cpu_percent=round(max_cpu, 1),
        cpu_utilization=round(med(cpu_utils), 2),
    )


def _print_table(results: List[SizeResult]) -> None:
    cols = [
        ("cells", "n_cells", "{}"),
        ("file MB", "file_bytes", lambda v: f"{v/1e6:.2f}"),
        ("time ms (med)", "wall_ms_median", "{}"),
        ("p95 ms", "wall_ms_p95", "{}"),
        ("read ms", "read_ms", "{}"),
        ("hash ms", "hash_ms", "{}"),
        ("manifest ms", "manifest_ms", "{}"),
        ("diff+msg ms", "diffmsg_ms", "{}"),
        ("mem peak MB", "tracemalloc_peak_mb_median", "{}"),
        ("RSS MB", "rss_after_mb", "{}"),
        ("threads", "max_threads", "{}"),
        ("max CPU %", "max_cpu_percent", "{}"),
        ("cpu util", "cpu_utilization", "{}"),
    ]
    header = " | ".join(f"{c[0]:>12}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in results:
        row = []
        for _title, attr, fmt in cols:
            v = getattr(r, attr)
            row.append((fmt(v) if callable(fmt) else fmt.format(v)))
        print(" | ".join(f"{cell:>12}" for cell in row))


def _render_chart(results: List[SizeResult], out_html: Path) -> bool:
    try:
        from plotly.subplots import make_subplots
        import plotly.graph_objects as go
    except ImportError:
        return False
    x = [r.n_cells for r in results]
    fig = make_subplots(
        rows=3,
        cols=2,
        subplot_titles=(
            "Time to process (ms)",
            "Where the time goes (stacked ms)",
            "Memory: tracemalloc peak (MB)",
            "Process RSS after (MB)",
            "Backend threads (max)",
            "Max backend CPU (%)",
        ),
    )
    fig.add_trace(go.Scatter(x=x, y=[r.wall_ms_median for r in results],
                             mode="lines+markers", name="median"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=[r.wall_ms_p95 for r in results],
                             mode="lines+markers", name="p95"), row=1, col=1)
    # Stage breakdown as stacked bars.
    for label, attr in (("read+validate", "read_ms"), ("whole-file hash", "hash_ms"),
                        ("build_manifest", "manifest_ms"), ("diff+msg", "diffmsg_ms")):
        fig.add_trace(go.Bar(x=x, y=[getattr(r, attr) for r in results], name=label),
                      row=1, col=2)
    fig.update_layout(barmode="stack")
    fig.add_trace(go.Scatter(x=x, y=[r.tracemalloc_peak_mb_median for r in results],
                             mode="lines+markers", name="peak MB",
                             showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=x, y=[r.rss_after_mb for r in results],
                             mode="lines+markers", name="RSS MB",
                             showlegend=False), row=2, col=2)
    fig.add_trace(go.Scatter(x=x, y=[r.max_threads for r in results],
                             mode="lines+markers", name="threads",
                             showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=[r.max_cpu_percent for r in results],
                             mode="lines+markers", name="CPU %",
                             showlegend=False), row=3, col=2)
    for rr in (1, 2, 3):
        for cc in (1, 2):
            fig.update_xaxes(title_text="cells", row=rr, col=cc)
    fig.update_layout(
        title_text="Resolve one random out-of-band single-cell edit vs notebook size",
        template="plotly_white",
        height=1050,
        width=1150,
    )
    fig.write_html(str(out_html), include_plotlyjs="cdn")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="+",
                    default=[1000, 2000, 3000],
                    help="Notebook sizes in cells to sweep.")
    ap.add_argument("--repeats", type=int, default=15,
                    help="Random single-cell edits measured per size.")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--outdir", type=str,
                    default=os.path.join(os.path.dirname(__file__), "results"))
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    results: List[SizeResult] = []
    for n in args.sizes:
        t0 = time.perf_counter()
        r = asyncio.run(bench_size(n, args.repeats, args.seed))
        results.append(r)
        print(f"[{n} cells] done in {time.perf_counter() - t0:.1f}s: "
              f"{r.wall_ms_median} ms median", file=sys.stderr)

    print()
    _print_table(results)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = Path(args.outdir) / f"resolve_single_edit_{stamp}.json"
    csv_path = Path(args.outdir) / f"resolve_single_edit_{stamp}.csv"
    html_path = Path(args.outdir) / f"resolve_single_edit_{stamp}.html"

    with open(json_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    with open(csv_path, "w") as f:
        keys = list(asdict(results[0]).keys())
        f.write(",".join(keys) + "\n")
        for r in results:
            d = asdict(r)
            f.write(",".join(str(d[k]) for k in keys) + "\n")
    charted = _render_chart(results, html_path)

    print(f"\nWrote {json_path}")
    print(f"Wrote {csv_path}")
    if charted:
        print(f"Wrote {html_path}")
    else:
        print("(plotly not installed; skipped HTML chart)")


if __name__ == "__main__":
    main()
