import pytest

from jupyter_live_content import ws_schema as s


def test_serialize_server_update():
    assert s.to_wire(s.ServerUpdate(path="a/b.txt")) == {
        "path": "a/b.txt",
        "type": "server_update",
    }


@pytest.mark.parametrize(
    "msg_type,cls",
    [
        ("client_opened", s.ClientOpened),
        ("client_closed", s.ClientClosed),
    ],
)
def test_parse_client_message(msg_type, cls):
    parsed = s.parse_client_message({"type": msg_type, "path": "notebooks/x.ipynb"})
    assert isinstance(parsed, cls)
    assert parsed.path == "notebooks/x.ipynb"
    assert parsed.type == msg_type


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"type": "server_update", "path": "p"},  # not a client->server type
        {"type": "unknown", "path": "p"},
        {"type": "client_opened"},  # missing path
        {"type": "client_opened", "path": ""},  # empty path
        {"type": "client_opened", "path": 3},  # wrong type
        "not-a-dict",
    ],
)
def test_parse_client_message_rejects_invalid(bad):
    with pytest.raises(ValueError):
        s.parse_client_message(bad)
