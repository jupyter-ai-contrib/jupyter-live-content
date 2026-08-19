import { IDocumentWidget } from '@jupyterlab/docregistry';
import { ISignal, Signal } from '@lumino/signaling';

import { ILiveDocumentRegistry } from './tokens';

/**
 * A mutable `path -> {widgets}` registry of the documents open in this client.
 * A path can carry several widgets (e.g. a notebook open in both notebook view
 * and text-editor view). Populated by the tracker plugin.
 */
export class LiveDocumentRegistry implements ILiveDocumentRegistry {
  get opened(): ISignal<this, string> {
    return this._opened;
  }

  get closed(): ISignal<this, string> {
    return this._closed;
  }

  get(path: string): IDocumentWidget | undefined {
    const set = this._widgets.get(path);
    return set ? set.values().next().value : undefined;
  }

  all(path: string): IDocumentWidget[] {
    const set = this._widgets.get(path);
    return set ? Array.from(set) : [];
  }

  has(path: string): boolean {
    return this._widgets.has(path);
  }

  add(path: string, widget: IDocumentWidget): void {
    let set = this._widgets.get(path);
    const isNew = !set;
    if (!set) {
      set = new Set<IDocumentWidget>();
      this._widgets.set(path, set);
    }
    set.add(widget);
    if (isNew) {
      this._opened.emit(path);
    }
  }

  remove(path: string, widget: IDocumentWidget): void {
    const set = this._widgets.get(path);
    if (!set) {
      return;
    }
    set.delete(widget);
    if (set.size === 0) {
      this._widgets.delete(path);
      this._closed.emit(path);
    }
  }

  private _widgets = new Map<string, Set<IDocumentWidget>>();
  private _opened = new Signal<this, string>(this);
  private _closed = new Signal<this, string>(this);
}
