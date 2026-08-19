# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Bridge between on-disk notebooks and the WebSocket protocol.

Reads notebooks through the Jupyter ``ContentsManager`` (so paths, drives, and
hashing all match what the frontend's ``Context`` sees) and turns them into the
wire messages defined in :mod:`jupyterlab_live_content.ws_schema`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from jupyter_server.utils import ensure_async

from . import nb_hash, ws_schema


def is_notebook_path(path: str) -> bool:
    return path.endswith(".ipynb")


async def read_notebook(
    contents_manager: Any, path: str
) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
    """Read a notebook's content and file-revision metadata.

    Returns ``(nbcontent, file_meta)`` where ``file_meta`` holds
    ``last_modified`` / ``hash`` / ``hash_algorithm`` for the current revision.
    Raises whatever the ContentsManager raises on a missing or invalid file; the
    caller is expected to treat that as "skip this event".
    """
    model = await ensure_async(
        contents_manager.get(path, content=True, type="notebook")
    )
    file_meta: Dict[str, Optional[str]] = {
        "last_modified": _iso(model.get("last_modified")),
        "hash": None,
        "hash_algorithm": None,
    }
    # Hash is optional and version-dependent; best-effort.
    try:
        meta_model = await ensure_async(
            contents_manager.get(path, content=False, require_hash=True)
        )
        file_meta["hash"] = meta_model.get("hash")
        file_meta["hash_algorithm"] = meta_model.get("hash_algorithm")
    except TypeError:
        # Older ContentsManager.get has no require_hash parameter.
        pass
    except Exception:  # noqa: BLE001 - hash is best-effort, never fatal
        pass
    return model["content"], file_meta


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


async def read_file_revision(
    contents_manager: Any, path: str
) -> Dict[str, Optional[str]]:
    """Read just the file-revision metadata (last_modified / hash) for any file.

    Best-effort: returns whatever the ContentsManager provides. Used so a client
    can recognize its own save (matching hash) and skip a needless reload.
    """
    revision: Dict[str, Optional[str]] = {
        "last_modified": None,
        "hash": None,
        "hash_algorithm": None,
    }
    try:
        model = await ensure_async(
            contents_manager.get(path, content=False, require_hash=True)
        )
    except TypeError:
        model = await ensure_async(contents_manager.get(path, content=False))
    except Exception:  # noqa: BLE001 - metadata is best-effort, never fatal
        return revision
    revision["last_modified"] = _iso(model.get("last_modified"))
    revision["hash"] = model.get("hash")
    revision["hash_algorithm"] = model.get("hash_algorithm")
    return revision


def build_manifest(nbcontent: Dict[str, Any], file_meta: Dict[str, Optional[str]]):
    return nb_hash.build_manifest(
        nbcontent,
        last_modified=file_meta.get("last_modified"),
        hash=file_meta.get("hash"),
        hash_algorithm=file_meta.get("hash_algorithm"),
    )


def manifest_message(path: str, manifest: nb_hash.NbManifest) -> ws_schema.NbManifest:
    """Build a hash-only ``NbManifest`` wire message."""
    return ws_schema.NbManifest(
        path=path,
        cell_order=list(manifest.cell_order),
        cells_by_id={
            cid: ws_schema.CellInfo(
                id=info.id,
                cell_type=info.cell_type,
                source_hash=info.source_hash,
                meta_hash=info.meta_hash,
            )
            for cid, info in manifest.cells_by_id.items()
        },
        nb_meta_hash=manifest.nb_meta_hash,
        last_modified=manifest.last_modified,
        hash=manifest.hash,
        hash_algorithm=manifest.hash_algorithm,
    )


def _cells_by_id(nbcontent: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for index, cell in enumerate(nbcontent.get("cells", []) or []):
        out[nb_hash._cell_id(cell, index)] = cell
    return out


def update_message(
    path: str,
    nbcontent: Dict[str, Any],
    manifest: nb_hash.NbManifest,
    include_ids: List[str],
) -> ws_schema.NbUpdate:
    """Build an ``NbUpdate`` carrying content for ``include_ids`` inline."""
    raw = _cells_by_id(nbcontent)
    cells_by_id: Dict[str, ws_schema.CellUpdateInfo] = {}
    for cid in include_ids:
        cell = raw.get(cid)
        info = manifest.cells_by_id.get(cid)
        if cell is None or info is None:
            continue
        cells_by_id[cid] = ws_schema.CellUpdateInfo(
            id=cid,
            cell_type=info.cell_type,
            source_hash=info.source_hash,
            meta_hash=info.meta_hash,
            source=nb_hash._normalize_source(cell.get("source", "")),
            metadata=cell.get("metadata", {}) or {},
            attachments=cell.get("attachments", {}) or {},
        )
    return ws_schema.NbUpdate(
        path=path,
        cell_order=list(manifest.cell_order),
        cells_by_id=cells_by_id,
        nb_meta_hash=manifest.nb_meta_hash,
        nb_metadata=nbcontent.get("metadata", {}) or {},
        last_modified=manifest.last_modified,
        hash=manifest.hash,
        hash_algorithm=manifest.hash_algorithm,
    )
