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
const MIN_ZOOM = 0.15
const MAX_ZOOM = 3.0

function snap(v: number) { return Math.round(v / GRID_MINOR) * GRID_MINOR }

function edgePath(
  src: ProcessNode,
  tgt: ProcessNode,
  pts: { x: number; y: number }[],
): { d: string; lx: number; ly: number } {
  if (pts && pts.length >= 2) {
    const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
    const mid = pts[Math.floor(pts.length / 2)]
    return { d, lx: mid.x, ly: mid.y - 8 }
  }

  const sw = src.geometry.width  || 160
  const sh = src.geometry.height || 70
  const tw = tgt.geometry.width  || 160
  const th = tgt.geometry.height || 70

  let x1 = src.geometry.x + sw
  let y1 = src.geometry.y + sh / 2
  let x2 = tgt.geometry.x
  let y2 = tgt.geometry.y + th / 2

  const vertBelow = tgt.geometry.y > src.geometry.y + sh + 5
  const hClose    = Math.abs(tgt.geometry.x - src.geometry.x) < sw

  if (vertBelow && hClose) {
    x1 = src.geometry.x + sw / 2
    y1 = src.geometry.y + sh
    x2 = tgt.geometry.x + tw / 2
    y2 = tgt.geometry.y
    return {
      d: `M${x1},${y1} L${x1},${y2} L${x2},${y2}`,
      lx: x1 + 6,
      ly: (y1 + y2) / 2,
    }
  }

  const midX = snap((x1 + x2) / 2)
  const d = Math.abs(y1 - y2) < 4
    ? `M${x1},${y1} L${x2},${y2}`
    : `M${x1},${y1} L${midX},${y1} L${midX},${y2} L${x2},${y2}`

  return { d, lx: midX, ly: (y1 + y2) / 2 - 10 }
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

export const ProcessVisualizer: React.FC<ProcessVisualizerProps> = ({
  process,
  onSelectNode,
  selectedNodeId,
}) => {
  const [zoom, setZoom]                 = useState(0.85)
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
    for (const n of process.nodes) bump(n.geometry.x, n.geometry.y, n.geometry.width || 160, n.geometry.height || 70)
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
      const delta = e.deltaY > 0 ? -0.1 : 0.1
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
    <div className={`flex flex-col bg-slate-50 dark:bg-[#1e1e1e] rounded-xl border shadow-sm overflow-hidden transition-all duration-200 ${
      isFullscreen ? 'fixed inset-0 z-50 rounded-none border-none' : 'flex-1 min-h-0 h-full'
    }`}>
      {/* Toolbar */}
      <div className="px-3 py-2 border-b bg-white dark:bg-slate-900 flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-bold text-slate-700 dark:text-slate-200 uppercase tracking-wide">BPMN Карта</span>
          <span className="text-[10px] text-muted-foreground bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded-full">
            {process.nodes.length} эл. · {process.lanes.length} дорожек
          </span>
          <div className="h-4 w-px bg-slate-200 mx-1" />
          {(['all', 'rpa', 'bottlenecks'] as const).map(f => (
            <button key={f} onClick={() => setActiveFilter(f)}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded transition-colors ${
                activeFilter === f
                  ? f === 'rpa' ? 'bg-emerald-600 text-white'
                  : f === 'bottlenecks' ? 'bg-amber-500 text-white'
                  : 'bg-slate-700 text-white'
                  : 'border hover:bg-slate-100 dark:hover:bg-slate-800 text-muted-foreground'
              }`}>
              {f === 'all' && `Все (${process.nodes.length})`}
              {f === 'rpa' && <><Cpu className="w-3 h-3 mr-0.5" />RPA ({process.nodes.filter(n=>n.category==='rpa_bot').length})</>}
              {f === 'bottlenecks' && <><AlertTriangle className="w-3 h-3 mr-0.5" />SLA</>}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
            <Input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
              placeholder="Поиск..." className="pl-6 h-7 text-xs w-32" />
          </div>
          <div className="flex items-center gap-1 border-l pl-2">
            <Button variant={showGrid ? 'secondary' : 'ghost'} size="icon" className="h-7 w-7"
              onClick={() => setShowGrid(v => !v)} title="Сетка 10px">
              <Grid className="w-3.5 h-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7"
              onClick={() => setZoom(z => Math.max(MIN_ZOOM, +(z-0.15).toFixed(2)))}>
              <ZoomOut className="w-3.5 h-3.5" />
            </Button>
            <span className="text-[11px] w-9 text-center font-mono text-muted-foreground">{Math.round(zoom*100)}%</span>
            <Button variant="ghost" size="icon" className="h-7 w-7"
              onClick={() => setZoom(z => Math.min(MAX_ZOOM, +(z+0.15).toFixed(2)))}>
              <ZoomIn className="w-3.5 h-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fitToScreen} title="Вписать">
              <RotateCcw className="w-3.5 h-3.5" />
            </Button>
            <Button variant={isFullscreen ? 'default' : 'ghost'} size="sm" className="h-7 gap-1 text-xs px-2"
              onClick={() => setIsFullscreen(v => !v)}>
              {isFullscreen ? <><Minimize2 className="w-3.5 h-3.5" />Свернуть</> : <><Maximize2 className="w-3.5 h-3.5" />На весь экран</>}
            </Button>
          </div>
        </div>
      </div>

      {/* SVG Canvas */}
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
            {/* Draw.io style grid: minor 10px lines + major 100px lines */}
            <pattern id="g-minor" width={GRID_MINOR * zoom} height={GRID_MINOR * zoom} patternUnits="userSpaceOnUse"
              x={patternOffset.x} y={patternOffset.y}>
              <path d={`M ${GRID_MINOR * zoom} 0 L 0 0 0 ${GRID_MINOR * zoom}`}
                fill="none" stroke="rgba(0,0,0,0.06)" strokeWidth="0.5" />
            </pattern>
            <pattern id="g-major" width={GRID_MAJOR * zoom} height={GRID_MAJOR * zoom} patternUnits="userSpaceOnUse"
              x={patternOffset.x} y={patternOffset.y}>
              <rect width={GRID_MAJOR * zoom} height={GRID_MAJOR * zoom} fill="url(#g-minor)" />
              <path d={`M ${GRID_MAJOR * zoom} 0 L 0 0 0 ${GRID_MAJOR * zoom}`}
                fill="none" stroke="rgba(0,0,0,0.14)" strokeWidth="1" />
            </pattern>
            <marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill="#64748b" />
            </marker>
            <marker id="arr-hi" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill="#2563eb" />
            </marker>
          </defs>

          <rect width="100%" height="100%" fill="#f8fafc" />

          {/* Grid overlay */}
          {showGrid && <rect width="100%" height="100%" fill="url(#g-major)" />}

          {/* All diagram content in one transform group */}
          <g transform={`translate(${panPos.x},${panPos.y}) scale(${zoom})`}>

            {/* 1. Swimlane backgrounds */}
            {process.lanes.map((lane, idx) => (
              <g key={lane.id}>
                <rect x={lane.geometry.x} y={lane.geometry.y}
                  width={lane.geometry.width} height={lane.geometry.height}
                  fill={idx % 2 === 0 ? '#eef2f7' : '#e2e8f0'}
                  stroke="#94a3b8" strokeWidth="1" />
                <rect x={lane.geometry.x} y={lane.geometry.y}
                  width={40} height={lane.geometry.height}
                  fill="#e2e8f0" stroke="#94a3b8" strokeWidth="1" />
                <text
                  x={lane.geometry.x + 20}
                  y={lane.geometry.y + lane.geometry.height / 2}
                  textAnchor="middle" dominantBaseline="central"
                  transform={`rotate(-90,${lane.geometry.x + 20},${lane.geometry.y + lane.geometry.height / 2})`}
                  fontSize="11" fontWeight="700" fill="#334155"
                  style={{ userSelect: 'none' }}>
                  {lane.name}
                </text>
              </g>
            ))}

            {/* 2. Edges (below nodes) */}
            {process.edges.map(edge => {
              const src = process.nodes.find(n => n.id === edge.sourceId)
              const tgt = process.nodes.find(n => n.id === edge.targetId)
              if (!src || !tgt) return null
              const { d, lx, ly } = edgePath(src, tgt, edge.points || [])
              const hi = selectedNodeId && (edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId)
              return (
                <g key={edge.id}>
                  <path d={d} fill="none"
                    stroke={hi ? '#2563eb' : '#64748b'}
                    strokeWidth={hi ? 2.5 : 1.5}
                    strokeLinejoin="round"
                    markerEnd={hi ? 'url(#arr-hi)' : 'url(#arr)'} />
                  {edge.name && (
                    <>
                      <rect x={lx-38} y={ly-9} width={76} height={16} rx={3}
                        fill="white" stroke="#cbd5e1" strokeWidth="0.8" />
                      <text x={lx} y={ly+4} textAnchor="middle"
                        fontSize="9" fill="#475569" fontWeight="600"
                        style={{ userSelect: 'none' }}>
                        {edge.name}
                      </text>
                    </>
                  )}
                </g>
              )
            })}

            {/* 3. Nodes */}
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
                  <g key={node.id} opacity={faded ? 0.2 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={x+24} cy={y+24} r={29} fill="none" stroke="#2563eb" strokeWidth="2" strokeDasharray="5 3" />}
                    <circle cx={x+24} cy={y+24} r={24} fill="#10b981" stroke={sel?'#2563eb':'#059669'} strokeWidth={sel?3:2} />
                    <polygon points={`${x+18},${y+16} ${x+34},${y+24} ${x+18},${y+32}`} fill="white" />
                    <text x={x+24} y={y+56} textAnchor="middle" fontSize="10" fontWeight="700"
                      fill="#0f172a" style={{ userSelect: 'none' }}>
                      {node.name.length > 22 ? node.name.slice(0,21)+'…' : node.name}
                    </text>
                  </g>
                )
              }

              if (node.type === 'endEvent') {
                const fc = isRej ? '#ef4444' : '#10b981'
                const sc = isRej ? '#b91c1c' : '#047857'
                return (
                  <g key={node.id} opacity={faded ? 0.2 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={x+24} cy={y+24} r={29} fill="none" stroke="#2563eb" strokeWidth="2" strokeDasharray="5 3" />}
                    <circle cx={x+24} cy={y+24} r={24} fill={fc} stroke={sel?'#2563eb':sc} strokeWidth={sel?3:4} />
                    <circle cx={x+24} cy={y+24} r={13} fill="none" stroke="white" strokeWidth="2.5" />
                    <text x={x+24} y={y+56} textAnchor="middle" fontSize="10" fontWeight="700"
                      fill={isRej?'#b91c1c':'#047857'} style={{ userSelect: 'none' }}>
                      {node.name.length > 22 ? node.name.slice(0,21)+'…' : node.name}
                    </text>
                  </g>
                )
              }

              if (node.type === 'exclusiveGateway' || node.type === 'parallelGateway' || node.type === 'inclusiveGateway') {
                const cx = x + 23, cy = y + 23
                return (
                  <g key={node.id} opacity={faded ? 0.2 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <polygon points={`${cx},${cy-29} ${cx+29},${cy} ${cx},${cy+29} ${cx-29},${cy}`}
                      fill="none" stroke="#2563eb" strokeWidth="2" strokeDasharray="5 3" />}
                    <polygon points={`${cx},${cy-23} ${cx+23},${cy} ${cx},${cy+23} ${cx-23},${cy}`}
                      fill="#fef3c7" stroke={sel?'#2563eb':'#f59e0b'} strokeWidth={sel?3:2} />
                    <text x={cx} y={cy+6} textAnchor="middle" fontSize="18" fontWeight="900"
                      fill="#92400e" style={{ userSelect: 'none' }}>
                      {node.type === 'parallelGateway' ? '+' : '×'}
                    </text>
                    {node.name && node.name !== 'Условие' && (
                      <text x={cx} y={y-8} textAnchor="middle" fontSize="9" fontWeight="700"
                        fill="#92400e" style={{ userSelect: 'none' }}>
                        {node.name.length > 28 ? node.name.slice(0,27)+'…' : node.name}
                      </text>
                    )}
                  </g>
                )
              }

              // Task card
              const fc2 = isRpa ? '#f0fdf4' : isBad ? '#fffbeb' : '#ffffff'
              const sc2 = sel ? '#2563eb' : isRpa ? '#16a34a' : isBad ? '#f59e0b' : '#94a3b8'
              const sw2 = sel || isRpa ? 2 : 1.5
              const lines = wrapText(node.name, Math.max(14, Math.floor((w - 18) / 6.5)))

              return (
                <g key={node.id} opacity={faded ? 0.2 : 1}
                  onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                  <rect x={x} y={y} width={w} height={h} rx={7} ry={7}
                    fill={fc2} stroke={sc2} strokeWidth={sw2} />
                  {sel && <rect x={x-2} y={y-2} width={w+4} height={h+4} rx={9} ry={9}
                    fill="none" stroke="#2563eb" strokeWidth="1.5" strokeDasharray="4 2" />}

                  {/* Code badge */}
                  {node.code && (
                    <>
                      <rect x={x+5} y={y+5} width={node.code.length*6+8} height={13} rx={3} fill="white" stroke="#cbd5e1" strokeWidth="0.8" />
                      <text x={x+9} y={y+14} fontSize="8" fill="#475569" fontWeight="700"
                        fontFamily="monospace" style={{ userSelect: 'none' }}>{node.code}</text>
                    </>
                  )}

                  {/* RPA badge */}
                  {isRpa && (
                    <>
                      <rect x={x+w-50} y={y+5} width={45} height={13} rx={3} fill="#16a34a" />
                      <text x={x+w-27} y={y+14} textAnchor="middle" fontSize="8" fill="white" fontWeight="700"
                        style={{ userSelect: 'none' }}>PIX RPA</text>
                    </>
                  )}

                  {/* Task name */}
                  {lines.map((line, i) => (
                    <text key={i} x={x+w/2} y={y + 26 + i * 13}
                      textAnchor="middle" fontSize="11" fontWeight="600"
                      fill="#0f172a" style={{ userSelect: 'none' }}>
                      {line}
                    </text>
                  ))}

                  {/* Divider + footer */}
                  <line x1={x+4} y1={y+h-19} x2={x+w-4} y2={y+h-19} stroke="#e2e8f0" strokeWidth="1" />
                  <text x={x+8} y={y+h-7} fontSize="9" fill="#64748b" style={{ userSelect: 'none' }}>
                    {(node.system || 'АБС').slice(0,14)}
                  </text>
                  <text x={x+w-6} y={y+h-7} textAnchor="end" fontSize="9"
                    fill={isBad ? '#d97706' : '#94a3b8'} fontWeight={isBad?'700':'400'}
                    style={{ userSelect: 'none' }}>
                    {node.slaMinutes||60}м
                  </text>
                </g>
              )
            })}
          </g>
        </svg>

        {/* Zoom readout */}
        <div className="absolute bottom-3 right-3 pointer-events-none bg-white/90 dark:bg-slate-900/90 border border-slate-200 dark:border-slate-700 rounded px-2 py-0.5 text-[11px] font-mono text-slate-500 shadow-sm select-none">
          {Math.round(zoom*100)}%
        </div>
      </div>

      {/* Legend */}
      <div className="px-3 py-1.5 bg-white dark:bg-slate-900 border-t flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded-full bg-emerald-500 inline-block" />Старт</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded-full bg-emerald-600 border-2 border-emerald-300 inline-block" />Успех</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded-full bg-rose-500 inline-block" />Отказ</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-emerald-50 border border-emerald-400 inline-block" />PIX RPA</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 inline-block border border-amber-400 bg-yellow-100" style={{transform:'rotate(45deg)'}} />Шлюз</div>
          <div className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-amber-50 border border-amber-400 inline-block" />SLA&gt;2ч</div>
        </div>
        <div className="flex items-center gap-1">
          <Info className="w-3 h-3 text-blue-500" />
          Клик — детали · Колёсико — зум · Тащить — пан
        </div>
      </div>
    </div>
  )
}
