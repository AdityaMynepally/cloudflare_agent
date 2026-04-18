/**
 * WebSocket client for bidirectional communication with the backend.
 * Used for future AI agent commands (navigate, click, fill_form, capture).
 */

export class WSClient {
  constructor() {
    this.ws = null;
    this.sessionId = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 2000;
    this.commandHandler = null;
    this.heartbeatInterval = null;
  }

  async connect(sessionId) {
    const config = await new Promise((resolve) => {
      chrome.storage.local.get(['backendUrl'], resolve);
    });

    const backendUrl = (config.backendUrl || 'http://localhost:8000').replace(/\/+$/, '');
    const wsUrl = backendUrl.replace(/^http/, 'ws') + `/ws/extension/${sessionId}`;
    this.sessionId = sessionId;

    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
          console.log('[WS] Connected to', wsUrl);
          this.reconnectAttempts = 0;
          this._startHeartbeat();
          resolve(true);
        };

        this.ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            this._handleMessage(message);
          } catch (err) {
            console.error('[WS] Failed to parse message:', err);
          }
        };

        this.ws.onclose = (event) => {
          console.log('[WS] Connection closed:', event.code, event.reason);
          this._stopHeartbeat();
          this._attemptReconnect();
        };

        this.ws.onerror = (error) => {
          console.error('[WS] Error:', error);
          reject(error);
        };
      } catch (err) {
        reject(err);
      }
    });
  }

  disconnect() {
    this._stopHeartbeat();
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
  }

  send(message) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  sendCaptureResult(data) {
    this.send({ type: 'capture_result', data });
  }

  onCommand(handler) {
    this.commandHandler = handler;
  }

  _handleMessage(message) {
    if (message.type === 'heartbeat_ack') return;

    if (this.commandHandler) {
      this.commandHandler(message);
    }
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatInterval = setInterval(() => {
      this.send({ type: 'heartbeat', timestamp: new Date().toISOString() });
    }, 30000);
  }

  _stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  _attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('[WS] Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(() => {
      if (this.sessionId) {
        this.connect(this.sessionId).catch(() => {});
      }
    }, delay);
  }

  get isConnected() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }
}
