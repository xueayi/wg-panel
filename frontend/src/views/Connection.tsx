import { useEffect, useState } from 'react'
import { api, ConnSettings } from '../api'
import { Badge, Button, Card, CardHeader, Field, Hint, inputClass } from '../components/ui'

export default function Connection({ onToast }: { onToast: (m: string, tone?: 'ok' | 'err') => void }) {
  const [cur, setCur] = useState<ConnSettings | null>(null)
  const [host, setHost] = useState('')
  const [port, setPort] = useState('22')
  const [user, setUser] = useState('root')
  const [iface, setIface] = useState('wg0')
  const [tool, setTool] = useState('/root/wireguard/wgagent.py')
  const [readOnly, setReadOnly] = useState(false)
  const [keyText, setKeyText] = useState('')
  const [busy, setBusy] = useState('')

  const load = async () => {
    try {
      const s = await api.connSettings()
      setCur(s)
      setHost(s.ssh_host)
      setPort(String(s.ssh_port))
      setUser(s.ssh_user)
      setIface(s.iface)
      setTool(s.ssh_remote_tool)
      setReadOnly(s.read_only)
    } catch (e) {
      onToast((e as Error).message, 'err')
    }
  }

  useEffect(() => {
    load()
  }, [])

  const save = async () => {
    setBusy('save')
    try {
      await api.patchConnSettings({
        ssh_host: host.trim(),
        ssh_port: Number(port) || 22,
        ssh_user: user.trim(),
        iface: iface.trim(),
        ssh_remote_tool: tool.trim(),
        read_only: readOnly,
      })
      onToast('已保存，连接已按新参数重建')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy('')
    }
  }

  const test = async () => {
    setBusy('test')
    try {
      const r = await api.testConnection()
      if (r.ok) onToast(`连接正常：读到 ${r.peers ?? 0} 个客户端，${r.online ?? 0} 个在线`)
      else onToast(r.error || '连接失败', 'err')
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy('')
      load()
    }
  }

  const upload = async () => {
    if (!keyText.trim()) return onToast('先把私钥内容粘进来', 'err')
    setBusy('key')
    try {
      const r = await api.uploadSshKey(keyText)
      setKeyText('')
      onToast(`私钥已保存（指纹 ${r.fingerprint}）`)
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="中转节点连接"
          desc="面板通过 SSH 遥控这台机器上的 WireGuard。初始值来自容器环境变量，在这里改过之后以这里的为准。"
          action={
            <div className="flex items-center gap-2">
              <Button disabled={busy === 'test'} onClick={test}>
                {busy === 'test' ? '测试中…' : '测试连接'}
              </Button>
              <Button variant="primary" disabled={busy === 'save'} onClick={save}>
                {busy === 'save' ? '保存中…' : '保存'}
              </Button>
            </div>
          }
        />
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
          <Field
            label="服务地址（Endpoint）"
            hint="中转节点的公网 IP 或域名——客户端最终连的就是它"
          >
            <input className={inputClass} value={host} onChange={(e) => setHost(e.target.value)} placeholder="203.0.113.10" />
          </Field>
          <Field label="SSH 端口">
            <input className={inputClass} value={port} onChange={(e) => setPort(e.target.value)} placeholder="22" />
          </Field>
          <Field label="SSH 用户" hint="需要能读写 /etc/wireguard，通常就是 root">
            <input className={inputClass} value={user} onChange={(e) => setUser(e.target.value)} placeholder="root" />
          </Field>
          <Field label="WireGuard 接口名">
            <input className={inputClass} value={iface} onChange={(e) => setIface(e.target.value)} placeholder="wg0" />
          </Field>
          <Field
            label="内核脚本落点"
            hint="面板把管理脚本放到节点上的位置，换位置不需要重装节点"
          >
            <input className={inputClass} value={tool} onChange={(e) => setTool(e.target.value)} />
          </Field>
          <Field label="只读模式" hint="打开后所有写操作被拒绝，适合刚接管一个陌生节点时先观察">
            <button
              onClick={() => setReadOnly(!readOnly)}
              className={`flex h-[38px] w-full items-center justify-between rounded-lg border px-3 text-[13px] transition ${
                readOnly ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-slate-200 bg-white text-slate-600'
              }`}
            >
              <span>{readOnly ? '只读（禁止修改）' : '可写（允许管理）'}</span>
              <span className={`h-4 w-8 rounded-full ${readOnly ? 'bg-amber-400' : 'bg-slate-300'} relative`}>
                <span
                  className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${
                    readOnly ? 'left-4' : 'left-0.5'
                  }`}
                />
              </span>
            </button>
          </Field>
        </div>
        {cur?.problems?.length ? (
          <div className="mx-5 mb-4 space-y-1.5">
            {cur.problems.map((p) => (
              <div key={p} className="rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-700">{p}</div>
            ))}
          </div>
        ) : null}
      </Card>

      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              SSH 私钥
              <Hint text="面板用它登录中转节点。只读挂载或上传后保存都行；接口永远不会把内容读回来，只显示指纹。" />
            </span>
          }
          desc="没有可用私钥时，面板连不上节点。上传后立刻以 600 落盘。"
          action={
            cur?.key.fingerprint ? (
              <Badge tone="green">已就绪</Badge>
            ) : (
              <Badge tone="red">缺失</Badge>
            )
          }
        />
        <div className="space-y-3 px-5 py-4">
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="text-slate-400">路径</span>
            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-700">
              {cur?.key.path || '—'}
            </code>
            {cur?.key.fingerprint && (
              <>
                <span className="text-slate-400">指纹</span>
                <code className="font-mono text-[12px] text-slate-600">{cur.key.fingerprint}</code>
              </>
            )}
            {cur?.key.managed && <Badge tone="indigo">面板托管</Badge>}
          </div>
          <Field
            label="粘贴私钥以替换（可选）"
            hint="从 BEGIN 到 END 整段粘进来。提交后内容立即落盘 600，接口不回显。"
          >
            <textarea
              className={`${inputClass} h-24 font-mono text-[12px]`}
              value={keyText}
              onChange={(e) => setKeyText(e.target.value)}
              placeholder="把私钥文件内容整段粘到这里（以 BEGIN 开头的那段）"
            />
          </Field>
          <Button disabled={busy === 'key'} onClick={upload}>
            {busy === 'key' ? '上传中…' : '上传并启用'}
          </Button>
        </div>
      </Card>
    </div>
  )
}
