# Design: Incremental notebook updates

Status: draft for discussion (tracks [#2](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/issues/2))

## Background

The proof of concept in [#1](https://github.com/jupyter-ai-contrib/jupyterlab-live-content/pull/1)
reacts to an on-disk change by calling `context.revert()`, which refetches and
replaces the whole document. Reviewers raised concrete problems: a hard reload
loses scroll and focus, discards live output-widget state, can deadlock the
kernel when an `input()` or debugger prompt is pending, and leaves no per-cell
granularity so dirty documents cannot update at all.

The goal here is to apply on-disk changes by writing directly into
`widget.context.model.sharedModel` (the `YNotebook`), so only the cells that
changed update in place.

We treat [jupyter_ydoc#401](https://github.com/jupyter-server/jupyter_ydoc/issues/401)
as the cautionary case: an O(n·m) `difflib` pass on the server main thread pegged
the CPU and made the whole server unresponsive. The lesson is that diffing must
be applied intelligently, with performance weighted heavily. Expensive work stays
off shared threads and is bounded per cell.

## Goals

- Apply out-of-band changes to a notebook incrementally whenever the changes can
  be reconciled on a cell-by-cell basis.
- Preserve kernel state, outputs, focus, and scroll wherever possible.
- Preserve unsaved local edits in cells the disk change did not touch.
- Keep potentially CPU-intensive work off main threads. The server hashes on
  worker threads; the client hashes in a Web Worker.
- Keep memory usage roughly proportional to file size, that is, no more than what
  holding the notebook already requires.

## Non-goals

- Real-time collaboration. No shared CRDT sync protocol, no server-side `YDoc`,
  no state-vector reconciliation. We do edit the local `@jupyter/ydoc` model, but
  that is the model JupyterLab already uses in every session, not RTC.
- Character-level merge of concurrent edits to the same cell.
- Merging updates into a dirty cell. We do apply an update to a notebook that has
  unsaved edits in other cells, but an update that touches a cell you are editing
  is rejected as a whole (see Reconciliation).
- Syncing `outputs` or `execution_count` from disk. These are kernel-owned.
- Intelligent diffing of large plain text files. This document is about notebooks.

## Assumptions

1. Cells have stable, immutable ids. nbformat 4.5 (JEP 62, shipped in nbformat
   5.1, 2021) added the cell `id`; JupyterLab adopted 4.5 around 3.1 and assigns
   ids on load, upgrading older 4.4 notebooks. Our target is JupyterLab 4, where
   every cell in the loaded `YNotebook` carries an id. Immutable ids let us match
   cells and distinguish a move from a delete-plus-insert with no content compare.
2. JupyterLab 4: `context.model.sharedModel` is a `YNotebook`, and edits to it
   propagate reactively to all views.
3. The writer produces notebook JSON on disk. A partial write or invalid JSON is
   handled gracefully: the server skips that event, logs it, and waits for the
   next change. The client is never sent a broken manifest.

## Proposal

At a high level, this design proposes incremental updates driven by cell-by-cell
hashes. The client maintains its set of per-cell hashes, updating them on each
local change. The server computes the set of per-cell hashes on each out-of-band
change and sends that set, together with the order of the cell ids, to the client.

The client uses the cell-id list and the hashes to determine whether the update
is _reconcilable_, and applies it if so. To minimize document updates, the client
runs the Myers diff algorithm over its current cell-id list and the server's
proposed cell-id list to find their longest common subsequence (LCS). The cells
in the LCS stay in place, and only the remaining cells are moved, inserted, or
deleted, which maximizes the number of cells whose live state is preserved.

Key implementation choices, and the new dependencies they add:

- Reconcile cell ids with the Myers LCS: `diff-sequences` (npm).
- Hash cells with BLAKE3: `@noble/hashes` (npm, client) and `blake3`
  (PyPI / conda-forge, server).
- Apply all edits through the notebook's `@jupyter/ydoc` shared model, the same
  model JupyterLab already uses in every session with or without RTC; reorders go
  through `YNotebook.moveCell`.

## Details

### Protocol

Messages are modeled as dataclasses, extending the `client_opened` /
`client_closed` protocol from #1. Cell content travels inline in the update, so a
changed cell always arrives with the content the client needs to apply it.

```python
from dataclasses import dataclass, field
from typing import Literal

# --- cell-level payloads ---

@dataclass
class CellInfo:
    """Identity and content hashes for one cell (no content). Used in the manifest."""
    id: str            # stable nbformat 4.5 cell id; the key for all matching
    cell_type: str     # "code" | "markdown" | "raw"
    source_hash: str   # BLAKE3 of the normalized cell source
    meta_hash: str     # BLAKE3 of filtered cell metadata + attachments

@dataclass
class CellUpdateInfo(CellInfo):
    """A cell the client must apply. Inherits the hashes and carries full content."""
    source: str                                  # the new cell source
    metadata: dict                               # the new cell metadata
    attachments: dict = field(default_factory=dict)  # markdown/raw only; {} otherwise

# --- server -> client ---

@dataclass
class NbManifest:
    """Full, hash-only snapshot of a notebook. Sent on open and on reconnect."""
    path: str                          # server-relative notebook path
    cell_order: list[str]              # cell ids in document order
    cells_by_id: dict[str, CellInfo]   # id -> hashes, no content
    nb_meta_hash: str                  # BLAKE3 of notebook-level metadata
    last_modified: str                 # ContentsManager last_modified for this revision
    hash: str | None                   # ContentsManager whole-file hash, if available
    hash_algorithm: str | None         # the hash algorithm, if a hash is provided
    type: Literal["nb_manifest"] = "nb_manifest"

@dataclass
class NbUpdateManifest:
    """Emitted on each out-of-band change. Describes the new target state."""
    path: str                                # server-relative notebook path
    cell_order: list[str]                    # full target cell-id order, always sent
    cells_by_id: dict[str, CellUpdateInfo]   # changed or inserted cells, with content
    nb_meta_hash: str                        # notebook-level metadata hash
    nb_metadata: dict                        # notebook-level metadata, applied if changed
    last_modified: str                       # ContentsManager last_modified for this revision
    hash: str | None                         # ContentsManager whole-file hash, if available
    hash_algorithm: str | None               # the hash algorithm, if a hash is provided
    type: Literal["nb_update"] = "nb_update"

# --- client -> server ---

@dataclass
class GetManifest:
    """Ask for a fresh full manifest (on reconnect or suspected drift)."""
    path: str                          # server-relative notebook path
    type: Literal["get_manifest"] = "get_manifest"

@dataclass
class FetchCells:
    """Ask for content of specific cells (drift recovery). Server replies with an
    NbUpdateManifest carrying those cells."""
    path: str                          # server-relative notebook path
    ids: list[str]                     # cell ids whose content is needed
    type: Literal["fetch_cells"] = "fetch_cells"
```

`cell_order` is always sent, because the server does not know what order a client
currently holds. From `cell_order` the client derives removals (ids it holds that
are absent), insertions (ids present that it lacks), and reorders (via the LCS).
Removed cells carry no content, so they never appear in `cells_by_id`. The
`last_modified` / `hash` / `hash_algorithm` fields carry the ContentsManager's
own metadata for the revision the manifest describes; they are distinct from the
per-cell hashes and are used only to advance the client's recorded revision so the
native save-conflict dialog can be suppressed (see Reconciliation). The frontend
mirrors these as TypeScript interfaces with the same fields.

### Example flow

Cell 500 of a 1000-cell notebook changes:

```
C -> S  client_opened     { path: "nb.ipynb" }
S -> C  NbManifest        { path, cell_order: [c1, ..., c1000],
                            cells_by_id: { c1: CellInfo(...), ..., c1000: CellInfo(...) },
                            nb_meta_hash }
        # client already has the content from opening the file; it stores the hashes as base

# --- agent rewrites nb.ipynb; only cell c500's source changed ---

S -> C  NbUpdateManifest  { path, cell_order: [c1, ..., c1000],
                            cells_by_id: { c500: CellUpdateInfo(cell_type="code",
                                            source_hash=s500', meta_hash=m500,
                                            source="...", metadata={...}) },
                            nb_meta_hash, nb_metadata, last_modified, hash, hash_algorithm }
        # cell_order unchanged -> no structural ops. c500 is present, not dirty, not busy.
        # The whole update is reconcilable: checkpoint, transact(setSource(c500)),
        # advance the recorded revision to (last_modified, hash). No save.
```

### How hashes are computed

We hash each cell's input, never its outputs, for two reasons: outputs are the
bulk of a notebook's bytes, and they are volatile kernel state we do not sync. We
use two hashes per cell, source and metadata, so a metadata-only change (for
example a per-cell language in a polyglot notebook) can update the cell's metadata
without rewriting its source and moving the cursor. We choose BLAKE3 because the
client and server must produce identical digests to compare them: BLAKE3 is
spec-defined and byte-identical across its Rust and JS implementations, and it is
fast. Hashes are never persisted, so the choice stays a private detail we can
change later. Metadata hashing excludes purely local view state (collapsed,
scrolled, hidden flags, execution timing) so a disk change to those never fights
the user; `attachments` (authored media on markdown and raw cells, distinct from
outputs) are folded into the metadata hash. Notebook-level metadata has its own
hash.

### Libraries chosen

- `diff-sequences` (Meta/Jest): the Myers LCS over cell ids. Output-sensitive, so
  small updates are near O(N), and impeccably maintained.
- `blake3` (PyO3 bindings, conda-forge): server-side hashing, GIL released,
  multithreaded.
- `@noble/hashes`: client-side BLAKE3 in the worker. Audited, zero-dependency,
  actively maintained. Pure JS, which is fine when hashing only changed cells.

## Reconciliation

An update is applied as a transaction: either every operation it implies is
reconcilable and we apply all of them, or one is not and we apply none. We do
apply an update to a notebook that has unsaved edits in other cells; we reject
only an update that touches a cell you are editing.

Per cell, from the two hashes and runtime state: `src-dirty` and `meta-dirty` mean
`current` differs from `base`; `busy` means the cell is executing or the kernel has
a pending `input()` or debugger stdin prompt. At the notebook level, `nb-meta-dirty`
means the notebook's own metadata (for example the selected kernel, which the user
can change locally) differs from its base hash. An update is reconcilable when every
one of its operations is:

| Operation on a cell                 | Reconcilable when                                                 |
| ----------------------------------- | ----------------------------------------------------------------- |
| Insert a new cell                   | Always. Nothing local to lose.                                    |
| Source update to a surviving cell   | Not `src-dirty` and not `busy`.                                   |
| Metadata update to a surviving cell | Not `meta-dirty` and not `busy`.                                  |
| Move a surviving cell               | Not `src-dirty`, not `meta-dirty`, not `busy`.                    |
| `cell_type` change                  | Not `src-dirty`, not `meta-dirty`, not `busy`. Replaces the cell. |
| Delete a cell                       | Not `src-dirty`, not `meta-dirty`, not `busy`.                    |
| Notebook-level metadata change      | Not `nb-meta-dirty`.                                              |

**How the reconcile runs.** The client keeps, per cell id, the server's last-sent
hashes (`base`) and its live hashes (`current`), the latter maintained by a Web
Worker that re-hashes only the cells a `YNotebook` observer flags, debounced. On an
`NbUpdateManifest`, the client runs the Myers LCS over its current cell-id list and
the target `cell_order`: the common subsequence stays in place, a cell present in
both the removed and inserted sets is a move via `YNotebook.moveCell`, a pure
removal is a delete, and a pure insertion is an insert. Moves apply sequentially,
recomputing each cell's live index by id, which removes the index-shift hazard.
Content is applied with `ycell.setSource` for a source change and a metadata-map
merge for a metadata change, all inside one `sharedModel.transact()`. Because a
move, delete, or `cell_type` change replaces or removes a cell and loses its live
widget state, we only do them when the cell is clean and idle, and the LCS keeps
that set as small as possible.

When an update is reconcilable, we create a checkpoint, apply all operations in one
`sharedModel.transact()`, and advance the context's recorded on-disk revision
(`contentsModel.last_modified` / `hash` / `hash_algorithm`) to the revision this
update reflects, taken from the manifest. We do not save. The user keeps typing. A
small notification reports that changes from disk were applied, with a button to
revert to the checkpoint if they were not wanted.

When an update is not reconcilable, we do not apply any of it, and we do not
advance the recorded revision. The model keeps your edits, the file stays diverged,
and JupyterLab's native save-conflict dialog on Cmd+S is the resolution path. A
lightweight notification points you there.

**Suppressing the conflict dialog without saving.** `Context._maybeSave` decides a
conflict by fetching the current disk model and comparing the recorded
`contentsModel.hash` against the disk `hash` (or `last_modified` when no hash is
available); a mismatch raises the dialog. That recorded revision is advanced only
by the private `_updateContentsModel`, and for a non-collaborative document
`_onFileChanged` ignores external writes, so a plain out-of-band change otherwise
leaves the recorded revision stale and a later save pops the dialog. By advancing
the recorded revision to the reconciled one, a client that has provably
incorporated a revision no longer counts it as divergence, so its next save writes
its merged content with no dialog, while disk moving again correctly re-triggers
one. This needs a small upstream addition to `@jupyterlab/docregistry` `Context`
(for example `overrideFileModel(model)`) since there is no public setter today; a
PoC can set the private `_contentsModel` field in the interim.

**Why no auto-save, and how this handles multiple clients.** Because no client
auto-saves, there is no competing write. Each client advances only its own recorded
revision, and only for updates it reconciled. A client editing a cell that a
revision touched does not reconcile it, keeps its older recorded revision, and
correctly gets the native dialog on its next save; other clients merge cleanly.
Saves stay explicit, so the recorded revision must always be advanced to the
specific revision the update carried, never a fresh `contents.get`, which could be
a newer revision the client has not reconciled. Applying leaves the model marked
dirty, which is fine: reconcilability is judged by the per-cell `base`/`current`
hashes, not the context dirty flag.

## FAQ

**Why compute the diff on the client, not the server?** Only the client holds the
authoritative live document, including unsaved edits, so only the client can
compare against the right baseline. Server-side diffing would compare disk against
disk and would need the client to stream its state up, which may be stale on
arrival. The server therefore only hashes; the client decides.

**How do I reject or undo a change the agent made on disk?** Updates are applied to
the in-browser shared model only; we never save on your behalf. For a
non-reconcilable update we do not apply it at all, so your edits stand and Cmd+S
invokes JupyterLab's native save-conflict dialog (Overwrite, Revert, Cancel). For a
reconcilable update we apply it and write a checkpoint first, showing a
notification, so you can roll back to the pre-update state from that notification or
File > Revert to Checkpoint.

**What happens when multiple clients have the notebook open?** Nothing races,
because no client auto-saves. Each client independently decides whether an update
is reconcilable and advances only its own recorded revision, and only for updates
it applied. A client editing a cell that the update changed does not reconcile it
and keeps its older recorded revision, so its next Cmd+S correctly raises the native
conflict dialog, while clients that were untouched merge cleanly. Saves are always
explicit, so whichever user saves first sets the next disk revision that the others
then reconcile or resolve through the dialog.

**How does this scale to very large notebooks?** The plan is to first establish
JupyterLab's own baseline behavior on very large notebooks, then confirm our
incremental updates do not meaningfully degrade it. A patch carries only changed
cells, server hashing runs on worker threads, and client hashing runs in a
debounced worker over only changed cells, so an update should cost far less than
the notebook already costs to hold and render. We do not add hard size limits until
that comparison shows they are needed.

**What happens on reconnect?** The server sends a fresh full `NbManifest` and the
client reconciles its model against it. There is no version or sequence number to
track: any drift is resolved by a full reconcile, and any content the server
reports is either reconcilable, in which case we apply it, or not, in which case
Cmd+S resolves it.

**How are multiple views of one notebook handled?** You can open a file as a
notebook and as plaintext at the same time. The notebook view is driven by the
shared `YNotebook` and receives incremental cell updates. The plaintext view is
raw JSON, and text diffing is out of scope, so we keep it on the coarse path: if it
is clean we revert it, and if it is dirty we leave it for Cmd+S. In short, we make
sure the plaintext view is in a non-dirty state before reloading it.

**What about nonconsequential changes, like a date or line-ending change?** Cells
are hashed independently, so a notebook-metadata date change rewrites no cell
source. EOL is normalized before hashing, so a line-ending-only change is a no-op
and `setSource` cannot corrupt content.

**What if a writer does not preserve cell ids?** Because we key on immutable ids, a
rewritten id looks like a delete plus an insert. We can reconcile that when the old
cell is clean and idle, recreating it and losing that cell's live widget state, so
it is safe but heavier than a plain content update.

**Why Myers for the LCS, and not another algorithm?** The reconcile runs over the
cell-id lists, not content, so the sequences are short (one entry per cell) and the
elements are unique. Myers (via `diff-sequences`) is output-sensitive, O(N·D) in
the number of differences, so the common case of a few changed cells is close to
O(N). Hunt-Szymanski, O(N log N) via the unique-key LIS reduction, has a better
worst case for a massive reorder, and general dynamic-programming LCS is O(N·m),
the #401 cost class, which we avoid. Given our workload is dominated by small
changes and the id lists are short, Myers is the pragmatic default, and since it is
a private detail we can revisit it if a pathological reorder ever appears.

## Future goals

- Live updates for plaintext files, including plaintext views of notebooks.
- Non-destructive moves once `@jupyter/ydoc` adopts Yjs's native move.

## Test plan

Uses the galata contents API so the server watcher fires:

- Agent edits cell 2 while the user has unsaved text in cell 1: the update applies,
  cell 1 untouched, and the user's next save shows no conflict dialog because the
  recorded revision was advanced.
- Update touches the cell the user is editing: the whole update is rejected, the
  user's edits stand, the recorded revision is not advanced, and Cmd+S shows the
  native conflict dialog.
- Two clients open: client A reconciles and later saves; client B, editing a cell
  that changed, does not reconcile and gets the native dialog on its next save.
- Reconcilable update creates a checkpoint and shows a revert notification.
- Delete and reorder of clean, idle cells reconcile; the surviving cells keep their
  live state.
- Pending `input()`: a busy cell is not rewritten or removed and the kernel does
  not deadlock.
- Reconnect with a diverged document: a full manifest reconciles cleanly.
- Notebook view and plaintext view of the same file open at once: the notebook view
  updates per cell, the clean plaintext view reverts.
- Nonconsequential changes: metadata-only date and EOL-only changes rewrite no cells.
- Invalid or partial file on disk: the server skips it and never sends a broken update.
- Scale: measure JupyterLab's baseline behavior on a very large notebook, then
  confirm incremental updates do not meaningfully degrade hashing time, apply
  latency, or memory beyond that baseline.
