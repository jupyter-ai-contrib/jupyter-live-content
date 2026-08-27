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
