import { useEffect, useMemo, useState } from 'react'
import { api, humanBytes, IpPool, Peer } from '../api'
import { Badge, Button, Card, CardHeader, Dot, Empty, Field, Hint, Modal, inputClass } from '../components/ui'

const TUNNEL_LABEL: Record<string, string> = {
  lan: '局域网',
  full: '全局',
  custom: '自定义',
}

/** 隧道模式随选项变化的说明（让用户看清每种模式到底会发生什么）。 */
const TUNNEL_INFO: Record<string, { title: string; desc: string; example?: string }> = {
  lan: {
    title: '局域网（推荐，不影响手机其它网络）',
    desc: '只把 VPN 内网（如 10.8.1.0/24）和你在「中转节点」里设的转发网段走隧道。手机上其它流量（刷网页、别的 App）仍然走它自己的蜂窝 / WiFi，完全不受影响——想「只接入家里网络、不改动其它网络」就选它。',
  },
  full: {
    title: '全局（所有流量都经节点）',
    desc: '所有流量（含上网）都经中转节点转发，等于把手机整体「搬」到家里出口：既能访问家里设备，连外网也走家里。缺点是耗节点带宽、速度受节点上行限制；人在外地时外网 IP 也会变成家里的。',
    example: 'AllowedIPs = 0.0.0.0/0 + ::/0',
  },
  custom: {
    title: '自定义（手填网段，最精细）',
    desc: '只放行你手填的网段。比如只想访问家里路由器背后的那台 NAS：填 192.168.1.0/24。需要你清楚到底要通哪些段——否则会连不通。',
  },
}

function stateTone(p: Peer): 'green' | 'slate' | 'amber' {
  if (p.disabled) return 'amber'
  return p.state === '在线' ? 'green' : 'slate'
}

export default function Peers({ onToast, nonce }: { onToast: (m: string, tone?: 'ok' | 'err') => void; nonce: number }) {
  const [rows, setRows] = useState<Peer[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [detail, setDetail] = useState<Peer | null>(null)
  const [fresh, setFresh] = useState<Peer | null>(null)

  const load = async () => {
    try {
      const data = await api.peers()
      setRows(data.rows)
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // 顶栏全局刷新：nonce 变化时重新拉列表（挂载时的 0 不触发）
  useEffect(() => {
    if (nonce) load()
  }, [nonce])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.ip.includes(q) ||
        (p.note || '').toLowerCase().includes(q),
    )
  }, [rows, query])

  const toggle = async (p: Peer) => {
    try {
      await api.updatePeer(p.name, { enabled: p.disabled })
      onToast(p.disabled ? `已启用 ${p.name}` : `已停用 ${p.name}`)
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  const remove = async (p: Peer) => {
    if (!confirm(`删除 ${p.name}？\n\n会一并清理节点上的私钥与配置文件（删前自动备份），此操作不可撤销。`)) return
    try {
      await api.deletePeer(p.name)
      onToast(`已删除 ${p.name}，虚拟 IP ${p.ip} 已回收`)
      setDetail(null)
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="客户端"
          desc="虚拟 IP 由面板自动分发，停用只移出配置、保留私钥，删除才真正回收。"
          action={
            <div className="flex items-center gap-2">
              <input
                className={`${inputClass} w-44`}
                placeholder="搜索名称 / IP / 备注"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <Button variant="primary" onClick={() => setCreating(true)}>
                + 新增客户端
              </Button>
            </div>
          }
        />
        {loading ? (
          <Empty text="载入中…" />
        ) : filtered.length === 0 ? (
          <Empty text="还没有客户端，点右上角新增一个" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-slate-100 text-left text-[12px] text-slate-500">
                  <th className="px-5 py-2.5 font-medium">客户端</th>
                  <th className="px-5 py-2.5 font-medium">虚拟 IP</th>
                  <th className="px-5 py-2.5 font-medium">隧道</th>
                  <th className="px-5 py-2.5 font-medium">网关网段</th>
                  <th className="px-5 py-2.5 font-medium">最近握手</th>
                  <th className="px-5 py-2.5 text-right font-medium">↓ 接收</th>
                  <th className="px-5 py-2.5 text-right font-medium">↑ 发送</th>
                  <th className="px-5 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p) => (
                  <tr key={p.name} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <Dot tone={stateTone(p)} />
                        <div>
                          <div className="font-medium text-slate-800">{p.name}</div>
                          {p.note && <div className="text-[12px] text-slate-400">{p.note}</div>}
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3 font-mono text-[12px] text-slate-600">{p.ip}</td>
                    <td className="px-5 py-3">
                      <Badge tone={p.tunnel === 'full' ? 'indigo' : 'slate'}>
                        {TUNNEL_LABEL[p.tunnel] || p.tunnel || '—'}
                      </Badge>
                    </td>
                    <td className="px-5 py-3">
                      {p.site_routes?.length ? (
                        <div className="flex flex-wrap gap-1">
                          {p.site_routes.map((c) => (
                            <Badge key={c} tone="green">{c}</Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-slate-500">
                      {p.disabled ? <Badge tone="amber">已停用</Badge> : p.handshake_human}
                    </td>
                    <td className="px-5 py-3 text-right font-mono text-[12px] text-slate-500">{humanBytes(p.rx)}</td>
                    <td className="px-5 py-3 text-right font-mono text-[12px] text-slate-500">{humanBytes(p.tx)}</td>
                    <td className="px-5 py-3">
                      <div className="flex justify-end gap-1.5">
                        <Button size="sm" onClick={() => setDetail(p)}>
                          配置
                        </Button>
                        <Button size="sm" onClick={() => toggle(p)}>
                          {p.disabled ? '启用' : '停用'}
                        </Button>
                        <Button size="sm" variant="danger" onClick={() => remove(p)}>
                          删除
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <CreateModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(p) => {
          setCreating(false)
          setFresh(p)
          load()
        }}
        onToast={onToast}
      />
      {detail && <ConfigModal peer={detail} onClose={() => setDetail(null)} onToast={onToast} />}
      {fresh && <ConfigModal peer={fresh} fresh onClose={() => setFresh(null)} onToast={onToast} />}
    </div>
  )
}

function CreateModal({
  open,
  onClose,
  onCreated,
  onToast,
}: {
  open: boolean
  onClose: () => void
  onCreated: (p: Peer) => void
  onToast: (m: string, tone?: 'ok' | 'err') => void
}) {
  const [name, setName] = useState('')
  const [tunnel, setTunnel] = useState('lan')
  const [ip, setIp] = useState('')
  const [net, setNet] = useState('')
  const [nets, setNets] = useState<string[]>([])
  const [pool, setPool] = useState<IpPool | null>(null)
  const [allowedIps, setAllowedIps] = useState('')
  const [siteRoutes, setSiteRoutes] = useState('')
  const [note, setNote] = useState('')
  const [dns, setDns] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    Promise.all([api.server(), api.ipPool()])
      .then(([s, p]) => {
        const list = [s.interface.address?.replace(/\d+\/(\d+)$/, '0/$1') || '', ...(s.extra_networks || [])]
        setNets(list.filter(Boolean))
        setPool(p)
      })
      .catch(() => {
        setNets([])
        setPool(null)
      })
  }, [open])

  const segments = pool?.networks?.length ? pool.networks : pool ? [pool] : []

  const submit = async () => {
    if (!name.trim()) return onToast('先填个客户端名', 'err')
    setBusy(true)
    try {
      const body: Record<string, unknown> = { name: name.trim(), tunnel }
      if (ip.trim()) body.ip = ip.trim()
      if (net.trim()) body.net = net.trim()
      if (tunnel === 'custom' && allowedIps.trim()) body.allowed_ips = allowedIps.trim()
      if (siteRoutes.trim()) body.site_routes = siteRoutes.trim()
      if (note.trim()) body.note = note.trim()
      if (dns.trim()) body.dns = dns.trim()
      const p = await api.createPeer(body)
      onToast(`已分配 ${p.ip} 给 ${p.name}`)
      onCreated({ ...p, conf_text: undefined })
      setName('')
      setIp('')
      setNet('')
      setAllowedIps('')
      setSiteRoutes('')
      setNote('')
      setDns('')
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新增客户端"
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={busy} onClick={submit}>
            {busy ? '创建中…' : '创建并生成配置'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="名称" hint="字母数字与 . _ -，1–32 字符。建议按设备命名，如 iphone15、mac-mini">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="iphone15" />
        </Field>
        <Field
          label={
            <span className="inline-flex items-center gap-1.5">
              隧道模式
              <Hint text="决定这个客户端走隧道的是哪些流量。局域网最省事、且不影响手机上其它网络；全局把所有流量都兜进 VPN；自定义由你手填要通的网段。" />
            </span>
          }
        >
          <div className="grid grid-cols-3 gap-2">
            {[
              { v: 'lan', t: '局域网', d: '只走内网' },
              { v: 'full', t: '全局', d: '全部流量' },
              { v: 'custom', t: '自定义', d: '手填网段' },
            ].map((o) => (
              <button
                key={o.v}
                onClick={() => setTunnel(o.v)}
                className={`rounded-lg border px-3 py-2 text-left transition ${
                  tunnel === o.v
                    ? 'border-indigo-400 bg-indigo-50 ring-2 ring-indigo-100'
                    : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <div className="text-[13px] font-medium text-slate-800">{o.t}</div>
                <div className="text-[12px] text-slate-400">{o.d}</div>
              </button>
            ))}
          </div>
        </Field>
        <div className="rounded-lg bg-slate-50 px-3 py-2.5 text-[12px] leading-relaxed text-slate-600">
          <div className="mb-1 font-medium text-slate-700">{TUNNEL_INFO[tunnel].title}</div>
          <div>{TUNNEL_INFO[tunnel].desc}</div>
          {TUNNEL_INFO[tunnel].example && (
            <code className="mt-1.5 block rounded bg-slate-900/90 px-2 py-1 font-mono text-[11px] text-slate-100">
              {TUNNEL_INFO[tunnel].example}
            </code>
          )}
        </div>
        {tunnel === 'custom' && (
          <Field
            label="自定义 AllowedIPs（CIDR）"
            hint="逗号分隔，例如 192.168.1.0/24, 10.8.1.0/24。自定义模式必填，否则无法创建。"
          >
            <input
              className={inputClass}
              value={allowedIps}
              onChange={(e) => setAllowedIps(e.target.value)}
              placeholder="192.168.1.0/24"
            />
          </Field>
        )}
        {nets.length > 1 && (
          <Field label="从哪个虚拟网段分配" hint="多个网段时指定；不选则自动落到第一个有空位的段">
            <select className={inputClass} value={net} onChange={(e) => setNet(e.target.value)}>
              <option value="">自动（按顺序找空位）</option>
              {nets.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </Field>
        )}
        {segments.length > 0 && (
          <div className="space-y-2">
            <div className="text-[13px] font-medium text-slate-700">虚拟 IP 池（点空格选一个，或手动填下方）</div>
            <div className="max-h-52 space-y-3 overflow-y-auto rounded-lg border border-slate-100 p-3">
              {segments.map((seg, idx) => {
                const base = seg.network.split('/')[0].split('.').slice(0, 3).join('.')
                const segUsed = new Set(seg.used.map((u) => u.ip))
                return (
                  <div key={seg.network}>
                    <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
                      <span className="font-medium text-slate-700">{seg.network}</span>
                      <span className="text-slate-400">已用 {seg.used_count} · 空闲 {seg.free_count}</span>
                      {idx === 0 && <Badge tone="indigo">主网段</Badge>}
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {Array.from({ length: 253 }, (_, i) => {
                        const addr = `${base}.${i + 2}`
                        const owner = seg.used.find((u) => u.ip === addr)
                        const selected = ip === addr
                        return (
                          <button
                            key={addr}
                            type="button"
                            title={owner ? `${addr} · ${owner.name}` : `${addr} 空闲`}
                            disabled={!!owner}
                            onClick={() => {
                              setIp(addr)
                              if (idx > 0) setNet(seg.network)
                            }}
                            className={`h-3.5 w-3.5 rounded-[3px] transition ${
                              owner ? 'cursor-default bg-indigo-500' : 'cursor-pointer bg-slate-100 hover:bg-indigo-300'
                            } ${selected ? 'ring-2 ring-emerald-500' : ''}`}
                          />
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="flex items-center gap-3 text-[12px] text-slate-500">
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-indigo-500" />已分配</span>
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-slate-100" />空闲（可点）</span>
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-slate-100 ring-2 ring-emerald-500" />已选</span>
            </div>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="虚拟 IP（可选）" hint="留空自动分配最小空闲地址；也可点上方格子选">
            <input className={inputClass} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.8.1.20" />
          </Field>
          <Field label="DNS（可选）">
            <input className={inputClass} value={dns} onChange={(e) => setDns(e.target.value)} placeholder="192.168.1.1" />
          </Field>
        </div>
        <Field
          label="网关网段（可选）"
          hint="如果这个节点本身是网关、背后还挂着一个局域网（比如家里的路由器），把它那边的网段填在这里。中转节点才知道「去往这个网段的包要交给它」。多个用逗号分隔，普通手机电脑留空"
        >
          <input
            className={inputClass}
            value={siteRoutes}
            onChange={(e) => setSiteRoutes(e.target.value)}
            placeholder="192.168.1.0/24"
          />
        </Field>
        <Field label="备注（可选）">
          <input className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} placeholder="谁的设备 / 用途" />
        </Field>
      </div>
    </Modal>
  )
}

function ConfigModal({
  peer,
  fresh = false,
  onClose,
  onToast,
}: {
  peer: Peer
  fresh?: boolean
  onClose: () => void
  onToast: (m: string, tone?: 'ok' | 'err') => void
}) {
  const [text, setText] = useState('')

  useEffect(() => {
    api
      .peer(peer.name)
      .then((p) => setText(p.conf_text || ''))
      .catch(() => setText(''))
  }, [peer.name])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      onToast('已复制到剪贴板（私钥已打码，导入前需下载明文版）')
    } catch {
      onToast('复制失败，请手动选中文本', 'err')
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`${peer.name} · 配置导入`}
      footer={
        <>
          <Button onClick={onClose}>关闭</Button>
          <a href={api.configUrl(peer.name)} download>
            <Button variant="primary">下载 .conf（含私钥）</Button>
          </a>
        </>
      }
    >
      <div className="space-y-4">
        {fresh && (
          <div className="rounded-lg bg-emerald-50 px-3 py-2 text-[13px] text-emerald-700">
            已分配虚拟 IP <span className="font-mono">{peer.ip}</span>
            ，用手机扫下面的二维码即可导入。
          </div>
        )}
        <div className="flex gap-4">
          <div className="shrink-0">
            <img
              src={api.qrUrl(peer.name)}
              alt="配置二维码"
              className="h-36 w-36 rounded-lg border border-slate-200 bg-white p-1"
            />
            <div className="mt-1.5 text-center text-[12px] text-slate-400">手机扫码导入</div>
          </div>
          <div className="min-w-0 flex-1">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[13px] font-medium text-slate-700">配置内容</span>
              <Button size="sm" onClick={copy}>
                复制
              </Button>
            </div>
            <pre className="max-h-36 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">
              {text || '载入中…'}
            </pre>
            <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
              这里显示的私钥是打码的。下载按钮给出的是可直接导入的明文版。
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 border-t border-slate-100 pt-3 text-[13px]">
          <div>
            <div className="text-slate-400">虚拟 IP</div>
            <div className="font-mono text-slate-700">{peer.ip}</div>
          </div>
          <div>
            <div className="text-slate-400">隧道模式</div>
            <div className="text-slate-700">{TUNNEL_LABEL[peer.tunnel] || peer.tunnel || '—'}</div>
          </div>
        </div>
      </div>
    </Modal>
  )
}
