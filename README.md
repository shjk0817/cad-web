# cad-web

> DWG 多图纸浏览与提取系统 — 上传一份 DWG，自动按标准图框拆出多张图纸，支持在线切换、图层显隐、AI 识别（占位中）。

## ✨ 功能特性

- **多图纸提取**：后端通过 GNU LibreDWG `dwg2dxf` 转换 + ezdxf 检测 A0–A4 标准图框，把一份 DWG 拆成多份独立图纸
- **在线浏览**：React + Vite + dxf-viewer（Three.js/WebGL）渲染，支持缩放、平移
- **图层交互**：勾选显隐 / 全部显示与隐藏 / 仅显示 / 隔离 / 关键字搜索
- **AI 识别占位**：右侧抽屉内置「提取标题栏 / 标注图元 / 图纸摘要 / 针对图纸提问」4 类任务入口，等待后端 `/api/ai/tasks` 实装
- **SHA-256 缓存**：相同 DWG 不重复解析
- **SSE 进度流**：上传 / 解析阶段实时进度

## 🧱 架构

```
cad-web/
├── backend/          # FastAPI + LibreDWG + ezdxf (Python 3.9+)
├── frontend/         # React 19 + Vite + dxf-viewer (TypeScript)
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

## 📝 备注

- `earthwork/`、`*.dwg`、`*.dxf`、`*.docx` 等真实工程资料均已在 `.gitignore` 中屏蔽
- 前端默认配置 [vue.config.ts](file:///Users/slouch/Desktop/cad-web/frontend/vite.config.ts) `/api` 代理到 `http://localhost:8000`
- AI 识别接口当前为占位，返回「AI 识别功能尚未上线」；后端实装后只需实现 `POST /api/ai/tasks` 返回 `AiTask` 类型

## 📄 License

MIT