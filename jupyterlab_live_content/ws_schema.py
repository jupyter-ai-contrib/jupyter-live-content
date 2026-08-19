# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Message schema for the ``jupyterlab-live-content`` WebSocket protocol.

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
from typing import Any, Dict, List, Optional, Union

# --- message type discriminators -------------------------------------------

MSG_CLIENT_OPENED = "client_opened"
MSG_CLIENT_CLOSED = "client_closed"
MSG_SERVER_UPDATE = "server_update"
MSG_GET_MANIFEST = "get_manifest"
MSG_FETCH_CELLS = "fetch_cells"
MSG_NB_MANIFEST = "nb_manifest"
MSG_NB_UPDATE = "nb_update"


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

    This is the coarse fallback path, still used for non-notebook documents. The
    file-revision metadata lets a client tell its own save apart from a genuine
    out-of-band change: if the hash matches what the client already recorded, the
    update is a no-op and no reload is needed.
    """

    path: str
    last_modified: Optional[str] = None
    hash: Optional[str] = None
    hash_algorithm: Optional[str] = None
    type: str = field(default=MSG_SERVER_UPDATE)


# --- notebook protocol -----------------------------------------------------


@dataclass
class GetManifest:
    """Client asks for a fresh full manifest (reconnect or suspected drift)."""

    path: str
    type: str = field(default=MSG_GET_MANIFEST)


@dataclass
class FetchCells:
    """Client asks for the content of specific cells (drift recovery)."""

    path: str
    ids: List[str]
    type: str = field(default=MSG_FETCH_CELLS)


@dataclass
class CellInfo:
    """Identity and content hashes for one cell (no content)."""

    id: str
    cell_type: str
    source_hash: str
    meta_hash: str


@dataclass
class CellUpdateInfo:
    """A cell the client must apply: hashes plus full content."""

    id: str
    cell_type: str
    source_hash: str
    meta_hash: str
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NbManifest:
    """Full, hash-only snapshot of a notebook. Sent on open and on reconnect."""

    path: str
    cell_order: List[str]
    cells_by_id: Dict[str, CellInfo]
    nb_meta_hash: str
    last_modified: Optional[str] = None
    hash: Optional[str] = None
    hash_algorithm: Optional[str] = None
    type: str = field(default=MSG_NB_MANIFEST)


@dataclass
class NbUpdate:
    """Emitted on each reconcilable out-of-band change to a notebook."""

    path: str
    cell_order: List[str]
    cells_by_id: Dict[str, CellUpdateInfo]
    nb_meta_hash: str
    nb_metadata: Dict[str, Any] = field(default_factory=dict)
    last_modified: Optional[str] = None
    hash: Optional[str] = None
    hash_algorithm: Optional[str] = None
    type: str = field(default=MSG_NB_UPDATE)


# Messages the server accepts from a client.
ClientMessage = Union[ClientOpened, ClientClosed, GetManifest, FetchCells]

_CLIENT_MESSAGE_TYPES = {
    MSG_CLIENT_OPENED: ClientOpened,
    MSG_CLIENT_CLOSED: ClientClosed,
    MSG_GET_MANIFEST: GetManifest,
    MSG_FETCH_CELLS: FetchCells,
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

    if cls is FetchCells:
        ids = data.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError("fetch_cells requires 'ids' to be a list of strings")
        return FetchCells(path=path, ids=ids)

    return cls(path=path)
