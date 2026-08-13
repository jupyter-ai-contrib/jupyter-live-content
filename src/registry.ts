import { IDocumentWidget } from '@jupyterlab/docregistry';
import { ISignal, Signal } from '@lumino/signaling';

import { ILiveDocumentRegistry } from './tokens';

/**
 * A mutable `path -> IDocumentWidget` registry of the documents open in this
 * client. Populated by the tracker plugin.
 */
export class LiveDocumentRegistry implements ILiveDocumentRegistry {
  get widgets(): ReadonlyMap<string, IDocumentWidget> {
    return this._widgets;
  }

  get opened(): ISignal<this, string> {
    return this._opened;
  }

  get closed(): ISignal<this, string> {
    return this._closed;
  }

  get(path: string): IDocumentWidget | undefined {
    return this._widgets.get(path);
  }

  add(path: string, widget: IDocumentWidget): void {
    this._widgets.set(path, widget);
    this._opened.emit(path);
  }

  remove(path: string): void {
    if (this._widgets.delete(path)) {
      this._closed.emit(path);
    }
  }

  private _widgets = new Map<string, IDocumentWidget>();
  private _opened = new Signal<this, string>(this);
  private _closed = new Signal<this, string>(this);
}
