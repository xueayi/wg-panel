import { useEffect, useState } from 'react'
import { api, DoctorIssue, humanBytes, Peer, ServerInfo, Status } from '../api'
import { Badge, Button, Card, CardHeader, Dot } from '../components/ui'

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
      await api.doctorFix(issue.fix, name)
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

  const load = () => {
    Promise.all([api.status(), api.server(), api.peers(), api.doctor()])
      .then(([s, srv, p, d]) => {
        setStatus(s)
        setServer(srv)
        setRows(p.rows)
        setProblems((d.problems || []).map(toIssue))
        setWarnings((d.warnings || []).map(toIssue))
      })
      .catch((e) => onToast((e as Error).message, 'err'))
  }

  useEffect(load, [])

  // 顶栏全局刷新：nonce 变化时重新拉数据（挂载时的 0 不触发，避免重复加载）
  useEffect(() => {
    if (nonce) load()
  }, [nonce])

  const healthy = problems.length === 0

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="中转节点"
          value={status?.connected ? '已连接' : '未连接'}
          tone={status?.connected ? 'ok' : 'bad'}
          sub={status?.driver?.target || '—'}
        />
        <Tile
          label="在线 / 总数"
          value={`${server?.stats.online ?? 0} / ${server?.stats.total ?? 0}`}
          sub={`${server?.stats.disabled ?? 0} 个已停用`}
        />
        <Tile
          label="累计流量"
          value={humanBytes((server?.stats.rx ?? 0) + (server?.stats.tx ?? 0))}
          sub={`↓ ${humanBytes(server?.stats.rx ?? 0)} · ↑ ${humanBytes(server?.stats.tx ?? 0)}`}
        />
        <Tile
          label="对外 Endpoint"
          mono
          value={server?.advertised_endpoint || '—'}
          sub={`端口 ${server?.interface.listen_port || '—'}`}
        />
      </div>

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
                <li key={p.name} className="flex items-center justify-between px-5 py-3">
                  <div className="flex items-center gap-2.5">
                    <Dot tone={p.disabled ? 'amber' : p.state === '在线' ? 'green' : 'slate'} />
                    <div>
                      <div className="text-[13px] font-medium text-slate-800">{p.name}</div>
                      <div className="font-mono text-[12px] text-slate-400">{p.ip}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-[12px] text-slate-400">
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
            {healthy && warnings.length === 0 && (
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

function Tile({
  label,
  value,
  sub,
  mono,
  tone,
}: {
  label: string
  value: string
  sub?: string
  mono?: boolean
  tone?: 'ok' | 'bad'
}) {
  return (
    <div className="rounded-xl border border-slate-200/80 bg-white px-4 py-3.5 shadow-card">
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
