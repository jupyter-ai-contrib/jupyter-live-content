# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Per-cell hashing and manifest building for notebooks.

The server hashes a notebook's *input* (cell source and a filtered subset of cell
metadata, plus notebook-level metadata) so it can tell a client exactly which
cells changed between two on-disk revisions. Outputs and execution counts are
deliberately excluded: they are the bulk of a notebook's bytes and are volatile
kernel state we never sync.

Hashing is O(n) over bytes with BLAKE3, never an O(n*m) diff, so it stays clear
of the jupyter_ydoc#401 failure class. The diff we do compute here
(:func:`diff_manifests`) is a cheap set/hash comparison over cell ids.

Nothing in this module is persisted, so the hash algorithm is a private detail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from blake3 import blake3

#: Cell metadata keys that reflect purely local view state. A change to any of
#: these on disk must not count as a content change, or we would fight the user
#: over their own UI toggles.
_VOLATILE_META_KEYS = frozenset(
    {"collapsed", "scrolled", "execution"}
)
#: Nested keys under ``metadata.jupyter`` that are also local view state.
_VOLATILE_JUPYTER_KEYS = frozenset({"source_hidden", "outputs_hidden"})


def _hash_bytes(data: bytes) -> str:
    return blake3(data).hexdigest()


def _normalize_source(source: Any) -> str:
    """nbformat allows source as a string or a list of strings; normalize both
    to a single string with normalized (``\\n``) line endings."""
    if isinstance(source, list):
        source = "".join(source)
    elif not isinstance(source, str):
        source = "" if source is None else str(source)
    # Normalize CRLF / CR to LF so a line-ending-only change is a no-op.
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _filter_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Drop volatile view-state keys so they do not affect the metadata hash."""
    out: Dict[str, Any] = {}
    for key, value in metadata.items():
        if key in _VOLATILE_META_KEYS:
            continue
        if key == "jupyter" and isinstance(value, dict):
            nested = {
                k: v for k, v in value.items() if k not in _VOLATILE_JUPYTER_KEYS
            }
            if nested:
                out[key] = nested
            continue
        out[key] = value
    return out


def _canonical(obj: Any) -> bytes:
    """Deterministic JSON encoding (sorted keys) for stable hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def source_hash(source: Any) -> str:
    return _hash_bytes(_normalize_source(source).encode("utf-8"))


def meta_hash(metadata: Optional[Dict[str, Any]], attachments: Any = None) -> str:
    """Hash the filtered cell metadata together with any attachments."""
    payload = {
        "metadata": _filter_metadata(metadata or {}),
        "attachments": attachments or {},
    }
    return _hash_bytes(_canonical(payload))


def nb_meta_hash(metadata: Optional[Dict[str, Any]]) -> str:
    return _hash_bytes(_canonical(metadata or {}))


@dataclass
class CellInfo:
    """Identity and content hashes for one cell (no content)."""

    id: str
    cell_type: str
    source_hash: str
    meta_hash: str


@dataclass
class NbManifest:
    """A hash-only description of a notebook revision plus its file metadata."""

    cell_order: List[str]
    cells_by_id: Dict[str, CellInfo]
    nb_meta_hash: str
    last_modified: Optional[str] = None
    hash: Optional[str] = None
    hash_algorithm: Optional[str] = None


def _cell_id(cell: Dict[str, Any], index: int) -> str:
    # nbformat 4.5+ guarantees an id; fall back to index for older/degenerate
    # cells so we never crash (such cells simply will not match across revisions).
    cid = cell.get("id")
    return cid if isinstance(cid, str) and cid else f"__index_{index}"


def build_manifest(
    nbcontent: Dict[str, Any],
    *,
    last_modified: Optional[str] = None,
    hash: Optional[str] = None,
    hash_algorithm: Optional[str] = None,
) -> NbManifest:
    """Build a :class:`NbManifest` from an nbformat notebook dict."""
    cells = nbcontent.get("cells", []) or []
    order: List[str] = []
    by_id: Dict[str, CellInfo] = {}
    for index, cell in enumerate(cells):
        cid = _cell_id(cell, index)
        order.append(cid)
        by_id[cid] = CellInfo(
            id=cid,
            cell_type=cell.get("cell_type", "code"),
            source_hash=source_hash(cell.get("source", "")),
            meta_hash=meta_hash(cell.get("metadata"), cell.get("attachments")),
        )
    return NbManifest(
        cell_order=order,
        cells_by_id=by_id,
        nb_meta_hash=nb_meta_hash(nbcontent.get("metadata")),
        last_modified=last_modified,
        hash=hash,
        hash_algorithm=hash_algorithm,
    )


@dataclass
class ManifestDiff:
    """The set-and-hash delta between a previous and current manifest."""

    #: ids present now whose source or metadata hash changed, or that are new.
    #: (Insertions and content changes both land here; the client tells them
    #: apart by whether it already holds the id.)
    changed: List[str] = field(default_factory=list)
    #: ids present before but gone now.
    removed: List[str] = field(default_factory=list)
    #: whether the cell order changed (reorder/insert/delete).
    order_changed: bool = False
    #: whether notebook-level metadata changed.
    nb_meta_changed: bool = False

    @property
    def is_empty(self) -> bool:
        return (
            not self.changed
            and not self.removed
            and not self.order_changed
            and not self.nb_meta_changed
        )


def diff_manifests(old: Optional[NbManifest], new: NbManifest) -> ManifestDiff:
    """Compute what changed between two manifests of the same notebook.

    Cheap: a dictionary/hash comparison over cell ids, never a sequence diff.
    """
    diff = ManifestDiff()
    old_by_id = old.cells_by_id if old else {}
    for cid, info in new.cells_by_id.items():
        prev = old_by_id.get(cid)
        if (
            prev is None
            or prev.source_hash != info.source_hash
            or prev.meta_hash != info.meta_hash
            or prev.cell_type != info.cell_type
        ):
            diff.changed.append(cid)
    diff.removed = [cid for cid in old_by_id if cid not in new.cells_by_id]
    diff.order_changed = (old.cell_order if old else []) != new.cell_order
    diff.nb_meta_changed = (old.nb_meta_hash if old else None) != new.nb_meta_hash
    return diff
