# cad-web

> DWG 多图纸浏览 + 工程量复核系统 — 上传一份 DWG，自动按标准图框拆出多张图纸，在线浏览与图层显隐；并通过 5 阶段流水线（规则管线 → VLM 选图 → 用户确认 → 文本 LLM 推理 → 三方对账）进行支护桩 / 钻孔灌注桩 / 地下连续墙 / 承台工程量复核。

## ✨ 功能特性

- **多图纸提取**：后端通过 GNU LibreDWG `dwg2dxf` 转换 + ezdxf 检测 A0–A4 标准图框，把一份 DWG 拆成多份独立图纸
- **在线浏览**：React + Vite + dxf-viewer（Three.js/WebGL）渲染，支持缩放、平移
- **图层交互**：勾选显隐 / 全部显示与隐藏 / 仅显示 / 隔离 / 关键字搜索
- **AI 识别占位**：右侧抽屉内置「提取标题栏 / 标注图元 / 图纸摘要 / 针对图纸提问」4 类任务入口，等待后端 `/api/ai/tasks` 实装
- **SHA-256 缓存**：相同 DWG 不重复解析
- **SSE 进度流**：上传 / 解析 / 复核阶段实时进度
- **工程量复核（5 阶段流水线）**：
  1. **规则管线**（`audit/rules.py`）：从 ezdxf 解析结果抽出 4 类（支护桩 / 钻孔灌注桩 / 地连墙 / 承台）候选数据
  2. **VLM 选图**（`audit/vlm_select.py`）：contact-sheet 拼接 + 国产 VLM Provider 打分与排序
  3. **用户确认**：前端 `SheetCandidateList` 勾选参与复核的图纸
  4. **文本 LLM 推理**（`audit/llm_infer.py`）：DeepSeek / 通义 / GLM / 豆包 / 文心 / 混元 / Kimi + Ollama / vLLM / 自定义
  5. **三方对账**（`audit/reconcile.py`）：规则值 ↔ 标注值 ↔ LLM 值的 match / warn / conflict 报告

## 🧱 架构

```
cad-web/
├── backend/          # FastAPI + LibreDWG + ezdxf (Python 3.9+)
├── frontend/         # React 18 + Vite + dxf-viewer (TypeScript)
├── tools/            # 第三方构建脚本（如 libredwg 工具下载）
└── docs/             # 设计与开发日志
```

## 🚀 本地启动

### 后端（端口 8000）

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 安装 LibreDWG，提供 dwg2dxf：https://www.gnu.org/software/libredwg/
uvicorn app.main:app --reload
```

### 前端（端口 5180）

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5180`，把 DWG 拖到窗口或上拉编辑器。

### 工程量复核端到端测试

```bash
cd backend
python -m app.tests.test_audit_pipeline
```

8 步集成测试：POST 任务 → 等待 awaiting_confirm → confirm-sheets → 等待 done → 调试模式 /step 重跑 rules → SSE 订阅 → 错误路径（404/400）。

## 🔌 复核 API 速览

| Method | Path | 说明 |
| ------ | ---- | ---- |
| POST   | `/api/audit/tasks`                            | 创建复核任务（body: `fileId / categories`） |
| GET    | `/api/audit/tasks`                            | 列出所有复核任务 |
| GET    | `/api/audit/tasks/{id}`                       | 任务详情 |
| POST   | `/api/audit/tasks/{id}/confirm-sheets`       | 用户确认 VLM 选图（body: `sheetIds: string[]`） |
| POST   | `/api/audit/tasks/{id}/step`                  | 调试模式单步重跑（body: `stage: rules\|vlm_select\|llm_infer\|reconcile`） |
| GET    | `/api/audit/tasks/{id}/stream`                | SSE 进度流（`text/event-stream`，15s ping + sig 去重） |
| GET    | `/api/audit/tasks/{id}/sheets/{sid}/png`      | 单张图纸 PNG 缩略图（vlm_select 后渲染） |

## 📝 备注

- `earthwork/`、`*.dwg`、`*.dxf`、`*.docx` 等真实工程资料均已在 `.gitignore` 中屏蔽
- 前端默认配置 [vite.config.ts](file:///Users/slouch/Desktop/cad-web/frontend/vite.config.ts) `/api` 代理到 `http://localhost:8000`
- 工程量复核依赖 LLM Provider 配置，详见 `backend/app/llm/registry.py`（国产 / Ollama / vLLM / 自定义 OpenAI 兼容）
- 报告页：`#/audit/<taskId>` 全屏展示 5 阶段时间线 + 三方对账明细 + 调试模式单步重跑

## 📄 License

MIT