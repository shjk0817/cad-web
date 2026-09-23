import { useEffect, useMemo, useState } from 'react'
import {
  auditSheetPngUrl,
  auditStream,
  getAuditTask,
  runAuditStep,
} from '../api'
import type {
  AuditCategory,
  AuditStage,
  AuditTask,
  ReconcileCategoryReport,
  ReconcileFieldRow,
} from '../types'
import './audit.css'

const CATEGORY_TITLE: Record<AuditCategory, string> = {
  retaining_pile: '支护桩',
  bored_pile: '钻孔灌注桩',
  diaphragm_wall: '地下连续墙',
  cap: '承台',
}

const CATEGORY_ORDER: AuditCategory[] = [
  'retaining_pile',
  'bored_pile',
  'diaphragm_wall',
  'cap',
]

// 全屏复核报告页：5 阶段时间线 + 三方对账表格 + 调试单步重跑
export default function AuditReportPage(props: { taskId?: string } = {}) {
  // 支持 ?task= 或 #audit/<taskId>，由 App.tsx 路由层注入
  const taskId = useMemo(() => {
    if (props.taskId) return props.taskId
    const url = new URL(window.location.href)
    const q = url.searchParams.get('task')
    if (q) return q
    const h = window.location.hash
    const m = h.match(/^#\/audit\/([^/?#]+)/)
    if (m) return decodeURIComponent(m[1])
    return ''
  }, [props.taskId])
  const navigateBack = () => {
    if (window.history.length > 1) window.history.back()
    else window.location.hash = '#/'
  }
  const [task, setTask] = useState<AuditTask | null>(null)
  const [error, setError] = useState<string>('')
  const [imgBroken, setImgBroken] = useState<Set<string>>(new Set())
  const [debugRunning, setDebugRunning] = useState<AuditStage | null>(null)

  useEffect(() => {
    let cancelled = false
    setError('')
    getAuditTask(taskId)
      .then((t) => {
        if (!cancelled) setTask(t)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : '加载任务失败')
        }
      })
    return () => {
      cancelled = true
    }
  }, [taskId])

  useEffect(() => {
    if (!task) return
    if (task.status === 'done' || task.status === 'error') return
    const close = auditStream(taskId, ({ data }) => {
      setTask(data as AuditTask)
    })
    return close
  }, [taskId, task?.id, task?.status])

  const handleDebugStep = async (stage: 'rules' | 'vlm_select' | 'llm_infer' | 'reconcile') => {
    setDebugRunning(stage)
    try {
      const updated = await runAuditStep(taskId, stage)
      setTask(updated)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '单步重跑失败')
    } finally {
      setDebugRunning(null)
    }
  }

  if (error) {
    return (
      <div className="audit-report">
        <header className="audit-report-header">
          <button
            type="button"
            className="audit-back"
            onClick={navigateBack}
          >
            ← 返回
          </button>
          <h1>工程量复核报告</h1>
        </header>
        <p className="audit-error">加载失败：{error}</p>
      </div>
    )
  }

  if (!task) {
    return (
      <div className="audit-report">
        <header className="audit-report-header">
          <button
            type="button"
            className="audit-back"
            onClick={navigateBack}
          >
            ← 返回
          </button>
          <h1>工程量复核报告</h1>
        </header>
        <p className="audit-loading">加载中…</p>
      </div>
    )
  }

  const summary = task.reconcileReport?.summary
  const confidencePct = Math.round((task.reconcileReport?.confidence || 0) * 100)

  return (
    <div className="audit-report">
      <header className="audit-report-header">
        <button
          type="button"
          className="audit-back"
          onClick={navigateBack}
        >
          ← 返回
        </button>
        <div className="audit-report-titles">
          <h1>工程量复核报告</h1>
          <p className="audit-task-meta">
            任务 ID：<code>{task.id}</code>
            {' · '}
            文件 ID：<code>{task.fileId}</code>
          </p>
        </div>
        <div className="audit-report-actions">
          <span
            className={`audit-status-badge audit-status-${task.status}`}
          >
            {statusLabel(task.status)}
          </span>
        </div>
      </header>

      <AuditStepTimeline task={task} />

      <section className="audit-summary">
        <h2 className="audit-section-title">三方对账总览</h2>
        {summary ? (
          <div className="audit-summary-grid">
            <div className="audit-stat">
              <span className="audit-stat-num">{summary.totalRows}</span>
              <span className="audit-stat-label">字段总数</span>
            </div>
            <div className="audit-stat match">
              <span className="audit-stat-num">{summary.matched}</span>
              <span className="audit-stat-label">一致</span>
            </div>
            <div className="audit-stat warn">
              <span className="audit-stat-num">{summary.warned}</span>
              <span className="audit-stat-label">需复核</span>
            </div>
            <div className="audit-stat conflict">
              <span className="audit-stat-num">{summary.conflicted}</span>
              <span className="audit-stat-label">冲突</span>
            </div>
            <div className="audit-stat confidence">
              <span className="audit-stat-num">{confidencePct}%</span>
              <span className="audit-stat-label">整体置信度</span>
            </div>
          </div>
        ) : (
          <p className="audit-helper">尚未生成报告（阶段：{labelOfStage(task.stage)}）</p>
        )}
      </section>

      <section className="audit-vlm">
        <h2 className="audit-section-title">参与复核的图纸</h2>
        <ul className="audit-vlm-list">
          {(task.vlmCandidates || []).map((c) => {
            const broken = imgBroken.has(c.sheetId)
            const used = task.confirmedSheets.includes(c.sheetId)
            return (
              <li
                key={c.sheetId}
                className={`audit-vlm-card ${used ? 'used' : 'skipped'}`}
              >
                <div className="audit-vlm-thumb">
                  {broken ? (
                    <span className="audit-vlm-broken">无预览</span>
                  ) : (
                    <img
                      src={auditSheetPngUrl(task.id, c.sheetId)}
                      alt={c.sheetName || c.sheetId}
                      onError={() =>
                        setImgBroken((prev) => {
                          const n = new Set(prev)
                          n.add(c.sheetId)
                          return n
                        })
                      }
                    />
                  )}
                </div>
                <div className="audit-vlm-text">
                  <span className="audit-vlm-title">
                    {c.sheetName || c.sheetId}
                  </span>
                  <span className="audit-vlm-meta">
                    score={c.score.toFixed(2)} · {c.role}
                  </span>
                  <span className="audit-vlm-reason">{c.reason}</span>
                  <span
                    className={`audit-vlm-tag ${used ? 'tag-used' : 'tag-skipped'}`}
                  >
                    {used ? '已参与复核' : '已剔除'}
                  </span>
                </div>
              </li>
            )
          })}
          {(task.vlmCandidates || []).length === 0 && (
            <p className="audit-helper">无候选图纸</p>
          )}
        </ul>
      </section>

      {task.reconcileReport && (
        <section className="audit-reconcile">
          <h2 className="audit-section-title">三方对账明细</h2>
          {CATEGORY_ORDER.filter((c) => task.categories.includes(c)).map((c) => {
            const catReport: ReconcileCategoryReport | undefined =
              task.reconcileReport?.byCategory?.[c]
            return (
              <ReconcileTable
                key={c}
                category={c}
                report={catReport}
                _ruleRows={task.ruleDraft?.byCategory?.[c] || []}
              />
            )
          })}
        </section>
      )}

      {task.detail && task.status === 'error' && (
        <section className="audit-error-section">
          <h2 className="audit-section-title">错误详情</h2>
          <pre className="audit-error-pre">{task.detail}</pre>
        </section>
      )}

      <section className="audit-debug">
        <h2 className="audit-section-title">调试模式</h2>
        <p className="audit-helper">
          仅用于开发调试；单步重跑会从该阶段开始重新计算，演示用
        </p>
        <div className="audit-debug-buttons">
          {(['rules', 'vlm_select', 'llm_infer', 'reconcile'] as const).map(
            (st) => (
              <button
                key={st}
                type="button"
                className="audit-debug-btn"
                onClick={() => handleDebugStep(st)}
                disabled={debugRunning !== null}
              >
                {debugRunning === st ? '运行中…' : `重跑 ${labelOfStage(st)}`}
              </button>
            ),
          )}
        </div>
      </section>
    </div>
  )
}

interface ReconcileTableProps {
  category: AuditCategory
  report: ReconcileCategoryReport | undefined
  _ruleRows?: { id: string; fields: Record<string, any> }[]
}

function ReconcileTable({ category, report }: ReconcileTableProps) {
  if (!report || report.rows.length === 0) {
    return (
      <div className="audit-cat-block">
        <h3 className="audit-cat-title">
          {CATEGORY_TITLE[category]}
          <span className="audit-cat-confidence">
            置信度 {Math.round((report?.confidence || 0) * 100)}%
          </span>
        </h3>
        <p className="audit-helper">该类别无对账数据</p>
      </div>
    )
  }
  return (
    <div className="audit-cat-block">
      <h3 className="audit-cat-title">
        {CATEGORY_TITLE[category]}
        <span className="audit-cat-confidence">
          置信度 {Math.round((report.confidence || 0) * 100)}%
        </span>
      </h3>
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th>字段</th>
              <th>规则值</th>
              <th>标注值</th>
              <th>LLM 值</th>
              <th>差异</th>
              <th>严重度</th>
            </tr>
          </thead>
          <tbody>
            {report.rows.map((r: ReconcileFieldRow, idx) => (
              <tr key={`${category}-${idx}-${r.field}`}>
                <td className="audit-table-field">{r.field}</td>
                <td>{fmt(r.ruleValue)}</td>
                <td>{fmt(r.annoValue)}</td>
                <td>{fmt(r.llmValue)}</td>
                <td>{r.diff || '—'}</td>
                <td>
                  <span className={`audit-sev audit-sev-${r.severity}`}>
                    {labelOfSeverity(r.severity)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

interface AuditStepTimelineProps {
  task: AuditTask
}

const STEP_ORDER: AuditStage[] = [
  'rules',
  'vlm_select',
  'awaiting_confirm',
  'llm_infer',
  'reconcile',
  'done',
]

function AuditStepTimeline({ task }: AuditStepTimelineProps) {
  const currentIdx = STEP_ORDER.indexOf(task.stage)
  return (
    <section className="audit-timeline">
      {STEP_ORDER.map((st, i) => {
        const stStatus =
          task.status === 'error' && i === currentIdx
            ? 'error'
            : i < currentIdx
              ? 'done'
              : i === currentIdx
                ? (task.status === 'awaiting_confirm'
                    ? 'pending'
                    : task.status)
                : 'pending'
        return (
          <div key={st} className={`audit-timeline-step audit-tl-${stStatus}`}>
            <span className="audit-timeline-dot" />
            <span className="audit-timeline-label">{labelOfStage(st)}</span>
            <span className="audit-timeline-time">
              {fmtStepTime(task.steps?.[st])}
            </span>
          </div>
        )
      })}
    </section>
  )
}

function fmtStepTime(step: { startedAt?: number; finishedAt?: number } | undefined): string {
  if (!step) return '—'
  if (step.finishedAt && step.startedAt) {
    const ms = step.finishedAt - step.startedAt
    return `${(ms / 1000).toFixed(1)}s`
  }
  if (step.startedAt) {
    return '运行中…'
  }
  return '—'
}

function fmt(v: any): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    if (Math.abs(v) >= 1e6) return v.toExponential(2)
    return Number.isInteger(v) ? v.toString() : v.toFixed(3)
  }
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

function statusLabel(s: AuditTask['status']): string {
  switch (s) {
    case 'running':
      return '运行中'
    case 'awaiting_confirm':
      return '等待确认'
    case 'done':
      return '完成'
    case 'error':
      return '失败'
  }
}

function labelOfSeverity(s: 'match' | 'warn' | 'conflict'): string {
  if (s === 'match') return '一致'
  if (s === 'warn') return '需复核'
  return '冲突'
}

function labelOfStage(st: AuditStage | string): string {
  switch (st) {
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
      return String(st)
  }
}