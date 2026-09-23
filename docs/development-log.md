# 开发日志

记录 cad-web 项目的方案决策、关键节点与里程碑。包含开发期间的过程性内容，与 `README.md` 的对外介绍互补。

> 原型使用 Trae 开发，本仓库为开源公开版本。

## 1. 项目起源与需求

需求：开发一个程序，前端能够上传 DWG 文件，解析后把同一份 DWG 中的多份图纸（带图框的）分别提取出来、并在浏览器中在线切换显示。优先采用开源方案。

## 2. 方案选型

- **DWG 解析**：GNU LibreDWG（GPL-3.0，独立子进程 + `dwg2dxf` 转换，避免传染）+ 浏览器端 [@mlightcad/libredwg-web](https://www.npmjs.com/package/@mlightcad/libredwg-web) 备选
- **DXF 渲染**：dxf-viewer + Three.js（MIT）
- **图框识别**：没有现成开源方案，需自研——按 A0–A4 标准图幅尺寸 / 长宽比识别闭合矩形多段线或图框块参照，再按几何包含关系归图元
- **后端**：FastAPI（MIT）调用 LibreDWG 子进程 → ezdxf 检测图框 → 按图框裁剪生成独立 DXF
- **前端**：React + Vite + TypeScript + dxf-viewer，侧栏图纸清单、在线切换、缩放平移、图层显隐

## 3. 关键节点

- A1 区承台图标题块帧与视口端到端修复（4 大根因：视口属性、矩阵偏移、union 副作用、签名重复、覆盖率阈值 0.9）
- SHA-256 内容哈希缓存（24h TTL）
- SSE 进度事件 + XHR POST 进度两段式进度条
- 前端重构：专业 CAD 桌面风 + 顶部 AI 入口 + 右侧 AI 抽屉 + 图层行内操作

## 4. 待办

- AI 识别接口实装：`POST /api/ai/tasks`，返回 `AiTask`
- 多视口模型→图纸变换矩阵调试（LibreDWG 转出 DXF：`view_center_point` + `view_target_point` 求真实取景中心）