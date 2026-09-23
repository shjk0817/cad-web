# DWG 多图框图纸拆分与在线浏览 Spec

## Why
工程实践中一个 DWG 文件的模型空间里常常排布多张带图框的图纸（如本仓库 `earthwork/` 下的示例文件）。用户需要在浏览器上传 DWG 后，自动把每张带图框的图纸分别提取出来，并可在线切换查看，而无需安装 AutoCAD。

## 方案概述（优先开源）

整体采用「轻量后端转换 + 前端渲染」架构，全部基于开源组件：

| 环节 | 方案 | 许可证 |
|---|---|---|
| DWG → DXF 转换 | GNU LibreDWG 自带的 `dwg2dxf` 命令行，由后端以独立子进程调用 | GPL-3.0（独立进程隔离） |
| DXF 解析 / 图框检测 / 图纸拆分 | Python `ezdxf` | MIT |
| 前端 DXF 渲染 | `dxf-viewer`（基于 Three.js / WebGL） | MPL-2.0 |
| 后端框架 | FastAPI + Uvicorn | MIT |
| 前端框架 | React + Vite + TypeScript | MIT |

数据流：

```
浏览器选择 DWG
  → POST /api/dwg/upload (FastAPI)
    → dwg2dxf 转成 DXF
    → ezdxf 检测图框（标准图幅尺寸 + 图框块）
    → 读取图名（块属性/图框内文字）
    → 按图框边界裁剪，每份图纸生成一个独立 DXF
  → 返回图纸清单（id、图名、尺寸）
浏览器侧栏列出图纸 → 选中后加载对应 DXF → dxf-viewer 渲染
```

## What Changes
- 新建后端服务（`backend/`，Python + FastAPI）：DWG 上传、`dwg2dxf` 转换、图框检测、图纸拆分、图纸 DXF 下载接口。
- 新建前端应用（`frontend/`，React + Vite + TS）：DWG 上传（拖拽/点选）、解析进度与错误提示、图纸清单侧栏、点击切换在线渲染、缩放/平移/自适应、图层显隐。
- 图框识别规则：优先按 A0–A4 标准图幅长宽尺寸识别闭合矩形多段线；其次识别图框块参照（块名或块包围盒匹配）；识别不到时回退为「整图一张」。
- 图名提取：优先读取图框块属性（如 `图名`/`DRAWING_TITLE` 等标签）；其次取图框右下角区域最大文字；均失败时用「图纸1、图纸2…」。
- 非目标（本期不做）：DWG 编辑/回写、外部参照（xref）解析、SHX 字体精确还原、用户体系与持久化存储（文件临时存放、定期清理）、OCR 光栅图纸。

## Impact
- Affected specs: 无（全新项目）。
- Affected code: 新建 `backend/` 与 `frontend/` 两个目录；`earthwork/*.dwg` 仅作为联调验证样例，不修改。
- 环境依赖：后端运行环境需安装 GNU LibreDWG（提供 `dwg2dxf`），Python ≥ 3.10；前端 Node ≥ 18。

## ADDED Requirements

### Requirement: DWG 文件上传
系统 SHALL 提供网页上传入口，支持点击选择与拖拽上传单个 `.dwg` 文件，并展示上传/解析状态。

#### Scenario: 上传合法 DWG
- **WHEN** 用户上传一个有效的 DWG 文件（支持 AutoCAD R14–2026 常见版本，`dwg2dxf` 可解析的范围）
- **THEN** 后端完成转换与拆分，前端展示图纸清单

#### Scenario: 上传非法或无法解析的文件
- **WHEN** 用户上传的文件不是 DWG、文件损坏，或 `dwg2dxf` 转换失败
- **THEN** 前端展示明确的错误提示，不产生崩溃或无响应

### Requirement: 图框自动检测
系统 SHALL 基于 DXF 模型空间自动检测图框，检测规则按优先级为：
1. 闭合的 4 点多段线/矩形，其长宽匹配标准图幅（A0 841×1189、A1 594×841、A2 420×594、A3 297×420、A4 210×297，单位 mm；允许横/竖方向，尺寸误差 ±2%，并容忍常见的非 1:1 出图比例带来的等比缩放）；
2. 块参照（INSERT）：块名命中常见图框命名（如含 `图框`、`TK`、`TITLE`、`A0`–`A4`），或其几何包围盒尺寸匹配标准图幅；
3. 多个嵌套候选时取最外层匹配矩形，去除重复/包含的候选。

#### Scenario: 一张 DWG 含多张标准图框图纸
- **WHEN** 模型空间中存在 2 个以上符合标准图幅尺寸的闭合图框
- **THEN** 每个图框被识别为一张独立图纸，数量与实际图框数一致

#### Scenario: 未检测到任何图框
- **WHEN** 图元中不存在符合规则的图框
- **THEN** 系统回退生成 1 张以整图几何范围为边界的图纸，名称为「全图」

### Requirement: 图纸命名
系统 SHALL 为每张图纸生成名称：优先取图框标题栏块属性中的图名标签值；其次取图框标题栏区域内字号最大的 TEXT/MTEXT；均无则命名为「图纸N」（N 为从 1 开始的序号）。名称允许重复时追加序号后缀。

#### Scenario: 图框带图名属性
- **WHEN** 图框块参照包含图名属性（如标签 `图名`）
- **THEN** 清单中该图纸显示属性值作为名称

### Requirement: 按图框拆分图纸
系统 SHALL 按每张图框的矩形边界对模型空间图元进行归属裁剪，生成仅包含该图纸内容的独立 DXF 文件；图元归属以其包围盒中心/插入点落在图框矩形内（含边界）为准，跨框共享图元（如图框线本身）允许出现在每份图纸中；拆分结果保留原图层、颜色、线型及块定义，渲染视觉效果与原图一致。

#### Scenario: 查看单张图纸
- **WHEN** 请求某张图纸的 DXF
- **THEN** 返回的 DXF 仅包含该图框范围内的内容，且图层、块、文字正常呈现

### Requirement: 图纸在线切换显示
系统 SHALL 在前端以侧栏清单展示全部图纸，用户点击任一图纸后在主视口加载并渲染该图纸 DXF，切换后视图自适应铺满；渲染器 SHALL 支持鼠标缩放、平移，并提供图层列表可切换图层显隐。

#### Scenario: 切换图纸
- **WHEN** 用户在清单中点击另一张图纸
- **THEN** 主视口在不刷新页面的情况下切换为该图纸内容并自适应缩放

#### Scenario: 图层显隐
- **WHEN** 用户在图层面板切换某图层可见性
- **THEN** 视口中该图层图元即时显示/隐藏

### Requirement: 后端接口
系统 SHALL 提供以下 HTTP 接口：
- `POST /api/dwg/upload`：multipart 上传 DWG，返回 `{ fileId, sheets: [{ id, name, width, height }] }`；
- `GET /api/dwg/{fileId}/sheets/{sheetId}`：下载该图纸的 DXF（`image/vnd.dxf`）。

上传文件及中间产物存于服务端临时目录，会话文件保留至可配置的过期时间后自动清理。

#### Scenario: 重复请求同一图纸
- **WHEN** 同一 `fileId` 下多次请求同一图纸 DXF
- **THEN** 接口幂等返回相同内容

## 开源参考资料
- [GNU LibreDWG 官网](https://www.gnu.org/software/libredwg/index.html)（`dwg2dxf` 转换器）
- [@mlightcad/libredwg-web](https://www.npmjs.com/package/@mlightcad/libredwg-web)（备选：纯浏览器 WASM 解析路线）
- [ezdxf 文档](https://ezdxf.mozman.at/)（Python DXF 处理）
- [dxf-viewer](https://github.com/vagran/dxf-viewer)（浏览器 DXF 渲染）
