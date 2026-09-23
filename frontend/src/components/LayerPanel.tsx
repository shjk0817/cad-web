import { useMemo, useState } from 'react'
import type { LayerInfo } from '../types'

interface LayerPanelProps {
  layers: LayerInfo[]
  hidden: Set<string>
  onToggle: (name: string) => void
  // 隔离显示：仅显示当前图层
  onSolo?: (name: string) => void
  // 仅显示当前图层（其它不变）
  onOnly?: (name: string) => void
}

// 将 dxf-viewer 返回的 RGB 数值转成 CSS 颜色
function toCssColor(color: number): string {
  return `#${color.toString(16).padStart(6, '0')}`
}

// 图层列表面板：搜索 + 批量工具 + 行内操作（仅显示/隔离）
export default function LayerPanel({ layers, hidden, onToggle, onSolo, onOnly }: LayerPanelProps) {
  const [keyword, setKeyword] = useState('')

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    if (!kw) return layers
    return layers.filter(
      (l) => l.displayName.toLowerCase().includes(kw) || l.name.toLowerCase().includes(kw),
    )
  }, [layers, keyword])

  const allShown = layers.length > 0 && hidden.size === 0
  const total = layers.length

  const toggleAll = () => {
    if (allShown) {
      // 全部隐藏
      layers.forEach((l) => {
        if (!hidden.has(l.name)) onToggle(l.name)
      })
    } else {
      // 全部显示
      layers.forEach((l) => {
        if (hidden.has(l.name)) onToggle(l.name)
      })
    }
  }

  return (
    <section className="layerpanel">
      <header className="side-section-header">
        <h2 className="side-title">图层</h2>
        <span className="side-count">{total - hidden.size}/{total}</span>
      </header>

      <div className="layer-tools">
        <button type="button" className="layer-tool-btn" onClick={toggleAll}>
          {allShown ? '全部隐藏' : '全部显示'}
        </button>
      </div>

      <div className="layer-search">
        <span className="ico">⌕</span>
        <input
          type="text"
          placeholder="搜索图层名"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </div>

      <div className="layerpanel-body">
        {layers.length === 0 ? (
          <p className="side-empty">当前图纸暂无图层</p>
        ) : filtered.length === 0 ? (
          <p className="side-empty">没有匹配的图层</p>
        ) : (
          <ul className="layer-list">
            {filtered.map((layer) => {
              const visible = !hidden.has(layer.name)
              return (
                <li key={layer.name}>
                  <label
                    className="layer-row"
                    title={layer.displayName}
                    onDoubleClick={() => onSolo?.(layer.name)}
                  >
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={() => onToggle(layer.name)}
                    />
                    <span
                      className="layer-color"
                      style={{ backgroundColor: toCssColor(layer.color) }}
                    />
                    <span className={`layer-name ${visible ? '' : 'muted'}`}>
                      {layer.displayName}
                    </span>
                    <span className="layer-actions">
                      <button
                        type="button"
                        className="layer-action"
                        title="仅显示此项"
                        onClick={(e) => {
                          e.preventDefault()
                          onOnly?.(layer.name)
                        }}
                      >
                        ◉
                      </button>
                      <button
                        type="button"
                        className="layer-action"
                        title="隔离显示"
                        onClick={(e) => {
                          e.preventDefault()
                          onSolo?.(layer.name)
                        }}
                      >
                        ⌖
                      </button>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}