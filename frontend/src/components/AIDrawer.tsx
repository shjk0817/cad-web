import { useEffect, useRef, useState } from 'react'
import type {
  AuditCategory,
  AuditTask,
  Sheet,
  VlmCandidate,
} from '../types'
import {
  auditSheetPngUrl,
  auditStream,
  confirmAuditSheets,
  createAuditTask,
} from '../api'
import ModelSettings from './ModelSettings'

interface AIDrawerProps {
  open: boolean
  fileId: string | null
  sheet: Sheet | null
  onClose: () => void
  onToast: (tone: 'success' | 'warn' | 'error' | 'info', text: string) => void
  // 任务完成后跳转报告页
  onAuditDone?: (task: AuditTask) => void
}

interface CategoryMeta {
  key: AuditCategory
  title: string
  desc: string
}

const CATEGORIES: CategoryMeta[] = [
  { key: 'retaining_pile', title: '支护桩', desc: '钢板桩 / 钻孔灌注排桩等' },
  { key: 'bored_pile', title: '钻孔灌注桩', desc: '圆形灌注桩 + 标注' },
  { key: 'diaphragm_wall', title: '地下连续墙', desc: '地连墙幅段与厚度' },
  { key: 'cap', title: '承台', desc: '矩形承台 + 厚度 + 工程量' },
]

// 右侧抽屉：4 类工程量复核入口 + SheetCandidateList
export default function AIDrawer({
  open,
  fileId,
  sheet,
  onClose,
  onToast,
  onAuditDone,
}: AIDrawerProps) {
  const [task, setTask] = useState<AuditTask | null>(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [imgBroken, setImgBroken] = useState<Set<string>>(new Set())
  const closeStreamRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    if (sheet) {
      // 切换图纸 / 文件时清空任务
      setTask(null)
      setSelected(new Set())
      setBusy(false)
    }
  }, [fileId, sheet?.id])

  // 组件卸载或任务 done/error 时关闭 SSE
  useEffect(() => {
    return () => {
      closeStreamRef.current?.()
      closeStreamRef.current = null
    }
  }, [])

  if (!open) return null

  const ready = Boolean(fileId && sheet)

  const closeStream = () => {
    closeStreamRef.current?.()
    closeStreamRef.current = null
  }

  const startStream = (tid: string) => {
    closeStream()
    closeStreamRef.current = auditStream(tid, ({ data }) => {
      setTask(data as AuditTask)
      const status = (data as AuditTask)?.status
      if (status === 'awaiting_confirm') {
        // 默认全选 VLM 候选（无 VLM provider 时 score=0.5 placeholder）
        const cands = ((data as AuditTask).vlmCandidates || []) as VlmCandidate[]
        setSelected(new Set(cands.map((c) => c.sheetId)))
      }
      if (status === 'done') {
        closeStream()
        setBusy(false)
        onToast('success', '工程量复核完成')
        onAuditDone?.(data as AuditTask)
      } else if (status === 'error') {
        closeStream()
        setBusy(false)
        const detail = (data as AuditTask).detail || '复核失败'
        onToast('error', detail)
      }
    })
  }

  const handleStart = async (cats: AuditCategory[]) => {
    if (!ready || busy) return
    setBusy(true)
    try {
      const created = await createAuditTask({
        fileId: fileId!,
        categories: cats,
      })
      setTask(created)
      // 等待到 awaiting_confirm 时再让用户挑图
      startStream(created.id)
    } catch (e: unknown) {
      setBusy(false)
      const msg = e instanceof Error ? e.message : '启动复核失败'
      onToast('error', msg)
    }
  }

  const toggleSelect = (sid: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(sid)) next.delete(sid)
      else next.add(sid)
      return next
    })
  }

  const handleConfirm = async () => {
    if (!task || busy) return
    if (selected.size === 0) {
      onToast('warn', '请至少勾选 1 张图纸')
      return
    }
    setBusy(true)
    try {
      const updated = await confirmAuditSheets(task.id, Array.from(selected))
      setTask(updated)
      // confirm 后 SSE 继续推 llm_infer → reconcile → done
      startStream(task.id)
    } catch (e: unknown) {
      setBusy(false)
      const msg = e instanceof Error ? e.message : '确认图纸失败'
      onToast('error', msg)
    }
  }

  const showCandidateList =
    task && task.stage === 'awaiting_confirm' && !busy
  const stageLabel = task ? labelOfStage(task.stage) : ''
  const stepList: string[] = task ? ALL_STAGE_ORDER : []
  const currentStepIndex = task ? stepList.indexOf(task.stage) : -1

  return (
    <aside className="ai-drawer" aria-label="工程量复核">
      <header className="ai-drawer-header">
        <h3 className="ai-drawer-title">
          <span className="pulse-dot" />
          工程量复核
        </h3>
        <button
          type="button"
          className="ai-drawer-close"
          onClick={onClose}
          aria-label="关闭"
        >
          ✕
        </button>
      </header>

      <div className="ai-drawer-body">
        {!ready ? (
          <div className="ai-empty">
            <span className="ico">✨</span>
            <p>请先选择文件与图纸，再发起工程量复核</p>
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

            {!task && (
              <div className="ai-section">
                <h4 className="ai-section-title">复核类别</h4>
                <p className="ai-helper-text">
                  选择需要复核的工程量类别；系统会跑 5 阶段流水线
                  （规则 → VLM 选图 → 你确认 → 文本 LLM 推理 → 三方对账）
                </p>
                <ul className="ai-task-list">
                  {CATEGORIES.map((c) => (
                    <li key={c.key}>
                      <button
                        type="button"
                        className="ai-task"
                        onClick={() => handleStart([c.key])}
                        disabled={busy}
                      >
                        <span className="ai-task-icon">Q</span>
                        <span className="ai-task-text">
                          <span className="ai-task-title">{c.title}</span>
                          <span className="ai-task-desc">{c.desc}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="ai-task"
                  onClick={() => handleStart(CATEGORIES.map((c) => c.key))}
                  disabled={busy}
                  style={{ marginTop: 8, background: 'rgba(99,102,241,0.12)' }}
                >
                  <span className="ai-task-icon">★</span>
                  <span className="ai-task-text">
                    <span className="ai-task-title">4 类一并复核</span>
                    <span className="ai-task-desc">一次跑完整 4 类工程量</span>
                  </span>
                </button>
              </div>
            )}

            {task && (
              <div className="ai-section">
                <h4 className="ai-section-title">任务进度</h4>
                <p className="ai-helper-text">
                  {stageLabel} · {Math.round((task.progress || 0) * 100)}%
                </p>
                <div className="progress">
                  <div
                    className="progress-bar"
                    style={{ width: `${Math.round((task.progress || 0) * 100)}%` }}
                  />
                </div>
                <p className="ai-helper-text">{task.message}</p>
                <ul className="audit-step-list">
                  {ALL_STAGE_ORDER.map((st, i) => {
                    const stStatus =
                      i < currentStepIndex
                        ? 'done'
                        : i === currentStepIndex
                          ? (task.status === 'error' ? 'error' : task.status)
                          : 'pending'
                    return (
                      <li
                        key={st}
                        className={`audit-step audit-step-${stStatus}`}
                      >
                        <span className="audit-step-dot" />
                        <span className="audit-step-label">{labelOfStage(st)}</span>
                        <span className="audit-step-status">
                          {stStatus === 'done' ? '✓' : stStatus === 'error' ? '!' : stStatus === 'running' ? '⋯' : '·'}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}

            {showCandidateList && task && (
              <div className="ai-section">
                <h4 className="ai-section-title">VLM 选图确认</h4>
                <p className="ai-helper-text">
                  勾选真正参与复核的图纸（默认全选）
                </p>
                <ul className="audit-candidate-list">
                  {task.vlmCandidates.map((c) => {
                    const checked = selected.has(c.sheetId)
                    const broken = imgBroken.has(c.sheetId)
                    return (
                      <li key={c.sheetId}>
                        <label
                          className={`audit-candidate ${checked ? 'checked' : ''}`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleSelect(c.sheetId)}
                          />
                          <span className="audit-candidate-thumb">
                            {broken ? (
                              <span className="audit-candidate-broken">无预览</span>
                            ) : (
                              <img
                                src={auditSheetPngUrl(task.id, c.sheetId)}
                                alt={c.sheetName || c.sheetId}
                                loading="lazy"
                                onError={() =>
                                  setImgBroken((prev) => {
                                    const n = new Set(prev)
                                    n.add(c.sheetId)
                                    return n
                                  })
                                }
                              />
                            )}
                          </span>
                          <span className="audit-candidate-text">
                            <span className="audit-candidate-title">
                              {c.sheetName || c.sheetId}
                            </span>
                            <span className="audit-candidate-meta">
                              score={c.score.toFixed(2)} · {c.role}
                            </span>
                            <span className="audit-candidate-reason">
                              {c.reason}
                            </span>
                          </span>
                        </label>
                      </li>
                    )
                  })}
                </ul>
                <button
                  type="button"
                  className="ai-task primary"
                  onClick={handleConfirm}
                  disabled={busy || selected.size === 0}
                  style={{ marginTop: 8 }}
                >
                  <span className="ai-task-icon">▶</span>
                  <span className="ai-task-text">
                    <span className="ai-task-title">
                      确认 {selected.size} 张图纸，继续推理
                    </span>
                    <span className="ai-task-desc">
                      LLM 推理 + 三方对账将自动开始
                    </span>
                  </span>
                </button>
              </div>
            )}
          </>
        )}
      </div>

      <ModelSettings onToast={onToast} />
    </aside>
  )
}

const ALL_STAGE_ORDER = [
  'rules',
  'vlm_select',
  'awaiting_confirm',
  'llm_infer',
  'reconcile',
  'done',
]

function labelOfStage(stage: string): string {
  switch (stage) {
    case 'rules':
      return '规则管线'
    case 'vlm_select':
      return 'VLM 选图'
    case 'awaiting_confirm':
      return '等待确认'
    case 'llm_infer':
      return 'LLM 推理'
    case 'reconcile':
      return '三方对账'
    case 'done':
      return '完成'
    case 'error':
      return '失败'
    default:
      return stage
  }
}