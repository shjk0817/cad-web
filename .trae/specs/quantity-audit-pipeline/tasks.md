# Tasks

> 原则：每条任务都是 1 个用户可见、可独立验证的工作单元；前序任务为后续任务铺垫能力。

## Task 1: 模型抽象层（provider + registry + 持久化 + REST）
- 1.1 新增 `backend/app/llm/provider.py`：`Provider` 抽象基类（`chat`/`vision`/`stream_chat`）
- 1.2 新增 `backend/app/llm/openai_compat.py`：OpenAI 兼容协议实现（覆盖 OpenAI / DeepSeek / 通义 / 智谱 / Moonshot / Ollama / vLLM / 自定义）
- 1.3 新增 `backend/app/llm/anthropic.py` / `gemini.py`：Anthropic 原生 / Gemini 原生协议
- 1.4 新增 `backend/app/llm/registry.py`：预置 Provider 元数据常量（名称/默认 baseUrl/支持 vision/默认 modelId）+ 运行时注册
- 1.5 新增 `backend/app/llm/store.py`：Provider 配置落盘到 `backend/tmp/llm_providers.json`
- 1.6 新增 `backend/app/llm/routes.py`：`GET /api/llm/providers`（预置+已配置）、`GET/POST/PUT/DELETE /api/llm/providers/configs`、`POST /api/llm/providers/configs/:id/test`
- 1.7 在 `backend/app/main.py` 注册新 router
- **验证**：用 curl 增/改/删/测一个假 Provider，确认 200/200/200/200；测试连接返回 success=false 时不修改配置

依赖：无

## Task 2: 阶段① 规则管线（audit/rules.py + 数据契约）
- 2.1 新增 `backend/app/audit/models.py`：`AuditCategory` / `AuditTask` / `RuleDraft` / `VlmCandidate` / `LlmResult` / `ReconcileReport` / `StepState` 等 dataclass（Pydantic）
- 2.2 新增 `backend/app/audit/rules.py`：复用 `frames.py` 的图框/splitter 的实体访问，扫 CIRCLE/INSERT/LWPOLYLINE + 文字结构化，输出 `RuleDraft`
- 2.3 新增 `backend/app/audit/rules.py` 内 helper：单位/桩径归一化（`φ800/Φ800/800mm`→800）、数量表行列抽取、标注正则
- **验证**：用仓库里 `earthwork/*.dwg` 跑 `python -m app.audit.rules`，对每张图输出 `RuleDraft` JSON，确认字段完整；CIRCLE 命中围护桩、LWPOLYLINE 命中地连墙、INSERT 命中承台

依赖：Task 1（共用 backend 包结构，可并行；不强依赖 provider）

## Task 3: 截图与 VLM 筛选（audit/screenshot.py + audit/vlm.py）
- 3.1 新增 `backend/app/audit/screenshot.py`：ezdxf + matplotlib（`Agg`）出图 DXF→PNG，文件落 `workspace/tmp/dxf_screenshots/<fileId>/<sheetId>.png`
- 3.2 新增 `backend/app/audit/vlm.py`：调用 `Provider.vision()`，拼 prompt（任务类别 + few-shot + 返回 schema），输出 `VlmCandidate[]`
- 3.3 防多选约束：score≥0.6、Top-K=10、必须带 reason
- **验证**：用真实 DWG 出图 + 调一个 mock provider（不接真实 API），返回假 `VlmCandidate[]`；前端拿到候选清单后可勾选确认

依赖：Task 1（Provider）、Task 2（数据契约）

## Task 4: 阶段③ 文本 LLM 推理（audit/llm_infer.py）
- 4.1 新增 `backend/app/audit/llm_infer.py`：调用 `Provider.chat()`，输入为 ② 确认图纸上 ① 输出的纯文字 + 表格结构（按 sheetId 切分）
- 4.2 prompt 要求 LLM 输出 JSON：`{byCategory: {<cat>: [{id, fields: {<k>: {value, trace}}}]}}`
- 4.3 输出解析与 schema 校验，失败回退原样
- **验证**：用 mock provider 跑通 schema 解析，失败时保留原始输出并记 trace

依赖：Task 1、Task 2、Task 3

## Task 5: 阶段④ 三方对账（audit/reconcile.py）
- 5.1 新增 `backend/app/audit/reconcile.py`：按 `(category, id)` join 三方数据，逐字段比对
- 5.2 severity 规则：三方一致 match（绿）/ 两方一致 warn（黄）/ 三方不一致 conflict（红）
- 5.3 summary 计算：`matched/totalRows`、counts
- **验证**：构造三方测试数据，验证 severity 着色与 confidence 计算

依赖：Task 2、Task 4

## Task 6: 流水线编排与 SSE（audit/pipeline.py + audit/routes.py）
- 6.1 新增 `backend/app/audit/pipeline.py`：状态机 `queued→rules→vlm_select→awaiting_confirm→llm_infer→reconcile→done/error`，复用 `tasks.py` 的内存 TaskManager + SSE 模式
- 6.2 新增 `audit/routes.py`：`POST /api/audit/tasks`、`GET /api/audit/tasks/:id`、`POST /api/audit/tasks/:id/confirm-sheets`、`POST /api/audit/tasks/:id/step`（调试模式单步重跑）、`GET /api/audit/tasks/:id/stream`
- 6.3 在 `backend/app/main.py` 注册新 router
- **验证**：curl 提交任务 → SSE 订阅 → 阶段变化正常推送；单步重跑接口可重跑指定阶段

依赖：Task 1–5

## Task 7: 前端类型与 API（types.ts + api.ts）
- 7.1 扩展 `frontend/src/types.ts`：`AuditCategory` / `AuditTask` / `VlmCandidate` / `LlmResult` / `ReconcileReport` / `LLMProvider` / `LLMProviderConfig`
- 7.2 扩展 `frontend/src/api.ts`：`createAuditTask` / `getAuditTask` / `confirmAuditSheets` / `auditStream` / `runAuditStep` / `listLlmProviders` / `crudLlmProvider` / `testLlmProvider`
- **验证**：`tsc --noEmit` 0 错误

依赖：无（与后端 Task 1–6 可并行）

## Task 8: AIDrawer 改造（4 类复核入口 + 任务列表）
- 8.1 改造 `frontend/src/components/AIDrawer.tsx`：原 4 占位任务 → 4 类复核入口（围护桩/钻孔灌注桩/地下连续墙/承台，可多选）
- 8.2 提交按钮调用 `createAuditTask`；任务列表展示 status / 阶段进度 / 最近摘要 / 「查看完整报告」跳转 `/audit/:taskId`
- 8.3 新增 `SheetCandidateList` 组件：VLM 候选确认弹层（默认勾选 score≥0.8、用户可调整、提交 `confirmAuditSheets`）
- **验证**：浏览器手动测试提交任务、候选确认、跳转报告页

依赖：Task 7

## Task 9: 全屏报告页 `/audit/:taskId`
- 9.1 新增 `frontend/src/audit/AuditReportPage.tsx`：顶栏（任务概览/类别徽章/置信度/调试开关）+ 左侧 PNG（带构件高亮 SVG 覆盖）+ 右侧 `ReconcileTable` + 底部 `AuditStepTimeline`
- 9.2 新增 `frontend/src/audit/ReconcileTable.tsx`：按 category 分组，行=构件编号，列=规则值/LLM值/标注值/差异/severity 着色
- 9.3 新增 `frontend/src/audit/AuditStepTimeline.tsx`：4 步状态 + 可展开中间产物 JSON
- 9.4 新增 `frontend/src/audit/audit.css`：报告页专用样式
- **验证**：浏览器跑通一个真实任务，跳转报告页查看对账表与时间线

依赖：Task 6、Task 7、Task 8

## Task 10: 模型设置面板
- 10.1 新增 `frontend/src/audit/ModelSettings.tsx`：列出已配置 Provider（名称/协议/baseUrl/modelId/启用），支持增删改与测试连接
- 10.2 新增入口：AIDrawer 顶栏「⚙ 模型设置」按钮 → 弹层 / 路由
- **验证**：在设置面板配置一个 mock provider，测试连接返回结果正确展示

依赖：Task 7

## Task 11: 路由注册 + 端到端联调
- 11.1 `frontend/src/App.tsx` 注册路由：`/audit/:taskId`（lazy 加载 `AuditReportPage`）
- 11.2 端到端：上传 DWG → 触发复核 → VLM 候选确认 → 报告页查看对账结果
- 11.3 调试模式开关：单步重跑流程打通
- **验证**：完整跑通「提交 → 候选 → 报告 → 调试」全链路

依赖：Task 6–10

## Task 12: 提交与文档
- 12.1 `backend/requirements.txt` 新增：`httpx`、`Pillow`、`matplotlib`
- 12.2 根 `README.md` 更新：新增「工程量复核」章节、模型设置说明、4 阶段流水线说明
- 12.3 git add + commit + push（按用户 GitHub 流程，仓库 `shjk0817/cad-web`）
- **验证**：CI/本地运行 `tsc --noEmit` 0 错误；后端 `python -c "from app.audit import routes"` 导入成功；远端 gh 推送成功

依赖：Task 1–11

## Task Dependencies
```
Task 1 (model layer)
  ├→ Task 2 (rules)
  │    ├→ Task 3 (screenshot+vlm) → Task 4 (llm_infer) → Task 5 (reconcile)
  │    └→ Task 6 (pipeline + SSE)
  └→ Task 6
Task 7 (frontend types/api) ─→ Task 8 (AIDrawer) ─→ Task 9 (报告页) ─→ Task 10 (ModelSettings) ─→ Task 11 (路由+E2E) ─→ Task 12 (提交)
```