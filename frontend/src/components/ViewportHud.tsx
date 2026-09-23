import type { ViewHud } from '../types'

interface ViewportHudProps {
  hud: ViewHud
  selectedLayerCount: number
  totalLayers: number
}

// 画布右下角浮层：缩放 / 鼠标坐标 / 图层
export default function ViewportHud({ hud, selectedLayerCount, totalLayers }: ViewportHudProps) {
  return (
    <div className="viewport-hud">
      <span>
        <span className="hud-key">缩放</span>
        <span className="hud-val">{(hud.zoom * 100).toFixed(0)}%</span>
      </span>
      <span className="hud-sep" />
      <span>
        <span className="hud-key">坐标</span>
        <span className="hud-val">
          {hud.cursor
            ? `X ${hud.cursor.x.toFixed(2)} · Y ${hud.cursor.y.toFixed(2)}`
            : '—'}
        </span>
      </span>
      <span className="hud-sep" />
      <span>
        <span className="hud-key">图层</span>
        <span className="hud-val">
          {totalLayers === 0 ? '—' : `${totalLayers - selectedLayerCount}/${totalLayers}`}
        </span>
      </span>
    </div>
  )
}