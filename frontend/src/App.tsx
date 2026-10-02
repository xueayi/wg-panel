import { Component, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from './api'
import { Button, Toast, inputClass } from './components/ui'

/** 任一视图渲染出错都不能变成整页白屏——至少把错误摆出来。 */
class ErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null }
  static getDerivedStateFromError(err: Error) {
    return { err }
  }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-5 py-4 text-[13px] text-red-700">
        <div className="mb-1 font-medium">这个页面渲染出错了</div>
        <div className="font-mono text-[12px]">{this.state.err.message}</div>
        <button
          className="mt-3 rounded-lg bg-white px-3 py-1.5 text-[13px] text-red-700 ring-1 ring-red-200"
          onClick={() => this.setState({ err: null })}
        >
          重试
        </button>
      </div>
    )
  }
}
import Connection from './views/Connection'
import Dashboard from './views/Dashboard'
import Peers from './views/Peers'
import Server from './views/Server'
import System from './views/System'

type Tab = 'overview' | 'peers' | 'server' | 'connection' | 'system'

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'peers', label: '客户端' },
  { key: 'server', label: '中转节点 & IP' },
  { key: 'connection', label: '连接设置' },
  { key: 'system', label: '备份 & 审计' },
]

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [toast, setToast] = useState<{ msg: string; tone: 'ok' | 'err' } | null>(null)
  const [readOnly, setReadOnly] = useState(false)

  const say = (msg: string, tone: 'ok' | 'err' = 'ok') => setToast({ msg, tone })

  const check = async () => {
    try {
      const h = await api.health()
      setReadOnly(h.read_only)
      setAuthed(true)
    } catch {
      setAuthed(false)
    }
  }

  useEffect(() => {
    check()
    const onUnauth = () => setAuthed(false)
    window.addEventListener('wgpanel:unauthorized', onUnauth)
    return () => window.removeEventListener('wgpanel:unauthorized', onUnauth)
  }, [])

  if (authed === null) {
    return <div className="flex h-full items-center justify-center text-[13px] text-slate-400">载入中…</div>
  }

  if (!authed) {
    return <Login onOk={() => setAuthed(true)} />
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
        <div className="flex items-center gap-2.5">
          <img src="/logo.png" alt="wg-panel" className="h-7 w-7 rounded-lg object-cover" />
          <span className="text-[15px] font-medium text-slate-900">wg-panel</span>
          {readOnly && (
            <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[12px] text-amber-700 ring-1 ring-inset ring-amber-200">
              只读模式
            </span>
          )}
        </div>
        <Button
          size="sm"
          onClick={async () => {
            await api.logout().catch(() => null)
            setAuthed(false)
          }}
        >
          退出
        </Button>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav className="hidden w-48 shrink-0 border-r border-slate-200 bg-white px-2 py-3 sm:block">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`mb-0.5 block w-full rounded-lg px-3 py-2 text-left text-[13px] transition ${
                tab === t.key ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-600 hover:bg-slate-50'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <main className="min-w-0 flex-1 overflow-y-auto px-5 py-5">
          <div className="mx-auto max-w-6xl">
            <ErrorBoundary>
              {tab === 'overview' && <Dashboard onToast={say} onGoPeers={() => setTab('peers')} />}
              {tab === 'peers' && <Peers onToast={say} />}
              {tab === 'server' && <Server onToast={say} />}
              {tab === 'connection' && <Connection onToast={say} />}
              {tab === 'system' && <System onToast={say} />}
            </ErrorBoundary>
          </div>
        </main>
      </div>

      <nav className="flex shrink-0 border-t border-slate-200 bg-white sm:hidden">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 py-2.5 text-[12px] ${
              tab === t.key ? 'text-indigo-700' : 'text-slate-500'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {toast && <Toast message={toast.msg} tone={toast.tone} />}
    </div>
  )
}

function Login({ onOk }: { onOk: () => void }) {
  const [user, setUser] = useState('admin')
  const [pass, setPass] = useState('')
  const [err, setErr] = useState('')

  const submit = async () => {
    setErr('')
    try {
      await api.login(user, pass)
      onOk()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  return (
    <div className="grid h-full place-items-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-card">
        <div className="mb-5 flex items-center gap-2.5">
          <img src="/logo.png" alt="wg-panel" className="h-9 w-9 rounded-xl object-cover" />
          <div>
            <div className="text-[15px] font-medium text-slate-900">wg-panel</div>
            <div className="text-[12px] text-slate-400">WireGuard 云端管理</div>
          </div>
        </div>
        <div className="space-y-3">
          <input className={inputClass} value={user} onChange={(e) => setUser(e.target.value)} placeholder="用户名" />
          <input
            className={inputClass}
            type="password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="口令"
          />
          {err && <div className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{err}</div>}
          <Button variant="primary" className="w-full" onClick={submit}>
            登录
          </Button>
        </div>
      </div>
    </div>
  )
}
