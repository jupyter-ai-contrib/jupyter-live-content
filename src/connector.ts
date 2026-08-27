import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';
import { Signal } from '@lumino/signaling';

import { ILiveContentConnector, LiveContentMessage } from './tokens';

/**
 * ``PageConfig`` key the server extension sets to ``true`` when it disabled
 * itself (e.g. because a real-time-collaboration provider is active). Mirrors
 * ``PAGE_CONFIG_DISABLED_KEY`` in ``jupyter_live_content/extension.py``.
 */
const DISABLED_PAGE_CONFIG_KEY = 'liveContentServerDisabled';

/**
 * Manages a single WebSocket connection to the live-content server extension.
 *
 * Outgoing messages sent before the socket is open are queued and flushed on
 * open. The connection auto-reconnects with a short fixed backoff; on reconnect
 * the tracker plugin does not need to do anything special because it re-sends
 * `client_opened` for every open document (see `index.ts`).
 *
 * If the server advertised (via `PageConfig`) that it disabled itself, the
 * connector stays dormant: it never opens a socket (which would 404 and
 * reconnect forever) and simply drops any messages.
 */
export class LiveContentConnector implements ILiveContentConnector {
  constructor(serverSettings: ServerConnection.ISettings) {
    this._serverSettings = serverSettings;
    this._ready = new Promise<void>(resolve => {
      this._resolveReady = resolve;
    });
    if (PageConfig.getOption(DISABLED_PAGE_CONFIG_KEY) === 'true') {
      // Server extension is disabled this session; do not connect.
      this._disabled = true;
      this._resolveReady();
      return;
    }
    this._connect();
  }

  get messageReceived(): Signal<this, LiveContentMessage> {
    return this._messageReceived;
  }

  get connected(): Signal<this, void> {
    return this._connected;
  }

  get ready(): Promise<void> {
    return this._ready;
  }

  sendMessage(message: LiveContentMessage): void {
    if (this._disabled) {
      return;
    }
    const raw = JSON.stringify(message);
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(raw);
    } else {
      this._pending.push(raw);
    }
  }

  private _connect(): void {
    const settings = this._serverSettings;
    let url = URLExt.join(settings.wsUrl, 'api', 'live-content', 'ws');
    // Same-origin requests authenticate via cookie/xsrf, but token auth (used
    // by the test server and many deployments) needs the token on the query.
    if (settings.token) {
      url += `?token=${encodeURIComponent(settings.token)}`;
    }

    const ws = new settings.WebSocket(url);
    this._ws = ws;

    ws.onopen = () => {
      // Flush anything queued while connecting.
      while (this._pending.length > 0) {
        ws.send(this._pending.shift() as string);
      }
      this._resolveReady();
      // Announce (re)connection so the tracker can (re)send its open set.
      this._connected.emit();
    };

    ws.onmessage = (event: MessageEvent) => {
      let message: LiveContentMessage;
      try {
        message = JSON.parse(event.data);
      } catch {
        console.warn(
          'live-content: ignoring malformed server message',
          event.data
        );
        return;
      }
      this._messageReceived.emit(message);
    };

    ws.onclose = () => {
      this._ws = null;
      if (!this._disposed) {
        // Reconnect after a short delay.
        window.setTimeout(() => this._connect(), 2000);
      }
    };

    ws.onerror = () => {
      // The close handler drives reconnection; just close here.
      ws.close();
    };
  }

  dispose(): void {
    this._disposed = true;
    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }
  }

  private _serverSettings: ServerConnection.ISettings;
  private _ws: WebSocket | null = null;
  private _pending: string[] = [];
  private _disabled = false;
  private _disposed = false;
  private _ready: Promise<void>;
  private _resolveReady!: () => void;
  private _messageReceived = new Signal<this, LiveContentMessage>(this);
  private _connected = new Signal<this, void>(this);
}
