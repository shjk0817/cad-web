import type { ToastMessage } from '../types'

interface ToastHostProps {
  toasts: ToastMessage[]
}

// 右上角通知集合
export default function ToastHost({ toasts }: ToastHostProps) {
  if (toasts.length === 0) return null
  return (
    <div className="toast-host">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`}>
          <span className="ico">
            {t.tone === 'success' ? '✓' : t.tone === 'warn' ? '!' : t.tone === 'error' ? '✕' : 'i'}
          </span>
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  )
}