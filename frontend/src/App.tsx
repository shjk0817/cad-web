import { useCallback, useEffect, useRef, useState } from 'react'
import { sheetUrl, uploadDwg } from './api'
import type {
  AuditTask,
  FileItem,
  LayerInfo,
  Sheet,
  TaskProgress,
  ToastMessage,
  ToastTone,
  ViewHud,
} from './types'
import Topbar from './components/Topbar'
import FileList from './components/FileList'
import SheetList from './components/SheetList'
import LayerPanel from './components/LayerPanel'
import DxfCanvas, { type DxfCanvasHandle } from './components/DxfCanvas'
import StatusBar from './components/StatusBar'
import ViewportHud from './components/ViewportHud'
import ToastHost from './components/ToastHost'
import AIDrawer from './components/AIDrawer'
import AuditReportPage from './pages/AuditReportPage'
import './App.css'

// 简易 hash 路由；约定：# → 主界面，#/audit/<taskId> → 复核报告页
function readRoute(): { page: 'main' | 'audit'; taskId: string } {
  const h = window.location.hash || ''
  const m = h.match(/^#\/audit\/([^/?#]+)/)
  if (m) return { page: 'audit', taskId: decodeURIComponent(m[1]) }
  return { page: 'main', taskId: '' }
}

export default function App() {
  const [route, setRoute] = useState(() => readRoute())
  useEffect(() => {
    const onHash = () => setRoute(readRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const [files, setFiles] = useState<FileItem[]>([])
  const [activeUid, setActiveUid] = useState<string | null>(null)
  const [activeSheet, setActiveSheet] = useState<Sheet | null>(null)
  const [layers, setLayers] = useState<LayerInfo[]>([])
  const [hiddenLayers, setHiddenLayers] = useState<Set<string>>(new Set())
  const [dragActive, setDragActive] = useState(false)
  const [showLayers, setShowLayers] = useState(true)
  const [aiOpen, setAiOpen] = useState(false)
  const [toasts, setToasts] = useState<ToastMessage[]>([])
  const [hud, setHud] = useState<ViewHud>({ zoom: 1, cursor: null })

  const canvasRef = useRef<DxfCanvasHandle>(null)
  const dragDepthRef = useRef(0)
  const filesRef = useRef<FileItem[]>([])
  useEffect(() => {
    filesRef.current = files
  }, [files])

  const activeFile: FileItem | null =
    files.find((f) => f.uid === activeUid) ?? null
  const parsingCount = files.filter(
    (f) => f.status === 'uploading' || f.status === 'parsing',
  ).length

  // ========== Toast ==========
  const pushToast = useCallback((tone: ToastTone, text: string) => {
    const id =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`
    setToasts((prev) => [...prev, { id, tone, text }])
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 3200)
  }, [])

  // ========== 文件条目更新 ==========
  const updateFile = useCallback(
    (uid: string, patch: Partial<FileItem>) => {
      setFiles((prev) =>
        prev.map((item) => (item.uid === uid ? { ...item, ...patch } : item)),
      )
    },
    [],
  )

  // ========== 上传 ==========
  const uploadOne = useCallback(
    (file: File, uid: string) => {
      updateFile(uid, { status: 'parsing', progress: 0, stageText: '准备上传' })

      const onProgress = (p: TaskProgress) => {
        const pct = Math.max(0, Math.min(100, Math.round(p.progress * 100)))
        updateFile(uid, {
          status: 'parsing',
          progress: pct,
          stageText: p.message || p.stage,
        })
      }

      uploadDwg(file, onProgress)
        .then((data) => {
          updateFile(uid, {
            status: 'ready',
            progress: 100,
            fileId: data.fileId,
            sheets: data.sheets,
            stageText: '完成',
          })
          setActiveUid(uid)
          if (data.sheets.length > 0) {
            setActiveSheet(data.sheets[0])
          }
          pushToast('success', `「${file.name}」解析完成（${data.sheets.length} 张图纸）`)
          if (data.cached) {
            pushToast('info', `「${file.name}」命中缓存，未重复解析`)
          }
        })
        .catch((e: unknown) => {
          updateFile(uid, {
            status: 'error',
            error: e instanceof Error ? e.message : '上传失败',
            stageText: '失败',
          })
          pushToast('error', e instanceof Error ? e.message : '上传失败')
        })
    },
    [updateFile, pushToast],
  )

  const handleFiles = useCallback(
    (incoming: File[]) => {
      const dwgs = incoming.filter((f) =>
        f.name.toLowerCase().endsWith('.dwg'),
      )
      if (dwgs.length === 0) {
        pushToast('warn', '仅支持 .dwg 文件')
        return
      }
      if (dwgs.length !== incoming.length) {
        pushToast('warn', `已忽略 ${incoming.length - dwgs.length} 个非 DWG 文件`)
      }

      const seen = new Set(
        filesRef.current.map((f) => `${f.name}|${f.size}`),
      )
      const additions: FileItem[] = []
      for (const file of dwgs) {
        const key = `${file.name}|${file.size}`
        if (seen.has(key)) {
          continue
        }
        seen.add(key)
        const uid =
          typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`
        additions.push({
          uid,
          name: file.name,
          size: file.size,
          status: 'uploading',
          progress: 0,
          stageText: '排队中',
        })
      }

      if (additions.length === 0) {
        pushToast('info', '这些文件已在列表中，无需重复上传')
        return
      }

      setFiles((prev) => [...prev, ...additions])

      additions.forEach((addition) => {
        const file = dwgs.find(
          (f) => f.name === addition.name && f.size === addition.size,
        )
        if (file) {
          uploadOne(file, addition.uid)
        }
      })
    },
    [uploadOne, pushToast],
  )

  const handleSelectFile = useCallback((file: FileItem) => {
    setActiveUid(file.uid)
    setLayers([])
    setHiddenLayers(new Set())
    setActiveSheet(file.sheets && file.sheets.length > 0 ? file.sheets[0] : null)
  }, [])

  const handleSelectSheet = useCallback((sheet: Sheet) => {
    setActiveSheet(sheet)
  }, [])

  const handleLayersLoaded = useCallback((loaded: LayerInfo[]) => {
    setLayers(loaded)
    setHiddenLayers(new Set())
  }, [])

  const handleToggleLayer = useCallback((name: string) => {
    setHiddenLayers((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
    const visible = !hiddenLayers.has(name)
    canvasRef.current?.setLayerVisible(name, visible)
  }, [hiddenLayers])

  // 仅显示此项：先把全部隐藏，再显示目标
  const handleOnlyLayer = useCallback(
    (name: string) => {
      const next = new Set(layers.map((l) => l.name))
      next.delete(name)
      setHiddenLayers(next)
      layers.forEach((l) => {
        canvasRef.current?.setLayerVisible(l.name, l.name === name)
      })
    },
    [layers],
  )

  // 隔离显示：清空隐藏集，仅显示目标
  const handleSoloLayer = useCallback(
    (name: string) => {
      setHiddenLayers(new Set())
      layers.forEach((l) => {
        canvasRef.current?.setLayerVisible(l.name, l.name === name)
      })
    },
    [layers],
  )

  // 全局拖拽
  useEffect(() => {
    const hasFiles = (e: DragEvent) =>
      Array.from(e.dataTransfer?.types ?? []).includes('Files')

    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      dragDepthRef.current += 1
      setDragActive(true)
    }
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
    }
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
      if (dragDepthRef.current === 0) setDragActive(false)
    }
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      dragDepthRef.current = 0
      setDragActive(false)
      const dropped = Array.from(e.dataTransfer?.files ?? [])
      if (dropped.length > 0) handleFiles(dropped)
    }

    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [handleFiles])

  const fileId = activeFile?.fileId ?? null
  const dxfUrl = fileId && activeSheet ? sheetUrl(fileId, activeSheet.id) : null

  // 顶层上传触发：通过隐藏的 input 点击
  const topInputRef = useRef<HTMLInputElement>(null)
  const handleTopUpload = () => topInputRef.current?.click()

  // 解析中的整体进度
  const parsingFiles = files.filter(
    (f) => f.status === 'uploading' || f.status === 'parsing',
  )
  const overallProgress =
    parsingFiles.length === 0
      ? 0
      : Math.round(
          parsingFiles.reduce(
            (sum, f) => sum + (typeof f.progress === 'number' ? f.progress : 0),
            0,
          ) / parsingFiles.length,
        )
  const currentStageText =
    parsingFiles[0]?.stageText ?? (parsingCount > 0 ? '准备解析…' : '')

  return (
    <div className="app">
      {route.page === 'audit' ? (
        <AuditReportPage taskId={route.taskId} />
      ) : (
        <>
          <Topbar
            busy={parsingCount > 0}
            fileName={activeFile?.name ?? null}
            sheetName={activeSheet?.name ?? null}
            canFitView={Boolean(dxfUrl)}
            showLayers={showLayers}
            aiOpen={aiOpen}
            onFiles={handleFiles}
            onSelectFile={() => activeFile && handleSelectFile(activeFile)}
            onFitView={() => canvasRef.current?.fitView()}
            onToggleLayers={() => setShowLayers((s) => !s)}
            onToggleAi={() => setAiOpen((o) => !o)}
          />
          <input
            ref={topInputRef}
            type="file"
            accept=".dwg"
            multiple
            className="file-input"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? [])
              if (files.length > 0) handleFiles(files)
              e.target.value = ''
            }}
          />

          <div className="main">
            <aside className="sidebar">
              <FileList
                files={files}
                activeUid={activeUid}
                onSelect={handleSelectFile}
                onUploadClick={handleTopUpload}
              />
              {activeFile &&
                (activeFile.sheets && activeFile.sheets.length > 0 ? (
                  <SheetList
                    sheets={activeFile.sheets}
                    activeId={activeSheet?.id ?? null}
                    onSelect={handleSelectSheet}
                  />
                ) : (
                  <section className="sidebar-section">
                    <header className="side-section-header">
                      <h2 className="side-title">图纸清单</h2>
                    </header>
                    <p className="side-empty">
                      {activeFile.status === 'error'
                        ? '解析失败，请重新上传'
                        : activeFile.status === 'parsing'
                          ? `解析中… ${activeFile.progress ?? 0}%`
                          : '未解析出图纸'}
                    </p>
                    {activeFile.status === 'parsing' && (
                      <div className="file-progress">
                        <div
                          className="file-progress-bar"
                          style={{ width: `${activeFile.progress ?? 0}%` }}
                        />
                      </div>
                    )}
                  </section>
                ))}
            </aside>

            <section className="viewport-wrap">
              <div className="viewport">
                {dxfUrl ? (
                  <>
                    <DxfCanvas
                      ref={canvasRef}
                      url={dxfUrl}
                      onLayersLoaded={handleLayersLoaded}
                      onHudChange={setHud}
                    />
                    <ViewportHud
                      hud={hud}
                      selectedLayerCount={hiddenLayers.size}
                      totalLayers={layers.length}
                    />
                  </>
                ) : null}

                {files.length === 0 && !dxfUrl && (
                  <div className="state-overlay">
                    <p className="state-title">拖入 DWG 文件，或点击左上角「选择 DWG」</p>
                    <p className="state-hint">
                      支持批量上传与多图纸 DWG，上传后可在左侧切换文件、图纸与图层
                    </p>
                    <button
                      type="button"
                      className="tool-btn primary"
                      onClick={handleTopUpload}
                      style={{ marginTop: 12 }}
                    >
                      ＋ 选择 DWG 文件
                    </button>
                  </div>
                )}

                {parsingCount > 0 && !dxfUrl && (
                  <div className="state-overlay state-overlay-mask">
                    <div className="spinner" />
                    <p className="state-title">
                      {parsingCount > 1
                        ? `正在解析 ${parsingCount} 个文件…`
                        : '正在解析…'}
                    </p>
                    <p className="state-hint">{currentStageText}</p>
                    <div className="progress">
                      <div
                        className="progress-bar"
                        style={{ width: `${overallProgress}%` }}
                      />
                    </div>
                    <p className="state-progress-text">{overallProgress}%</p>
                  </div>
                )}
              </div>

              <StatusBar
                fileCount={files.length}
                parsingCount={parsingCount}
                hiddenLayerCount={hiddenLayers.size}
                totalLayers={layers.length}
                ready={Boolean(dxfUrl)}
              />
            </section>

            {showLayers && (
              <LayerPanel
                layers={layers}
                hidden={hiddenLayers}
                onToggle={handleToggleLayer}
                onSolo={handleSoloLayer}
                onOnly={handleOnlyLayer}
              />
            )}
          </div>

          <AIDrawer
            open={aiOpen}
            fileId={fileId}
            sheet={activeSheet}
            onClose={() => setAiOpen(false)}
            onToast={pushToast}
            onAuditDone={(task: AuditTask) => {
              window.location.hash = `#/audit/${encodeURIComponent(task.id)}`
            }}
          />
        </>
      )}

      {dragActive && (
        <div className="drop-mask">
          <div className="drop-hint">
            <span className="ico">⤓</span>
            <span>松开鼠标以上传 DWG 文件</span>
            <span className="drop-hint-sub">支持批量多选</span>
          </div>
        </div>
      )}

      <ToastHost toasts={toasts} />
    </div>
  )
}