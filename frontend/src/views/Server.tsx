import { useEffect, useState } from 'react'
import { api, IpPool, Peer, ServerInfo } from '../api'
import { Badge, Button, Card, CardHeader, Empty, Field, Hint, inputClass } from '../components/ui'

export default function Server({ onToast, nonce }: { onToast: (m: string, tone?: 'ok' | 'err') => void; nonce: number }) {
  const [info, setInfo] = useState<ServerInfo | null>(null)
  const [pool, setPool] = useState<IpPool | null>(null)
  const [peerRows, setPeerRows] = useState<Peer[]>([])
  const [newNet, setNewNet] = useState('')
  const [busyNet, setBusyNet] = useState(false)
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

  // 顶栏全局刷新：nonce 变化时重新拉数据（挂载时的 0 不触发）
  useEffect(() => {
    if (nonce) load()
  }, [nonce])

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

  const segments = pool?.networks?.length ? pool.networks : pool ? [pool] : []
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
          desc="有些客户端本身就是网关（比如家里/公司的路由器），它们背后还带着一个内网。把这些内网段并入 VPN，设备之间就能跨站点互访。"
          action={
            <span className="inline-flex items-center gap-1">
            <Hint text="把各个站点背后的局域网网段，一次性写进所有客户端的 AllowedIPs 并重新生成配置。例如站点 A（家里路由器）背后是 192.168.1.0/24、站点 B（公司）背后是 10.0.0.0/24——点它之后，两边的手机/电脑就能互相访问对方的内网。改前会自动备份。注意：被访问那一侧的网关要开着内核转发与 NAT 伪装才能真正通。" />
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
              每新增一个带内网的站点，点一次右上角这个按钮，所有设备的配置就会被更新、能访问对方的内网（改前自动备份）。
              注意：被访问那一侧的网关仍需开启内核转发与 NAT 伪装，否则内网进不去。
            </p>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              虚拟 IP 池
              <Hint text="每个虚拟网段都是一个可分配的地址空间。主网段用满后，在这里挂一个新网段（比如 10.8.2.0/24），新增客户端会自动落到有空位的段。面板会把新网段加到接口地址上并同步到内核。" />
            </span>
          }
          desc={pool ? `共 ${segments.length} 个网段 · 已分配 ${pool.used_count + segments.slice(1).reduce((s, x) => s + x.used_count, 0)} · 下一个 ${pool.next || '（主网段已满）'}` : '载入中…'}
        />
        <div className="px-5 py-4">
          {pool ? (
            <>
              {segments.map((seg, idx) => {
                const base = seg.network.split('/')[0].split('.').slice(0, 3).join('.')
                const segUsed = new Set(seg.used.map((u) => u.ip))
                return (
                  <div key={seg.network} className={idx ? 'mt-6' : ''}>
                    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
                      <span className="font-medium text-slate-700">{seg.network}</span>
                      <span className="text-slate-400">网关 {seg.gateway}</span>
                      <span className="text-slate-400">
                        已用 {seg.used_count} · 空闲 {seg.free_count}
                      </span>
                      {idx === 0 ? (
                        <Badge tone="indigo">主网段</Badge>
                      ) : (
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={async () => {
                            if (!confirm(`摘除虚拟网段 ${seg.network}？\n\n段内若还有客户端会被拒绝；摘除会重渲染并同步配置（先自动备份）。`)) return
                            try {
                              await api.removeNetwork(seg.network)
                              onToast(`已摘除 ${seg.network}`)
                              load()
                            } catch (e) {
                              onToast((e as Error).message, 'err')
                            }
                          }}
                        >
                          摘除
                        </Button>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {Array.from({ length: 253 }, (_, i) => {
                        const ip = `${base}.${i + 2}`
                        const owner = seg.used.find((u) => u.ip === ip)
                        return (
                          <span
                            key={ip}
                            title={owner ? `${ip} · ${owner.name}` : `${ip} 空闲`}
                            className={`h-4 w-4 rounded-[3px] ${
                              segUsed.has(ip) ? 'bg-indigo-500' : 'bg-slate-100'
                            } ${ip === seg.next ? 'ring-2 ring-emerald-400' : ''}`}
                          />
                        )
                      })}
                    </div>
                  </div>
                )
              })}

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

              <div className="mt-5 flex flex-wrap items-end gap-2 border-t border-slate-100 pt-4">
                <div className="w-56">
                  <Field label="添加虚拟网段" hint="主机位必须是 0">
                    <input
                      className={inputClass}
                      value={newNet}
                      onChange={(e) => setNewNet(e.target.value)}
                      placeholder="10.8.2.0/24"
                    />
                  </Field>
                </div>
                <Button
                  variant="primary"
                  disabled={!newNet.trim() || busyNet}
                  onClick={async () => {
                    setBusyNet(true)
                    try {
                      await api.addNetwork(newNet.trim())
                      onToast(`已挂上网段 ${newNet.trim()}`)
                      setNewNet('')
                      load()
                    } catch (e) {
                      onToast((e as Error).message, 'err')
                    } finally {
                      setBusyNet(false)
                    }
                  }}
                >
                  {busyNet ? '添加中…' : '添加网段'}
                </Button>
              </div>

              <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {segments.flatMap((seg) =>
                  seg.used.map((u) => (
                    <div key={u.ip} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2">
                      <span className="font-mono text-[12px] text-slate-600">{u.ip}</span>
                      <span className="text-[13px] text-slate-700">{u.name}</span>
                    </div>
                  )),
                )}
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
