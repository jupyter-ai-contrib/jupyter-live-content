import asyncio
import json


async def test_ws_broadcasts_server_update_on_disk_change(jp_ws_fetch, jp_root_dir):
    """End-to-end: an authenticated client that has a file open receives a
    ``server_update`` when that file changes on disk."""
    target = jp_root_dir / "live.txt"
    target.write_text("before")

    ws = await jp_ws_fetch("api", "live-content", "ws")
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
        import hashlib

        expected_hash = hashlib.sha256(b"after").hexdigest()
        assert json.loads(raw) == {
            "type": "server_update",
            "path": "live.txt",
            "hash": expected_hash,
        }
    finally:
        ws.close()


async def test_server_shutdown_stops_watchers(jp_serverapp, jp_ws_fetch, jp_root_dir):
    """Server shutdown stops and awaits the watcher tasks. A watcher left
    pending when the event loop stops keeps its non-daemon anyio worker thread
    alive, so the server process never exits."""
    (jp_root_dir / "live.txt").write_text("before")
    manager = jp_serverapp.web_app.settings["live_content_manager"]

    ws = await jp_ws_fetch("api", "live-content", "ws")
    try:
        ws.write_message(json.dumps({"type": "client_opened", "path": "live.txt"}))
        for _ in range(50):
            if manager._dir_tasks:
                break
            await asyncio.sleep(0.1)
        tasks = list(manager._dir_tasks.values())
        assert tasks, "watcher never started"

        await jp_serverapp.cleanup_extensions()

        assert manager._dir_tasks == {}
        assert all(task.done() for task in tasks)
    finally:
        ws.close()
