import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import {
  ZoomIn, ZoomOut, Maximize2, Minimize2,
  Cpu, AlertTriangle, Info, Search, RotateCcw, Grid,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { BusinessProcess, ProcessEdge, ProcessNode } from '@/types/process'

interface ProcessVisualizerProps {
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
  selectedNodeId?: string
}

const GRID_MINOR = 10
const GRID_MAJOR = 100
const MIN_ZOOM = 0.08
const MAX_ZOOM = 3.0
const LANE_HEAD_DEFAULT = 44
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif'

function laneHeaderWidth(lane: ProcessNode): number {
  const m = parseStyleMap(lane.style || '')
  const raw = m['startsize']
  if (raw) {
    const n = Number(raw)
    if (Number.isFinite(n) && n > 0) return Math.max(18, Math.min(80, Math.round(n)))
  }
  // эвристика: узбекские swimlane обычно 26-80
  const low = (lane.style || '').toLowerCase()
  if (low.includes('swimlane')) return LANE_HEAD_DEFAULT
  return LANE_HEAD_DEFAULT
}

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

type Box = { x: number; y: number; w: number; h: number; cx: number; cy: number }
type Pt = { x: number; y: number }

function rawBox(n: ProcessNode): Box {
  const w = Math.max(n.geometry.width || 8, 8)
  const h = Math.max(n.geometry.height || 8, 8)
  return {
    x: n.geometry.x,
    y: n.geometry.y,
    w,
    h,
    cx: n.geometry.x + w / 2,
    cy: n.geometry.y + h / 2,
  }
}

function isEventNode(n: ProcessNode): boolean {
  return n.type === 'startEvent' || n.type === 'endEvent'
}

function parseStyleMap(style: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of (style || '').split(';')) {
    if (!part) continue
    const eq = part.indexOf('=')
    if (eq < 0) {
      out[part.trim().toLowerCase()] = '1'
      continue
    }
    out[part.slice(0, eq).trim().toLowerCase()] = part.slice(eq + 1).trim()
  }
  return out
}

function styleNum(map: Record<string, string>, key: string, fallback = 0): number {
  const n = Number(map[key])
  return Number.isFinite(n) ? n : fallback
}

/** Perimeter point from mxGraph exitX/exitY (0..1 of the box). */
function constraintPoint(box: Box, fx?: number, fy?: number): Pt | null {
  if (fx == null || fy == null || Number.isNaN(fx) || Number.isNaN(fy)) return null
  return { x: box.x + fx * box.w, y: box.y + fy * box.h }
}

function isGatewayNode(n: ProcessNode): boolean {
  return n.type === 'exclusiveGateway' || n.type === 'parallelGateway' || n.type === 'inclusiveGateway'
}

// Точный ромб-пересечение (gateway)
function intersectRhombus(box: Box, toward: Pt): Pt {
  const dx = toward.x - box.cx
  const dy = toward.y - box.cy
  if (dx === 0 && dy === 0) return { x: box.x + box.w, y: box.cy }
  const hw = box.w / 2
  const hh = box.h / 2
  const t = 1 / (Math.abs(dx) / hw + Math.abs(dy) / hh || 1)
  return { x: box.cx + dx * t, y: box.cy + dy * t }
}

/** Intersection of the ray from the box centre toward `toward` with the shape border. */
function intersectBorder(box: Box, toward: Pt, circular: boolean, isGateway = false): Pt {
  if (isGateway) return intersectRhombus(box, toward)
  const dx = toward.x - box.cx
  const dy = toward.y - box.cy
  if (dx === 0 && dy === 0) return { x: box.x + box.w, y: box.cy }
  if (circular) {
    const r = Math.max(4, Math.min(box.w, box.h) / 2)
    const len = Math.hypot(dx, dy) || 1
    return { x: box.cx + (dx / len) * r, y: box.cy + (dy / len) * r }
  }
  // Прямоугольник
  const hw = box.w / 2
  const hh = box.h / 2
  const sx = dx === 0 ? Infinity : hw / Math.abs(dx)
  const sy = dy === 0 ? Infinity : hh / Math.abs(dy)
  const t = Math.min(sx, sy)
  return { x: box.cx + dx * t, y: box.cy + dy * t }
}

function labelT(x?: number): number {
  if (x == null || Number.isNaN(x)) return 0.5
  // mxGraph uses -1..1 (0 = midpoint); the spec also allows 0..1 along the path.
  if (x < 0 || x > 1) return Math.max(0, Math.min(1, (x + 1) / 2))
  return x
}

function pointAlong(pts: Pt[], t: number, perp = 0): Pt {
  if (pts.length === 0) return { x: 0, y: 0 }
  if (pts.length === 1) return { ...pts[0] }
  const segs: { a: Pt; b: Pt; len: number }[] = []
  let total = 0
  for (let i = 0; i < pts.length - 1; i++) {
    const len = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
    segs.push({ a: pts[i], b: pts[i + 1], len })
    total += len
  }
  if (total <= 0) return { ...pts[0] }
  let walk = Math.max(0, Math.min(1, t)) * total
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i]
    if (walk > s.len && i < segs.length - 1) {
      walk -= s.len
      continue
    }
    const r = s.len === 0 ? 0 : walk / s.len
    const x = s.a.x + (s.b.x - s.a.x) * r
    const y = s.a.y + (s.b.y - s.a.y) * r
    if (!perp) return { x, y }
    const nx = -(s.b.y - s.a.y)
    const ny = s.b.x - s.a.x
    const nl = Math.hypot(nx, ny) || 1
    return { x: x + (nx / nl) * perp, y: y + (ny / nl) * perp }
  }
  return { ...pts[pts.length - 1] }
}

function isOrthogonalEdge(edge: ProcessEdge): boolean {
  const s = (edge.style || '').toLowerCase()
  const es = (edge.edgeStyle || '').toLowerCase()
  return s.includes('orthogonal') || es.includes('orthogonal')
}

function buildOrthogonalPts(start: Pt, end: Pt, wp: Pt[], edge: ProcessEdge, src?: ProcessNode, tgt?: ProcessNode): Pt[] {
  if (wp.length > 0) return [start, ...wp, end]
  const dx = end.x - start.x
  const dy = end.y - start.y
  const adx = Math.abs(dx)
  const ady = Math.abs(dy)
  if (adx < 8 || ady < 8) return [start, end]

  // Для разных дорожек — делаем 3-сегментный маршрут через середину, чтобы не резать узлы
  const differentLanes = src?.laneId && tgt?.laneId && src.laneId !== tgt.laneId
  if (differentLanes) {
    const midY = Math.round((start.y + end.y) / 2)
    // Вертикально выйти, горизонтально пройти по межполосью, вертикально войти
    const pts: Pt[] = [start, { x: start.x, y: midY }, { x: end.x, y: midY }, end]
    return pts.filter((p, i, arr) => i === 0 || p.x !== arr[i-1].x || p.y !== arr[i-1].y)
  }

  let horizontalFirst: boolean
  if (edge.exitX === 0 || edge.exitX === 1) horizontalFirst = true
  else if (edge.exitY === 0 || edge.exitY === 1) horizontalFirst = false
  else if (edge.entryX === 0 || edge.entryX === 1) horizontalFirst = true
  else if (edge.entryY === 0 || edge.entryY === 1) horizontalFirst = false
  else horizontalFirst = adx > ady * 0.7

  const jetty = 12
  let sx = start.x
  let sy = start.y
  let ex = end.x
  let ey = end.y

  if (edge.exitX != null && edge.exitY != null) {
    if (edge.exitX === 1) sx += jetty
    else if (edge.exitX === 0) sx -= jetty
    else if (edge.exitY === 0) sy -= jetty
    else if (edge.exitY === 1) sy += jetty
  }
  if (edge.entryX != null && edge.entryY != null) {
    if (edge.entryX === 1) ex += jetty
    else if (edge.entryX === 0) ex -= jetty
    else if (edge.entryY === 0) ey -= jetty
    else if (edge.entryY === 1) ey += jetty
  }

  const hasJetty = edge.exitX != null || edge.entryX != null
  if (hasJetty) {
    const mid = horizontalFirst ? { x: ex, y: sy } : { x: sx, y: ey }
    const pts: Pt[] = [start]
    if (Math.hypot(sx - start.x, sy - start.y) > 2) pts.push({ x: sx, y: sy })
    pts.push(mid)
    if (Math.hypot(ex - mid.x, ey - mid.y) > 2) pts.push({ x: ex, y: ey })
    pts.push(end)
    return pts.filter((p, i, arr) => i === 0 || p.x !== arr[i-1].x || p.y !== arr[i-1].y)
  }

  const mid = horizontalFirst ? { x: end.x, y: start.y } : { x: start.x, y: end.y }
  return [start, mid, end]
}

function edgePath(
  src: ProcessNode,
  tgt: ProcessNode,
  srcBox: Box,
  tgtBox: Box,
  edge: ProcessEdge,
): { d: string; lx: number; ly: number; pts: Pt[] } {
  const wp = edge.points || []
  const startToward = wp[0] || { x: tgtBox.cx, y: tgtBox.cy }
  const endToward = wp.length ? wp[wp.length - 1] : { x: srcBox.cx, y: srcBox.cy }
  const isSrcLane = src.type === 'lane'
  const isTgtLane = tgt.type === 'lane'
  const start = isSrcLane
    ? {
        x: Math.max(srcBox.x, Math.min(tgtBox.cx, srcBox.x + srcBox.w)),
        y: tgtBox.cy < srcBox.cy ? srcBox.y : srcBox.y + srcBox.h,
      }
    : constraintPoint(srcBox, edge.exitX, edge.exitY) ||
      intersectBorder(srcBox, startToward, isEventNode(src), isGatewayNode(src))
  const end = isTgtLane
    ? {
        x: Math.max(tgtBox.x, Math.min(srcBox.cx, tgtBox.x + tgtBox.w)),
        y: srcBox.cy < tgtBox.cy ? tgtBox.y : tgtBox.y + tgtBox.h,
      }
    : constraintPoint(tgtBox, edge.entryX, edge.entryY) ||
      intersectBorder(tgtBox, endToward, isEventNode(tgt), isGatewayNode(tgt))

  let pts: Pt[]
  if (wp.length > 0) {
    pts = [start, ...wp, end]
  } else if (isOrthogonalEdge(edge)) {
    pts = buildOrthogonalPts(start, end, wp, edge, src, tgt)
  } else {
    const dx = end.x - start.x
    const dy = end.y - start.y
    if (Math.abs(dx) < 8 || Math.abs(dy) < 8) pts = [start, end]
    else pts = buildOrthogonalPts(start, end, wp, edge, src, tgt)
  }

  // Скругление как в draw.io: вместо резких углов используем небольшие дуги через path
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  // Метка ребра: если labelY задан — используем его, иначе -8 только для длинных ребер
  const rawLabelY = edge.labelY
  const perp = rawLabelY != null ? rawLabelY : (pts.length > 2 ? -8 : -10)
  const label = pointAlong(pts, labelT(edge.labelX), perp)
  return { d, lx: label.x, ly: label.y, pts }
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
  const innerW = Math.max(20, width)
  const innerH = Math.max(14, height)
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
    let maxX = -Infinity
    let maxY = -Infinity
    const bump = (x: number, y: number, w: number, h: number) => {
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + w)
      maxY = Math.max(maxY, y + h)
    }
    // Ланес и ноды уже в абсолютных координатах (с учетом startSize)
    for (const l of process.lanes) bump(l.geometry.x - 8, l.geometry.y - 8, l.geometry.width + 16, l.geometry.height + 16)
    for (const n of process.nodes) {
      const padY = n.type === 'startEvent' || n.type === 'endEvent' ? 22 : 18
      const padW = 16
      bump(n.geometry.x - 8, n.geometry.y - padY, (n.geometry.width || 140) + padW, (n.geometry.height || 60) + padY * 2)
    }
    // Учитываем заголовки дорожек и внешние отступы для huge диаграмм (4700x2000)
    for (const e of process.edges) {
      for (const p of e.points) bump(p.x - 4, p.y - 4, 8, 8)
    }
    if (!Number.isFinite(minX)) {
      minX = 0
      minY = 0
      maxX = 800
      maxY = 500
    }
    // Минимальные размеры и padding для центрирования
    const w = Math.max(maxX - minX + 24, 400)
    const h = Math.max(maxY - minY + 24, 300)
    return { minX: minX - 12, minY: minY - 12, w, h }
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
    for (const n of process.nodes) map.set(n.id, rawBox(n))
    return map
  }, [process.nodes])

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
            <marker id="arr-dashed" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill="#f0f0f0" />
            </marker>
            <marker id="arr-dashed-red" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill="#ff6b6b" />
            </marker>
            {process.edges.map(edge => {
              // динамический маркер под цвет ребра (зелёные/красные/серые пунктиры)
              const col = (edge.strokeColor || '').trim()
              if (!col) return null
              // нормализуем #fff -> #ffffff уже есть, просто используем как есть
              const safeId = `arr-${edge.id}`
              return (
                <marker key={safeId} id={safeId} markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
                  <polygon points="0 0,8 3,0 6" fill={col} />
                </marker>
              )
            })}
            {process.nodes.map((node) => {
              const b = nodeBoxes.get(node.id) || rawBox(node)
              const st = parseStyleMap(node.style)
              const padL = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingleft')
              const padT = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingtop')
              const padR = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingright')
              const padB = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingbottom')
              return (
                <clipPath key={`clip-${node.id}`} id={`clip-${node.id}`}>
                  <rect
                    x={b.x + padL}
                    y={b.y + padT}
                    width={Math.max(8, b.w - padL - padR)}
                    height={Math.max(8, b.h - padT - padB)}
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

            {/* Рёбра — только линии, лейблы отдельным слоем поверх узлов */}
            {process.edges.map(edge => {
              let src: ProcessNode | undefined = process.nodes.find(n => n.id === edge.sourceId)
              let tgt: ProcessNode | undefined = process.nodes.find(n => n.id === edge.targetId)
              if (!src) src = process.lanes.find(l => l.id === edge.sourceId) as ProcessNode | undefined
              if (!tgt) tgt = process.lanes.find(l => l.id === edge.targetId) as ProcessNode | undefined
              if (!src || !tgt) return null
              if (activeFilter !== 'all' || searchQuery.trim()) {
                if (!visibleIds.has(edge.sourceId!) || !visibleIds.has(edge.targetId!)) {
                  // lane-target dashed — проверяем видимость только source
                  if (!process.lanes.find(l => l.id === edge.targetId) || !visibleIds.has(edge.sourceId!)) return null
                }
              }
              const sb = nodeBoxes.get(src.id) || rawBox(src)
              const tb = nodeBoxes.get(tgt.id) || rawBox(tgt)
              const { d } = edgePath(src, tgt, sb, tb, edge)
              const hi = Boolean(selectedNodeId && (edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId))
              const isDashed = edge.dashed || Boolean(edge.dashPattern)
              let dashArray: string | undefined
              if (edge.dashPattern) {
                const cleaned = edge.dashPattern.replace(/;/g, ' ').trim()
                dashArray = cleaned || '9 9'
              } else if (isDashed) dashArray = '9 9'
              const baseColor = edge.strokeColor ? edge.strokeColor : (isDashed ? '#e8e8e8' : C.edge)
              const strokeColor = hi ? C.edgeHi : baseColor
              const sw = isDashed ? 1.7 : (edge.strokeWidth ? Math.max(1, Math.min(3.5, edge.strokeWidth)) : (hi ? 2 : 1.35))
              let markerId: string
              if (hi) markerId = 'url(#arr-hi)'
              else if (isDashed && edge.strokeColor === '#ff6b6b') markerId = 'url(#arr-dashed-red)'
              else if (isDashed) markerId = edge.strokeColor ? `url(#arr-${edge.id})` : 'url(#arr-dashed)'
              else if (edge.strokeColor) markerId = `url(#arr-${edge.id})`
              else markerId = 'url(#arr)'
              return (
                <path key={edge.id} d={d} fill="none"
                  stroke={strokeColor}
                  strokeWidth={sw}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  strokeDasharray={dashArray}
                  opacity={isDashed ? 1 : 1}
                  markerEnd={markerId} />
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
              const st = parseStyleMap(node.style)
              const align = (st.align || 'center').toLowerCase()
              const valign = (st.verticalalign || 'middle').toLowerCase()

              if (node.type === 'startEvent') {
                const r = Math.max(8, Math.min(w, h) / 2 - 1)
                const cap = fitCaption(node.name, 90, 2)
                const vpos = (st.verticallabelposition || 'bottom').toLowerCase()
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : C.start} strokeWidth={1.8} />
                    {cap.lines.map((line, i) => (
                      <text key={i}
                        x={cx}
                        y={vpos === 'top' ? y - 6 - (cap.lines.length - 1 - i) * (cap.fontSize + 2) : y + h + 11 + i * (cap.fontSize + 2)}
                        textAnchor="middle"
                        fontSize={cap.fontSize} fill="#d8d8d8"
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              if (node.type === 'endEvent') {
                const r = Math.max(8, Math.min(w, h) / 2 - 1)
                const sc = isRej ? C.endNo : C.endOk
                const cap = fitCaption(node.name, 90, 2)
                const vpos = (st.verticallabelposition || 'bottom').toLowerCase()
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : sc} strokeWidth={3.4} />
                    <circle cx={cx} cy={cy} r={Math.max(4, r - 6)} fill="none" stroke={sel ? C.edgeHi : sc} strokeWidth="1.4" />
                    {cap.lines.map((line, i) => (
                      <text key={i}
                        x={cx}
                        y={vpos === 'top' ? y - 6 - (cap.lines.length - 1 - i) * (cap.fontSize + 2) : y + h + 11 + i * (cap.fontSize + 2)}
                        textAnchor="middle"
                        fontSize={cap.fontSize} fill={isRej ? C.endNo : C.endOk}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              if (node.type === 'exclusiveGateway' || node.type === 'parallelGateway' || node.type === 'inclusiveGateway') {
                const s = Math.max(12, Math.min(w, h) / 2)
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
              const padL = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingleft')
              const padT = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingtop')
              const padR = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingright')
              const padB = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingbottom')
              const innerW = Math.max(12, w - padL - padR)
              const innerH = Math.max(12, h - padT - padB)
              const fitted = fitBoxText(node.name, innerW, innerH, 4)
              const sla = slaLabel(node.slaMinutes)
              const textAnchor = align === 'left' ? 'start' : align === 'right' ? 'end' : 'middle'
              const tx = align === 'left' ? x + padL : align === 'right' ? x + w - padR : cx
              const lineH = fitted.fontSize + 3
              const blockH = fitted.lines.length * lineH
              let ty0: number
              if (valign === 'top') ty0 = y + padT + fitted.fontSize / 2
              else if (valign === 'bottom') ty0 = y + h - padB - blockH + fitted.fontSize / 2
              else ty0 = cy - (fitted.lines.length - 1) * lineH / 2

              return (
                <g key={node.id} opacity={faded ? 0.18 : 1}
                  onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                  <rect x={x} y={y} width={w} height={h} rx={8} ry={8}
                    fill={fill} stroke={stroke} strokeWidth={sel ? 2 : 1.5} />
                  <g clipPath={`url(#clip-${node.id})`}>
                    {fitted.lines.map((line, i) => (
                      <text key={i}
                        x={tx}
                        y={ty0 + i * lineH}
                        textAnchor={textAnchor} dominantBaseline="central"
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

            {/* Лейблы рёбер — поверх узлов, с анти-коллизией */}
            {(() => {
              type Lbl = { id: string; lx: number; ly: number; lw: number; cap: { lines: string[]; fontSize: number } }
              const labels: Lbl[] = []
              for (const edge of process.edges) {
                let src: ProcessNode | undefined = process.nodes.find(n => n.id === edge.sourceId)
                let tgt: ProcessNode | undefined = process.nodes.find(n => n.id === edge.targetId)
                if (!src) src = process.lanes.find(l => l.id === edge.sourceId) as ProcessNode | undefined
                if (!tgt) tgt = process.lanes.find(l => l.id === edge.targetId) as ProcessNode | undefined
                if (!src || !tgt) continue
                if (activeFilter !== 'all' || searchQuery.trim()) {
                  if (!visibleIds.has(edge.sourceId!) || !visibleIds.has(edge.targetId!)) {
                    if (!process.lanes.find(l => l.id === edge.targetId) || !visibleIds.has(edge.sourceId!)) continue
                  }
                }
                const raw = (edge.name || '').trim()
                if (!raw) continue
                const sb = nodeBoxes.get(src.id) || rawBox(src)
                const tb = nodeBoxes.get(tgt.id) || rawBox(tgt)
                const { lx, ly } = edgePath(src, tgt, sb, tb, edge)
                const cap = fitCaption(raw, 90, 1)
                if (!cap) continue
                const lw = Math.min(96, Math.max(28, cap.lines[0].length * cap.fontSize * 0.58 + 10))
                labels.push({ id: edge.id, lx, ly, lw, cap })
              }
              // Анти-коллизия: раздвигаем близкие лейблы и от узлов
              const boxes: Box[] = []
              for (const n of process.nodes) boxes.push(rawBox(n))
              for (let i = 0; i < labels.length; i++) {
                for (let j = i + 1; j < labels.length; j++) {
                  const a = labels[i], b = labels[j]
                  if (Math.abs(a.lx - b.lx) < 70 && Math.abs(a.ly - b.ly) < 16) {
                    b.ly += 14
                    b.lx += 6
                  }
                }
                // Отталкиваем от узлов (если центр лейбла внутри узла — сдвигаем вверх)
                const lb = labels[i]
                for (const bx of boxes) {
                  if (lb.lx > bx.x - lb.lw/2 && lb.lx < bx.x + bx.w + lb.lw/2 && lb.ly > bx.y - 10 && lb.ly < bx.y + bx.h + 14) {
                    // сдвигаем выше узла
                    if (lb.ly < bx.cy) lb.ly = bx.y - 10
                    else lb.ly = bx.y + bx.h + 18
                  }
                }
              }
              return labels.map(lb => (
                <g key={`lbl-${lb.id}`}>
                  <rect x={lb.lx - lb.lw / 2} y={lb.ly - 7} width={lb.lw} height={14} rx={3}
                    fill={C.labelBg} stroke="#3a3a3a" strokeWidth="0.7" />
                  <text x={lb.lx} y={lb.ly + 3} textAnchor="middle"
                    fontSize={lb.cap.fontSize} fill="#e8e8e8"
                    style={{ userSelect: 'none', fontFamily: FONT }}>
                    {lb.cap.lines[0]}
                  </text>
                </g>
              ))
            })()}

            {process.lanes.map((lane) => {
              const label = laneLabels.get(lane.id) || { lines: [lane.name], fontSize: 11 }
              const hw = laneHeaderWidth(lane)
              const hx = lane.geometry.x + hw / 2
              const hy = lane.geometry.y + lane.geometry.height / 2
              const lineH = label.fontSize + 3
              return (
                <g key={`head-${lane.id}`}>
                  <rect
                    x={lane.geometry.x} y={lane.geometry.y}
                    width={hw} height={lane.geometry.height}
                    fill={C.laneHead} stroke={C.laneLine} strokeWidth="1"
                  />
                  <g transform={`rotate(-90, ${hx}, ${hy})`}>
                    {label.lines.map((line, i) => (
                      <text
                        key={i}
                        x={hx}
                        y={hy - ((label.lines.length - 1) * lineH) / 2 + i * lineH}
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
