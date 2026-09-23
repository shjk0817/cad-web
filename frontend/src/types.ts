// 后端接口相关类型定义

// 单张图纸（布局）信息
export interface Sheet {
  id: string
  name: string
  width: number
  height: number
}

// POST /api/dwg/upload 成功响应
export interface UploadResponse {
  fileId: string
  // 是否命中内容缓存（相同文件免解析）
  cached?: boolean
  sheets: Sheet[]
}

// 任务阶段标识
export type TaskStage =
  | 'queued'
  | 'hashing'
  | 'converting'
  | 'loading'
  | 'detecting'
  | 'splitting'
  | 'indexing'
  | 'done'
  | 'error'

// SSE 进度事件
export interface TaskProgress {
  taskId: string
  status: 'running' | 'done' | 'error'
  stage: TaskStage
  // 0.0 - 1.0
  progress: number
  message: string
}

// 单个上传文件的状态
export type FileItemStatus =
  | 'uploading'
  | 'parsing'
  | 'ready'
  | 'error'

export interface FileItem {
  // 前端内部标识
  uid: string
  name: string
  size: number
  status: FileItemStatus
  // 解析进度（0-100，仅在 parsing 时有值）
  progress?: number
  // 阶段文案
  stageText?: string
  // 已解析完成后的后端文件 ID
  fileId?: string
  sheets?: Sheet[]
  error?: string
}

// GET /api/health 响应
export interface HealthResponse {
  ok: boolean
  dwg2dxf?: string
}

// 图层信息（来自 dxf-viewer 的 GetLayers）
export interface LayerInfo {
  name: string
  displayName: string
  color: number
}

// ========= 通知系统 =========
export type ToastTone = 'success' | 'warn' | 'error' | 'info'

export interface ToastMessage {
  id: string
  tone: ToastTone
  text: string
}

// ========= AI 识别系统 =========
// 后续接入真实 AI 时，前端只需消费 AiTaskResult 与 ApiTaskRequest 这两类数据。
export type AiTaskKind =
  | 'extract-tables'      // 提取图纸表格/标题栏
  | 'classify-entities'   // 标注图元类别
  | 'summary'             // 总结图纸内容
  | 'qa'                   // 针对图纸提问

export type AiTaskStatus =
  | 'idle'
  | 'pending'
  | 'running'
  | 'done'
  | 'error'

export interface AiTask {
  id: string
  kind: AiTaskKind
  title: string
  description: string
  // 触发该任务时使用的图纸 / 文件标识，AI 后续可据此取源数据
  sheetId?: string
  fileId?: string
  status: AiTaskStatus
  // 任务输出：当前后端尚未实装，先用字符串承接预览
  result?: string
  // 任务创建时间，便于排序
  createdAt?: number
}

// AI 任务草稿：用户在抽屉中选好场景后真正发起请求
export interface AiTaskRequest {
  kind: AiTaskKind
  sheetId: string
  fileId: string
  prompt?: string
}

// ========= 视图状态 =========
export interface ViewHud {
  zoom: number
  cursor: { x: number; y: number } | null
}

// ========= 工程量审计系统 =========
export type AuditCategory =
  | 'retaining_pile'
  | 'bored_pile'
  | 'diaphragm_wall'
  | 'cap'

export type AuditStage =
  | 'queued'
  | 'rules'
  | 'vlm_select'
  | 'awaiting_confirm'
  | 'llm_infer'
  | 'reconcile'
  | 'done'
  | 'error'

export interface RuleDraftRow {
  id: string
  fields: Record<
    string,
    { value: number | string | null; source: 'geometry' | 'table' | 'annotation' }
  >
}

export interface RuleDraft {
  byCategory: Record<AuditCategory, RuleDraftRow[]>
}

export interface VlmCandidate {
  sheetId: string
  sheetName?: string
  role: string
  score: number
  reason: string
  relevantLayers?: string[]
}

export interface LlmField {
  value: number | string | null
  trace: string
}

export interface LlmResultRow {
  id: string
  fields: Record<string, LlmField>
}

export interface LlmResult {
  byCategory: Record<AuditCategory, LlmResultRow[]>
}

export interface ReconcileFieldRow {
  field: string
  ruleValue?: any
  llmValue?: any
  annoValue?: any
  diff?: string
  severity: 'match' | 'warn' | 'conflict'
}

export interface ReconcileCategoryReport {
  rows: ReconcileFieldRow[]
  confidence: number
}

export interface ReconcileReport {
  byCategory: Record<AuditCategory, ReconcileCategoryReport>
  summary: {
    totalRows: number
    matched: number
    warned: number
    conflicted: number
  }
  confidence: number
}

export interface StepState<T = any> {
  status: 'idle' | 'running' | 'done' | 'error'
  result?: T
  error?: string
  startedAt?: number
  finishedAt?: number
}

export interface AuditTask {
  id: string
  fileId: string
  status: 'running' | 'awaiting_confirm' | 'done' | 'error'
  stage: AuditStage
  progress: number
  message: string
  createdAt: number
  updatedAt: number
  categories: AuditCategory[]
  visionProviderId: string
  chatProviderId: string
  sheetIds: string[]
  sheetNames: string[]
  vlmCandidates: VlmCandidate[]
  confirmedSheets: string[]
  steps: Record<AuditStage, AuditStepState>
  ruleDraft?: RuleDraft
  llmResult?: LlmResult
  reconcileReport?: ReconcileReport
  detail?: string
}

export interface AuditStepState {
  stage: AuditStage
  status: 'pending' | 'running' | 'done' | 'error'
  startedAt?: number
  finishedAt?: number
  message: string
  payload?: any
}

// ========= LLM Provider 配置 =========
export type LLMProtocol = 'openai' | 'anthropic' | 'gemini'

export interface LLMProviderPreset {
  id: string
  name: string
  protocol: LLMProtocol
  defaultBaseUrl: string
  defaultChatModel: string
  defaultVisionModel: string
  supportsVision: boolean
  docUrl?: string
}

export interface LLMProviderConfig {
  id: string
  presetId: string
  name: string
  protocol: LLMProtocol
  baseUrl: string
  apiKey: string
  chatModel: string
  visionModel: string
  enabled: boolean
}

export interface LLMProviderListResponse {
  presets: LLMProviderPreset[]
  configs: LLMProviderConfig[]
}

export interface LLMProviderTestResult {
  success: boolean
  error?: string
}