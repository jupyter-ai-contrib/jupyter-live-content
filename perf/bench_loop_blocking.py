# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Stage 3 (focused): how much does resolving one out-of-band edit actually
BLOCK the event loop, and what fraction of that blocking is the ContentsManager
read?

Wall time != event-loop-blocked time. jupyter_server's async ContentsManager
offloads the file read and ``nbformat.reads`` (parse + validate) to an
``anyio`` worker thread via ``run_sync``. So in the asyncio sense the loop is
free during the read. BUT ``nbformat.reads`` is pure-Python CPU work that holds
the GIL, so it can still starve the loop thread of the GIL even from a worker
thread. Our own ``build_manifest`` / ``diff`` / ``update_message`` run directly
on the loop (no offload).

We measure the truth with a heartbeat lag probe: a coroutine that ``await``s a
tiny sleep on a fixed tick and records how late each wakeup is. Any lateness =
the loop could not run a ready callback on time = the loop was blocked (whether
by a direct on-loop computation or by GIL contention from a worker thread). We
attribute the accumulated lag to whichever resolve stage was running.

Run:
  .venv/bin/python perf/bench_loop_blocking.py --sizes 1000 2000 4000 8000 10000
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
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nbformat  # noqa: E402
from jupyter_server.services.contents.largefilemanager import (  # noqa: E402
    AsyncLargeFileManager,
)
from jupyter_server.utils import ensure_async  # noqa: E402

from jupyterlab_live_content import nb_hash, nb_service  # noqa: E402

from notebook_factory import make_notebook  # noqa: E402

STAGES = ("read", "hash", "manifest", "diffmsg")


class LagProbe:
    """Heartbeat coroutine that records when the event loop was BUSY (could not
    run a ready callback on time).

    Each wakeup that arrives later than ``tick_s`` means the loop was blocked
    for the overshoot. We record the busy interval ``[expected_wake, actual_wake]``
    and later attribute it to resolve stages by time-overlap. This correctly
    handles both:
      - offloaded stages (read/hash): many small gaps from GIL contention while
        the worker thread holds the GIL; the probe still ticks between them.
      - on-loop sync stages (build_manifest/diff): one long gap covering the
        whole computation, since the loop cannot run the probe at all until the
        next ``await``.
    """

    def __init__(self, tick_s: float = 0.0005) -> None:
        self.tick_s = tick_s
        self.busy: List[tuple] = []  # (busy_start, busy_end) in perf_counter secs
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    async def _run(self) -> None:
        while not self._stop:
            t0 = time.perf_counter()
            await asyncio.sleep(self.tick_s)
            t1 = time.perf_counter()
            overshoot = t1 - t0 - self.tick_s
            if overshoot > 0:
                # The loop was busy from the expected wake time until now.
                self.busy.append((t0 + self.tick_s, t1))

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    def attribute(self, spans: List[tuple]) -> Dict[str, float]:
        """Distribute busy intervals across stage spans by time overlap.

        ``spans`` is a list of ``(stage, start, end)`` in perf_counter seconds.
        Returns per-stage blocked seconds.
        """
        blocked: Dict[str, float] = {s: 0.0 for s in STAGES}
        for bs, be in self.busy:
            for stage, ss, se in spans:
                overlap = min(be, se) - max(bs, ss)
                if overlap > 0:
                    blocked[stage] += overlap
        return blocked


@dataclass
class SizeResult:
    n_cells: int
    file_bytes: int
    repeats: int
    # wall time per stage (median ms)
    wall_read_ms: float
    wall_hash_ms: float
    wall_manifest_ms: float
    wall_diffmsg_ms: float
    wall_total_ms: float
    # event-loop-BLOCKED time per stage (median ms across repeats)
    blk_read_ms: float
    blk_hash_ms: float
    blk_manifest_ms: float
    blk_diffmsg_ms: float
    blk_total_ms: float
    # the headline answer
    read_pct_of_blocking: float   # (blk_read + blk_hash) / blk_total * 100
    blocking_pct_of_wall: float   # blk_total / wall_total * 100


def _write_notebook(root: str, path: str, nb: Dict[str, Any]) -> int:
    abspath = os.path.join(root, path)
    with open(abspath, "w", encoding="utf-8") as f:
        f.write(nbformat.writes(nbformat.from_dict(nb)))
    return os.path.getsize(abspath)


def _edit_one_cell(root: str, path: str, rng: random.Random) -> None:
    abspath = os.path.join(root, path)
    with open(abspath, "r", encoding="utf-8") as f:
        nb = json.load(f)
    cells = nb["cells"]
    idx = rng.randrange(len(cells))
    src = cells[idx].get("source", "")
    if isinstance(src, list):
        src = "".join(src)
    cells[idx]["source"] = src + f"\n# edit {rng.randint(0, 10**9)}\n"
    with open(abspath, "w", encoding="utf-8") as f:
        json.dump(nb, f)


def _iso(v: Any):
    iso = getattr(v, "isoformat", None)
    return iso() if callable(iso) else (None if v is None else str(v))


async def _staged_resolve(cm, path, baseline, probe: LagProbe):
    """Run the resolve, recording per-stage (start, end) spans and wall time.
    Blocking is attributed afterwards by overlapping the probe's busy intervals
    with these spans."""
    wall: Dict[str, float] = {}
    spans: List[tuple] = []

    ss = time.perf_counter()
    model = await ensure_async(cm.get(path, content=True, type="notebook"))
    nbcontent = model["content"]
    se = time.perf_counter()
    spans.append(("read", ss, se))
    wall["read"] = (se - ss) * 1000.0

    ss = time.perf_counter()
    file_meta = {"last_modified": _iso(model.get("last_modified")),
                 "hash": None, "hash_algorithm": None}
    try:
        mm = await ensure_async(cm.get(path, content=False, require_hash=True))
        file_meta["hash"] = mm.get("hash")
        file_meta["hash_algorithm"] = mm.get("hash_algorithm")
    except TypeError:
        pass
    se = time.perf_counter()
    spans.append(("hash", ss, se))
    wall["hash"] = (se - ss) * 1000.0

    ss = time.perf_counter()
    manifest = nb_service.build_manifest(nbcontent, file_meta)
    se = time.perf_counter()
    spans.append(("manifest", ss, se))
    wall["manifest"] = (se - ss) * 1000.0

    ss = time.perf_counter()
    diff = nb_hash.diff_manifests(baseline, manifest)
    nb_service.update_message(path, nbcontent, manifest, diff.changed)
    se = time.perf_counter()
    spans.append(("diffmsg", ss, se))
    wall["diffmsg"] = (se - ss) * 1000.0

    return manifest, wall, spans


async def bench_size(n_cells: int, repeats: int, seed: int, tick_us: int) -> SizeResult:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as root:
        cm = AsyncLargeFileManager(root_dir=root)
        path = "bench.ipynb"
        nb = make_notebook(n_cells, seed=seed)
        file_bytes = _write_notebook(root, path, nb)

        nbcontent, file_meta = await nb_service.read_notebook(cm, path)
        baseline = nb_service.build_manifest(nbcontent, file_meta)

        walls: Dict[str, List[float]] = {s: [] for s in STAGES}
        blks: Dict[str, List[float]] = {s: [] for s in STAGES}

        for _ in range(repeats):
            _edit_one_cell(root, path, rng)
            probe = LagProbe(tick_s=tick_us / 1e6)
            probe.start()
            # Let the probe settle into a steady tick before we start.
            await asyncio.sleep(0.01)
            manifest, wall, spans = await _staged_resolve(cm, path, baseline, probe)
            await probe.stop()
            baseline = manifest
            blocked = probe.attribute(spans)
            for st in STAGES:
                walls[st].append(wall[st])
                blks[st].append(blocked[st] * 1000.0)

    med = statistics.median
    wr, wh = med(walls["read"]), med(walls["hash"])
    wm, wd = med(walls["manifest"]), med(walls["diffmsg"])
    br, bh = med(blks["read"]), med(blks["hash"])
    bm, bd = med(blks["manifest"]), med(blks["diffmsg"])
    blk_total = br + bh + bm + bd
    wall_total = wr + wh + wm + wd
    return SizeResult(
        n_cells=n_cells,
        file_bytes=file_bytes,
        repeats=repeats,
        wall_read_ms=round(wr, 3), wall_hash_ms=round(wh, 3),
        wall_manifest_ms=round(wm, 3), wall_diffmsg_ms=round(wd, 3),
        wall_total_ms=round(wall_total, 3),
        blk_read_ms=round(br, 3), blk_hash_ms=round(bh, 3),
        blk_manifest_ms=round(bm, 3), blk_diffmsg_ms=round(bd, 3),
        blk_total_ms=round(blk_total, 3),
        read_pct_of_blocking=round(100.0 * (br + bh) / blk_total, 1) if blk_total else 0.0,
        blocking_pct_of_wall=round(100.0 * blk_total / wall_total, 1) if wall_total else 0.0,
    )


def _print_table(results: List[SizeResult]) -> None:
    cols = [
        ("cells", "n_cells", "{}"),
        ("wall tot ms", "wall_total_ms", "{}"),
        ("BLOCKED ms", "blk_total_ms", "{}"),
        ("blk/wall %", "blocking_pct_of_wall", "{}"),
        ("blk read", "blk_read_ms", "{}"),
        ("blk hash", "blk_hash_ms", "{}"),
        ("blk manifest", "blk_manifest_ms", "{}"),
        ("blk diff+msg", "blk_diffmsg_ms", "{}"),
        ("READ % of blk", "read_pct_of_blocking", "{}"),
    ]
    header = " | ".join(f"{c[0]:>13}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in results:
        row = [(fmt.format(getattr(r, attr))) for _t, attr, fmt in cols]
        print(" | ".join(f"{c:>13}" for c in row))


def _render_chart(results: List[SizeResult], out_html: Path) -> bool:
    try:
        from plotly.subplots import make_subplots
        import plotly.graph_objects as go
    except ImportError:
        return False
    x = [r.n_cells for r in results]
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            "Event-loop BLOCKED time by stage (stacked ms)",
            "Wall time by stage (stacked ms)",
            "Read as % of loop blocking",
            "Loop blocking as % of wall time",
        ),
    )
    for label, attr in (("read", "blk_read_ms"), ("hash", "blk_hash_ms"),
                        ("build_manifest", "blk_manifest_ms"),
                        ("diff+msg", "blk_diffmsg_ms")):
        fig.add_trace(go.Bar(x=x, y=[getattr(r, attr) for r in results],
                             name=label, legendgroup="blk"), row=1, col=1)
    for label, attr in (("read", "wall_read_ms"), ("hash", "wall_hash_ms"),
                        ("build_manifest", "wall_manifest_ms"),
                        ("diff+msg", "wall_diffmsg_ms")):
        fig.add_trace(go.Bar(x=x, y=[getattr(r, attr) for r in results],
                             name=label, legendgroup="wall", showlegend=False),
                      row=1, col=2)
    fig.add_trace(go.Scatter(x=x, y=[r.read_pct_of_blocking for r in results],
                             mode="lines+markers", showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=x, y=[r.blocking_pct_of_wall for r in results],
                             mode="lines+markers", showlegend=False), row=2, col=2)
    for rr in (1, 2):
        for cc in (1, 2):
            fig.update_xaxes(title_text="cells", row=rr, col=cc)
    fig.update_layout(barmode="stack", template="plotly_white",
                      height=780, width=1150,
                      title_text="Event-loop blocking of one out-of-band edit resolve")
    fig.write_html(str(out_html), include_plotlyjs="cdn")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="+",
                    default=[1000, 2000, 4000, 8000, 10000])
    ap.add_argument("--repeats", type=int, default=9)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--tick-us", type=int, default=500,
                    help="Heartbeat tick in microseconds (probe resolution).")
    ap.add_argument("--outdir", type=str,
                    default=os.path.join(os.path.dirname(__file__), "results"))
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    results: List[SizeResult] = []
    for n in args.sizes:
        r = asyncio.run(bench_size(n, args.repeats, args.seed, args.tick_us))
        results.append(r)
        print(f"[{n} cells] blocked {r.blk_total_ms} ms of {r.wall_total_ms} ms wall; "
              f"read = {r.read_pct_of_blocking}% of blocking", file=sys.stderr)

    print()
    _print_table(results)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = Path(args.outdir) / f"loop_blocking_{stamp}.json"
    html_path = Path(args.outdir) / f"loop_blocking_{stamp}.html"
    with open(json_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    charted = _render_chart(results, html_path)
    print(f"\nWrote {json_path}")
    if charted:
        print(f"Wrote {html_path}")


if __name__ == "__main__":
    main()
