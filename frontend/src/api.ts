const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: 'same-origin',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (res.status === 401) {
    window.dispatchEvent(new Event('wgpanel:unauthorized'))
    throw new ApiError(401, '未登录或会话已过期')
  }
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const msg =
      (data && typeof data === 'object' && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : '') || `请求失败（${res.status}）`
    throw new ApiError(res.status, msg)
  }
  return data as T
}

export interface Peer {
  name: string
  ip: string
  pubkey: string
  state: string
  enabled: boolean
  disabled: boolean
  tunnel: string
  note: string
  created: string
  endpoint: string
  handshake_human: string
  rx: number
  tx: number
  client_allowed_ips?: string[]
  site_routes?: string[]
  conf_text?: string
}

export interface ServerInfo {
  interface: { name: string; address: string; listen_port: string; public_key: string; save_config: string }
  advertised_endpoint: string
  client_lan_allowed_ips: string[]
  client_dns: string
  client_mtu: string
  stats: { total: number; online: number; disabled: number; rx: number; tx: number }
}

export interface IpPool {
  network: string
  used: { ip: string; name: string }[]
  used_count: number
  free_count: number
  next: string | null
  free: string[]
}

export interface Backup {
  name: string
  size: number
  mtime: string
}

export interface AuditRow {
  ts: number
  action: string
  target: string
  result: string
  detail: string
}

export interface Status {
  connected: boolean
  peers: number
  online: number
  error?: string
  problems: string[]
  driver: { driver: string; target: string; read_only: boolean }
}

export const api = {
  health: () => req<{ ok: boolean; driver: string; read_only: boolean }>('/health'),
  authState: () => req<{ initialized: boolean }>('/auth/state'),
  login: (username: string, password: string) =>
    req<{ ok: boolean }>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => req<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  status: () => req<Status>('/status'),
  server: () => req<ServerInfo>('/server'),
  patchServer: (body: Record<string, unknown>) =>
    req<Record<string, unknown>>('/server', { method: 'PATCH', body: JSON.stringify(body) }),
  ipPool: () => req<IpPool>('/ip-pool'),
  doctor: () => req<{ problems: string[]; warnings: string[] }>('/doctor'),
  syncSiteRoutes: () =>
    req<{ changed: boolean; site_routes: string[]; client_lan_allowed_ips: string[] }>(
      '/sync-site-routes?confirm=true', { method: 'POST' }),

  peers: () => req<{ rows: Peer[]; total: number; online: number; disabled: number }>('/peers'),
  peer: (name: string) => req<Peer>(`/peers/${name}`),
  createPeer: (body: Record<string, unknown>) =>
    req<Peer>('/peers', { method: 'POST', body: JSON.stringify(body) }),
  updatePeer: (name: string, body: Record<string, unknown>) =>
    req<Record<string, unknown>>(`/peers/${name}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deletePeer: (name: string) => req<Record<string, unknown>>(`/peers/${name}?confirm=true`, { method: 'DELETE' }),

  configUrl: (name: string, reveal = false) => `/api/peers/${name}/config${reveal ? '?reveal=true' : ''}`,
  qrUrl: (name: string) => `/api/peers/${name}/qr`,

  backups: async (): Promise<Backup[]> => {
    const d = await req<Backup[] | { data: Backup[] }>('/backups')
    // 契约是数组；万一后端回包了一层，也不能让页面崩
    return Array.isArray(d) ? d : (d?.data ?? [])
  },
  restore: (name: string) => req<Record<string, unknown>>(`/backups/${name}/restore?confirm=true`, { method: 'POST' }),
  adopt: () => req<Record<string, unknown>>('/adopt?confirm=true', { method: 'POST' }),
  refresh: () => req<Record<string, unknown>>('/connect/refresh', { method: 'POST' }),
  audit: (limit = 100) => req<{ rows: AuditRow[] }>(`/audit?limit=${limit}`),
}

export function humanBytes(n: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${i === 0 ? v : v.toFixed(1)}${units[i]}`
}
