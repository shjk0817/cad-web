# Checklist

> 用于提交前系统化校验每一项 spec 要求。

## 多 Provider 模型抽象层（Task 1）
- [ ] `backend/app/llm/provider.py` 定义 `Provider` 抽象基类（chat/vision/stream_chat）
- [ ] `backend/app/llm/openai_compat.py` 支持 OpenAI 兼容协议（覆盖 OpenAI/DeepSeek/通义/智谱/Moonshot/Ollama/vLLM）
- [ ] `backend/app/llm/anthropic.py` 支持 Anthropic 原生协议
- [ ] `backend/app/llm/gemini.py` 支持 Gemini 原生协议
- [ ] `backend/app/llm/registry.py` 预置 Provider 元数据常量
- [ ] `backend/app/llm/store.py` 配置落盘 `backend/tmp/llm_providers.json`
- [ ] `backend/app/llm/routes.py` 暴露 6 个 REST 接口（list/configs CRUD/test）
- [ ] `backend/app/main.py` 注册 llm router
- [ ] curl 测试：列出预置 Provider、增/改/删/测一个假 Provider 全 200

## 阶段① 规则管线（Task 2）
- [ ] `backend/app/audit/models.py` 定义全部数据契约 dataclass/Pydantic
- [ ] `backend/app/audit/rules.py` 扫 CIRCLE 围护桩/钻孔灌注桩（按图层+半径聚类）
- [ ] `backend/app/audit/rules.py` 扫 LWPOLYLINE 地连墙（按邻接连线成幅）
- [ ] `backend/app/audit/rules.py` 扫 INSERT/LWPOLYLINE 承台
- [ ] `backend/app/audit/rules.py` 抽取工程数量汇总表（行列结构化）
- [ ] `backend/app/audit/rules.py` 抽取逐构件标注（正则归一化 `φ800/Φ800/800mm`）
- [ ] 每行带 `source: 'geometry'|'table'|'annotation'`
- [ ] 用仓库 `earthwork/*.dwg` 跑通产出完整 RuleDraft JSON

## 截图与 VLM 筛选（Task 3）
- [ ] `backend/app/audit/screenshot.py` ezdxf + matplotlib Agg 出 PNG
- [ ] `backend/app/audit/vlm.py` 调用 `Provider.vision()` + 拼 prompt（few-shot + schema）
- [ ] 防多选约束：score≥0.6、Top-K=10、必须带 reason
- [ ] mock provider 跑通返回 `VlmCandidate[]`

## 阶段③ 文本 LLM 推理（Task 4）
- [ ] `backend/app/audit/llm_infer.py` 调用 `Provider.chat()`
- [ ] prompt 输出 JSON schema 校验
- [ ] trace 字段强制保留
- [ ] 解析失败回退保留原始输出

## 阶段④ 三方对账（Task 5）
- [ ] `backend/app/audit/reconcile.py` 按 `(category, id)` join 三方
- [ ] severity 规则：match（绿）/ warn（黄）/ conflict（红）
- [ ] summary 字段完整：totalRows/matched/warned/conflicted/confidence
- [ ] 构造三方测试数据验证

## 流水线编排与 SSE（Task 6）
- [ ] `backend/app/audit/pipeline.py` 状态机 `queued→rules→vlm_select→awaiting_confirm→llm_infer→reconcile→done/error`
- [ ] `audit/routes.py` 5 个接口：POST tasks / GET / confirm-sheets / step / stream
- [ ] `backend/app/main.py` 注册 audit router
- [ ] SSE 推送 stage/progress/error/done 事件

## 前端类型与 API（Task 7）
- [ ] `frontend/src/types.ts` 扩展 AuditCategory / AuditTask / VlmCandidate / LlmResult / ReconcileReport / LLMProvider
- [ ] `frontend/src/api.ts` 扩展 9 个 API 函数
- [ ] `tsc --noEmit` 0 错误

## AIDrawer 改造（Task 8）
- [ ] AIDrawer 4 类复核入口（围护桩/钻孔灌注桩/地连墙/承台，可多选）
- [ ] 任务列表展示 status / 阶段进度 / 摘要 / 「查看完整报告」
- [ ] `SheetCandidateList` 候选确认弹层（默认勾选 score≥0.8、可调整、提交确认）
- [ ] 浏览器手动测试提交任务全流程

## 全屏报告页（Task 9）
- [ ] `AuditReportPage.tsx` 顶栏/左图+高亮/右对账表/底部时间线/调试开关
- [ ] `ReconcileTable.tsx` 按 category 分组，severity 着色
- [ ] `AuditStepTimeline.tsx` 4 步状态 + 中间产物可展开
- [ ] `audit.css` 报告页样式

## 模型设置面板（Task 10）
- [ ] `ModelSettings.tsx` Provider 增删改 + 测试连接
- [ ] 入口在 AIDrawer 顶栏
- [ ] 测试连接结果正确展示

## 路由注册与端到端（Task 11）
- [ ] `/audit/:taskId` 路由注册 + lazy 加载
- [ ] 端到端：上传 DWG → 复核 → 候选 → 报告 → 调试 全链路
- [ ] 调试模式单步重跑打通

## 提交与文档（Task 12）
- [ ] `backend/requirements.txt` 新增 httpx/Pillow/matplotlib
- [ ] `README.md` 新增「工程量复核」章节
- [ ] git add + commit + push 到 `shjk0817/cad-web`