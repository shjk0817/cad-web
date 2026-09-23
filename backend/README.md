# DWG 多图框拆分后端

基于 FastAPI 的服务：上传 DWG → 用 GNU LibreDWG 的 `dwg2dxf` 转为 DXF →
检测标准图幅图框 → 按图框拆分为多份独立图纸 DXF。

## 环境依赖

- Python 3.9+
- 依赖见 `requirements.txt`：`fastapi`、`uvicorn[standard]`、`python-multipart`、`ezdxf`

```bash
pip install -r requirements.txt
```

## 安装 LibreDWG（提供 dwg2dxf）

- Windows：到 [GNU LibreDWG](https://www.gnu.org/software/libredwg/) 下载
  Windows win64 release 的 zip 包，解压后其中包含 `dwg2dxf.exe` 及运行所需的 dll。
- Linux：可使用发行版包管理器安装，如 `apt install libredwg-tools`。

### 配置 dwg2dxf 路径

后端按以下顺序探测 `dwg2dxf`：

1. 环境变量 `DWG2DXF_PATH`（指向可执行文件完整路径）；
2. 项目根目录 `tools/libredwg/dwg2dxf.exe`；
3. 系统 `PATH` 中的 `dwg2dxf`。

Windows PowerShell 示例：

```powershell
$env:DWG2DXF_PATH = "C:\libredwg\dwg2dxf.exe"
```

## 其他配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `WORK_DIR` | `backend/tmp` | 上传文件与中间产物目录 |
| `TTL_HOURS` | `24` | 会话文件保留小时数，启动时清理过期目录 |

## 启动

在 `backend/` 目录下执行：

```bash
uvicorn app.main:app --reload
```

服务默认监听 `http://127.0.0.1:8000`，接口：

- `GET /api/health`：健康检查；
- `POST /api/dwg/upload`：multipart 上传字段 `file`（.dwg）；
- `GET /api/dwg/{fileId}/sheets/{sheetId}`：下载图纸 DXF。
