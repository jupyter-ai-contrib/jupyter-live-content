# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Message schema for the ``jupyter-live-content`` WebSocket protocol.

The protocol is intentionally tiny. Every message is a JSON object with a
``type`` discriminator plus a small, fixed set of fields. Each message type is
modelled as a ``dataclass`` so the wire format is defined in exactly one place
and is shared, conceptually, with the TypeScript ``LiveContentMessage`` union on
the frontend (see ``src/tokens.ts``).

Direction of travel:

  client -> server
    * ``client_opened``  a document at ``path`` was opened in this client
    * ``client_closed``  a document at ``path`` was closed in this client

  server -> client
    * ``server_update``  the file at ``path`` changed on disk; reload it
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Union

# --- message type discriminators -------------------------------------------

MSG_CLIENT_OPENED = "client_opened"
MSG_CLIENT_CLOSED = "client_closed"
MSG_SERVER_UPDATE = "server_update"


@dataclass
class ClientOpened:
    """Sent by a client when a user opens a document at ``path``."""

    path: str
    type: str = field(default=MSG_CLIENT_OPENED)


@dataclass
class ClientClosed:
    """Sent by a client when a user closes a document at ``path``."""

    path: str
    type: str = field(default=MSG_CLIENT_CLOSED)


@dataclass
class ServerUpdate:
    """Sent by the server when the file at ``path`` changed on disk.

    ``hash`` is the ContentsManager content hash (e.g. sha256) of the file after
    the change. The client compares it to the hash of the version it already has
    (``context.contentsModel.hash``) and only reloads when they differ - so a
    client's own save, which leaves disk and model in sync, does not trigger a
    redundant reload. ``None`` when the hash could not be computed (reload).
    """

    path: str
    type: str = field(default=MSG_SERVER_UPDATE)
    hash: Optional[str] = None


# Messages the server accepts from a client.
ClientMessage = Union[ClientOpened, ClientClosed]

_CLIENT_MESSAGE_TYPES = {
    MSG_CLIENT_OPENED: ClientOpened,
    MSG_CLIENT_CLOSED: ClientClosed,
}


def to_wire(message: Any) -> Dict[str, Any]:
    """Serialize a message dataclass to a JSON-ready dict."""
    return asdict(message)


def parse_client_message(data: Dict[str, Any]) -> ClientMessage:
    """Parse a decoded JSON object sent by a client into a message dataclass.

    Raises
    ------
    ValueError
        If ``type`` is missing/unknown or required fields are absent.
    """
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")

    msg_type = data.get("type")
    cls = _CLIENT_MESSAGE_TYPES.get(msg_type)
    if cls is None:
        raise ValueError(f"unknown or missing message type: {msg_type!r}")

    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"message {msg_type!r} requires a non-empty 'path'")

    return cls(path=path)
