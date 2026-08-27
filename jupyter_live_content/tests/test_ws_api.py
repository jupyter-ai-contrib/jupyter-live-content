import logging
import os

from jupyter_live_content.ws_api import LiveContentManager


class FakeClient:
    """Stand-in for a websocket handler; records broadcast payloads."""

    def __init__(self):
        self.sent = []

    def write_message(self, payload):
        self.sent.append(payload)


def make_manager(root="/tmp/live-root"):
    return LiveContentManager(root_dir=root, log=logging.getLogger("test"))


def test_broadcast_only_reaches_subscribed_clients():
    mgr = make_manager()
    c1, c2 = FakeClient(), FakeClient()
    mgr.add_client(c1)
    mgr.add_client(c2)

    mgr.subscribe(c1, "a.txt")
    mgr.subscribe(c2, "a.txt")
    mgr.subscribe(c2, "b.txt")

    mgr.broadcast_update("a.txt")
    assert c1.sent == [{"path": "a.txt", "type": "server_update"}]
    assert c2.sent == [{"path": "a.txt", "type": "server_update"}]

    mgr.broadcast_update("b.txt")
    assert len(c1.sent) == 1  # not subscribed to b.txt
    assert len(c2.sent) == 2

    # A path nobody has open reaches nobody.
    mgr.broadcast_update("unopened.txt")
    assert len(c1.sent) == 1
    assert len(c2.sent) == 2


def test_unsubscribe_and_disconnect_stop_updates():
    mgr = make_manager()
    c1 = FakeClient()
    mgr.add_client(c1)
    mgr.subscribe(c1, "a.txt")

    mgr.unsubscribe(c1, "a.txt")
    mgr.broadcast_update("a.txt")
    assert c1.sent == []

    # Re-subscribe then fully disconnect: the client is dropped from all paths.
    mgr.subscribe(c1, "a.txt")
    mgr.remove_client(c1)
    mgr.broadcast_update("a.txt")
    assert c1.sent == []


def test_to_api_path():
    root = "/tmp/live-root"
    mgr = make_manager(root)
    assert mgr._to_api_path(os.path.join(root, "sub", "f.txt")) == "sub/f.txt"
    assert mgr._to_api_path(root) is None  # the root itself
    assert mgr._to_api_path("/etc/passwd") is None  # outside root


def test_watched_dirs_reflects_open_documents():
    """The watcher's scope tracks exactly the directories of open documents,
    not the whole server root."""
    mgr = make_manager()
    root = mgr.root_dir
    c1 = FakeClient()
    mgr.add_client(c1)

    # Nothing open => nothing watched.
    assert mgr.watched_dirs == set()

    mgr.subscribe(c1, "sub/a.txt")
    assert mgr.watched_dirs == {os.path.join(root, "sub")}

    # A second file in the same directory does not widen the set.
    mgr.subscribe(c1, "sub/b.txt")
    assert mgr.watched_dirs == {os.path.join(root, "sub")}

    # A file in another directory adds that directory.
    mgr.subscribe(c1, "other/c.txt")
    assert mgr.watched_dirs == {
        os.path.join(root, "sub"),
        os.path.join(root, "other"),
    }

    # A root-level document is watched via the root directory.
    mgr.subscribe(c1, "top.txt")
    assert root in mgr.watched_dirs


def test_watched_dirs_shrink_on_close_and_disconnect():
    mgr = make_manager()
    root = mgr.root_dir
    c1, c2 = FakeClient(), FakeClient()
    mgr.add_client(c1)
    mgr.add_client(c2)

    mgr.subscribe(c1, "sub/a.txt")
    mgr.subscribe(c2, "sub/b.txt")
    assert mgr.watched_dirs == {os.path.join(root, "sub")}

    # Closing one of two docs in a directory keeps the directory watched.
    mgr.unsubscribe(c1, "sub/a.txt")
    assert mgr.watched_dirs == {os.path.join(root, "sub")}

    # Disconnecting the last opener drops the directory from the watch scope.
    mgr.remove_client(c2)
    assert mgr.watched_dirs == set()
