interface StatusBarProps {
  fileCount: number
  parsingCount: number
  hiddenLayerCount: number
  totalLayers: number
  ready: boolean
}

// 底部状态栏：摘要信息
export default function StatusBar({
  fileCount,
  parsingCount,
  hiddenLayerCount,
  totalLayers,
  ready,
}: StatusBarProps) {
  return (
    <footer className="statusbar">
      <span className="statusbar-item">
        <span className="status-dot ready" />
        <span>已就绪</span>
      </span>
      <span className="statusbar-spacer" />
      <span className="statusbar-item">
        <span>文件</span>
        <span style={{ color: 'var(--color-text)' }}>{fileCount}</span>
      </span>
      {parsingCount > 0 && (
        <span className="statusbar-item">
          <span>解析中</span>
          <span style={{ color: 'var(--color-primary)' }}>{parsingCount}</span>
        </span>
      )}
      <span className="statusbar-item">
        <span>图层可见</span>
        <span style={{ color: 'var(--color-text)' }}>
          {totalLayers === 0 ? '—' : `${totalLayers - hiddenLayerCount}/${totalLayers}`}
        </span>
      </span>
      <span className="statusbar-item">
        <span>视图</span>
        <span style={{ color: 'var(--color-text)' }}>
          {ready ? '已加载' : '空闲'}
        </span>
      </span>
    </footer>
  )
}