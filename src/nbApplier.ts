import { IDocumentWidget } from '@jupyterlab/docregistry';
import { Notification } from '@jupyterlab/apputils';
import type { CellChange, ISharedCell, ISharedNotebook } from '@jupyter/ydoc';

import { planReconcile, IReconcileInput } from './reconcile';
import { ICellHashes } from './reconcile';
import { clientHasRevision } from './revision';
import { INbManifest, INbUpdate } from './tokens';

/**
 * Cell metadata keys that are local view state or kernel-written timing, not
 * user content. Mirrors the server-side exclusions in `nb_hash.py`. A change
 * confined to these must not mark a cell dirty.
 */
const VOLATILE_META_KEYS = new Set([
  'collapsed',
  'scrolled',
  'execution',
  'jupyter'
]);

interface INbCellSnapshot {
  cell_type: string;
  source: string;
  metadata: any;
}

interface INbSnapshot {
  cellOrder: string[];
  cellsById: Record<string, INbCellSnapshot>;
  nbMetadata: any;
}

/**
 * Drives incremental sync for a single open notebook.
 *
 * It records the server's per-cell hashes as `base`, tracks which cells the user
 * has locally edited since the last sync (the dirty set), and on each server
 * `nb_update` decides via {@link planReconcile} whether the update is
 * reconcilable. If so it applies the cell operations to the shared `YNotebook`
 * inside one transaction, advances the context's recorded revision so the native
 * save-conflict dialog stays quiet, and offers a revert-to-checkpoint action. If
 * not, it leaves the document untouched for the native dialog to resolve.
 */
export class NotebookLiveSync {
  constructor(widget: IDocumentWidget) {
    this._widget = widget;
    this._model = (widget.context.model as any).sharedModel as ISharedNotebook;
    this._wireCellObservers();
    this._model.changed.connect(this._onModelChanged, this);
    // Clear the dirty set once the user saves (model becomes clean).
    widget.context.model.stateChanged.connect(this._onStateChanged, this);
  }

  dispose(): void {
    this._model.changed.disconnect(this._onModelChanged, this);
    this._widget.context.model.stateChanged.disconnect(
      this._onStateChanged,
      this
    );
  }

  /** Whether the bound notebook widget has been disposed. */
  get isDisposed(): boolean {
    return this._widget.isDisposed;
  }

  /** Establish the baseline hashes from a full manifest. */
  onManifest(msg: INbManifest): void {
    this._base = {};
    for (const [id, info] of Object.entries(msg.cells_by_id)) {
      this._base[id] = {
        source_hash: info.source_hash,
        meta_hash: info.meta_hash
      };
    }
    this._nbMetaHash = msg.nb_meta_hash;
    this._dirty.clear();
  }

  /** Handle an incremental update from the server. */
  async onUpdate(msg: INbUpdate): Promise<void> {
    // A client's own save echoes back as a change event. If we already hold this
    // revision (matching hash), the update is a no-op: skip it entirely so no
    // reload happens and no "applied changes" notification appears.
    if (clientHasRevision(this._widget.context, msg)) {
      return;
    }

    const changed: Record<string, ICellHashes> = {};
    for (const [id, info] of Object.entries(msg.cells_by_id)) {
      changed[id] = {
        source_hash: info.source_hash,
        meta_hash: info.meta_hash
      };
    }

    const input: IReconcileInput = {
      currentIds: this._currentIds(),
      targetOrder: msg.cell_order,
      changed,
      base: this._base,
      nbMetaChanged: msg.nb_meta_hash !== this._nbMetaHash,
      nbMetaDirty: false, // TODO: track notebook-metadata dirtiness
      isDirty: id => this._dirty.has(id),
      isBusy: () => false // TODO: detect executing / awaiting-input cells
    };

    const plan = planReconcile(input);
    if (plan === null) {
      // Not reconcilable: leave the document alone; Cmd+S resolves it natively.
      return;
    }

    // Snapshot the current model content BEFORE applying, so "Revert" can undo
    // the change. A ContentsManager checkpoint would be useless here: the agent
    // has already written the new content to disk, so a disk checkpoint captures
    // the post-change state, not the state we want to roll back to.
    const snapshot = this._captureSnapshot();
    const wasDirty = this._widget.context.model.dirty;

    this._applying = true;
    try {
      this._model.transact(() => {
        // 1. Deletions.
        for (const id of plan.deletes) {
          const idx = this._indexOfId(id);
          if (idx >= 0) {
            this._model.deleteCell(idx);
          }
        }
        // 2. Reorder + insert to match the target order exactly, recomputing the
        //    live index of each cell so shifting indices cannot corrupt it.
        plan.targetOrder.forEach((id, target) => {
          const curIdx = this._indexOfId(id);
          if (curIdx === -1) {
            this._model.insertCell(target, this._cellPayload(msg, id));
          } else if (curIdx !== target) {
            this._model.moveCell(curIdx, target);
          }
        });
        // 3. Content updates on surviving cells.
        for (const id of plan.sourceUpdates) {
          const cell = this._cellById(id);
          if (cell) {
            cell.setSource(msg.cells_by_id[id].source);
          }
        }
        for (const id of plan.metaUpdates) {
          const cell = this._cellById(id);
          if (cell) {
            cell.setMetadata(msg.cells_by_id[id].metadata as any);
          }
        }
        // 4. Notebook-level metadata.
        if (plan.applyNbMetadata) {
          this._model.setMetadata(msg.nb_metadata as any);
        }
      });
    } finally {
      this._applying = false;
    }

    // Fold the applied cells into the baseline.
    for (const [id, info] of Object.entries(msg.cells_by_id)) {
      this._base[id] = {
        source_hash: info.source_hash,
        meta_hash: info.meta_hash
      };
    }
    for (const id of plan.deletes) {
      delete this._base[id];
    }
    this._nbMetaHash = msg.nb_meta_hash;

    // Applying mutated the shared model, which flips the document to dirty. But
    // we advanced the recorded revision to match disk, so the model now matches
    // disk: a clean document must stay clean. If it was already dirty (the user
    // has unsaved edits elsewhere), leave it dirty.
    if (!wasDirty) {
      this._widget.context.model.dirty = false;
    }

    this._advanceRecordedRevision(msg);
    this._notifyApplied(snapshot);
  }

  /**
   * Advance the context's recorded on-disk revision so `Context._maybeSave` does
   * not flag a spurious conflict on the user's next save.
   *
   * There is no public API for this yet, so we set the private `_contentsModel`.
   * The proper fix is a small upstream `Context.overrideFileModel(model)`.
   */
  private _advanceRecordedRevision(msg: INbUpdate): void {
    try {
      const cm = (this._widget.context as any)._contentsModel;
      if (cm) {
        if (msg.last_modified) {
          cm.last_modified = msg.last_modified;
        }
        if (msg.hash) {
          cm.hash = msg.hash;
          cm.hash_algorithm = msg.hash_algorithm;
        }
      }
    } catch {
      /* best-effort until the upstream API exists */
    }
  }

  private _notifyApplied(snapshot: INbSnapshot): void {
    if (this._lastNotification) {
      Notification.dismiss(this._lastNotification);
    }
    this._lastNotification = Notification.info('Applied changes from disk', {
      autoClose: false,
      actions: [
        {
          label: 'Revert',
          callback: () => {
            this._restore(snapshot);
            if (this._lastNotification) {
              Notification.dismiss(this._lastNotification);
              this._lastNotification = null;
            }
          }
        }
      ]
    });
  }

  /** Capture the model's current content so a later apply can be undone. */
  private _captureSnapshot(): INbSnapshot {
    const cellOrder: string[] = [];
    const cellsById: Record<string, INbCellSnapshot> = {};
    for (const cell of this._model.cells) {
      const id = cell.getId();
      cellOrder.push(id);
      cellsById[id] = {
        cell_type: (cell as any).cell_type ?? 'code',
        source: String(cell.getSource()),
        metadata: cell.getMetadata()
      };
    }
    return { cellOrder, cellsById, nbMetadata: this._model.getMetadata() };
  }

  /** Restore a previously captured snapshot into the shared model in place. */
  private _restore(snap: INbSnapshot): void {
    this._applying = true;
    try {
      this._model.transact(() => {
        // Remove cells that are not in the snapshot.
        for (const cell of [...this._model.cells]) {
          if (!snap.cellsById[cell.getId()]) {
            const idx = this._indexOfId(cell.getId());
            if (idx >= 0) {
              this._model.deleteCell(idx);
            }
          }
        }
        // Reorder and re-insert to match the snapshot order.
        snap.cellOrder.forEach((id, target) => {
          const curIdx = this._indexOfId(id);
          const c = snap.cellsById[id];
          if (curIdx === -1) {
            this._model.insertCell(target, {
              id,
              cell_type: c.cell_type,
              source: c.source,
              metadata: c.metadata
            } as any);
          } else if (curIdx !== target) {
            this._model.moveCell(curIdx, target);
          }
        });
        // Restore source and metadata where they differ.
        for (const id of snap.cellOrder) {
          const cell = this._cellById(id);
          const c = snap.cellsById[id];
          if (!cell) {
            continue;
          }
          if (String(cell.getSource()) !== c.source) {
            cell.setSource(c.source);
          }
          if (
            JSON.stringify(cell.getMetadata()) !== JSON.stringify(c.metadata)
          ) {
            cell.setMetadata(c.metadata as any);
          }
        }
        this._model.setMetadata(snap.nbMetadata as any);
      });
    } finally {
      this._applying = false;
    }
  }

  private _cellPayload(msg: INbUpdate, id: string): any {
    const info = msg.cells_by_id[id];
    return {
      id,
      cell_type: info.cell_type,
      source: info.source,
      metadata: info.metadata,
      attachments: info.attachments
    };
  }

  private _currentIds(): string[] {
    return this._model.cells.map(c => c.getId());
  }

  private _indexOfId(id: string): number {
    return this._model.cells.findIndex(c => c.getId() === id);
  }

  private _cellById(id: string): ISharedCell | undefined {
    return this._model.cells.find(c => c.getId() === id);
  }

  private _wireCellObservers(): void {
    for (const cell of this._model.cells) {
      if (this._wired.has(cell)) {
        continue;
      }
      this._wired.add(cell);
      cell.changed.connect(this._onCellChanged, this);
    }
  }

  private _onCellChanged(cell: ISharedCell, change: CellChange): void {
    if (this._applying) {
      return;
    }
    // Only user edits should mark a cell dirty. Kernel activity (outputs,
    // execution count, execution state, and the `metadata.execution` timing it
    // writes) must NOT: those are not user edits, we never sync them, and
    // treating them as dirty would wrongly block out-of-band updates to a cell
    // the user merely ran.
    if (this._isUserEdit(change)) {
      this._dirty.add(cell.getId());
    }
  }

  private _isUserEdit(change: CellChange): boolean {
    if (change.sourceChange || change.attachmentsChange) {
      return true;
    }
    if (change.metadataChange) {
      for (const key of change.metadataChange.keys()) {
        if (!VOLATILE_META_KEYS.has(key)) {
          return true;
        }
      }
    }
    return false;
  }

  private _onModelChanged(): void {
    // Re-wire observers for any newly created cells.
    this._wireCellObservers();
  }

  private _onStateChanged(
    _: unknown,
    change: { name: string; newValue: any }
  ): void {
    if (change.name === 'dirty' && change.newValue === false) {
      // A save (or revert) reconciled us with disk; clear local dirtiness.
      this._dirty.clear();
    }
  }

  private _widget: IDocumentWidget;
  private _model: ISharedNotebook;
  private _base: Record<string, ICellHashes> = {};
  private _nbMetaHash: string | null = null;
  private _dirty = new Set<string>();
  private _wired = new WeakSet<ISharedCell>();
  private _applying = false;
  private _lastNotification: string | null = null;
}
