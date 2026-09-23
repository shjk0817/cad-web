import { useRef } from 'react'

interface TopbarProps {
  busy: boolean
  fileName?: string | null
  sheetName?: string | null
  canFitView: boolean
  showLayers: boolean
  aiOpen: boolean
  onFiles: (files: File[]) => void
  onSelectFile: () => void
  onFitView: () => void
  onToggleLayers: () => void
  onToggleAi: () => void
}

// 顶部工具栏：品牌 / 面包屑 / 视图控制 / AI 识别 / 设置
export default function Topbar({
  busy,
  fileName,
  sheetName,
  canFitView,
  showLayers,
  aiOpen,
  onFiles,
  onSelectFile,
  onFitView,
  onToggleLayers,
  onToggleAi,
}: TopbarProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length > 0) {
      onFiles(files)
    }
    // 允许再次选择同一个文件
    e.target.value = ''
  }

  return (
    <header className="topbar">
      {/* 品牌区 */}
      <div className="topbar-brand">
        <span className="brand-mark">CAD</span>
        <span className="brand-text">DWG 浏览</span>
        <span className="brand-sub">Multi-Sheet</span>
      </div>

      {/* 面包屑：文件 / 图纸 */}
      <nav className="crumbs" aria-label="当前对象">
        {fileName ? (
          <>
            <button
              type="button"
              className="crumb active"
              onClick={onSelectFile}
              title={fileName}
            >
              {fileName}
            </button>
            {sheetName && (
              <>
                <span className="crumb-sep">/</span>
                <span className="crumb active" title={sheetName}>
                  {sheetName}
                </span>
              </>
            )}
          </>
        ) : (
          <span className="crumbs-empty">尚未选择文件</span>
        )}
      </nav>

      {/* 上传按钮（保留 .dwg 选择入口） */}
      <button
        type="button"
        className="tool-btn"
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        title="选择 DWG 文件"
      >
        <span className="ico">＋</span>
        <span>{busy ? '上传中…' : '选择 DWG'}</span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".dwg"
        multiple
        className="file-input"
        onChange={handleChange}
      />

      {/* 视图控制组 */}
      <div className="tool-group" role="toolbar" aria-label="视图控制">
        <button
          type="button"
          className="tool-btn"
          title="自适应"
          disabled={!canFitView}
          onClick={onFitView}
        >
          自适应
        </button>
        <button
          type="button"
          className={`tool-btn ${showLayers ? 'primary' : ''}`}
          title="图层"
          onClick={onToggleLayers}
        >
          图层
        </button>
      </div>

      {/* AI 识别按钮 */}
      <button
        type="button"
        className={`tool-btn ai ${aiOpen ? 'active' : ''}`}
        onClick={onToggleAi}
        title="AI 识别"
      >
        ✨ AI 识别
      </button>
    </header>
  )
}