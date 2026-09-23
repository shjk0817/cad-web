# Tasks

- [x] Task 1: 搭建后端骨架与环境
  - [x] SubTask 1.1: 创建 `backend/`，初始化 Python 项目（requirements：fastapi、uvicorn、python-multipart、ezdxf），补充 README 中的 LibreDWG 安装说明
  - [x] SubTask 1.2: 封装 `dwg2dxf` 子进程调用工具（检测可执行文件是否存在、超时、失败返回明确错误）
  - [x] SubTask 1.3: 配置临时目录与过期清理机制（按 fileId 隔离）

- [x] Task 2: 实现图框检测与图名提取
  - [x] SubTask 2.1: 用 ezdxf 读取模型空间，收集闭合 4 点多段线/矩形及块参照候选
  - [x] SubTask 2.2: 按 A0–A4 标准图幅尺寸（横/竖、±2%、等比缩放兼容）匹配，做去重、去嵌套
  - [x] SubTask 2.3: 块参照匹配（块名关键字 + 包围盒尺寸）作为补充规则
  - [x] SubTask 2.4: 图名提取（块属性标签 → 标题栏区域最大文字 → 「图纸N」兜底），重名加序号
  - [x] SubTask 2.5: 无图框时回退整图范围，名称「全图」

- [x] Task 3: 实现按图框拆分 DXF
  - [x] SubTask 3.1: 计算各图元包围盒/插入点，按图框矩形归属（中心点在内，含边界）
  - [x] SubTask 3.2: 用 ezdxf 新建 DXF，拷贝归属图元并携带所需图层、线型、文字样式、块定义
  - [x] SubTask 3.3: 为每张图纸输出独立 `.dxf` 文件并生成图纸清单 JSON

- [x] Task 4: 实现 FastAPI 接口
  - [x] SubTask 4.1: `POST /api/dwg/upload`（multipart 校验 .dwg、串联转换/检测/拆分、返回 fileId 与 sheets 清单、异常转 4xx 错误信息）
  - [x] SubTask 4.2: `GET /api/dwg/{fileId}/sheets/{sheetId}`（返回对应 DXF 文件）

- [x] Task 5: 搭建前端骨架
  - [x] SubTask 5.1: 创建 `frontend/`（React + Vite + TS），配置后端 API 代理
  - [x] SubTask 5.2: 实现 DWG 上传组件（点击选择 + 拖拽、.dwg 校验、上传中/失败状态提示）

- [x] Task 6: 实现图纸清单与在线浏览
  - [x] SubTask 6.1: 集成 `dxf-viewer`，封装图纸 DXF 加载/销毁
  - [x] SubTask 6.2: 侧栏图纸清单（名称、尺寸），点击切换并自适应铺满，支持缩放/平移
  - [x] SubTask 6.3: 图层面板：列出图层并切换显隐

- [x] Task 7: 端到端联调与验证
  - [x] SubTask 7.1: 用 `earthwork/` 下 3 个示例 DWG 验证上传、图框数量、图名与逐张渲染
  - [x] SubTask 7.2: 验证损坏文件/非 DWG/缺少 dwg2dxf 的错误提示

- [x] Task 8: 修复图形文字加载
  - [x] SubTask 8.1: 确认 dxf-viewer 文字机制（Load 须传 TTF/OTF 字体 URL，未传字体不渲染文字）
  - [x] SubTask 8.2: 用 fontTools 从 Noto Sans SC 可变字体构建 GB2312 工程字符子集（ASCII/拉丁/希腊/数学符/全角/6763 汉字），随附 OFL 许可证
  - [x] SubTask 8.3: DxfCanvas 加载时传 fonts；后端拆分 DXF 统一输出 UTF-8（修复 GBK 图纸中文在前端变成 ? 的问题）

- [x] Task 9: 覆盖所有布局（paperspace）中的图纸
  - [x] SubTask 9.1: 图框检测遍历全部布局（layout_names_in_taborder），图纸空间图名加「[布局名] 」前缀，空布局跳过
  - [x] SubTask 9.2: 拆分时用 xref.Loader.load_paperspace_layout_into 把图纸空间实体导入目标模型空间，manifest 记录 layout 字段

- [x] Task 10: 批量上传与解析缓存
  - [x] SubTask 10.1: 前端文件选择框加 multiple，支持一次选择/拖拽多个 DWG，新增文件清单与多文件切换
  - [x] SubTask 10.2: 后端按文件内容 SHA-256 做缓存（cache_index.json，线程锁 + 原子写 + 过期联动清理），命中直接复用并顺延 TTL，返回 cached 标记

# Task Dependencies
- Task 2、Task 3 依赖 Task 1；Task 3 依赖 Task 2 的检测结果
- Task 4 依赖 Task 3
- Task 6 依赖 Task 5 与 Task 4（接口可用）
- Task 7 依赖 Task 4 与 Task 6
- Task 5 可与 Task 1–4 并行
