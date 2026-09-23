import { useEffect, useRef, useState } from 'react'
import type { AiTask, AiTaskKind, AiTaskRequest, Sheet } from '../types'
import { submitAiTask } from '../api'

interface AIDrawerProps {
  open: boolean
  fileId: string | null
  sheet: Sheet | null
  onClose: () => void
  onToast: (tone: 'success' | 'warn' | 'error' | 'info', text: string) => void
}

// 预设 AI 任务模板
const TEMPLATE_TASKS: {
  kind: AiTaskKind
  title: string
  description: string
}[] = [
  { kind: 'extract-tables', title: '提取标题栏与表格', description: '从当前图纸识别标题栏、明细表' },
  { kind: 'classify-entities', title: '标注图元类别', description: '对线条、文字、标注分类' },
  { kind: 'summary', title: '图纸摘要', description: '生成当前图纸的简要说明' },
  { kind: 'qa', title: '针对图纸提问', description: '基于图纸内容回答工程问题' },
]

function buildId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

// 右侧 AI 识别抽屉：占位任务面板（待接入真实接口）
export default function AIDrawer({ open, fileId, sheet, onClose, onToast }: AIDrawerProps) {
  const [tasks, setTasks] = useState<AiTask[]>([])
  const [prompt, setPrompt] = useState('')
  const seqRef = useRef(0)

  // 切换图纸时清空列表
  useEffect(() => {
    if (sheet) {
      setTasks([])
      setPrompt('')
    }
  }, [sheet?.id])

  if (!open) return null

  const ready = Boolean(fileId && sheet)

  const handlePick = async (kind: AiTaskKind) => {
    if (!ready) {
      onToast('warn', '请先选择文件与图纸')
      return
    }
    const template = TEMPLATE_TASKS.find((t) => t.kind === kind)!
    const task: AiTask = {
      id: buildId(),
      kind,
      title: template.title,
      description: template.description,
      fileId: fileId!,
      sheetId: sheet!.id,
      status: 'running',
      createdAt: Date.now(),
    }
    setTasks((prev) => [task, ...prev])

    const req: AiTaskRequest = {
      kind,
      sheetId: sheet!.id,
      fileId: fileId!,
      prompt: kind === 'qa' ? prompt || undefined : undefined,
    }
    const seq = ++seqRef.current
    try {
      const result = await submitAiTask(req)
      // 防止快速取消/重复导致的过期更新
      if (seq !== seqRef.current) return
      setTasks((prev) =>
        prev.map((t) =>
          t.id === task.id
            ? { ...t, status: result.status, result: result.result }
            : t,
        ),
      )
    } catch (e: unknown) {
      if (seq !== seqRef.current) return
      const msg = e instanceof Error ? e.message : '识别失败'
      setTasks((prev) =>
        prev.map((t) =>
          t.id === task.id ? { ...t, status: 'error', result: msg } : t,
        ),
      )
      onToast('warn', msg)
    }
  }

  return (
    <aside className="ai-drawer" aria-label="AI 识别">
      <header className="ai-drawer-header">
        <h3 className="ai-drawer-title">
          <span className="pulse-dot" />
          AI 识别
        </h3>
        <button type="button" className="ai-drawer-close" onClick={onClose} aria-label="关闭">
          ✕
        </button>
      </header>

      <div className="ai-drawer-body">
        {!ready ? (
          <div className="ai-empty">
            <span className="ico">✨</span>
            <p>请先选择文件与图纸，再发起 AI 识别任务</p>
          </div>
        ) : (
          <>
            <div className="ai-section">
              <h4 className="ai-section-title">当前对象</h4>
              <p className="ai-helper-text">
                文件 ID：<code>{fileId}</code>
                <br />
                图纸：{sheet?.name}
              </p>
            </div>

            <div className="ai-section">
              <h4 className="ai-section-title">识别任务</h4>
              <ul className="ai-task-list">
                {TEMPLATE_TASKS.map((t) => (
                  <li key={t.kind}>
                    <button
                      type="button"
                      className="ai-task"
                      onClick={() => handlePick(t.kind)}
                    >
                      <span className="ai-task-icon">AI</span>
                      <span className="ai-task-text">
                        <span className="ai-task-title">{t.title}</span>
                        <span className="ai-task-desc">{t.description}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            {tasks.length > 0 && (
              <div className="ai-section">
                <h4 className="ai-section-title">历史结果</h4>
                <ul className="ai-task-list">
                  {tasks.map((t) => (
                    <li key={t.id}>
                      <div className="ai-task" style={{ cursor: 'default' }}>
                        <span className="ai-task-icon">
                          {t.status === 'running' ? '⋯' : t.status === 'error' ? '!' : '✓'}
                        </span>
                        <span className="ai-task-text">
                          <span className="ai-task-title">{t.title}</span>
                          <span className="ai-task-desc">
                            {t.status === 'running' && '识别中…'}
                            {t.status === 'error' && (t.result || '失败')}
                            {t.status === 'done' && (t.result || '完成')}
                          </span>
                        </span>
                      </div>
                      {t.status === 'done' && t.result && (
                        <pre className="ai-result">{t.result}</pre>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      <div className="ai-drawer-footer">
        <input
          className="ai-input"
          type="text"
          placeholder="对图纸提问…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <button
          type="button"
          className="ai-send"
          disabled={!ready || !prompt.trim()}
          onClick={() => handlePick('qa')}
        >
          发送
        </button>
      </div>
    </aside>
  )
}