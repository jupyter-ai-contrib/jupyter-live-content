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
import { applyServerUpdate } from './applier';
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

    const track = (widget: IDocumentWidget): void => {
      const context = widget.context;
      let path = context.path;

      registry.add(path, widget);
      connector.sendMessage({ type: 'client_opened', path });

      // Follow renames so the routing key stays correct.
      context.pathChanged.connect((_, newPath: string) => {
        if (newPath === path) {
          return;
        }
        registry.remove(path);
        connector.sendMessage({ type: 'client_closed', path });
        path = newPath;
        registry.add(path, widget);
        connector.sendMessage({ type: 'client_opened', path });
      });

      widget.disposed.connect(() => {
        registry.remove(path);
        connector.sendMessage({ type: 'client_closed', path });
      });
    };

    // New opens (any file type - notebooks, text, images, ...).
    opener.opened.connect((_, widget: IDocumentWidget) => track(widget));

    // On every (re)connect, re-announce the documents this client has open so
    // the server rebuilds its watch set (e.g. after a socket drop/restart).
    connector.connected.connect(() => {
      for (const path of registry.widgets.keys()) {
        connector.sendMessage({ type: 'client_opened', path });
      }
    });

    // Startup sweep: pick up documents already restored into the shell.
    if (labShell) {
      for (const widget of labShell.widgets('main')) {
        const context = docManager?.contextForWidget(widget);
        if (context && !registry.widgets.has(context.path)) {
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
 * we have open, it delegates to `applyServerUpdate` (see `applier.ts`), which
 * reloads the document from disk only when it is eligible for live updates (a
 * simple file editor or a read-only viewer) and not dirty.
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
      applyServerUpdate(registry, message.path);
    });

    console.log(`${PLUGIN_NAMESPACE}:applier is activated`);
  }
};

const plugins: JupyterFrontEndPlugin<any>[] = [
  connectorPlugin,
  trackerPlugin,
  applierPlugin
];

export default plugins;
