# 工程量复核（视觉 + 文字三方对账）Spec

## Why
工程实践中需要按图纸统计围护桩、钻孔灌注桩、地下连续墙、承台四类构件的工程量。图纸千差万别，单一路线的识别都不够可靠：纯几何计数会被变体块名/匿名块/非标比例干扰；纯文字抽取会被表格断行/单位不一/缩写干扰；纯 VLM 单 VLM 会幻觉数字。因此需要一套「规则管线 + VLM 筛选 + 文本 LLM 推理 + 三方对账」的多源校验流水线，并辅以人工闸门在关键环节确认，最终给出有差异标注、可审计的复核结果。

## 方案概述
- **后端**：在 `backend/app/` 下新增 `llm/`（多 Provider 模型抽象层）与 `audit/`（四阶段流水线）两个 package，沿用 FastAPI + 内存任务状态机 + SSE 推送模式。
- **模型层**：`Provider` 抽象基类；预置国产 + 本地主流厂商的 OpenAI 兼容协议（DeepSeek、通义千问、智谱 GLM、豆包、文心 ERNIE、腾讯混元、Moonshot Kimi、Ollama、vLLM），保留「自定义」入口以接入国外厂商（OpenAI / Anthropic / Gemini 原生协议）。各厂商视觉主力模型：DeepSeek `deepseek-flash`（V4.1-Flash 原生多模态）、Qwen `qwen-vl-max` / `qwen3-vl-max`、GLM `glm-4.6v`、Doubao `doubao-seed-2-1-pro`、ERNIE `ernie-5.0`、混元 `hy-vision-2.0`、Kimi `kimi-k3`。
- **流水线**：阶段① 规则管线（确定性几何 + 文字结构化）→ 阶段② VLM 筛选（防多选 + 人工闸门）→ 阶段③ 文本 LLM 推理计算 → 阶段④ 三方对账 → 输出差异报告。
- **前端**：保留 AIDrawer 作为触发入口（4 类构件入口 + 任务列表 + 「查看完整报告」）；新增 `/audit/:taskId` 全屏报告页（图纸截图 + 构件高亮 + 对账表 + 步骤时间线 + 调试模式开关）；新增「模型设置」面板（Provider 管理 + Key 配置 + 测试连接）。
- **数据流**：

```
AIDrawer 选 4 类构件 → POST /api/audit/tasks
  → ① rules.py: ezdxf 扫 CIRCLE/INSERT/LWPOLYLINE + 文字结构化 → RuleDraft
  → ② vlm.py: 每张图纸 PNG + prompt → VlmCandidate[] → 人工 confirm-sheets
  → ③ llm_infer.py: 选中图纸的文字上下文 → LlmResult
  → ④ reconcile.py: ①↔③↔anno → ReconcileReport
AIDrawer 摘要 + 「查看完整报告」→ /audit/:taskId
```

## What Changes
- 新增 `backend/app/llm/`：`provider.py`、`openai_compat.py`、`anthropic.py`、`gemini.py`、`registry.py`、`routes.py`
- 新增 `backend/app/audit/`：`models.py`、`rules.py`、`screenshot.py`、`vlm.py`、`llm_infer.py`、`reconcile.py`、`pipeline.py`、`routes.py`
- 新增 `backend/tmp/llm_providers.json`（Provider 配置落盘，已在 `.gitignore` 屏蔽 `backend/tmp/`）
- 新增 `backend/app/main.py` 注册两个新 router
- 扩展 `frontend/src/types.ts`：`AuditCategory` / `AuditTask` / `VlmCandidate` / `LlmResult` / `ReconcileReport` / `LLMProvider`
- 扩展 `frontend/src/api.ts`：`createAuditTask` / `getAuditTask` / `confirmAuditSheets` / `auditStream` / `runAuditStep` / `llmProvider*`
- 新增 `frontend/src/audit/`：`AuditReportPage.tsx`、`ReconcileTable.tsx`、`SheetCandidateList.tsx`、`AuditStepTimeline.tsx`、`ModelSettings.tsx`、`audit.css`
- 改造 `frontend/src/components/AIDrawer.tsx`：原 4 个占位任务 → 4 类复核入口 + 任务列表 + 「查看完整报告」
- 新增 `frontend/src/App.tsx` 路由注册：`/audit/:taskId` lazy 加载
- **BREAKING**：无对外契约破坏（现有 `/api/dwg/*` 与 AIDrawer 占位行为兼容）

## Impact
- Affected specs：`extract-multi-sheet-dwg`（复用 `frames.py` 的图框与 splitter 的实体访问，不修改其接口）
- Affected code：
  - 后端：`backend/app/{main,config,converter,frames,splitter,tasks}.py`、`backend/requirements.txt`（新增 `httpx`、`matplotlib`、`Pillow`、`pydantic` 等）
  - 前端：`frontend/src/{App.tsx,types.ts,api.ts,components/AIDrawer.tsx}`
  - 根：`.gitignore`（已屏蔽 `backend/tmp/`，新增 `*.db`、`llm_providers.json` 兜底）

## ADDED Requirements

### Requirement: 多 Provider 模型抽象层
系统 SHALL 提供统一的 `Provider` 接口（`chat(messages)`、`vision(images, prompt)`、`stream_chat(messages)`），并按「国产优先」原则预置以下 Provider（全部 `supportsVision=True`，厂商均已具备原生多模态能力）：

| Provider | 协议 | 视觉主力模型 | BaseURL |
|---|---|---|---|
| DeepSeek | OpenAI 兼容 | `deepseek-flash`（V4.1-Flash） | `https://api.deepseek.com/v1` |
| 通义千问 | OpenAI 兼容 | `qwen-vl-max` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | OpenAI 兼容 | `glm-4.6v` | `https://open.bigmodel.cn/api/paas/v4/` |
| 豆包 Doubao | OpenAI 兼容 | `doubao-seed-2-1-pro` | `https://ark.cn-beijing.volces.com/api/v3/` |
| 文心 ERNIE | OpenAI 兼容 | `ernie-5.0` | `https://qianfan.baidubce.com/v2/` |
| 腾讯混元 | OpenAI 兼容 | `hy-vision-2.0` | `https://api.hunyuan.cloud.tencent.com/v1` |
| Moonshot Kimi | OpenAI 兼容 | `kimi-k3` | `https://api.moonshot.cn/v1` |
| Ollama（本地） | OpenAI 兼容 | `qwen3-vl` | `http://localhost:11434/v1` |
| vLLM（自托管） | OpenAI 兼容 | `Qwen/Qwen3-VL-72B-Instruct` | `http://localhost:8000/v1` |
| 自定义（OpenAI 兼容 / Anthropic / Gemini 原生） | — | 用户填 | 用户填 |

国外厂商（OpenAI / Anthropic / Gemini）不再预置，需要时通过「自定义」入口填 baseUrl + apiKey + modelId 接入。模型层 SHALL 不绑定任何商业 SDK，全部 HTTP 直连。

系统 SHALL 通过 `GET /api/llm/providers` 列出预置 Provider 的元数据（名称/默认 baseUrl/支持 vision/默认 modelId），通过 `GET/POST/PUT/DELETE /api/llm/providers/configs` 管理已配置的 Provider 实例（name / providerType / baseUrl / apiKey / modelId / visionModelId / enabled），通过 `POST /api/llm/providers/configs/:id/test` 测试连接返回 `success` 或 `error`。

Provider 配置 SHALL 落盘到 `backend/tmp/llm_providers.json`，重启后保留。

#### Scenario: 用户在「模型设置」直接启用 DeepSeek 预置
- **WHEN** 用户在「模型设置」从预置列表选「DeepSeek」，只填 apiKey，其他字段（baseUrl、文本 modelId=`deepseek-chat`、视觉 modelId=`deepseek-flash`）自动带出
- **THEN** 配置保存并出现在 Provider 列表可用状态；后续 VLM/文本任务调用时按此配置走 OpenAI 兼容协议，VLM 调用使用 `deepseek-flash`（原生多模态）

#### Scenario: 用户接入国外厂商走「自定义」入口
- **WHEN** 用户在「模型设置」新增 → 选「自定义（Anthropic 原生）」 → 填 baseUrl/apiKey/modelId
- **THEN** 系统保存配置并出现在 Provider 列表可用状态；后续 VLM/文本任务调用时按此配置走 Anthropic 原生协议

#### Scenario: 测试连接失败
- **WHEN** 用户在「模型设置」点「测试连接」并配置错误 Key
- **THEN** 接口返回 `success=false, error="..."`；前端展示失败原因（如 401/超时）但不修改已保存配置

### Requirement: 阶段① 规则管线（几何 + 文字）
系统 SHALL 对给定 fileId + rootSheetId 的图纸，扫描 ezdxf 实体与文字，按下列规则生成 `RuleDraft`：

- **围护桩 / 钻孔灌注桩**：扫 `CIRCLE`，按「图层名匹配关键词 + 半径落在 {200,300,400,500,600,800,1000,1200}mm ±2%」聚类；输出 `数量/桩径/单位截面积/单孔体积/合计体积`，单孔长度 L 从构件标注或数量表读取
- **地下连续墙**：闭合 `LWPOLYLINE`（图层名匹配关键词）按图层+邻接连线成幅，每幅输出 `组/幅数/长度/宽度/深度/体积`
- **承台**：`INSERT` 块名匹配关键词或闭合多段线 + 附近 `CT-N` 文字；输出 `编号/数量/形状/面积/厚度/单个体积/合计体积`
- **文字侧**：① 工程数量汇总表——识别密集 TEXT/MTEXT 网格做行列结构化；② 逐构件标注——构件几何中心 ±阈值 内的 TEXT 用正则抽取参数
- 每行 SHALL 带 `source: 'geometry'|'table'|'annotation'`

#### Scenario: 模型空间含 30 根围护桩 CIRCLE + 一张数量汇总表
- **WHEN** 用户在 AIDrawer 选「围护桩复核」并提交
- **THEN** 阶段① 输出 `RuleDraft.retaining_pile` 含数量/桩径/单位截面积/单孔体积/合计体积，且每行标注来源（geometry 或 table）

#### Scenario: 围护桩桩长无法从几何获取
- **WHEN** CIRCLE 周边 200 单位内未找到桩长标注
- **THEN** 该字段标 `value=null, source=null`，留给阶段③ LLM 从上下文补全

### Requirement: 阶段② VLM 筛选 + 人工闸门
系统 SHALL 调用 `Provider.vision()`，输入为该 fileId 下每张图纸的 PNG（`screenshot.py` 用 ezdxf + matplotlib 出图）+ prompt（任务类别 + few-shot 例子 + 要求返回 `[{sheetId, score, role, reason}]`）。

输出 SHALL 为 `VlmCandidate[]`，并施加防多选约束：score 阈值 ≥0.6、Top-K=10、必须带 reason。

状态机 SHALL 在 `vlm_select → awaiting_confirm` 停下，由前端展示 `SheetCandidateList` 弹层（默认勾选 score≥0.8），用户调整后调用 `POST /api/audit/tasks/:id/confirm-sheets` 提交 `confirmedSheetIds[]`，状态推进至 `llm_infer`。

#### Scenario: VLM 推荐 3 张平面图 + 1 张详图
- **WHEN** 任务类别为「围护桩」，VLM 返回 3 张平面图 score≥0.85、1 张详图 score=0.72、1 张无关图纸 score=0.3
- **THEN** 前端默认勾选前 3 张，用户可手动调整后确认；状态推进至 `llm_infer`，传入选中的 sheetId 列表

#### Scenario: VLM 未返回任何候选
- **WHEN** 所有图纸 score < 0.6
- **THEN** 系统 SHALL 允许用户手动勾选图纸继续（前端 SheetCandidateSelect 列出全部图纸供手动选）

### Requirement: 阶段③ 文本 LLM 推理计算
系统 SHALL 调用 `Provider.chat()`，输入为 ② 确认图纸上 ① 输出的纯文字 + 表格结构（按 sheetId 切分）作为上下文，要求 LLM 输出结构化 JSON：

```json
{
  "byCategory": {
    "<cat>": [{ "id": "...", "fields": { "<k>": { "value": ..., "trace": "..." } } }]
  }
}
```

LLM SHALL 做归一化（`φ800/Φ800/800mm`→800）、单位换算、推理（`长度×截面积=体积`）、合计。每字段 MUST 包含 `trace` 文字描述推理过程，供报告页展开。

#### Scenario: LLM 收到 `φ800 L=15` 标注 + `桩径 800 长度 15` 表格
- **WHEN** 阶段① 同时抽出标注与表格且两者口径一致
- **THEN** LLM 输出 `fields.diameter.value=800, fields.length.value=15, fields.volume.value=7540`，trace 写明「截面积=π×(800/2)² ≈ 502655 mm²，单孔体积=502655×15≈7540×10³ mm³ ≈ 7.54 m³」

### Requirement: 阶段④ 三方对账
系统 SHALL 按 `(category, id)` join 三方数据：① RuleDraft（geometry/table）、③ LlmResult、① 的 annotation 聚合，逐字段比对，输出 `ReconcileReport`：

- 字段值一致 → `severity='match'`（绿）
- 两方一致、一方缺失或不一致 → `severity='warn'`（黄）
- 三方均存在但互不一致 → `severity='conflict'`（红）

报告 SHALL 含 `byCategory` 分组、`summary={totalRows,matched,warned,conflicted}`、整体 `confidence = matched/totalRows`。

#### Scenario: 数量三方一致 + 单孔体积两方冲突
- **WHEN** ① RuleDraft 与 ③ LlmResult 的「数量」一致，但「单孔体积」字段差 5%
- **THEN** 该字段行 severity=conflict；前端表格化、列=规则值/LLM值/标注值/差异描述/置信度

### Requirement: 流水线编排与 SSE 推送
系统 SHALL 在 `audit/pipeline.py` 实现四阶段状态机，复用 `tasks.py` 模式：

```
queued → rules → vlm_select → awaiting_confirm → llm_infer → reconcile → done
                                                                          ↘ error
```

每次状态变化 SHALL 通过 `GET /api/audit/tasks/:id/stream` SSE 推送（事件名 `stage`、`progress`、`error`、`done`）。

#### Scenario: 阶段② VLM 调用失败
- **WHEN** Provider 不可用或返回错误
- **THEN** 状态转 `error`，SSE 推送 `error` 事件；前端 AIDrawer 任务卡片显示「阶段②失败：<reason>」，可点「重试该步」

### Requirement: AIDrawer 触发入口与任务列表
AIDrawer SHALL 提供 4 类复核入口（围护桩/钻孔灌注桩/地下连续墙/承台，可多选），点击提交 `POST /api/audit/tasks`。任务列表 SHALL 显示任务状态、阶段进度条、最近一条中间摘要（如「候选图纸 3 张」「对账冲突 2 条」），并提供「查看完整报告」按钮跳转 `/audit/:taskId`。

#### Scenario: 用户选围护桩 + 承台 复核
- **WHEN** 用户勾选 2 个类别点提交
- **THEN** AIDrawer 任务列表新增一行，状态为 `rules`；SSE 推送阶段变化，最终显示「候选图纸 3 张 · 置信度 87%」摘要

### Requirement: 全屏报告页 `/audit/:taskId`
报告页 SHALL 包含：

- 顶栏：任务概览（图名 / 类别 / 状态 / 置信度徽章 / 调试模式开关）
- 左侧：图纸截图（PNG），构件高亮覆盖（CIRCLE 描边、地连墙幅描边、承台框）
- 右侧：对账表 `ReconcileTable`，按 category 分组、行=构件编号、列=规则值/LLM值/标注值/差异/severity 着色
- 底部：步骤时间线 `AuditStepTimeline`，4 步状态 + 可展开中间产物 JSON
- 右上：调试模式开关，开启后每步可单独重跑、看中间产物

#### Scenario: 调试模式重跑阶段③
- **WHEN** 用户打开调试模式并点「重跑 LLM 推理」
- **THEN** 前端调用 `POST /api/audit/tasks/:id/step` with `{step:"llm_infer"}`，SSE 推送阶段变化；最终报告更新

### Requirement: 模型设置面板
`ModelSettings` SHALL 列出已配置 Provider（名称/协议/baseUrl/modelId/启用状态），支持增删改与测试连接。预置 Provider 元数据 SHALL 内置为常量，前端可枚举展示。

#### Scenario: 用户配置 DeepSeek 自定义 Provider
- **WHEN** 用户在设置面板新增 → 选「自定义」 → 协议 OpenAI 兼容 → 填 baseUrl/apiKey/modelId → 保存
- **THEN** 该 Provider 出现在列表并设为可用；AIDrawer 复核任务提交时若用户选了此 Provider，则走此配置调用

## MODIFIED Requirements
（无对外契约破坏，无 MODIFIED Requirements）

## REMOVED Requirements
（无）

## 开源依赖与外部 API
- 后端新增 Python 包：`httpx`（LLM HTTP 客户端）、`Pillow`（PNG 处理）、`matplotlib`（DXF 截图）
- 模型层不绑定任何商业 SDK，全部用 HTTP 直连，遵循 OpenAI 兼容 / Gemini 原生 / Anthropic 原生公开协议
- 截图出图：ezdxf + matplotlib（headless 后端 `Agg`），不引入 dxf-viewer 服务端版以免 GPL 传染

## 非目标（本期不做）
- 批量复核调度队列、报告导出 PDF/Excel、多用户权限、复核结果持久化（任务内存态，复用 `tasks.py` 模式，重启即失，可后续接库）
- dxf-viewer 服务端渲染、SHX 字体精确还原、外部参照（xref）解析、DWG 编辑回写
- Agent 编排（Function Calling / MCP 暴露 DXF 工具），本期走流水线编排，不引入通用智能体