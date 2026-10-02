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

/** 线性图标，跟随文字颜色 */
function TabIcon({ tab }: { tab: Tab }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className: 'shrink-0',
  }
  switch (tab) {
    case 'overview':
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
          <rect x="13.5" y="3" width="7.5" height="7.5" rx="2" />
          <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
          <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" />
        </svg>
      )
    case 'peers':
      return (
        <svg {...common}>
          <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
          <path d="M10.5 18.5h3" />
        </svg>
      )
    case 'server':
      return (
        <svg {...common}>
          <circle cx="12" cy="5" r="2.2" />
          <circle cx="5.5" cy="18" r="2.2" />
          <circle cx="18.5" cy="18" r="2.2" />
          <path d="M10.8 7l-3.6 8.9M13.2 7l3.6 8.9M7.7 18h8.6" />
        </svg>
      )
    case 'connection':
      return (
        <svg {...common}>
          <path d="M9.5 3v5M14.5 3v5" />
          <path d="M6.5 8h11v2.5a5.5 5.5 0 01-11 0V8z" />
          <path d="M12 16v5" />
        </svg>
      )
    case 'system':
      return (
        <svg {...common}>
          <ellipse cx="12" cy="6" rx="7.5" ry="2.8" />
          <path d="M4.5 6v12c0 1.6 3.4 2.8 7.5 2.8s7.5-1.2 7.5-2.8V6" />
          <path d="M4.5 12c0 1.6 3.4 2.8 7.5 2.8s7.5-1.2 7.5-2.8" />
        </svg>
      )
  }
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [toast, setToast] = useState<{ msg: string; tone: 'ok' | 'err' } | null>(null)
  const [readOnly, setReadOnly] = useState(false)
  const [mustChange, setMustChange] = useState(false)
  const [username, setUsername] = useState('admin')

  const say = (msg: string, tone: 'ok' | 'err' = 'ok') => setToast({ msg, tone })

  const check = async () => {
    try {
      const h = await api.health()
      setReadOnly(h.read_only)
      const who = await api.me().catch(() => null)
      setUsername(who?.username || 'admin')
      setMustChange(!!who?.must_change)
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
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
        <div className="flex items-center gap-3">
          <img src="/logo.png" alt="wg-panel" className="h-10 w-10 rounded-xl object-cover shadow-sm ring-1 ring-slate-200" />
          <span className="text-[16px] font-medium text-slate-900">wg-panel</span>
          {readOnly && (
            <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[12px] text-amber-700 ring-1 ring-inset ring-amber-200">
              只读模式
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <a
            href="https://github.com/xueayi/wg-panel"
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" />
            </svg>
            GitHub
          </a>
          <span className="hidden text-[13px] text-slate-400 sm:inline">{username}</span>
          <Button
            size="sm"
            onClick={async () => {
              await api.logout().catch(() => null)
              setAuthed(false)
            }}
          >
            退出
          </Button>
        </div>
      </header>

      {mustChange && (
        <div className="flex shrink-0 items-center gap-3 border-b border-amber-200 bg-amber-50 px-5 py-2 text-[13px] text-amber-800">
          <span>⚠ 你还在使用初始口令（admin / admin），任何人都能登进来。</span>
          <button
            onClick={() => setTab('connection')}
            className="whitespace-nowrap rounded-md bg-white px-2.5 py-1 text-[12px] font-medium text-amber-800 ring-1 ring-amber-300 hover:bg-amber-100"
          >
            去「连接设置」修改
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <nav className="hidden w-52 shrink-0 border-r border-slate-200 bg-white px-2 py-3 sm:block">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`mb-0.5 flex w-full items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-left text-[13px] transition ${
                tab === t.key
                  ? 'bg-indigo-50 font-medium text-indigo-700'
                  : 'text-slate-600 hover:bg-slate-50'
              }`}
            >
              <TabIcon tab={t.key} />
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
  const [hint, setHint] = useState('')

  useEffect(() => {
    // 还没改过初始口令时，直接在登录框下方把账号密码告诉用户
    api
      .authState()
      .then((s) => s.must_change && setHint(s.default_hint))
      .catch(() => null)
  }, [])

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
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <img
            src="/logo.png"
            alt="wg-panel"
            className="h-24 w-24 rounded-2xl object-cover shadow-card ring-1 ring-slate-200"
          />
          <div>
            <div className="text-[17px] font-medium text-slate-900">wg-panel</div>
            <div className="mt-0.5 text-[12px] text-slate-400">WireGuard 云端管理</div>
          </div>
        </div>
        {hint && (
          <div className="mb-3 rounded-lg bg-indigo-50 px-3 py-2 text-[12px] leading-relaxed text-indigo-700">
            {hint}
          </div>
        )}
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
