import {
  ILabShell,
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import {
  IDocumentManager,
  IDocumentWidgetOpener
} from '@jupyterlab/docmanager';
import { IDocumentWidget } from '@jupyterlab/docregistry';

import { LiveContentConnector } from './connector';
import { coarseRevert } from './coarse';
import { NotebookLiveSync } from './nbApplier';
import { LiveDocumentRegistry } from './registry';
import { ILiveContentConnector, ILiveDocumentRegistry } from './tokens';

export { ILiveContentConnector, ILiveDocumentRegistry } from './tokens';
export type { LiveContentMessage } from './tokens';

const PLUGIN_NAMESPACE = '@jupyter-ai-contrib/live-content';

/**
 * Plugin 1 - the transport.
 *
 * Provides `ILiveContentConnector`: the single WebSocket channel to the server
 * extension, plus the typed message API defined in `tokens.ts`.
 */
const connectorPlugin: JupyterFrontEndPlugin<ILiveContentConnector> = {
  id: `${PLUGIN_NAMESPACE}:connector`,
  description: 'Provides the live-content WebSocket channel to the server.',
  autoStart: true,
  provides: ILiveContentConnector,
  activate: (app: JupyterFrontEnd): ILiveContentConnector => {
    const connector = new LiveContentConnector(
      app.serviceManager.serverSettings
    );
    console.log(`${PLUGIN_NAMESPACE}:connector is activated`);
    return connector;
  }
};

/**
 * Plugin 2 - the tracker.
 *
 * Requires the connector. Watches document opens/closes, maintains the
 * `path -> IDocumentWidget` registry (provided as `ILiveDocumentRegistry`), and
 * tells the server which files this client has open via `client_opened` /
 * `client_closed`.
 */
const trackerPlugin: JupyterFrontEndPlugin<ILiveDocumentRegistry> = {
  id: `${PLUGIN_NAMESPACE}:tracker`,
  description:
    'Tracks open documents and notifies the server which files are open.',
  autoStart: true,
  requires: [ILiveContentConnector, IDocumentWidgetOpener],
  optional: [ILabShell, IDocumentManager],
  provides: ILiveDocumentRegistry,
  activate: (
    app: JupyterFrontEnd,
    connector: ILiveContentConnector,
    opener: IDocumentWidgetOpener,
    labShell: ILabShell | null,
    docManager: IDocumentManager | null
  ): ILiveDocumentRegistry => {
    const registry = new LiveDocumentRegistry();
    const tracked = new WeakSet<IDocumentWidget>();

    const track = (widget: IDocumentWidget): void => {
      if (tracked.has(widget)) {
        return;
      }
      tracked.add(widget);
      const context = widget.context;
      let path = context.path;

      registry.add(path, widget);
      connector.sendMessage({ type: 'client_opened', path });

      // Follow renames so the routing key stays correct.
      context.pathChanged.connect((_, newPath: string) => {
        if (newPath === path) {
          return;
        }
        registry.remove(path, widget);
        connector.sendMessage({ type: 'client_closed', path });
        path = newPath;
        registry.add(path, widget);
        connector.sendMessage({ type: 'client_opened', path });
      });

      widget.disposed.connect(() => {
        registry.remove(path, widget);
        connector.sendMessage({ type: 'client_closed', path });
      });
    };

    // New opens (any file type - notebooks, text, images, ...). The same file
    // can be opened in multiple views; each is tracked separately.
    opener.opened.connect((_, widget: IDocumentWidget) => track(widget));

    // Startup sweep: pick up documents already restored into the shell.
    if (labShell) {
      for (const widget of labShell.widgets('main')) {
        const context = docManager?.contextForWidget(widget);
        if (context) {
          track(widget as IDocumentWidget);
        }
      }
    }

    console.log(`${PLUGIN_NAMESPACE}:tracker is activated`);
    return registry;
  }
};

/**
 * Plugin 3 - the applier.
 *
 * Requires both the connector and the registry. On a `server_update` for a path
 * we have open, it reloads the document from disk via `context.revert()` -
 * unless the document has unsaved changes, in which case we leave it alone and
 * let JupyterLab's native save-conflict dialog handle the divergence at save
 * time.
 */
const applierPlugin: JupyterFrontEndPlugin<void> = {
  id: `${PLUGIN_NAMESPACE}:applier`,
  description: 'Reloads open documents when their files change on disk.',
  autoStart: true,
  requires: [ILiveContentConnector, ILiveDocumentRegistry],
  activate: (
    app: JupyterFrontEnd,
    connector: ILiveContentConnector,
    registry: ILiveDocumentRegistry
  ): void => {
    connector.messageReceived.connect((_, message) => {
      if (message.type !== 'server_update') {
        return;
      }
      for (const widget of registry.all(message.path)) {
        coarseRevert(widget, message);
      }
    });

    console.log(`${PLUGIN_NAMESPACE}:applier is activated`);
  }
};

/**
 * Plugin 4 - the notebook live-sync.
 *
 * Requires the connector and the registry. For each open notebook it maintains a
 * `NotebookLiveSync` that applies incremental `nb_update` messages to the shared
 * `YNotebook` (see `nbApplier.ts`), rather than the coarse `context.revert()`
 * path used for other document types.
 */
const notebookSyncPlugin: JupyterFrontEndPlugin<void> = {
  id: `${PLUGIN_NAMESPACE}:notebook-sync`,
  description: 'Applies incremental notebook updates to the shared model.',
  autoStart: true,
  requires: [ILiveContentConnector, ILiveDocumentRegistry],
  activate: (
    app: JupyterFrontEnd,
    connector: ILiveContentConnector,
    registry: ILiveDocumentRegistry
  ): void => {
    const syncs = new Map<string, NotebookLiveSync>();

    const isNotebookWidget = (widget: IDocumentWidget): boolean => {
      const shared = (widget.context.model as any).sharedModel;
      return !!shared && Array.isArray(shared.cells);
    };

    const getOrCreate = (path: string): NotebookLiveSync | undefined => {
      const existing = syncs.get(path);
      if (existing && !existing.isDisposed) {
        return existing;
      }
      if (existing) {
        syncs.delete(path);
      }
      // A path can have several views (notebook + text editor). Bind the sync to
      // the notebook view; text views are handled by the coarse revert path.
      const widget = registry.all(path).find(isNotebookWidget);
      if (!widget) {
        return undefined;
      }
      const sync = new NotebookLiveSync(widget);
      syncs.set(path, sync);
      return sync;
    };

    registry.closed.connect((_, path) => {
      const sync = syncs.get(path);
      if (sync) {
        sync.dispose();
        syncs.delete(path);
      }
    });

    connector.messageReceived.connect((_, message) => {
      if (message.type === 'nb_manifest') {
        getOrCreate(message.path)?.onManifest(message);
      } else if (message.type === 'nb_update') {
        // Incrementally update the notebook view's shared model...
        void getOrCreate(message.path)?.onUpdate(message);
        // ...and coarsely reload any text-editor views of the same file, which
        // we do not diff per cell.
        for (const widget of registry.all(message.path)) {
          if (!isNotebookWidget(widget)) {
            coarseRevert(widget, message);
          }
        }
      }
    });

    console.log(`${PLUGIN_NAMESPACE}:notebook-sync is activated`);
  }
};

const plugins: JupyterFrontEndPlugin<any>[] = [
  connectorPlugin,
  trackerPlugin,
  applierPlugin,
  notebookSyncPlugin
];

export default plugins;
