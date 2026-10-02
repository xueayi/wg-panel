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
  extra_networks: string[]
  client_lan_allowed_ips: string[]
  client_dns: string
  client_mtu: string
  stats: { total: number; online: number; disabled: number; rx: number; tx: number }
}

export interface PoolSegment {
  network: string
  gateway: string
  used: { ip: string; name: string }[]
  used_count: number
  free_count: number
  next: string | null
  free: string[]
}

export interface IpPool extends PoolSegment {
  networks: PoolSegment[]
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

export interface DoctorIssue {
  code: string
  message: string
  fix: string
  hint: string
  target: string
}

export interface DoctorResult {
  problems: (DoctorIssue | string)[]
  warnings: (DoctorIssue | string)[]
}

export interface ConnSettings {
  driver: string
  read_only: boolean
  iface: string
  ssh_host: string
  ssh_port: number
  ssh_user: string
  ssh_remote_tool: string
  agent_path: string
  ssh_auth: 'key' | 'password'
  password: { path: string; set: boolean; managed: boolean }
  key: {
    path: string
    uploaded: boolean
    fingerprint: string
    public_line: string
    managed: boolean
  }
  problems: string[]
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
  login: (username: string, password: string) =>
    req<{ ok: boolean }>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => req<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  status: () => req<Status>('/status'),
  server: () => req<ServerInfo>('/server'),
  patchServer: (body: Record<string, unknown>) =>
    req<Record<string, unknown>>('/server', { method: 'PATCH', body: JSON.stringify(body) }),
  ipPool: () => req<IpPool>('/ip-pool'),
  doctor: () => req<DoctorResult>('/doctor'),
  doctorFix: (code: string, name = '') =>
    req<Record<string, unknown>>(
      `/doctor/fix?code=${encodeURIComponent(code)}${name ? `&name=${encodeURIComponent(name)}` : ''}`,
      { method: 'POST' }),
  addNetwork: (cidr: string) =>
    req<Record<string, unknown>>(`/networks?cidr=${encodeURIComponent(cidr)}&confirm=true`, { method: 'POST' }),
  removeNetwork: (cidr: string, force = false) =>
    req<Record<string, unknown>>(
      `/networks?cidr=${encodeURIComponent(cidr)}&confirm=true${force ? '&force=true' : ''}`,
      { method: 'DELETE' }),
  deleteBackup: (name: string) =>
    req<Record<string, unknown>>(`/backups/${encodeURIComponent(name)}?confirm=true`, { method: 'DELETE' }),
  resignPeer: (name: string) =>
    req<Record<string, unknown>>(`/peers/${name}/resign`, { method: 'POST' }),

  connSettings: () => req<ConnSettings>('/settings'),
  patchConnSettings: (body: Record<string, unknown>) =>
    req<Record<string, unknown>>('/settings', { method: 'PATCH', body: JSON.stringify(body) }),
  uploadSshKey: (privateKey: string) =>
    req<{ uploaded: boolean; path: string; fingerprint: string }>('/settings/ssh-key', {
      method: 'POST',
      body: JSON.stringify({ private_key: privateKey }),
    }),
  uploadSshPassword: (password: string) =>
    req<{ saved: boolean; path: string }>('/settings/ssh-password', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authState: () => req<{ initialized: boolean; must_change: boolean; default_hint: string }>('/auth/state'),
  me: () => req<{ username: string; must_change: boolean }>('/auth/me'),
  updateAccount: (body: { old_password: string; username?: string; new_password?: string }) =>
    req<{ ok: boolean; changed: boolean; username: string; relogin?: boolean }>('/auth/account', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  testConnection: () =>
    req<{ ok: boolean; error?: string; peers?: number; online?: number; agent_redeployed?: boolean }>(
      '/settings/test', { method: 'POST' }),
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
