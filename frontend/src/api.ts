import type {
  Sheet,
  TaskProgress,
  UploadResponse,
  AiTaskRequest,
  AiTask,
  AuditCategory,
  AuditTask,
  LLMProviderConfig,
  LLMProviderListResponse,
  LLMProviderTestResult,
} from './types'

// 上传阶段占总进度的 0%–15%（POST 字节回传进度），
// SSE 阶段从 15% 开始覆盖到 100%。两段加起来用户能看到条子在动。
const UPLOAD_PROGRESS_MAX = 0.15

// 启动上传 + 监听进度。前端用 XHR 才能拿到「POST 字节流」的进度。
// 拿到 taskId 后再切到 SSE 监听后续阶段。
export async function uploadDwg(
  file: File,
  onProgress?: (p: TaskProgress) => void,
): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)

  const taskId = await new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/dwg/upload')
    xhr.responseType = 'json'

    let lastUploaded = 0
    xhr.upload.onprogress = (ev) => {
      // ev.lengthComputable 在大文件下通常为 true；按已上传/总大小估算
      if (ev.lengthComputable && ev.total > 0) {
        const ratio = ev.loaded / ev.total
        lastUploaded = ratio
        if (onProgress) {
          onProgress({
            taskId: '',
            status: 'running',
            stage: 'queued',
            progress: ratio * UPLOAD_PROGRESS_MAX,
            message: `上传中 ${Math.round(ratio * 100)}%`,
          })
        }
      } else if (ev.loaded > lastUploaded) {
        // lengthComputable 不可用时用 ev.loaded 给个递增的「伪进度」
        lastUploaded = ev.loaded
        if (onProgress) {
          onProgress({
            taskId: '',
            status: 'running',
            stage: 'queued',
            progress: UPLOAD_PROGRESS_MAX * 0.5,
            message: '上传中…',
          })
        }
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const data = xhr.response as { taskId?: string } | null
        if (!data || !data.taskId) {
          reject(new Error('后端未返回任务 ID'))
          return
        }
        resolve(data.taskId)
      } else {
        // 尝试从响应体里取 detail
        let detail = `上传失败（HTTP ${xhr.status}）`
        try {
          const body = xhr.response as { detail?: string } | null
          if (body && typeof body.detail === 'string') {
            detail = body.detail
          }
        } catch {
          // 忽略：响应体非 JSON
        }
        reject(new Error(detail))
      }
    }

    xhr.onerror = () => {
      reject(new Error('无法连接后端服务，请确认服务已启动'))
    }

    xhr.onabort = () => {
      reject(new Error('上传已取消'))
    }

    xhr.send(form)
  })

  // 上传完成：从 15% 开始订阅 SSE 进度
  return new Promise<UploadResponse>((resolve, reject) => {
    const es = new EventSource(`/api/dwg/tasks/${encodeURIComponent(taskId)}`)
    let finished = false

    const cleanup = () => {
      try {
        es.close()
      } catch {
        /* noop */
      }
    }

    es.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data) as TaskProgress & {
          fileId?: string
          cached?: boolean
          sheets?: Sheet[]
          detail?: string
        }
        // 把后端进度整体上移到 [15%, 100%] 区间，前端才能 0→100 平滑
        const normalized = {
          ...payload,
          progress:
            UPLOAD_PROGRESS_MAX +
            (payload.progress ?? 0) * (1 - UPLOAD_PROGRESS_MAX),
        }
        if (onProgress) onProgress(normalized)

        if (payload.status === 'done' && payload.fileId && payload.sheets) {
          finished = true
          cleanup()
          resolve({
            fileId: payload.fileId,
            cached: payload.cached ?? false,
            sheets: payload.sheets,
          })
        } else if (payload.status === 'error') {
          finished = true
          cleanup()
          reject(new Error(payload.detail || '处理失败'))
        }
      } catch {
        // 解析失败不能让前端断流 —— 仅当状态已 done/error 才关闭
      }
    }

    es.onerror = () => {
      if (finished) return
      cleanup()
      reject(new Error('与后端进度连接中断'))
    }
  })
}

// 生成某张图纸 DXF 内容的地址（交给 dxf-viewer 直接加载）
export function sheetUrl(fileId: string, sheetId: string): string {
  return `/api/dwg/${encodeURIComponent(fileId)}/sheets/${encodeURIComponent(sheetId)}`
}

// 复核 VLM 用截图（用于 SheetCandidateList 缩略图）
export function auditSheetPngUrl(taskId: string, sheetId: string): string {
  return `/api/audit/tasks/${encodeURIComponent(taskId)}/sheets/${encodeURIComponent(sheetId)}/png`
}

// ========= AI 识别接口 =========
// 当前后端尚未提供 AI 识别，这里先封装 fetch；不通时返回占位结果。
// 后续接入真实服务时只需替换 fetch 逻辑与响应类型。
export async function submitAiTask(req: AiTaskRequest): Promise<AiTask> {
  // 兜底：接口尚未实装时直接抛出，前端会显示「未实装」占位。
  try {
    const res = await fetch('/api/ai/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!res.ok) {
      throw new Error(`AI 接口未实装（HTTP ${res.status}）`)
    }
    return (await res.json()) as AiTask
  } catch {
    throw new Error('AI 识别功能尚未上线')
  }
}

// ========= 工程量审计接口 =========

async function readError(res: Response): Promise<Error> {
  let detail = `请求失败（HTTP ${res.status}）`
  try {
    const text = await res.text()
    if (text) {
      try {
        const body = JSON.parse(text) as { detail?: string }
        if (body && typeof body.detail === 'string') {
          detail = body.detail
        } else {
          detail = text
        }
      } catch {
        detail = text
      }
    }
  } catch {
    /* noop */
  }
  const err = new Error(detail)
  ;(err as Error & { status?: number }).status = res.status
  return err
}

export async function createAuditTask(req: {
  fileId: string
  categories: AuditCategory[]
  visionProviderId?: string
  chatProviderId?: string
}): Promise<AuditTask> {
  const res = await fetch('/api/audit/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw await readError(res)
  return (await res.json()) as AuditTask
}

export async function getAuditTask(taskId: string): Promise<AuditTask> {
  const res = await fetch(
    `/api/audit/tasks/${encodeURIComponent(taskId)}`,
  )
  if (!res.ok) throw await readError(res)
  const body = await res.json()
  // 兼容后端可能包装为 { task: ... }
  if (body && typeof body === 'object' && 'task' in body) {
    return body.task as AuditTask
  }
  return body as AuditTask
}

export async function listAuditTasks(): Promise<AuditTask[]> {
  const res = await fetch('/api/audit/tasks')
  if (!res.ok) throw await readError(res)
  const body = await res.json()
  return (body?.tasks || []) as AuditTask[]
}

export async function confirmAuditSheets(
  taskId: string,
  sheetIds: string[],
): Promise<AuditTask> {
  const res = await fetch(
    `/api/audit/tasks/${encodeURIComponent(taskId)}/confirm-sheets`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheetIds }),
    },
  )
  if (!res.ok) throw await readError(res)
  return (await res.json()) as AuditTask
}

export async function runAuditStep(
  taskId: string,
  step: 'rules' | 'vlm_select' | 'llm_infer' | 'reconcile',
): Promise<AuditTask> {
  const res = await fetch(
    `/api/audit/tasks/${encodeURIComponent(taskId)}/step`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage: step }),
    },
  )
  if (!res.ok) throw await readError(res)
  return (await res.json()) as AuditTask
}

// SSE 流：订阅 audit 任务的 stage / progress / error / done 事件
export function auditStream(
  taskId: string,
  onEvent: (e: { type: string; data: any }) => void,
): () => void {
  const es = new EventSource(
    `/api/audit/tasks/${encodeURIComponent(taskId)}/stream`,
  )
  // 后端只推单条 "data: <json>\n\n" 事件；前端自己根据 payload.stage/status 区分
  es.onmessage = (ev) => {
    let parsed: any = ev.data
    if (typeof parsed === 'string') {
      try {
        parsed = JSON.parse(parsed)
      } catch {
        /* 保留字符串原值 */
      }
    }
    const stage = parsed?.stage || 'unknown'
    const status = parsed?.status || 'unknown'
    onEvent({ type: `${stage}.${status}`, data: parsed })
    if (status === 'done' || status === 'error') {
      try {
        es.close()
      } catch {
        /* noop */
      }
    }
  }
  es.onerror = () => {
    try {
      es.close()
    } catch {
      /* noop */
    }
  }
  return () => {
    try {
      es.close()
    } catch {
      /* noop */
    }
  }
}

// ========= LLM Provider 接口 =========

function stripEmpty<T extends Record<string, unknown>>(obj: T): Partial<T> {
  const out: Partial<T> = {}
  for (const k of Object.keys(obj) as (keyof T)[]) {
    const v = obj[k]
    if (v === undefined || v === null) continue
    if (typeof v === 'string' && v.trim() === '') continue
    out[k] = v
  }
  return out
}

export async function listLlmProviders(): Promise<LLMProviderListResponse> {
  const res = await fetch('/api/llm/providers')
  if (!res.ok) throw await readError(res)
  return (await res.json()) as LLMProviderListResponse
}

export async function createLlmProvider(
  cfg: Omit<LLMProviderConfig, 'id'>,
): Promise<LLMProviderConfig> {
  const body = stripEmpty(cfg as unknown as Record<string, unknown>)
  const res = await fetch('/api/llm/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await readError(res)
  return (await res.json()) as LLMProviderConfig
}

export async function updateLlmProvider(
  id: string,
  patch: Partial<LLMProviderConfig>,
): Promise<LLMProviderConfig> {
  const body = stripEmpty(patch as unknown as Record<string, unknown>)
  const res = await fetch(
    `/api/llm/providers/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  if (!res.ok) throw await readError(res)
  return (await res.json()) as LLMProviderConfig
}

export async function deleteLlmProvider(
  id: string,
): Promise<{ ok: true }> {
  const res = await fetch(
    `/api/llm/providers/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw await readError(res)
  return (await res.json()) as { ok: true }
}

export async function testLlmProvider(
  id: string,
): Promise<LLMProviderTestResult> {
  const res = await fetch(
    `/api/llm/providers/${encodeURIComponent(id)}/test`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' } },
  )
  if (!res.ok) throw await readError(res)
  return (await res.json()) as LLMProviderTestResult
}