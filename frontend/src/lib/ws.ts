import type { Job, Shot } from './api'

export type CutlineWsEvent =
  | { type: 'connected'; project_id: string | null }
  | { type: 'pong' }
  | { type: 'job.updated'; project_id: string; episode_id?: string | null; job: Job }
  | { type: 'shot.updated'; project_id: string; episode_id?: string | null; shot: Shot }

type WsListener = (event: CutlineWsEvent) => void

class CutlineSocketClient {
  private socket: WebSocket | null = null
  private listeners = new Set<WsListener>()
  private subscribedProjectId: string | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectAttempts = 0
  private connected = false

  get isConnected(): boolean {
    return this.connected && this.socket?.readyState === WebSocket.OPEN
  }

  private buildUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const base = `${protocol}//${window.location.host}/api/ws`
    if (this.subscribedProjectId) {
      return `${base}?project_id=${encodeURIComponent(this.subscribedProjectId)}`
    }
    return base
  }

  ensureConnected(projectId?: string | null): void {
    if (typeof window === 'undefined' || typeof WebSocket === 'undefined') {
      return
    }
    if (projectId !== undefined && projectId !== this.subscribedProjectId) {
      this.subscribedProjectId = projectId
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        try {
          this.socket.send(JSON.stringify({ type: 'subscribe', project_id: projectId }))
        } catch {
          // Will resync on reconnect
        }
        return
      }
    }
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return
    }
    this.openSocket()
  }

  private openSocket(): void {
    if (typeof window === 'undefined' || typeof WebSocket === 'undefined') {
      return
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    try {
      const ws = new WebSocket(this.buildUrl())
      this.socket = ws

      ws.onopen = () => {
        this.connected = true
        this.reconnectAttempts = 0
        if (this.subscribedProjectId !== null) {
          try {
            ws.send(JSON.stringify({ type: 'subscribe', project_id: this.subscribedProjectId }))
          } catch {}
        }
        this.startHeartbeat()
      }

      ws.onmessage = (message) => {
        try {
          const parsed = JSON.parse(String(message.data)) as CutlineWsEvent
          if (!parsed || typeof parsed !== 'object' || !('type' in parsed)) return
          for (const listener of this.listeners) {
            listener(parsed)
          }
        } catch {
          // Ignore malformed payloads
        }
      }

      ws.onclose = () => {
        this.connected = false
        this.stopHeartbeat()
        if (this.socket === ws) {
          this.socket = null
        }
        if (this.listeners.size > 0) {
          this.scheduleReconnect()
        }
      }

      ws.onerror = () => {
        this.connected = false
      }
    } catch {
      this.connected = false
      if (this.listeners.size > 0) {
        this.scheduleReconnect()
      }
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.pingTimer = setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        try {
          this.socket.send(JSON.stringify({ type: 'ping' }))
        } catch {}
      }
    }, 25000)
  }

  private stopHeartbeat(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return
    const delayMs = Math.min(5000, 1000 * Math.pow(1.6, this.reconnectAttempts))
    this.reconnectAttempts += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (this.listeners.size > 0) {
        this.openSocket()
      }
    }, delayMs)
  }

  subscribe(listener: WsListener, projectId?: string | null): () => void {
    this.listeners.add(listener)
    this.ensureConnected(projectId)
    return () => {
      this.listeners.delete(listener)
    }
  }
}

export const cutlineWs = new CutlineSocketClient()
