import { useEffect, useState } from 'react'
import { api, IpPool, Peer, ServerInfo } from '../api'
import { Badge, Button, Card, CardHeader, Empty, Field, Hint, inputClass } from '../components/ui'

export default function Server({ onToast }: { onToast: (m: string, tone?: 'ok' | 'err') => void }) {
  const [info, setInfo] = useState<ServerInfo | null>(null)
  const [pool, setPool] = useState<IpPool | null>(null)
  const [peerRows, setPeerRows] = useState<Peer[]>([])
  const [endpoint, setEndpoint] = useState('')
  const [lan, setLan] = useState('')
  const [dns, setDns] = useState('')
  const [mtu, setMtu] = useState('')
  const [resign, setResign] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try {
      const [s, p, pr] = await Promise.all([api.server(), api.ipPool(), api.peers()])
      setInfo(s)
      setPool(p)
      setPeerRows(pr.rows)
      setEndpoint(s.advertised_endpoint)
      setLan((s.client_lan_allowed_ips || []).join(', '))
      setDns(s.client_dns || '')
      setMtu(String(s.client_mtu || ''))
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  useEffect(() => {
    load()
  }, [])

  const save = async () => {
    setBusy(true)
    try {
      const body: Record<string, unknown> = { resign_all: resign }
      if (endpoint !== info?.advertised_endpoint) body.endpoint = endpoint
      if (lan !== (info?.client_lan_allowed_ips || []).join(', ')) body.lan_allowed_ips = lan
      if (dns !== (info?.client_dns || '')) body.dns = dns
      if (mtu !== String(info?.client_mtu || '')) body.mtu = mtu ? Number(mtu) : ''
      if (Object.keys(body).length === 1) return onToast('没有改动')
      await api.patchServer(body)
      onToast(resign ? '已保存并重签全部客户端配置' : '已保存（旧配置需手动重签）')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy(false)
    }
  }

  const usedIps = new Set((pool?.used || []).map((u) => u.ip))
  const siteNodes = peerRows.filter((r) => (r.site_routes || []).length > 0)

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title="中转节点" desc="客户端实际连的地址与端口，以及转发给客户端的网段。" />
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="接口地址" value={info?.interface.address || '—'} />
          <Stat label="监听端口" value={info?.interface.listen_port || '—'} />
          <Stat label="客户端总数" value={String(info?.stats.total ?? '—')} />
          <Stat
            label="在线"
            value={`${info?.stats.online ?? 0} / ${info?.stats.total ?? 0}`}
            badge={<Badge tone={(info?.stats.online ?? 0) > 0 ? 'green' : 'slate'}>实时</Badge>}
          />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="参数"
          desc="改 Endpoint 或网段后，建议同时重签客户端配置，否则旧配置仍指向旧地址。"
          action={
            <Button variant="primary" disabled={busy} onClick={save}>
              {busy ? '保存中…' : '保存'}
            </Button>
          }
        />
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
          <Field label="对外 Endpoint" hint="客户端连过来的地址:端口，可以是域名">
            <input className={inputClass} value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="vpn.example.com:10015" />
          </Field>
          <Field label="转发网段" hint="局域网模式下客户端的 AllowedIPs，逗号分隔">
            <input className={inputClass} value={lan} onChange={(e) => setLan(e.target.value)} placeholder="10.8.1.0/24, 192.168.1.0/24" />
          </Field>
          <Field label="客户端 DNS">
            <input className={inputClass} value={dns} onChange={(e) => setDns(e.target.value)} placeholder="192.168.1.1" />
          </Field>
          <Field label="客户端 MTU" hint="留空不下发">
            <input className={inputClass} value={mtu} onChange={(e) => setMtu(e.target.value)} placeholder="1420" />
          </Field>
          <label className="flex items-center gap-2 text-[13px] text-slate-600 sm:col-span-2">
            <input
              type="checkbox"
              checked={resign}
              onChange={(e) => setResign(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-200"
            />
            保存后重签全部客户端配置（含 Endpoint / 网段变更）
          </label>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="站点互联"
          desc="这些节点本身是网关，背后各挂着一个局域网。中转节点靠这些网段知道「包该转给谁」。"
          action={
            <span className="inline-flex items-center gap-1">
            <Hint text="把所有站点背后的局域网网段，加进每个客户端的 AllowedIPs 并重新下发配置。加了一个带内网的节点后点它，其它设备才能访问那个内网。会先自动备份。" />
            <Button
              variant="primary"
              disabled={!siteNodes.length}
              onClick={async () => {
                try {
                  const r = await api.syncSiteRoutes()
                  onToast(r.changed
                    ? '已并入客户端 AllowedIPs 并重签全部配置'
                    : '站点网段已在客户端 AllowedIPs 中，无需变更')
                  load()
                } catch (e) {
                  onToast((e as Error).message, 'err')
                }
              }}
            >
              并入客户端并重签
            </Button>
            </span>
          }
        />
        {siteNodes.length === 0 ? (
          <Empty text="还没有节点带局域网网段。在「客户端」页新增或编辑时填写「网关网段」" />
        ) : (
          <div className="px-5 py-4">
            <ul className="space-y-2">
              {siteNodes.map((r) => (
                <li key={r.name} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2">
                  <div className="flex items-center gap-2.5">
                    <span className="text-[13px] font-medium text-slate-800">{r.name}</span>
                    <span className="font-mono text-[12px] text-slate-400">{r.ip}</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {r.site_routes!.map((c) => (
                      <Badge key={c} tone="green">{c}</Badge>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[12px] leading-relaxed text-slate-400">
              新加站点后要让他们互相访问，得让每个客户端的配置里都包含对方的网段——
              点右上角一次性并入并重签。被访问侧的网关还要开内核转发与 NAT 伪装。
            </p>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="虚拟 IP 池"
          desc={
            pool
              ? `${pool.network} · 已分配 ${pool.used_count} · 空闲 ${pool.free_count} · 下一个可分配 ${pool.next || '（耗尽）'}`
              : '载入中…'
          }
        />
        <div className="px-5 py-4">
          {pool ? (
            <>
              <div className="flex flex-wrap gap-1">
                {Array.from({ length: 253 }, (_, i) => {
                  const ip = `10.8.1.${i + 2}`
                  const taken = usedIps.has(ip)
                  const owner = (pool.used || []).find((u) => u.ip === ip)
                  return (
                    <span
                      key={ip}
                      title={taken ? `${ip} · ${owner?.name}` : `${ip} 空闲`}
                      className={`h-4 w-4 rounded-[3px] ${
                        taken ? 'bg-indigo-500' : 'bg-slate-100'
                      } ${ip === pool.next ? 'ring-2 ring-emerald-400' : ''}`}
                    />
                  )
                })}
              </div>
              <div className="mt-3 flex items-center gap-4 text-[12px] text-slate-500">
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 rounded-[3px] bg-indigo-500" />已分配
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 rounded-[3px] bg-slate-100" />空闲
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 rounded-[3px] bg-slate-100 ring-2 ring-emerald-400" />下一个
                </span>
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {pool.used.map((u) => (
                  <div key={u.ip} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2">
                    <span className="font-mono text-[12px] text-slate-600">{u.ip}</span>
                    <span className="text-[13px] text-slate-700">{u.name}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-[13px] text-slate-400">载入中…</div>
          )}
        </div>
      </Card>
    </div>
  )
}

function Stat({ label, value, badge }: { label: string; value: string; badge?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-100 px-3.5 py-3">
      <div className="flex items-center justify-between text-[12px] text-slate-400">
        {label}
        {badge}
      </div>
      <div className="mt-1 font-mono text-[15px] text-slate-800">{value}</div>
    </div>
  )
}
