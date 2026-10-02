import { useEffect, useState } from 'react'
import { api, AuditRow, Backup, Status } from '../api'
import { Badge, Button, Card, CardHeader, Empty, Hint } from '../components/ui'

const ACTION_LABEL: Record<string, string> = {
  'peer.add': '新增客户端',
  'peer.remove': '删除客户端',
  'peer.update': '修改客户端',
  'peer.disable': '停用',
  'peer.enable': '启用',
  'peer.reveal': '下载明文配置',
  'peer.qr': '生成二维码',
  'server.update': '改中转节点参数',
  'server.set_endpoint': '改 Endpoint',
  'server.set_lan': '改转发网段',
  'system.adopt': '接管已有配置',
  'system.restore': '回滚配置',
  'system.refresh': '重连节点',
  'auth.login': '登录',
  'auth.password': '修改密码',
}

function time(ts: number) {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export default function System({ onToast, nonce }: { onToast: (m: string, tone?: 'ok' | 'err') => void; nonce: number }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [backups, setBackups] = useState<Backup[]>([])
  const [audit, setAudit] = useState<AuditRow[]>([])

  const load = async () => {
    try {
      const [s, b, a] = await Promise.all([api.status(), api.backups(), api.audit(60)])
      setStatus(s)
      setBackups(b)
      setAudit(a.rows)
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

  const restore = async (b: Backup) => {
    if (!confirm(`用 ${b.name} 覆盖现网配置？\n\n覆盖前会自动再备份一份当前的，可再回滚。`)) return
    try {
      await api.restore(b.name)
      onToast('已回滚，配置已同步到内核')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  const adopt = async () => {
    if (!confirm('接管既有的 wg0.conf：把现网 peer 收进登记表并重写配置（会先自动备份）。\n\n幂等操作，可重复执行。继续？'))
      return
    try {
      await api.adopt()
      onToast('已接管已有配置')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  const refresh = async () => {
    try {
      await api.refresh()
      onToast('已重连并重新下发 wgagent.py')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              连接
              <Hint text="面板只发指令、不承载流量。隧道永远是 客户端 ↔ 中转节点。" />
            </span>
          }
          desc="节点地址与私钥在「连接设置」里改。"
          action={
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1">
                <Button onClick={refresh}>重连并下发</Button>
                <Hint text="重新建立 SSH 连接，并把面板内核脚本（wgagent.py）重新传到节点上——升级面板之后点它，让节点上的脚本跟面板一致。只读，不改任何 VPN 配置。" />
              </span>
              <span className="inline-flex items-center gap-1">
                <Button onClick={adopt}>接管已有配置</Button>
                <Hint text="接管节点上现有的 wg0.conf 里的 peer 收进面板的登记表，之后配置由面板统一管理。会先自动备份，再把 SaveConfig 改成 false 并重渲染配置（不断线）。幂等，可重复执行。" />
              </span>
            </div>
          }
        />
        <div className="grid gap-3 px-5 py-4 sm:grid-cols-3">
          <div className="rounded-lg border border-slate-100 px-3.5 py-3">
            <div className="text-[12px] text-slate-400">驱动</div>
            <div className="mt-1 text-[14px] text-slate-800">{status?.driver?.driver || '—'}</div>
          </div>
          <div className="rounded-lg border border-slate-100 px-3.5 py-3">
            <div className="text-[12px] text-slate-400">目标</div>
            <div className="mt-1 truncate text-[14px] text-slate-800">{status?.driver?.target || '—'}</div>
          </div>
          <div className="rounded-lg border border-slate-100 px-3.5 py-3">
            <div className="text-[12px] text-slate-400">模式</div>
            <div className="mt-1 flex items-center gap-2 text-[14px] text-slate-800">
              {status?.driver?.read_only ? <Badge tone="amber">只读</Badge> : <Badge tone="green">可写</Badge>}
              {status?.connected ? <Badge tone="green">已连接</Badge> : <Badge tone="red">未连接</Badge>}
            </div>
          </div>
        </div>
        {status?.error && (
          <div className="mx-5 mb-4 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{status.error}</div>
        )}
      </Card>

      <Card>
        <CardHeader title="备份" desc="每次写配置前自动落一份，保留最近 20 份。" />
        {backups.length === 0 ? (
          <Empty text="还没有备份" />
        ) : (
          <ul className="divide-y divide-slate-50">
            {backups.map((b) => (
              <li key={b.name} className="flex items-center justify-between px-5 py-3">
                <div className="min-w-0">
                  <div className="truncate font-mono text-[12px] text-slate-700">{b.name}</div>
                  <div className="text-[12px] text-slate-400">
                    {b.mtime} · {b.size} 字节
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-flex items-center gap-1">
                    <Button size="sm" onClick={() => restore(b)}>
                      回滚
                    </Button>
                    <Hint text="用这份备份覆盖当前配置并同步到内核。覆盖前会自动再备份一份当前的，所以回滚本身也可以再回滚。" />
                  </span>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={async () => {
                      if (!confirm(`删除备份 ${b.name}？\n\n只删这一个备份文件，不影响当前配置。`)) return
                      try {
                        await api.deleteBackup(b.name)
                        onToast('已删除该备份')
                        load()
                      } catch (e) {
                        onToast((e as Error).message, 'err')
                      }
                    }}
                  >
                    删除
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader title="操作审计" desc="谁在什么时候动了什么，尤其是私钥的每一次暴露都留痕。" />
        {audit.length === 0 ? (
          <Empty text="暂无记录" />
        ) : (
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-[13px]">
              <tbody>
                {audit.map((a, i) => (
                  <tr key={i} className="border-b border-slate-50 last:border-0">
                    <td className="w-44 px-5 py-2.5 font-mono text-[12px] text-slate-400">{time(a.ts)}</td>
                    <td className="w-32 px-5 py-2.5 text-slate-700">{ACTION_LABEL[a.action] || a.action}</td>
                    <td className="px-5 py-2.5 text-slate-600">{a.target}</td>
                    <td className="w-20 px-5 py-2.5">
                      <Badge tone={a.result === 'ok' ? 'green' : 'red'}>{a.result}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
