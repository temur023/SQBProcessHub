import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import {
  ZoomIn, ZoomOut, Maximize2, Minimize2,
  Cpu, AlertTriangle, Info, Search, RotateCcw, Grid,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { BusinessProcess, ProcessNode } from '@/types/process'

interface ProcessVisualizerProps {
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
  selectedNodeId?: string
}

const GRID_MINOR = 10
const GRID_MAJOR = 100
const MIN_ZOOM = 0.12
const MAX_ZOOM = 3.0
const LANE_HEAD = 44
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif'

const C = {
  canvas: '#1a1a1a',
  gridMinor: 'rgba(255,255,255,0.05)',
  gridMajor: 'rgba(255,255,255,0.12)',
  laneLine: 'rgba(255,255,255,0.42)',
  laneHead: '#0f0f0f',
  laneText: '#f3f3f3',
  taskFill: '#141414',
  taskStroke: '#f2f2f2',
  taskText: '#f7f7f7',
  rpaFill: '#101c16',
  rpaStroke: '#3dd68c',
  badFill: '#231c0c',
  badStroke: '#e8b84a',
  edge: '#dedede',
  edgeHi: '#7db7ff',
  labelBg: '#1a1a1a',
  gwStroke: '#e6b422',
  start: '#f3f3f3',
  endOk: '#5ee08a',
  endNo: '#ff6b6b',
}

function snap(v: number) { return Math.round(v / GRID_MINOR) * GRID_MINOR }

type Box = { x: number; y: number; w: number; h: number; cx: number; cy: number }

function rawBox(n: ProcessNode): Box {
  const w = Math.max(n.geometry.width || 40, 24)
  const h = Math.max(n.geometry.height || 40, 24)
  return {
    x: n.geometry.x,
    y: n.geometry.y,
    w,
    h,
    cx: n.geometry.x + w / 2,
    cy: n.geometry.y + h / 2,
  }
}

function laneOf(node: ProcessNode, lanes: ProcessNode[]): ProcessNode | undefined {
  if (node.laneId) {
    const hit = lanes.find((l) => l.id === node.laneId)
    if (hit) return hit
  }
  const cx = node.geometry.x + (node.geometry.width || 0) / 2
  const cy = node.geometry.y + (node.geometry.height || 0) / 2
  return lanes.find((l) =>
    cx >= l.geometry.x &&
    cx <= l.geometry.x + l.geometry.width &&
    cy >= l.geometry.y &&
    cy <= l.geometry.y + l.geometry.height,
  )
}

function isEventNode(n: ProcessNode): boolean {
  return n.type === 'startEvent' || n.type === 'endEvent'
}

/** Keep event circles inside the lane body so they never sit on the title column. */
function displayBox(n: ProcessNode, lanes: ProcessNode[]): Box {
  const b = rawBox(n)
  if (!isEventNode(n)) return b
  const lane = laneOf(n, lanes)
  if (!lane) return b
  const pad = 8
  const minX = lane.geometry.x + LANE_HEAD + pad
  const maxX = lane.geometry.x + lane.geometry.width - b.w - pad
  const minY = lane.geometry.y + pad
  const maxY = lane.geometry.y + lane.geometry.height - b.h - pad
  let { x, y } = b
  if (x < minX) x = minX
  if (maxX >= minX && x > maxX) x = maxX
  if (y < minY) y = minY
  if (maxY >= minY && y > maxY) y = maxY
  return { x, y, w: b.w, h: b.h, cx: x + b.w / 2, cy: y + b.h / 2 }
}

function edgePath(
  a: Box,
  b: Box,
  pts: { x: number; y: number }[],
): { d: string; lx: number; ly: number } {
  if (pts && pts.length >= 2) {
    const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
    const mid = pts[Math.floor(pts.length / 2)]
    return { d, lx: mid.x, ly: mid.y - 8 }
  }

  const dx = b.cx - a.cx
  const dy = b.cy - a.cy
  const horiz = Math.abs(dx) >= Math.abs(dy) * 0.55

  let x1: number, y1: number, x2: number, y2: number
  if (horiz) {
    if (dx >= 0) { x1 = a.x + a.w; y1 = a.cy; x2 = b.x; y2 = b.cy }
    else { x1 = a.x; y1 = a.cy; x2 = b.x + b.w; y2 = b.cy }
  } else {
    if (dy >= 0) { x1 = a.cx; y1 = a.y + a.h; x2 = b.cx; y2 = b.y }
    else { x1 = a.cx; y1 = a.y; x2 = b.cx; y2 = b.y + b.h }
  }

  if (Math.abs(y1 - y2) < 2) {
    return { d: `M${x1},${y1} L${x2},${y2}`, lx: (x1 + x2) / 2, ly: y1 - 9 }
  }
  if (Math.abs(x1 - x2) < 2) {
    return { d: `M${x1},${y1} L${x2},${y2}`, lx: x1 + 9, ly: (y1 + y2) / 2 }
  }

  if (horiz) {
    const midX = snap((x1 + x2) / 2)
    return {
      d: `M${x1},${y1} L${midX},${y1} L${midX},${y2} L${x2},${y2}`,
      lx: midX,
      ly: (y1 + y2) / 2 - 9,
    }
  }
  const midY = snap((y1 + y2) / 2)
  return {
    d: `M${x1},${y1} L${x1},${midY} L${x2},${midY} L${x2},${y2}`,
    lx: (x1 + x2) / 2,
    ly: midY - 9,
  }
}

function wrapText(text: string, maxChars: number): string[] {
  if (!text) return []
  const words = text.split(/\s+/)
  const lines: string[] = []
  let cur = ''
  for (const w of words) {
    if ((cur + ' ' + w).trim().length <= maxChars) {
      cur = (cur + ' ' + w).trim()
    } else {
      if (cur) lines.push(cur)
      cur = w.length > maxChars ? w.slice(0, maxChars - 1) + '\u2026' : w
    }
  }
  if (cur) lines.push(cur)
  return lines
}

function fitBoxText(text: string, width: number, height: number, maxLines = 3): { lines: string[]; fontSize: number } {
  const innerW = Math.max(20, width - 14)
  const innerH = Math.max(14, height - 12)
  for (let fs = 12; fs >= 8; fs--) {
    const maxChars = Math.max(6, Math.floor(innerW / (fs * 0.56)))
    const lines = wrapText(text, maxChars).slice(0, maxLines)
    if (lines.length * (fs + 3) <= innerH) return { lines, fontSize: fs }
  }
  const fs = 8
  const maxChars = Math.max(6, Math.floor(innerW / (fs * 0.56)))
  return { lines: wrapText(text, maxChars).slice(0, maxLines), fontSize: fs }
}

function fitCaption(text: string, maxWidth: number, maxLines = 2): { lines: string[]; fontSize: number } {
  for (let fs = 10; fs >= 8; fs--) {
    const maxChars = Math.max(6, Math.floor(maxWidth / (fs * 0.56)))
    const lines = wrapText(text, maxChars).slice(0, maxLines)
    if (lines.every((l) => l.length * fs * 0.56 <= maxWidth)) return { lines, fontSize: fs }
  }
  const fs = 8
  const maxChars = Math.max(6, Math.floor(maxWidth / (fs * 0.56)))
  return { lines: wrapText(text, maxChars).slice(0, maxLines), fontSize: fs }
}

function fitLaneLabel(name: string, laneHeight: number): { lines: string[]; fontSize: number } {
  const full = name.replace(/\s+/g, ' ').trim()
  const avail = Math.max(48, laneHeight - 20)
  for (let fs = 13; fs >= 7; fs--) {
    if (full.length * fs * 0.56 <= avail) return { lines: [full], fontSize: fs }
  }
  const fs = 7
  const maxChars = Math.max(8, Math.floor(avail / (fs * 0.56)))
  const lines = wrapText(full, maxChars).slice(0, 2)
  return { lines, fontSize: fs }
}

function slaLabel(mins?: number): string {
  if (mins == null) return ''
  if (mins < 1) return `${mins} min`
  if (mins % 60 === 0 && mins >= 60) return `${mins / 60} ч`
  return `${mins} min`
}

export const ProcessVisualizer: React.FC<ProcessVisualizerProps> = ({
  process,
  onSelectNode,
  selectedNodeId,
}) => {
  const [zoom, setZoom]                 = useState(0.7)
  const [showGrid, setShowGrid]         = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [activeFilter, setActiveFilter] = useState<'all' | 'rpa' | 'bottlenecks'>('all')
  const [searchQuery, setSearchQuery]   = useState('')
  const [panPos, setPanPos]             = useState({ x: 24, y: 24 })
  const [isPanning, setIsPanning]       = useState(false)
  const panRef   = useRef({ ox: 0, oy: 0, px: 0, py: 0 })
  const wrapRef  = useRef<HTMLDivElement>(null)

  const bounds = useMemo(() => {
    let minX = Infinity
    let minY = Infinity
    let maxX = 0
    let maxY = 0
    const bump = (x: number, y: number, w: number, h: number) => {
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + w)
      maxY = Math.max(maxY, y + h)
    }
    for (const l of process.lanes) bump(l.geometry.x - 6, l.geometry.y - 10, l.geometry.width + 12, l.geometry.height + 20)
    for (const n of process.nodes) bump(n.geometry.x - 8, n.geometry.y - 22, (n.geometry.width || 160) + 16, (n.geometry.height || 70) + 40)
    if (!Number.isFinite(minX)) {
      minX = 0
      minY = 0
      maxX = 800
      maxY = 500
    }
    return { minX, minY, w: Math.max(maxX - minX, 200), h: Math.max(maxY - minY, 160) }
  }, [process])

  const laneLabels = useMemo(() => {
    const map = new Map<string, { lines: string[]; fontSize: number }>()
    for (const lane of process.lanes) {
      map.set(lane.id, fitLaneLabel(lane.name, lane.geometry.height))
    }
    return map
  }, [process.lanes])

  const nodeBoxes = useMemo(() => {
    const map = new Map<string, Box>()
    for (const n of process.nodes) map.set(n.id, displayBox(n, process.lanes))
    return map
  }, [process.nodes, process.lanes])

  const paintOrder = useMemo(
    () => [...process.nodes].sort((a, b) => Number(isEventNode(b)) - Number(isEventNode(a))),
    [process.nodes],
  )

  const visibleIds = useMemo(() => {
    let list = process.nodes
    if (activeFilter === 'rpa')         list = list.filter(n => n.category === 'rpa_bot')
    if (activeFilter === 'bottlenecks') list = list.filter(n => (n.slaMinutes || 0) >= 120)
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      list = list.filter(n =>
        n.name.toLowerCase().includes(q) ||
        (n.code   && n.code .toLowerCase().includes(q)) ||
        (n.role   && n.role .toLowerCase().includes(q)) ||
        (n.system && n.system.toLowerCase().includes(q)),
      )
    }
    return new Set(list.map(n => n.id))
  }, [process.nodes, activeFilter, searchQuery])

  const fitToScreen = useCallback(() => {
    if (!wrapRef.current) return
    const { width, height } = wrapRef.current.getBoundingClientRect()
    if (width < 40 || height < 40) return
    const pad = 28
    const s = Math.min((width - pad * 2) / bounds.w, (height - pad * 2) / bounds.h, 1)
    const z = +Math.max(s, MIN_ZOOM).toFixed(2)
    setZoom(z)
    setPanPos({
      x: (width  - bounds.w * z) / 2 - bounds.minX * z,
      y: (height - bounds.h * z) / 2 - bounds.minY * z,
    })
  }, [bounds])

  const onMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.nb')) return
    e.preventDefault()
    setIsPanning(true)
    panRef.current = { ox: e.clientX, oy: e.clientY, px: panPos.x, py: panPos.y }
  }
  const onMouseMove = (e: React.MouseEvent) => {
    if (!isPanning) return
    setPanPos({
      x: panRef.current.px + e.clientX - panRef.current.ox,
      y: panRef.current.py + e.clientY - panRef.current.oy,
    })
  }
  const onMouseUp = () => setIsPanning(false)

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') setIsFullscreen(false) }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [])

  useEffect(() => {
    let fitted = false
    const tryFit = () => {
      if (fitted || !wrapRef.current) return
      const { width, height } = wrapRef.current.getBoundingClientRect()
      if (width < 40 || height < 40) return
      fitToScreen()
      fitted = true
    }
    const raf = requestAnimationFrame(tryFit)
    const el = wrapRef.current
    const ro = el ? new ResizeObserver(tryFit) : null
    if (el && ro) ro.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [fitToScreen, process.id, isFullscreen])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const handler = (e: WheelEvent) => {
      e.preventDefault()
      const delta = e.deltaY > 0 ? -0.08 : 0.08
      setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, +(z + delta).toFixed(2))))
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [])

  const patternOffset = {
    x: ((panPos.x % (GRID_MAJOR * zoom)) + GRID_MAJOR * zoom) % (GRID_MAJOR * zoom),
    y: ((panPos.y % (GRID_MAJOR * zoom)) + GRID_MAJOR * zoom) % (GRID_MAJOR * zoom),
  }

  return (
    <div className={`flex flex-col bg-[#141414] rounded-xl border border-zinc-800 shadow-sm overflow-hidden transition-all duration-200 ${
      isFullscreen ? 'fixed inset-0 z-50 rounded-none border-none' : 'flex-1 min-h-0 h-full'
    }`}>
      <div className="px-3 py-2 border-b border-zinc-800 bg-[#121212] flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-bold text-zinc-200 uppercase tracking-wide">BPMN Карта</span>
          <span className="text-[10px] text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded-full">
            {process.nodes.length} эл. · {process.lanes.length} дорожек
          </span>
          <div className="h-4 w-px bg-zinc-700 mx-1" />
          {(['all', 'rpa', 'bottlenecks'] as const).map(f => (
            <button key={f} onClick={() => setActiveFilter(f)}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded transition-colors ${
                activeFilter === f
                  ? f === 'rpa' ? 'bg-emerald-600 text-white'
                  : f === 'bottlenecks' ? 'bg-amber-500 text-white'
                  : 'bg-zinc-200 text-zinc-900'
                  : 'border border-zinc-700 hover:bg-zinc-800 text-zinc-400'
              }`}>
              {f === 'all' && `Все (${process.nodes.length})`}
              {f === 'rpa' && <><Cpu className="w-3 h-3 mr-0.5" />RPA ({process.nodes.filter(n=>n.category==='rpa_bot').length})</>}
              {f === 'bottlenecks' && <><AlertTriangle className="w-3 h-3 mr-0.5" />SLA</>}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-500" />
            <Input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
              placeholder="Поиск..." className="pl-6 h-7 text-xs w-32 bg-zinc-900 border-zinc-700 text-zinc-200" />
          </div>
          <div className="flex items-center gap-1 border-l border-zinc-700 pl-2">
            <Button variant={showGrid ? 'secondary' : 'ghost'} size="icon" className="h-7 w-7 text-zinc-300"
              onClick={() => setShowGrid(v => !v)} title="Сетка 10px">
              <Grid className="w-3.5 h-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7 text-zinc-300"
              onClick={() => setZoom(z => Math.max(MIN_ZOOM, +(z-0.15).toFixed(2)))}>
              <ZoomOut className="w-3.5 h-3.5" />
            </Button>
            <span className="text-[11px] w-9 text-center font-mono text-zinc-400">{Math.round(zoom*100)}%</span>
            <Button variant="ghost" size="icon" className="h-7 w-7 text-zinc-300"
              onClick={() => setZoom(z => Math.min(MAX_ZOOM, +(z+0.15).toFixed(2)))}>
              <ZoomIn className="w-3.5 h-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7 text-zinc-300" onClick={fitToScreen} title="Вписать">
              <RotateCcw className="w-3.5 h-3.5" />
            </Button>
            <Button variant={isFullscreen ? 'default' : 'ghost'} size="sm" className="h-7 gap-1 text-xs px-2 text-zinc-200"
              onClick={() => setIsFullscreen(v => !v)}>
              {isFullscreen ? <><Minimize2 className="w-3.5 h-3.5" />Свернуть</> : <><Maximize2 className="w-3.5 h-3.5" />На весь экран</>}
            </Button>
          </div>
        </div>
      </div>

      <div
        ref={wrapRef}
        className={`relative flex-1 min-h-0 overflow-hidden ${isPanning ? 'cursor-grabbing' : 'cursor-grab'}`}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <svg
          width="100%"
          height="100%"
          className="absolute inset-0 block"
          style={{ fontFamily: FONT }}
        >
          <defs>
            <pattern id="g-minor" width={GRID_MINOR * zoom} height={GRID_MINOR * zoom} patternUnits="userSpaceOnUse"
              x={patternOffset.x} y={patternOffset.y}>
              <path d={`M ${GRID_MINOR * zoom} 0 L 0 0 0 ${GRID_MINOR * zoom}`}
                fill="none" stroke={C.gridMinor} strokeWidth="1" />
            </pattern>
            <pattern id="g-major" width={GRID_MAJOR * zoom} height={GRID_MAJOR * zoom} patternUnits="userSpaceOnUse"
              x={patternOffset.x} y={patternOffset.y}>
              <rect width={GRID_MAJOR * zoom} height={GRID_MAJOR * zoom} fill="url(#g-minor)" />
              <path d={`M ${GRID_MAJOR * zoom} 0 L 0 0 0 ${GRID_MAJOR * zoom}`}
                fill="none" stroke={C.gridMajor} strokeWidth="1" />
            </pattern>
            <marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill={C.edge} />
            </marker>
            <marker id="arr-hi" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill={C.edgeHi} />
            </marker>
            {process.nodes.map((node) => {
              const b = nodeBoxes.get(node.id) || rawBox(node)
              return (
                <clipPath key={`clip-${node.id}`} id={`clip-${node.id}`}>
                  <rect
                    x={b.x + 6}
                    y={b.y + 5}
                    width={Math.max(8, b.w - 12)}
                    height={Math.max(8, b.h - 10)}
                    rx={6}
                  />
                </clipPath>
              )
            })}
          </defs>

          <rect width="100%" height="100%" fill={C.canvas} />
          {showGrid && <rect width="100%" height="100%" fill="url(#g-major)" />}

          <g transform={`translate(${panPos.x},${panPos.y}) scale(${zoom})`}>
            {process.lanes.map((lane) => (
              <rect
                key={`body-${lane.id}`}
                x={lane.geometry.x} y={lane.geometry.y}
                width={lane.geometry.width} height={lane.geometry.height}
                fill="none" stroke={C.laneLine} strokeWidth="1"
              />
            ))}

            {process.edges.map(edge => {
              const src = process.nodes.find(n => n.id === edge.sourceId)
              const tgt = process.nodes.find(n => n.id === edge.targetId)
              if (!src || !tgt) return null
              const sb = nodeBoxes.get(src.id) || rawBox(src)
              const tb = nodeBoxes.get(tgt.id) || rawBox(tgt)
              const { d, lx, ly } = edgePath(sb, tb, edge.points || [])
              const hi = Boolean(selectedNodeId && (edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId))
              const raw = (edge.name || '').trim()
              const cap = raw ? fitCaption(raw, 90, 1) : null
              const lw = cap ? Math.min(96, Math.max(28, cap.lines[0].length * cap.fontSize * 0.58 + 8)) : 0
              return (
                <g key={edge.id}>
                  <path d={d} fill="none"
                    stroke={hi ? C.edgeHi : C.edge}
                    strokeWidth={hi ? 2 : 1.25}
                    strokeLinejoin="round"
                    markerEnd={hi ? 'url(#arr-hi)' : 'url(#arr)'} />
                  {cap && (
                    <>
                      <rect x={lx - lw / 2} y={ly - 7} width={lw} height={14} rx={2}
                        fill={C.labelBg} stroke="#3a3a3a" strokeWidth="0.7" />
                      <text x={lx} y={ly + 3} textAnchor="middle"
                        fontSize={cap.fontSize} fill="#e8e8e8"
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {cap.lines[0]}
                      </text>
                    </>
                  )}
                </g>
              )
            })}

            {process.nodes.map(node => {
              const faded = !visibleIds.has(node.id)
              const sel   = selectedNodeId === node.id
              const isRpa = node.category === 'rpa_bot'
              const isBad = (node.slaMinutes || 0) >= 120
              const isRej = node.id.toLowerCase().includes('reject') ||
                            node.name.toLowerCase().includes('отказ') ||
                            node.name.toLowerCase().includes('rad etildi')
              const b = nodeBoxes.get(node.id) || rawBox(node)
              const { x, y, w, h, cx, cy } = b
              const home = laneOf(node, process.lanes)

              if (node.type === 'startEvent') {
                const r = Math.max(10, Math.min(w, h) / 2 - 1)
                const cap = fitCaption(node.name, 90, 2)
                const labelRight = Boolean(home && x <= home.geometry.x + LANE_HEAD + 16)
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : C.start} strokeWidth={1.8} />
                    {cap.lines.map((line, i) => (
                      <text key={i}
                        x={labelRight ? x + w + 6 : cx}
                        y={labelRight ? cy - ((cap.lines.length - 1) * (cap.fontSize + 2)) / 2 + i * (cap.fontSize + 2) + 3 : y + h + 11 + i * (cap.fontSize + 2)}
                        textAnchor={labelRight ? 'start' : 'middle'}
                        fontSize={cap.fontSize} fill="#d8d8d8"
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              if (node.type === 'endEvent') {
                const r = Math.max(10, Math.min(w, h) / 2 - 1)
                const sc = isRej ? C.endNo : C.endOk
                const cap = fitCaption(node.name, 90, 2)
                const labelLeft = Boolean(home && x + w >= home.geometry.x + home.geometry.width - 20)
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : sc} strokeWidth={3.4} />
                    <circle cx={cx} cy={cy} r={Math.max(4, r - 6)} fill="none" stroke={sel ? C.edgeHi : sc} strokeWidth="1.4" />
                    {cap.lines.map((line, i) => (
                      <text key={i}
                        x={labelLeft ? x - 6 : cx}
                        y={labelLeft ? cy - ((cap.lines.length - 1) * (cap.fontSize + 2)) / 2 + i * (cap.fontSize + 2) + 3 : y + h + 11 + i * (cap.fontSize + 2)}
                        textAnchor={labelLeft ? 'end' : 'middle'}
                        fontSize={cap.fontSize} fill={isRej ? C.endNo : C.endOk}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              if (node.type === 'exclusiveGateway' || node.type === 'parallelGateway' || node.type === 'inclusiveGateway') {
                const s = Math.max(16, Math.min(w, h) / 2)
                const cap = node.name && node.name !== 'Условие' ? fitCaption(node.name, 92, 2) : null
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <polygon points={`${cx},${cy-s-6} ${cx+s+6},${cy} ${cx},${cy+s+6} ${cx-s-6},${cy}`}
                      fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <polygon points={`${cx},${cy-s} ${cx+s},${cy} ${cx},${cy+s} ${cx-s},${cy}`}
                      fill={C.canvas} stroke={sel ? C.edgeHi : C.gwStroke} strokeWidth={1.8} />
                    <text x={cx} y={cy + 5} textAnchor="middle" fontSize={Math.max(14, s * 0.7)} fontWeight="700"
                      fill={C.gwStroke} style={{ userSelect: 'none', fontFamily: FONT }}>
                      {node.type === 'parallelGateway' ? '+' : '×'}
                    </text>
                    {cap && cap.lines.map((line, i) => (
                      <text key={i} x={cx} y={y - 6 - (cap.lines.length - 1 - i) * (cap.fontSize + 1)}
                        textAnchor="middle" fontSize={cap.fontSize} fill="#e8e8e8"
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              const fill = isRpa ? C.rpaFill : isBad ? C.badFill : C.taskFill
              const stroke = sel ? C.edgeHi : isRpa ? C.rpaStroke : isBad ? C.badStroke : C.taskStroke
              const fitted = fitBoxText(node.name, w, h, 3)
              const sla = slaLabel(node.slaMinutes)

              return (
                <g key={node.id} opacity={faded ? 0.18 : 1}
                  onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                  <rect x={x} y={y} width={w} height={h} rx={8} ry={8}
                    fill={fill} stroke={stroke} strokeWidth={sel ? 2 : 1.5} />
                  <g clipPath={`url(#clip-${node.id})`}>
                    {fitted.lines.map((line, i) => (
                      <text key={i}
                        x={cx}
                        y={cy - ((fitted.lines.length - 1) * (fitted.fontSize + 2)) / 2 + i * (fitted.fontSize + 3)}
                        textAnchor="middle" dominantBaseline="central"
                        fontSize={fitted.fontSize} fill={C.taskText}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                  {sla && (
                    <text x={cx} y={y + h + 12} textAnchor="middle"
                      fontSize="9" fill="#9a9a9a" style={{ userSelect: 'none', fontFamily: FONT }}>
                      {sla}
                    </text>
                  )}
                </g>
              )
            })}

            {process.lanes.map((lane) => {
              const label = laneLabels.get(lane.id) || { lines: [lane.name], fontSize: 11 }
              const cx = lane.geometry.x + LANE_HEAD / 2
              const cy = lane.geometry.y + lane.geometry.height / 2
              const lineH = label.fontSize + 3
              return (
                <g key={`head-${lane.id}`}>
                  <rect
                    x={lane.geometry.x} y={lane.geometry.y}
                    width={LANE_HEAD} height={lane.geometry.height}
                    fill={C.laneHead} stroke={C.laneLine} strokeWidth="1"
                  />
                  <g transform={`rotate(-90, ${cx}, ${cy})`}>
                    {label.lines.map((line, i) => (
                      <text
                        key={i}
                        x={cx}
                        y={cy - ((label.lines.length - 1) * lineH) / 2 + i * lineH}
                        textAnchor="middle"
                        dominantBaseline="middle"
                        fontSize={label.fontSize} fontWeight="600" fill={C.laneText}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                </g>
              )
            })}
          </g>
        </svg>

        <div className="absolute bottom-3 right-3 pointer-events-none bg-black/70 border border-zinc-700 rounded px-2 py-0.5 text-[11px] font-mono text-zinc-400 shadow-sm select-none">
          {Math.round(zoom * 100)}%
        </div>
      </div>

      <div className="px-3 py-1.5 bg-[#121212] border-t border-zinc-800 flex flex-wrap items-center justify-between gap-2 text-[10px] text-zinc-400 shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded-full border-2 border-zinc-200 inline-block" />Старт</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded-full border-2 border-emerald-400 inline-block" />Успех</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded-full border-2 border-rose-400 inline-block" />Отказ</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-emerald-950 border border-emerald-400 inline-block" />PIX RPA</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 inline-block border border-amber-400" style={{ transform: 'rotate(45deg)' }} />Шлюз</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-amber-950 border border-amber-400 inline-block" />SLA&gt;2ч</div>
        </div>
        <div className="flex items-center gap-1">
          <Info className="w-3 h-3 text-sky-400" />
          Клик — детали · Колёсико — зум · Тащить — пан
        </div>
      </div>
    </div>
  )
}
