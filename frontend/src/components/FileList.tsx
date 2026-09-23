import type { FileItem } from '../types'

interface FileListProps {
  files: FileItem[]
  activeUid: string | null
  onSelect: (file: FileItem) => void
  onUploadClick: () => void
}

// 文件大小格式化为 KB / MB
function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(0)} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function dotClass(file: FileItem): string {
  if (file.status === 'ready') return 'status-dot ready'
  if (file.status === 'error') return 'status-dot error'
  if (file.status === 'uploading' || file.status === 'parsing')
    return 'status-dot parsing'
  return 'status-dot'
}

// 已上传文件清单，含状态点 / 进度条 / 选中环
export default function FileList({ files, activeUid, onSelect, onUploadClick }: FileListProps) {
  return (
    <section className="sidebar-section">
      <header className="side-section-header">
        <h2 className="side-title">文件</h2>
        <span className="side-count">{files.length}</span>
      </header>

      <button type="button" className="side-action" onClick={onUploadClick}>
        <span>＋</span>
        <span>添加 DWG</span>
      </button>

      <div className="side-body">
        {files.length === 0 ? (
          <p className="side-empty">
            拖拽文件到窗口，或点击「添加 DWG」开始解析
          </p>
        ) : (
          <ul className="file-list">
            {files.map((file) => {
              const isParsing =
                file.status === 'uploading' || file.status === 'parsing'
              return (
                <li key={file.uid}>
                  <button
                    type="button"
                    className={`file-item ${file.uid === activeUid ? 'active' : ''}`}
                    disabled={isParsing}
                    onClick={() => onSelect(file)}
                  >
                    <div className="file-row">
                      <span className="file-icon">DWG</span>
                      <span className="file-name" title={file.name}>
                        {file.name}
                      </span>
                    </div>
                    <div className="file-meta">
                      <span className="file-status">
                        <span className={dotClass(file)} />
                        {file.status === 'ready' &&
                          `${file.sheets?.length ?? 0} 张图纸`}
                        {file.status === 'uploading' && '排队中…'}
                        {file.status === 'parsing' &&
                          `${file.progress ?? 0}% · ${file.stageText ?? '解析中'}`}
                        {file.status === 'error' && (file.error ? '失败' : '失败')}
                      </span>
                      <span className="file-size">{formatSize(file.size)}</span>
                    </div>
                    {file.status === 'error' && file.error && (
                      <span className="file-error" title={file.error}>
                        {file.error}
                      </span>
                    )}
                    {(file.status === 'uploading' || file.status === 'parsing') && (
                      <div className="file-progress">
                        <div
                          className="file-progress-bar"
                          style={{ width: `${file.progress ?? 0}%` }}
                        />
                      </div>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}