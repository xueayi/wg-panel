import { useEffect, useState } from 'react'

export function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-slate-200/80 bg-white shadow-card ${className}`}>
      {children}
    </section>
  )
}

export function CardHeader({
  title,
  desc,
  action,
}: {
  title: React.ReactNode
  desc?: string
  action?: React.ReactNode
}) {
  return (
    <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
      <div>
        <h2 className="text-[15px] font-medium text-slate-900">{title}</h2>
        {desc && <p className="mt-0.5 text-[13px] leading-relaxed text-slate-500">{desc}</p>}
      </div>
      {action}
    </header>
  )
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger' | 'soft'
  size?: 'sm' | 'md'
}

export function Button({ variant = 'soft', size = 'md', className = '', ...rest }: ButtonProps) {
  const base =
    'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50'
  const sizes = size === 'sm' ? 'px-2.5 py-1.5 text-[13px]' : 'px-3.5 py-2 text-[13px]'
  const variants = {
    primary: 'bg-indigo-600 text-white hover:bg-indigo-500',
    soft: 'border border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50',
    ghost: 'text-slate-600 hover:bg-slate-100',
    danger: 'border border-red-200 bg-white text-red-600 hover:bg-red-50',
  }
  return <button className={`${base} ${sizes} ${variants[variant]} ${className}`} {...rest} />
}

export function Badge({
  children,
  tone = 'slate',
}: {
  children: React.ReactNode
  tone?: 'slate' | 'green' | 'amber' | 'indigo' | 'red'
}) {
  const tones = {
    slate: 'bg-slate-100 text-slate-600 ring-slate-200',
    green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    amber: 'bg-amber-50 text-amber-700 ring-amber-200',
    indigo: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
    red: 'bg-red-50 text-red-700 ring-red-200',
  }
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[12px] ring-1 ring-inset ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function Dot({ tone }: { tone: 'green' | 'slate' | 'amber' }) {
  const tones = { green: 'bg-emerald-500', slate: 'bg-slate-300', amber: 'bg-amber-400' }
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${tones[tone]}`} />
}

/** 悬浮问号：把「这个按钮到底干嘛的」写在旁边，不占版面。 */
export function Hint({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex align-middle">
      <span className="grid h-4 w-4 cursor-help select-none place-items-center rounded-full border border-slate-300 text-[10px] leading-none text-slate-400 hover:border-slate-400 hover:text-slate-500">
        ?
      </span>
      <span className="pointer-events-none absolute bottom-full left-1/2 z-40 mb-2 hidden w-64 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-[12px] font-normal leading-relaxed text-white shadow-pop group-hover:block">
        {text}
      </span>
    </span>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[13px] font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] leading-relaxed text-slate-400">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100'

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  footer?: React.ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <div className="relative w-full max-w-lg overflow-hidden rounded-xl bg-white shadow-pop">
        <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <h3 className="text-[15px] font-medium text-slate-900">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            ✕
          </button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  )
}

export function Toast({ message, tone = 'ok' }: { message: string; tone?: 'ok' | 'err' }) {
  const [show, setShow] = useState(true)
  useEffect(() => {
    const t = setTimeout(() => setShow(false), 3200)
    return () => clearTimeout(t)
  }, [message])
  if (!show || !message) return null
  return (
    <div
      className={`fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-lg px-4 py-2.5 text-[13px] shadow-pop ${
        tone === 'ok' ? 'bg-slate-900 text-white' : 'bg-red-600 text-white'
      }`}
    >
      {message}
    </div>
  )
}

export function Empty({ text }: { text: string }) {
  return <div className="px-5 py-12 text-center text-[13px] text-slate-400">{text}</div>
}
