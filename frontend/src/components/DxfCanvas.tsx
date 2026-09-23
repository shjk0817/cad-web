import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import * as THREE from 'three'
import { DxfViewer } from 'dxf-viewer'
import type { LayerInfo, ViewHud } from '../types'

// 暴露给父组件的视图操作
export interface DxfCanvasHandle {
  fitView: () => void
  zoomIn: () => void
  zoomOut: () => void
  setLayerVisible: (name: string, visible: boolean) => void
  getZoom: () => number
}

interface DxfCanvasProps {
  // 当前要加载的 DXF 地址（经 vite 代理的相对路径）
  url: string
  // 图纸加载完成，回传图层列表
  onLayersLoaded: (layers: LayerInfo[]) => void
  // 视图 HUD 状态变更：缩放 / 鼠标坐标
  onHudChange?: (hud: ViewHud) => void
}

// dxf-viewer 文字依赖 opentype 字体（TTF/OTF），未传字体时不渲染任何文字。
// 源绝对路径由 public/fonts 提供，浏览器缓存后所有图纸复用。
const FONT_URLS = ['/fonts/noto-sc-regular.ttf']

const DxfCanvas = forwardRef<DxfCanvasHandle, DxfCanvasProps>(function DxfCanvas(
  { url, onLayersLoaded, onHudChange },
  ref,
) {
  // (onHudChange 在下面用 ref 同步，最新值用于事件回调)
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<DxfViewer | null>(null)
  // 每次加载的序号，用于丢弃过期的加载结果
  const loadSeqRef = useRef(0)
  const mountedRef = useRef(true)
  const onLayersLoadedRef = useRef(onLayersLoaded)
  onLayersLoadedRef.current = onLayersLoaded
  const onHudChangeRef = useRef(onHudChange)
  onHudChangeRef.current = onHudChange

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // viewer 只创建一次，挂在容器 div 上
  useEffect(() => {
    mountedRef.current = true
    if (!containerRef.current) {
      return
    }
    const viewer = new DxfViewer(containerRef.current, {
      autoResize: true,
      clearColor: new THREE.Color('#1f2937'),
      antialias: true,
      // 黑底白字模式下由 dxf-viewer 自动反相；保持关闭，使图层颜色与原图一致。
      blackWhiteInversion: false,
    })
    viewerRef.current = viewer
    return () => {
      mountedRef.current = false
      viewer.Destroy()
      viewerRef.current = null
    }
  }, [])

  // 切换图纸：重新 Load（Load 内部会先 Clear）
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) {
      return
    }
    const seq = ++loadSeqRef.current
    setLoading(true)
    setError(null)

    viewer
      .Load({ url, fonts: FONT_URLS })
      .then(() => {
        if (!mountedRef.current || seq !== loadSeqRef.current) {
          return
        }
        const layers: LayerInfo[] = []
        for (const info of viewer.GetLayers()) {
          layers.push({
            name: info.name,
            displayName: info.displayName,
            color: info.color,
          })
        }
        onLayersLoadedRef.current(layers)
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!mountedRef.current || seq !== loadSeqRef.current) {
          return
        }
        const message =
          e instanceof Error ? e.message : '图纸加载失败，请稍后重试'
        setError(message)
        setLoading(false)
      })
  }, [url])

  // 自适应铺满当前图纸范围
  const fitView = useCallback(() => {
    const viewer = viewerRef.current
    if (!viewer) {
      return
    }
    const b = viewer.GetBounds()
    if (!b) {
      return
    }
    const origin = viewer.GetOrigin()
    viewer.FitView(b.minX - origin.x, b.maxX - origin.x, b.minY - origin.y, b.maxY - origin.y)
  }, [])

  // 以当前视图中心为基点缩放，factor < 1 放大，> 1 缩小
  const zoomBy = useCallback((factor: number) => {
    const viewer = viewerRef.current
    if (!viewer) {
      return
    }
    const cam = viewer.GetCamera()
    const width = (cam.right - cam.left) * factor
    viewer.SetView(
      new THREE.Vector3(cam.position.x, cam.position.y, cam.position.z),
      width,
    )
  }, [])

  // 计算当前相机对应的世界宽度，用于估算缩放比例（1 = 标准）。
  // 此处粗略用 200 世界单位的参考宽度，可显示为百分比。
  const computeZoom = useCallback((): number => {
    const viewer = viewerRef.current
    if (!viewer) {
      return 1
    }
    const cam = viewer.GetCamera()
    const worldWidth = cam.right - cam.left
    // 1.0 = 200 世界单位，>1 放大，<1 缩小
    return worldWidth > 0 ? 200 / worldWidth : 1
  }, [])

  useImperativeHandle(ref, () => ({
    fitView,
    zoomIn: () => zoomBy(0.8),
    zoomOut: () => zoomBy(1.25),
    setLayerVisible: (name, visible) => {
      viewerRef.current?.ShowLayer(name, visible)
    },
    getZoom: () => computeZoom(),
  }), [fitView, zoomBy, computeZoom])

  // 监听容器内鼠标与滚轮，向父组件回传 HUD 状态
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    let lastCursor: { x: number; y: number } | null = null
    const emit = () => {
      onHudChangeRef.current?.({
        zoom: computeZoom(),
        cursor: lastCursor,
      })
    }
    const onMove = (e: MouseEvent) => {
      const rect = el.getBoundingClientRect()
      lastCursor = { x: e.clientX - rect.left, y: e.clientY - rect.top }
      emit()
    }
    const onLeave = () => {
      lastCursor = null
      emit()
    }
    el.addEventListener('mousemove', onMove)
    el.addEventListener('mouseleave', onLeave)
    // wheel/click 后重发一次缩放
    el.addEventListener('wheel', emit, { passive: true })
    el.addEventListener('click', emit)
    return () => {
      el.removeEventListener('mousemove', onMove)
      el.removeEventListener('mouseleave', onLeave)
      el.removeEventListener('wheel', emit)
      el.removeEventListener('click', emit)
    }
  }, [computeZoom])

  return (
    <div className="dxf-canvas" ref={containerRef}>
      {loading && (
        <div className="dxf-overlay">
          <div className="spinner" />
          <span>正在加载图纸…</span>
        </div>
      )}
      {!loading && error && (
        <div className="dxf-overlay dxf-overlay-error">
          <span className="dxf-overlay-title">图纸加载失败</span>
          <span className="dxf-overlay-detail">{error}</span>
        </div>
      )}
    </div>
  )
})

export default DxfCanvas
