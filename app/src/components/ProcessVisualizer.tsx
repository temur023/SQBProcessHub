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
const LANE_HEAD = 56

const C = {
  canvas: '#1c1c1c',
  gridMinor: 'rgba(255,255,255,0.055)',
  gridMajor: 'rgba(255,255,255,0.13)',
  laneLine: 'rgba(255,255,255,0.38)',
  laneHead: '#111111',
  laneText: '#f4f4f5',
  taskFill: '#1a1a1a',
  taskStroke: '#ececec',
  taskText: '#f5f5f5',
  rpaFill: '#10261a',
  rpaStroke: '#34d399',
  badFill: '#2a1f0a',
  badStroke: '#fbbf24',
  edge: '#d4d4d4',
  edgeHi: '#60a5fa',
  labelBg: '#1c1c1c',
  gwStroke: '#eab308',
  start: '#f4f4f5',
  endOk: '#4ade80',
  endNo: '#f87171',
}

function snap(v: number) { return Math.round(v / GRID_MINOR) * GRID_MINOR }

function boxOf(n: ProcessNode) {
  const isEv = n.type === 'startEvent' || n.type === 'endEvent'
  const isGw = n.type === 'exclusiveGateway' || n.type === 'parallelGateway' || n.type === 'inclusiveGateway'
  const w = isEv ? 44 : isGw ? 48 : (n.geometry.width || 160)
  const h = isEv ? 44 : isGw ? 48 : (n.geometry.height || 70)
  return {
    x: n.geometry.x,
    y: n.geometry.y,
    w,
    h,
    cx: n.geometry.x + w / 2,
    cy: n.geometry.y + h / 2,
  }
}

function edgePath(
  src: ProcessNode,
  tgt: ProcessNode,
  pts: { x: number; y: number }[],
): { d: string; lx: number; ly: number } {
  if (pts && pts.length >= 2) {
    const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
    const mid = pts[Math.floor(pts.length / 2)]
    return { d, lx: mid.x, ly: mid.y - 10 }
  }

  const a = boxOf(src)
  const b = boxOf(tgt)
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
    return { d: `M${x1},${y1} L${x2},${y2}`, lx: (x1 + x2) / 2, ly: y1 - 10 }
  }
  if (Math.abs(x1 - x2) < 2) {
    return { d: `M${x1},${y1} L${x2},${y2}`, lx: x1 + 10, ly: (y1 + y2) / 2 }
  }

  if (horiz) {
    const midX = snap((x1 + x2) / 2)
    return {
      d: `M${x1},${y1} L${midX},${y1} L${midX},${y2} L${x2},${y2}`,
      lx: midX,
      ly: (y1 + y2) / 2 - 10,
    }
  }
  const midY = snap((y1 + y2) / 2)
  return {
    d: `M${x1},${y1} L${x1},${midY} L${x2},${midY} L${x2},${y2}`,
    lx: (x1 + x2) / 2,
    ly: midY - 10,
  }
}

function wrapText(text: string, maxChars: number): string[] {
  if (!text) return []
  const words = text.split(' ')
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
  return lines.slice(0, 3)
}

function slaLabel(mins?: number): string {
  if (mins == null) return ''
  if (mins < 1) return `${mins} min`
  if (mins % 60 === 0 && mins >= 60) return `${mins / 60} ч`
  return `${mins} min`
}

function shortLane(name: string): string {
  const inner = name.match(/\(([^)]+)\)/)
  if (inner && inner[1].length <= 22) return inner[1]
  return name.length > 28 ? name.slice(0, 27) + '…' : name
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
    for (const l of process.lanes) bump(l.geometry.x, l.geometry.y, l.geometry.width, l.geometry.height)
    for (const n of process.nodes) bump(n.geometry.x, n.geometry.y, n.geometry.width || 160, (n.geometry.height || 70) + 18)
    if (!Number.isFinite(minX)) {
      minX = 0
      minY = 0
      maxX = 800
      maxY = 500
    }
    return { minX, minY, w: Math.max(maxX - minX, 200), h: Math.max(maxY - minY, 160) }
  }, [process])

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
    const pad = 32
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
    <div className={`flex flex-col bg-[#161616] rounded-xl border border-zinc-800 shadow-sm overflow-hidden transition-all duration-200 ${
      isFullscreen ? 'fixed inset-0 z-50 rounded-none border-none' : 'flex-1 min-h-0 h-full'
    }`}>
      <div className="px-3 py-2 border-b border-zinc-800 bg-[#141414] flex flex-wrap items-center justify-between gap-2 shrink-0">
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
          preserveAspectRatio="xMidYMid meet"
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
          </defs>

          <rect width="100%" height="100%" fill={C.canvas} />
          {showGrid && <rect width="100%" height="100%" fill="url(#g-major)" />}

          <g transform={`translate(${panPos.x},${panPos.y}) scale(${zoom})`}>
            {process.lanes.map((lane) => (
              <g key={lane.id}>
                <rect
                  x={lane.geometry.x} y={lane.geometry.y}
                  width={lane.geometry.width} height={lane.geometry.height}
                  fill="none" stroke={C.laneLine} strokeWidth="1.2"
                />
                <rect
                  x={lane.geometry.x} y={lane.geometry.y}
                  width={LANE_HEAD} height={lane.geometry.height}
                  fill={C.laneHead} stroke={C.laneLine} strokeWidth="1.2"
                />
                <text
                  x={lane.geometry.x + LANE_HEAD / 2}
                  y={lane.geometry.y + lane.geometry.height / 2}
                  textAnchor="middle" dominantBaseline="central"
                  transform={`rotate(-90,${lane.geometry.x + LANE_HEAD / 2},${lane.geometry.y + lane.geometry.height / 2})`}
                  fontSize="12" fontWeight="600" fill={C.laneText}
                  style={{ userSelect: 'none' }}>
                  {shortLane(lane.name)}
                </text>
              </g>
            ))}

            {process.edges.map(edge => {
              const src = process.nodes.find(n => n.id === edge.sourceId)
              const tgt = process.nodes.find(n => n.id === edge.targetId)
              if (!src || !tgt) return null
              const { d, lx, ly } = edgePath(src, tgt, edge.points || [])
              const hi = Boolean(selectedNodeId && (edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId))
              const label = (edge.name || '').trim()
              const lw = Math.min(140, Math.max(36, label.length * 6.2 + 10))
              return (
                <g key={edge.id}>
                  <path d={d} fill="none"
                    stroke={hi ? C.edgeHi : C.edge}
                    strokeWidth={hi ? 2.2 : 1.4}
                    strokeLinejoin="round"
                    markerEnd={hi ? 'url(#arr-hi)' : 'url(#arr)'} />
                  {label && (
                    <>
                      <rect x={lx - lw / 2} y={ly - 8} width={lw} height={16} rx={3}
                        fill={C.labelBg} stroke="#3f3f46" strokeWidth="0.8" />
                      <text x={lx} y={ly + 4} textAnchor="middle"
                        fontSize="10" fill="#e4e4e7" fontWeight="500"
                        style={{ userSelect: 'none' }}>
                        {label.length > 22 ? label.slice(0, 21) + '…' : label}
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
              const { x, y } = node.geometry
              const w = node.geometry.width  || 160
              const h = node.geometry.height || 70

              if (node.type === 'startEvent') {
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={x + 22} cy={y + 22} r={28} fill="none" stroke={C.edgeHi} strokeWidth="1.5" strokeDasharray="4 3" />}
                    <circle cx={x + 22} cy={y + 22} r={21} fill={C.canvas} stroke={sel ? C.edgeHi : C.start} strokeWidth={2} />
                    <text x={x + 22} y={y + 54} textAnchor="middle" fontSize="10" fontWeight="500"
                      fill="#d4d4d8" style={{ userSelect: 'none' }}>
                      {node.name.length > 24 ? node.name.slice(0, 23) + '…' : node.name}
                    </text>
                  </g>
                )
              }

              if (node.type === 'endEvent') {
                const sc = isRej ? C.endNo : C.endOk
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={x + 22} cy={y + 22} r={28} fill="none" stroke={C.edgeHi} strokeWidth="1.5" strokeDasharray="4 3" />}
                    <circle cx={x + 22} cy={y + 22} r={21} fill={C.canvas} stroke={sel ? C.edgeHi : sc} strokeWidth={4} />
                    <circle cx={x + 22} cy={y + 22} r={13} fill="none" stroke={sel ? C.edgeHi : sc} strokeWidth="1.6" />
                    <text x={x + 22} y={y + 54} textAnchor="middle" fontSize="10" fontWeight="500"
                      fill={isRej ? C.endNo : C.endOk} style={{ userSelect: 'none' }}>
                      {node.name.length > 24 ? node.name.slice(0, 23) + '…' : node.name}
                    </text>
                  </g>
                )
              }

              if (node.type === 'exclusiveGateway' || node.type === 'parallelGateway' || node.type === 'inclusiveGateway') {
                const cx = x + 24, cy = y + 24
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <polygon points={`${cx},${cy-30} ${cx+30},${cy} ${cx},${cy+30} ${cx-30},${cy}`}
                      fill="none" stroke={C.edgeHi} strokeWidth="1.5" strokeDasharray="4 3" />}
                    <polygon points={`${cx},${cy-22} ${cx+22},${cy} ${cx},${cy+22} ${cx-22},${cy}`}
                      fill={C.canvas} stroke={sel ? C.edgeHi : C.gwStroke} strokeWidth={2} />
                    <text x={cx} y={cy + 6} textAnchor="middle" fontSize="18" fontWeight="700"
                      fill={C.gwStroke} style={{ userSelect: 'none' }}>
                      {node.type === 'parallelGateway' ? '+' : '×'}
                    </text>
                    {node.name && node.name !== 'Условие' && (
                      <text x={cx} y={y - 8} textAnchor="middle" fontSize="10" fontWeight="500"
                        fill="#e4e4e7" style={{ userSelect: 'none' }}>
                        {node.name.length > 32 ? node.name.slice(0, 31) + '…' : node.name}
                      </text>
                    )}
                  </g>
                )
              }

              const fill = isRpa ? C.rpaFill : isBad ? C.badFill : C.taskFill
              const stroke = sel ? C.edgeHi : isRpa ? C.rpaStroke : isBad ? C.badStroke : C.taskStroke
              const lines = wrapText(node.name, Math.max(12, Math.floor((w - 16) / 6.6)))
              const sla = slaLabel(node.slaMinutes)

              return (
                <g key={node.id} opacity={faded ? 0.18 : 1}
                  onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                  <rect x={x} y={y} width={w} height={h} rx={10} ry={10}
                    fill={fill} stroke={stroke} strokeWidth={sel ? 2.2 : 1.6} />
                  {isRpa && (
                    <text x={x + w - 8} y={y + 13} textAnchor="end" fontSize="8" fill={C.rpaStroke} fontWeight="700"
                      style={{ userSelect: 'none' }}>PIX RPA</text>
                  )}
                  {lines.map((line, i) => (
                    <text key={i} x={x + w / 2} y={y + h / 2 - ((lines.length - 1) * 12) / 2 + i * 13}
                      textAnchor="middle" fontSize="12" fontWeight="500"
                      fill={C.taskText} style={{ userSelect: 'none' }}>
                      {line}
                    </text>
                  ))}
                  {sla && (
                    <text x={x + w / 2} y={y + h + 14} textAnchor="middle"
                      fontSize="10" fill="#a1a1aa" style={{ userSelect: 'none' }}>
                      {sla}
                    </text>
                  )}
                </g>
              )
            })}
          </g>
        </svg>

        <div className="absolute bottom-3 right-3 pointer-events-none bg-black/70 border border-zinc-700 rounded px-2 py-0.5 text-[11px] font-mono text-zinc-400 shadow-sm select-none">
          {Math.round(zoom * 100)}%
        </div>
      </div>

      <div className="px-3 py-1.5 bg-[#141414] border-t border-zinc-800 flex flex-wrap items-center justify-between gap-2 text-[10px] text-zinc-400 shrink-0">
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
