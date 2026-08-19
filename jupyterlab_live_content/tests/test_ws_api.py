import logging
import os

from jupyterlab_live_content.ws_api import LiveContentManager
from jupyterlab_live_content.ws_schema import ServerUpdate


class FakeClient:
    """Stand-in for a websocket handler; records broadcast payloads."""

    def __init__(self):
        self.sent = []

    def write_message(self, payload):
        self.sent.append(payload)


def make_manager(root="/tmp/live-root"):
    return LiveContentManager(root_dir=root, log=logging.getLogger("test"))


def _emit(mgr, path):
    """Route a coarse server_update synchronously (no ContentsManager)."""
    mgr._route(path, ServerUpdate(path=path))


def test_broadcast_only_reaches_subscribed_clients():
    mgr = make_manager()
    c1, c2 = FakeClient(), FakeClient()
    mgr.add_client(c1)
    mgr.add_client(c2)

    mgr.subscribe(c1, "a.txt")
    mgr.subscribe(c2, "a.txt")
    mgr.subscribe(c2, "b.txt")

    _emit(mgr, "a.txt")
    assert [m["path"] for m in c1.sent] == ["a.txt"]
    assert [m["path"] for m in c2.sent] == ["a.txt"]

    _emit(mgr, "b.txt")
    assert len(c1.sent) == 1  # not subscribed to b.txt
    assert len(c2.sent) == 2

    # A path nobody has open reaches nobody.
    _emit(mgr, "unopened.txt")
    assert len(c1.sent) == 1
    assert len(c2.sent) == 2


def test_unsubscribe_and_disconnect_stop_updates():
    mgr = make_manager()
    c1 = FakeClient()
    mgr.add_client(c1)
    mgr.subscribe(c1, "a.txt")

    mgr.unsubscribe(c1, "a.txt")
    _emit(mgr, "a.txt")
    assert c1.sent == []

    # Re-subscribe then fully disconnect: the client is dropped from all paths.
    mgr.subscribe(c1, "a.txt")
    mgr.remove_client(c1)
    _emit(mgr, "a.txt")
    assert c1.sent == []


def test_to_api_path():
    root = "/tmp/live-root"
    mgr = make_manager(root)
    assert mgr._to_api_path(os.path.join(root, "sub", "f.txt")) == "sub/f.txt"
    assert mgr._to_api_path(root) is None  # the root itself
    assert mgr._to_api_path("/etc/passwd") is None  # outside root
