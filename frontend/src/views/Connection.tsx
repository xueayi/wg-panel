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
  const [auth, setAuth] = useState<'key' | 'password'>('key')

  const [keyText, setKeyText] = useState('')
  const [sshPassword, setSshPassword] = useState('')

  const [me, setMe] = useState('admin')
  const [oldPw, setOldPw] = useState('')
  const [newUser, setNewUser] = useState('')
  const [newPw, setNewPw] = useState('')

  const [busy, setBusy] = useState('')

  const load = async () => {
    try {
      const [s, who] = await Promise.all([api.connSettings(), api.me()])
      setCur(s)
      setHost(s.ssh_host)
      setPort(String(s.ssh_port))
      setUser(s.ssh_user)
      setIface(s.iface)
      setTool(s.ssh_remote_tool)
      setReadOnly(s.read_only)
      setAuth(s.ssh_auth === 'password' ? 'password' : 'key')
      setMe(who.username)
      setNewUser(who.username)
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
        ssh_auth: auth,
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

  const uploadKey = async () => {
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

  const uploadPassword = async () => {
    if (!sshPassword) return onToast('先填密码', 'err')
    setBusy('pw')
    try {
      await api.uploadSshPassword(sshPassword)
      setSshPassword('')
      onToast('密码已保存（只写进 600 权限的文件，接口不回显），认证方式已切到密码')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy('')
    }
  }

  const saveAccount = async () => {
    if (!oldPw) return onToast('先填当前密码', 'err')
    setBusy('account')
    try {
      const r = await api.updateAccount({
        old_password: oldPw,
        username: newUser.trim() || undefined,
        new_password: newPw || undefined,
      })
      setOldPw('')
      setNewPw('')
      if (!r.changed) onToast('没有改动')
      else if (r.relogin) {
        onToast('账号已更新，请用新用户名重新登录')
        setTimeout(() => location.reload(), 1200)
      } else onToast('密码已更新')
      load()
    } catch (e) {
      onToast((e as Error).message, 'err')
    } finally {
      setBusy('')
    }
  }

  const keyReady = !!cur?.key.fingerprint
  const pwReady = !!cur?.password.set

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
          <Field label="服务地址（Endpoint）" hint="中转节点的公网 IP 或域名——客户端最终连的就是它">
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
          <Field label="内核脚本落点" hint="面板把管理脚本放到节点上的位置">
            <input className={inputClass} value={tool} onChange={(e) => setTool(e.target.value)} />
          </Field>
          <Field label="登录方式" hint="密钥更安全；只有密码能登的机器才用密码">
            <div className="grid grid-cols-2 gap-2">
              {[
                { v: 'key' as const, t: '密钥', d: keyReady ? '已就绪' : '未配置' },
                { v: 'password' as const, t: '密码', d: pwReady ? '已设置' : '未设置' },
              ].map((o) => (
                <button
                  key={o.v}
                  onClick={() => setAuth(o.v)}
                  className={`rounded-lg border px-3 py-2 text-left transition ${
                    auth === o.v
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
          <Field label="只读模式" hint="打开后所有写操作被拒绝，适合刚接管一个陌生节点时先观察">
            <button
              onClick={() => setReadOnly(!readOnly)}
              className={`flex h-[38px] w-full items-center justify-between rounded-lg border px-3 text-[13px] transition ${
                readOnly ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-slate-200 bg-white text-slate-600'
              }`}
            >
              <span>{readOnly ? '只读（禁止修改）' : '可写（允许管理）'}</span>
              <span className={`relative h-4 w-8 rounded-full ${readOnly ? 'bg-amber-400' : 'bg-slate-300'}`}>
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
              <Hint text="面板用它登录中转节点。指纹是 OpenSSH 风格的 SHA256——与你在电脑上跑 ssh-keygen -lf 私钥 得到的那串完全一样，可用它确认两边是同一把钥匙。接口永远不会把私钥内容读回来。" />
            </span>
          }
          desc="面板只保存指纹用于核对，私钥内容既不回显也不写进数据库。"
          action={keyReady ? <Badge tone="green">已就绪</Badge> : <Badge tone="amber">未配置</Badge>}
        />
        <div className="space-y-3 px-5 py-4">
          <div className="space-y-1 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-slate-400">路径</span>
              <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-700">
                {cur?.key.path || '—'}
              </code>
              {cur?.key.managed && <Badge tone="indigo">面板托管</Badge>}
            </div>
            {keyReady && (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-400">指纹</span>
                  <code className="font-mono text-[12px] text-slate-700">{cur?.key.fingerprint}</code>
                  <span className="text-[12px] text-slate-400">（等于 ssh-keygen -lf 该私钥 的输出）</span>
                </div>
                {cur?.key.public_line && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-slate-400">公钥</span>
                    <code className="max-w-full truncate rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-600">
                      {cur.key.public_line}
                    </code>
                    <span className="text-[12px] text-slate-400">（拿去节点的 authorized_keys 对账）</span>
                  </div>
                )}
              </>
            )}
          </div>
          <Field label="粘贴私钥以替换（可选）" hint="整段粘进来即可；提交后立即以 600 落盘，接口不回显。">
            <textarea
              className={`${inputClass} h-24 font-mono text-[12px]`}
              value={keyText}
              onChange={(e) => setKeyText(e.target.value)}
              placeholder="把私钥文件内容整段粘到这里（以 BEGIN 开头的那段）"
            />
          </Field>
          <Button disabled={busy === 'key'} onClick={uploadKey}>
            {busy === 'key' ? '上传中…' : '上传并启用'}
          </Button>
        </div>
      </Card>

      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              SSH 密码
              <Hint text="给只允许密码登录的机器用。密码写进 600 权限的文件，不进数据库、不进日志、接口不回显；填写后认证方式会自动切到「密码」。" />
            </span>
          }
          desc="密钥不方便时用密码登录中转节点。"
          action={pwReady ? <Badge tone="green">已设置</Badge> : <Badge tone="slate">未设置</Badge>}
        />
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
          <Field label="SSH 密码" hint={pwReady ? '已设置；重新填写会覆盖' : '填写后立即生效'}>
            <input
              className={inputClass}
              type="password"
              value={sshPassword}
              onChange={(e) => setSshPassword(e.target.value)}
              placeholder={pwReady ? '••••••••（留空表示不修改）' : '输入中转节点的登录密码'}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={busy === 'pw' || !sshPassword} onClick={uploadPassword}>
              {busy === 'pw' ? '保存中…' : '保存密码并切换认证方式'}
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              面板账号
              <Hint text="这是登录这个面板用的用户名与密码，和上面的 SSH 凭证无关。改用户名后需要重新登录。" />
            </span>
          }
          desc={`当前登录：${me}`}
        />
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-3">
          <Field label="用户名">
            <input className={inputClass} value={newUser} onChange={(e) => setNewUser(e.target.value)} placeholder="admin" />
          </Field>
          <Field label="新密码" hint="至少 8 位；留空表示只改用户名">
            <input
              className={inputClass}
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="留空则不改密码"
            />
          </Field>
          <Field label="当前密码" hint="需要验证身份">
            <input
              className={inputClass}
              type="password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
              placeholder="输入当前密码"
            />
          </Field>
          <div className="sm:col-span-3">
            <Button variant="primary" disabled={busy === 'account'} onClick={saveAccount}>
              {busy === 'account' ? '保存中…' : '保存账号设置'}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}
