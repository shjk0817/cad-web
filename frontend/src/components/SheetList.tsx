import type { Sheet } from '../types'

interface SheetListProps {
  sheets: Sheet[]
  activeId: string | null
  onSelect: (sheet: Sheet) => void
}

// 尺寸数字去掉无意义的 .0
function formatSize(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

// 图纸缩略图列表：含序号 + 选中环
export default function SheetList({ sheets, activeId, onSelect }: SheetListProps) {
  return (
    <section className="sidebar-section flex">
      <header className="side-section-header">
        <h2 className="side-title">图纸清单</h2>
        <span className="side-count">{sheets.length}</span>
      </header>
      <div className="side-body">
        {sheets.length === 0 ? (
          <p className="side-empty">暂无图纸</p>
        ) : (
          <ul className="sheet-list">
            {sheets.map((sheet, idx) => (
              <li key={sheet.id}>
                <button
                  type="button"
                  className={`sheet-item ${sheet.id === activeId ? 'active' : ''}`}
                  onClick={() => onSelect(sheet)}
                  title={sheet.name}
                >
                  <div className="file-row">
                    <span className="sheet-num">{idx + 1}</span>
                    <span className="sheet-name">{sheet.name}</span>
                  </div>
                  <span className="sheet-meta">
                    <span>
                      {formatSize(sheet.width)} × {formatSize(sheet.height)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}