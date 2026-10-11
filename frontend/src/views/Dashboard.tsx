import { useEffect, useRef, useState } from 'react'
import { api, DoctorIssue, humanBytes, Peer, ServerInfo, Status, TrafficSeries, PanelState, confirmAdoption } from '../api'
import { Badge, Button, Card, CardHeader, Dot, inputClass } from '../components/ui'

/** 体检条目：默认一行文字保持简洁，「处理」能自动修的直接修，「指引」展开看怎么做。 */
function IssueRow({
  issue,
  tone,
  onFixed,
  onToast,
}: {
  issue: DoctorIssue
  tone: 'err' | 'warn'
  onFixed: () => void
  onToast: (m: string, t?: 'ok' | 'err') => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const box = tone === 'err' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'

  const fix = async () => {
    setBusy(true)
    try {
      // resign 这类动作必须指名客户端，target 里带着（形如 istore_zhangjiang.key）
      const name = issue.fix === 'resign' ? (issue.target || '').replace(/\.key$|\.conf$/, '') : ''
      if (issue.fix === 'adopt') {
        if (!await confirmAdoption()) return
      } else {
        await api.doctorFix(issue.fix, name)
      }
      onToast('已处理，正在复核体检结果')
      onFixed()
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`rounded-lg ${box}`}>
      <div className="flex items-start justify-between gap-2 px-3 py-2">
        <span className="text-[13px] leading-relaxed">{issue.message}</span>
        <span className="flex shrink-0 items-center gap-1">
          {issue.fix && (
            <button
              onClick={fix}
              disabled={busy}
              className="rounded-md bg-white/80 px-2 py-0.5 text-[12px] font-medium text-slate-700 hover:bg-white disabled:opacity-50"
            >
              {busy ? '处理中…' : '处理'}
            </button>
          )}
          {issue.hint && (
            <button
              onClick={() => setOpen(!open)}
              className="rounded-md px-1.5 py-0.5 text-[12px] text-slate-500 hover:bg-white/60"
            >
              {open ? '收起' : '指引'}
            </button>
          )}
        </span>
      </div>
      {open && issue.hint && (
        <div className="mx-3 mb-2 rounded-md bg-white/70 px-2.5 py-2 text-[12px] leading-relaxed text-slate-600">
          {issue.hint}
        </div>
      )}
    </div>
  )
}

function toIssue(x: DoctorIssue | string): DoctorIssue {
  return typeof x === 'string'
    ? { code: 'legacy', message: x, fix: '', hint: '', target: '' }
    : x
}

export default function Dashboard({
  onToast,
  onGoPeers,
  nonce,
}: {
  onToast: (m: string, tone?: 'ok' | 'err') => void
  onGoPeers: () => void
  nonce: number
}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [server, setServer] = useState<ServerInfo | null>(null)
  const [rows, setRows] = useState<Peer[]>([])
  const [problems, setProblems] = useState<DoctorIssue[]>([])
  const [warnings, setWarnings] = useState<DoctorIssue[]>([])
  const [traffic, setTraffic] = useState<TrafficSeries | null>(null)
  const [trafficWindow, setTrafficWindow] = useState(3600)
  const [scope, setScope] = useState('all')
  const [state, setState] = useState<PanelState | null>(null)

  const loadState = async () => {
    const current = await api.state()
    setState(current)
    setRows(current.peers)
  }
  const load = () => {
    loadState().catch((e) => onToast((e as Error).message, 'err'))
    Promise.all([api.status(), api.server(), api.doctor()])
      .then(([s, srv, d]) => {
        setStatus(s)
        setServer(srv)
        setProblems((d.problems || []).map(toIssue))
        setWarnings((d.warnings || []).map(toIssue))
      })
      .catch((e) => onToast((e as Error).message, 'err'))
  }

  useEffect(load, [])
  useEffect(() => { if (nonce) load() }, [nonce])

  useEffect(() => {
    let active = true
    let pending = false
    const refresh = async () => {
      if (document.visibilityState !== 'visible' || pending) return
      pending = true
      try {
        const [current, history] = await Promise.all([api.state(), api.traffic(trafficWindow, scope)])
        if (active) {
          setState(current)
          setRows(current.peers)
          setTraffic(history)
        }
      } catch {
        // 首次加载会报告连接问题；轮询期间保留已读到的数据。
      } finally {
        pending = false
      }
    }
    refresh()
    const timer = window.setInterval(refresh, 5000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      active = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [trafficWindow, scope])

  const connected = state?.connected ?? state?.traffic.connected ?? status?.connected ?? false

  return (
    <div className="space-y-5">
      {/* 接口身份区：隧道本身是主体，地址/端口/密钥一眼可见（参考 docker-wireguard-panel 的排版） */}
      <InterfaceCard server={server} status={status} connected={connected} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4">
        <Tile
          label="在线 / 总数"
          value={`${state?.stats.online ?? server?.stats.online ?? 0} / ${state?.stats.total ?? server?.stats.total ?? 0}`}
          sub={`${state?.stats.disabled ?? server?.stats.disabled ?? 0} 个已停用`}
        />
        <Tile
          label="累计流量"
          value={humanBytes((state?.traffic.rx ?? 0) + (state?.traffic.tx ?? 0))}
          sub={`↑ ${humanBytes(state?.traffic.rx ?? 0)} · ↓ ${humanBytes(state?.traffic.tx ?? 0)}`}
        />
        <Tile
          className="col-span-2 sm:col-span-1"
          label="实时速率"
          value={`↑ ${rateText(state?.traffic.rx_rate)} · ↓ ${rateText(state?.traffic.tx_rate)}`}
          sub="客户端 → 服务器 / 服务器 → 客户端"
        />
      </div>

      <Card>
        <CardHeader
          title="流量趋势"
          desc="上传为客户端 → 服务器，下载为服务器 → 客户端；浅色斜纹为数据缺口。"
          action={
            <div className="flex gap-2">
              <select
                className={`${inputClass} w-auto max-w-32 sm:max-w-none`}
                value={scope}
                onChange={(e) => setScope(e.target.value)}
                aria-label="统计对象"
              >
                <option value="all">全部客户端</option>
                {rows.map((p) => (
                  <option key={p.name} value={p.pubkey}>{p.name}</option>
                ))}
              </select>
              <select
                className={`${inputClass} w-auto`}
                value={trafficWindow}
                onChange={(e) => setTrafficWindow(Number(e.target.value))}
                aria-label="时间范围"
              >
                <option value={60}>1 分钟</option>
                <option value={3600}>1 小时</option>
                <option value={43200}>12 小时</option>
                <option value={86400}>24 小时</option>
                <option value={604800}>7 天</option>
                <option value={1296000}>15 天</option>
              </select>
            </div>
          }
        />
        <div className="px-3 pb-4 pt-3 sm:px-5 sm:pb-5">
          <TrafficChart series={traffic} />
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="最近活跃"
            desc="按最近握手排序，前几条就是正在用的设备。"
            action={
              <Button onClick={onGoPeers}>
                管理客户端
              </Button>
            }
          />
          <ul className="divide-y divide-slate-50">
            {rows
              .slice()
              .sort((a, b) => (a.state === '在线' ? -1 : 1))
              .slice(0, 6)
              .map((p) => (
                <li key={p.name} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-3 sm:px-5">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <Dot tone={p.disabled ? 'amber' : p.state === '在线' ? 'green' : 'slate'} />
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-medium text-slate-800">{p.name}</div>
                      <div className="truncate font-mono text-[12px] text-slate-400">
                        {p.ip}{p.endpoint ? ` · ${p.endpoint}` : ''}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-slate-400">
                    {!p.disabled && (p.rx_rate != null || p.tx_rate != null) && (
                      <span className="font-mono" title="↑ 客户端发来 · ↓ 服务器发出">
                        ↑{rateText(p.rx_rate)} ↓{rateText(p.tx_rate)}
                      </span>
                    )}
                    <span>{p.disabled ? '已停用' : p.handshake_human}</span>
                    <span className="font-mono">{humanBytes(p.rx + p.tx)}</span>
                  </div>
                </li>
              ))}
            {rows.length === 0 && <li className="px-5 py-8 text-center text-[13px] text-slate-400">还没有客户端</li>}
          </ul>
        </Card>

        <Card>
          <CardHeader
            title="体检"
            desc="配置与运行时的一致性。可自动修的给「处理」，其余点「指引」看怎么做。"
            action={
              <Button size="sm" onClick={load}>
                重新检查
              </Button>
            }
          />
          <div className="space-y-2 px-5 py-4">
            {problems.length === 0 && warnings.length === 0 && (
              <div className="rounded-lg bg-emerald-50 px-3 py-2 text-[13px] text-emerald-700">一切正常</div>
            )}
            {problems.map((p) => (
              <IssueRow key={p.code + p.message} issue={p} tone="err" onFixed={load} onToast={onToast} />
            ))}
            {warnings.map((w) => (
              <IssueRow key={w.code + w.message} issue={w} tone="warn" onFixed={load} onToast={onToast} />
            ))}
            {(status?.problems || []).length > 0 && (
              <div className="space-y-2 pt-2">
                <div className="text-[12px] text-slate-400">面板配置自检</div>
                {status?.problems.map((p) => (
                  <div key={p} className="rounded-lg bg-slate-100 px-3 py-2 text-[13px] text-slate-600">
                    {p}
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

/** 接口身份区：把「这条隧道是谁、从哪连」放在页面最上面，而不是缩在卡片角落。 */
function InterfaceCard({
  server,
  status,
  connected,
}: {
  server: ServerInfo | null
  status: Status | null
  connected: boolean
}) {
  const iface = server?.interface
  return (
    <Card className="px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Dot tone={connected ? 'green' : 'amber'} />
        <span className="text-[16px] font-medium text-slate-900">{iface?.name || 'wg*'}</span>
        <Badge tone={connected ? 'green' : 'red'}>{connected ? '已连接' : '未连接'}</Badge>
        {status?.driver?.target && <span className="truncate text-[12px] text-slate-400">{status.driver.target}</span>}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2.5 md:grid-cols-3 xl:grid-cols-5">
        <Readout label="隧道地址" value={iface?.address} />
        <Readout label="监听端口" value={iface?.listen_port} />
        <Readout label="客户端 MTU" value={server?.client_mtu} />
        <Readout label="对外 Endpoint" value={server?.advertised_endpoint} />
        <Readout label="服务器公钥" value={iface?.public_key} />
      </dl>
    </Card>
  )
}

function Readout({ label, value }: { label: string; value: string | undefined }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] text-slate-400">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-[13px] text-slate-800" title={value || ''}>{value || '—'}</dd>
    </div>
  )
}

const CHART_PAD = { left: 62, right: 14, top: 14, bottom: 26 }
const CHART_UP = '#818cf8'   // 上传（客户端 → 服务器）
const CHART_DOWN = '#34d399' // 下载（服务器 → 客户端）

/** 流量折线图：Y 轴速率刻度 + 横向网格，X 轴时间刻度；数据缺口断线并铺斜纹。
 *  viewBox 按容器实际宽度取值，文字保持真实大小不随图形缩放。 */
export function TrafficChart({ series }: { series: TrafficSeries | null }) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(720)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let raf = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const w = el.clientWidth
        if (w > 0) setWidth(Math.max(320, w))
      })
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [])

  const points = series?.points || []
  const win = series?.window ?? 3600

  const timeTick = (ts: number) => {
    const d = new Date(ts * 1000)
    const two = (n: number) => String(n).padStart(2, '0')
    if (win <= 60) return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
    if (win <= 86400) return `${two(d.getHours())}:${two(d.getMinutes())}`
    return `${d.getMonth() + 1}-${d.getDate()}`
  }

  const legend = (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500">
      <span className="inline-flex items-center gap-1.5"><i className="inline-block h-0.5 w-4 rounded" style={{ background: CHART_UP }} />上传</span>
      <span className="inline-flex items-center gap-1.5"><i className="inline-block h-0.5 w-4 rounded" style={{ background: CHART_DOWN }} />下载</span>
      <span className="inline-flex items-center gap-1.5"><i className="inline-block h-3 w-4 rounded-sm" style={{ backgroundImage: 'repeating-linear-gradient(135deg, transparent, transparent 2px, #cbd5e1 2px, #cbd5e1 3px)' }} />数据缺口</span>
    </div>
  )

  if (points.length < 2) {
    return (
      <div ref={wrapRef}>
        {legend}
        <div className="grid h-40 place-items-center rounded-lg bg-slate-50 text-[12px] text-slate-400">
          {points.length ? '正在采集…' : '暂无流量采样'}
        </div>
      </div>
    )
  }

  const height = width < 520 ? 220 : 300
  const pad = CHART_PAD
  const plotW = width - pad.left - pad.right
  const plotH = height - pad.top - pad.bottom

  let max = 0
  for (const p of points) max = Math.max(max, p[1] ?? 0, p[2] ?? 0)
  // 全程无流量的窗口也要有轴：按 1 KiB/s 起画，别让四个 "0" 挤在一起
  const idle = !(max > 0)
  if (idle) max = 1024

  const x = (i: number) => pad.left + (i / (points.length - 1)) * plotW
  const y = (v: number) => pad.top + (1 - v / max) * plotH

  // 折线在数据缺口处断开（null 之后重新 M），而不是把缺口拉成 0
  const pathFor = (col: 1 | 2) => {
    let d = ''
    let open = false
    points.forEach((p, i) => {
      const v = p[col]
      if (v == null) { open = false; return }
      d += `${open ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)} `
      open = true
    })
    return d.trim()
  }

  // 缺口范围铺淡斜纹：只标记「两侧都有采样」的内部缺口；
  // 窗口头部/尾部的未采样区留白，避免整屏斜纹淹没图形。
  const known = (i: number) => points[i][1] != null || points[i][2] != null
  const bands: { x1: number; x2: number }[] = []
  let runStart: number | null = null
  points.forEach((p, i) => {
    const gap = !known(i)
    if (gap && runStart == null) {
      // 只有关前一个点有数据时才开始记录（内部缺口）
      if (i > 0 && known(i - 1)) runStart = i
    }
    if (!gap && runStart != null) {
      bands.push({ x1: (x(runStart - 1) + x(runStart)) / 2, x2: (x(i - 1) + x(i)) / 2 })
      runStart = null
    }
  })

  const last = points.length - 1
  const grid: React.ReactNode[] = []
  for (let i = 0; i <= 4; i++) {
    const gy = y((max * i) / 4)
    grid.push(
      <line key={`g${i}`} x1={pad.left} y1={gy} x2={width - pad.right} y2={gy}
        stroke="#e2e8f0" strokeWidth={i === 0 ? 1 : 0.75} />,
      <text key={`t${i}`} x={pad.left - 8} y={gy + 4} textAnchor="end"
        fontSize="11" fill="#94a3b8">{humanBytes((max * i) / 4)}/s</text>,
    )
  }
  const xTicks = [...new Set([0, Math.floor(last / 3), Math.floor((2 * last) / 3), last])].map((i) => (
    <text key={`x${i}`} x={x(i)} y={height - 8}
      textAnchor={i === 0 ? 'start' : i === last ? 'end' : 'middle'}
      fontSize="11" fill="#94a3b8">{timeTick(points[i][0])}</text>
  ))

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = svgRef.current?.getBoundingClientRect()
    if (!box?.width) return
    const scale = width / box.width
    const ratio = ((e.clientX - box.left) * scale - pad.left) / plotW
    setHover(Math.max(0, Math.min(last, Math.round(ratio * last))))
  }

  const hoverPoint = hover != null ? points[hover] : null
  const tipLeft = hover != null ? Math.min(88, Math.max(12, (x(hover) / width) * 100)) : 0

  return (
    <div ref={wrapRef} className="relative">
      {legend}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', height }}
        role="img"
        aria-label="上传和下载速率折线图，浅色斜纹为数据缺口"
      >
        <defs>
          <pattern id="chart-gap" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(135)">
            <rect width="6" height="6" fill="#f1f5f9" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#cbd5e1" strokeWidth="2.5" />
          </pattern>
        </defs>
        {grid}
        {xTicks}
        {bands.map((b, i) => (
          <rect key={i} x={b.x1} y={pad.top} width={Math.max(1, b.x2 - b.x1)} height={plotH} fill="url(#chart-gap)" rx="2" />
        ))}
        {idle && (
          <text x={pad.left + plotW / 2} y={pad.top + plotH / 2} textAnchor="middle" fontSize="12" fill="#94a3b8">
            窗口内无流量
          </text>
        )}
        <path d={pathFor(1)} fill="none" stroke={CHART_UP} strokeWidth="1.6" strokeLinejoin="round" />
        <path d={pathFor(2)} fill="none" stroke={CHART_DOWN} strokeWidth="1.6" strokeLinejoin="round" />
        {hover != null && (
          <line x1={x(hover)} y1={pad.top} x2={x(hover)} y2={pad.top + plotH} stroke="#94a3b8" strokeDasharray="3 3" />
        )}
        {hoverPoint && [[1, CHART_UP], [2, CHART_DOWN]].map(([col, color]) => {
          const v = hoverPoint[col as 1 | 2]
          if (v == null) return null
          return <circle key={col} cx={x(hover!)} cy={y(v)} r="3.5" fill={color as string} />
        })}
        <rect x={pad.left} y={pad.top} width={plotW} height={plotH} fill="transparent"
          onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hoverPoint && (
        <div
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-lg bg-slate-900/95 px-2.5 py-1.5 text-[12px] leading-relaxed text-white shadow-pop"
          style={{ left: `${tipLeft}%` }}
        >
          <div className="font-medium">{new Date(hoverPoint[0] * 1000).toLocaleString('zh-CN', { hour12: false })}</div>
          <div style={{ color: CHART_UP }}>↑ {hoverPoint[1] == null ? '缺口' : `${humanBytes(hoverPoint[1])}/s`}</div>
          <div style={{ color: CHART_DOWN }}>↓ {hoverPoint[2] == null ? '缺口' : `${humanBytes(hoverPoint[2])}/s`}</div>
        </div>
      )}
    </div>
  )
}

function rateText(rate: number | null | undefined): string {
  return rate == null ? '—' : `${humanBytes(rate)}/s`
}

function Tile({
  label,
  value,
  sub,
  mono,
  tone,
  className = '',
}: {
  label: string
  value: string
  sub?: string
  mono?: boolean
  tone?: 'ok' | 'bad'
  className?: string
}) {
  return (
    <div className={`rounded-xl border border-slate-200/80 bg-white px-4 py-3.5 shadow-card ${className}`}>
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-slate-400">{label}</span>
        {tone && (
          <Badge tone={tone === 'ok' ? 'green' : 'red'}>{tone === 'ok' ? '正常' : '异常'}</Badge>
        )}
      </div>
      <div className={`mt-1 text-[17px] text-slate-900 ${mono ? 'font-mono text-[15px]' : ''}`}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[12px] text-slate-400">{sub}</div>}
    </div>
  )
}
