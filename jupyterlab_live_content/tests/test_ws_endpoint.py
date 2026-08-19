import asyncio
import json


async def test_ws_broadcasts_server_update_on_disk_change(jp_ws_fetch, jp_root_dir):
    """End-to-end: an authenticated client that has a file open receives a
    ``server_update`` when that file changes on disk."""
    target = jp_root_dir / "live.txt"
    target.write_text("before")

    ws = await jp_ws_fetch("jupyterlab-live-content", "ws")
    try:
        # Tell the server we have this file open. This both records the
        # subscription and (via connect) starts the filesystem watcher.
        ws.write_message(json.dumps({"type": "client_opened", "path": "live.txt"}))

        # Give the watcher a moment to spin up and register the subscription.
        await asyncio.sleep(1.0)

        # Out-of-band change on disk.
        target.write_text("after")

        raw = await asyncio.wait_for(ws.read_message(), timeout=20)
        assert raw is not None, "connection closed before an update arrived"
        msg = json.loads(raw)
        assert msg["type"] == "server_update"
        assert msg["path"] == "live.txt"
    finally:
        ws.close()


_NB = {
    "cells": [
        {"id": "c1", "cell_type": "code", "source": "print(1)", "metadata": {}, "outputs": [], "execution_count": None},
        {"id": "c2", "cell_type": "code", "source": "x = 2", "metadata": {}, "outputs": [], "execution_count": None},
    ],
    "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def _write_nb(path, nb):
    path.write_text(json.dumps(nb))


async def _read_of_type(ws, wanted, timeout=20):
    """Read messages until one of type ``wanted`` arrives."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        raw = await asyncio.wait_for(ws.read_message(), timeout=max(0.1, remaining))
        assert raw is not None, "connection closed"
        msg = json.loads(raw)
        if msg.get("type") == wanted:
            return msg


async def test_notebook_manifest_on_open(jp_ws_fetch, jp_root_dir):
    import copy

    _write_nb(jp_root_dir / "nb.ipynb", _NB)
    ws = await jp_ws_fetch("jupyterlab-live-content", "ws")
    try:
        ws.write_message(json.dumps({"type": "client_opened", "path": "nb.ipynb"}))
        manifest = await _read_of_type(ws, "nb_manifest")
        assert manifest["cell_order"] == ["c1", "c2"]
        assert set(manifest["cells_by_id"]) == {"c1", "c2"}
        # hashes only, no content
        assert "source" not in manifest["cells_by_id"]["c1"]
    finally:
        ws.close()


async def test_notebook_update_sends_changed_cell_inline(jp_ws_fetch, jp_root_dir):
    import copy

    nb_path = jp_root_dir / "nb2.ipynb"
    _write_nb(nb_path, _NB)
    ws = await jp_ws_fetch("jupyterlab-live-content", "ws")
    try:
        ws.write_message(json.dumps({"type": "client_opened", "path": "nb2.ipynb"}))
        await _read_of_type(ws, "nb_manifest")
        await asyncio.sleep(1.0)  # let the watcher register

        changed = copy.deepcopy(_NB)
        changed["cells"][1]["source"] = "x = 999"
        _write_nb(nb_path, changed)

        update = await _read_of_type(ws, "nb_update")
        assert update["cell_order"] == ["c1", "c2"]
        # only the changed cell is sent, with its content inline
        assert set(update["cells_by_id"]) == {"c2"}
        assert update["cells_by_id"]["c2"]["source"] == "x = 999"
    finally:
        ws.close()
