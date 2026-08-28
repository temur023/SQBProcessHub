import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import {
  ZoomIn, ZoomOut, Maximize2, Minimize2,
  Cpu, AlertTriangle, Info, Search, Grid, X,
  ChevronLeft, ChevronRight, Crosshair, XCircle,
  type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useIsDark } from '@/hooks/use-dark-mode'
import type { BusinessProcess, NodeType, ProcessEdge, ProcessNode, ProcessValidation } from '@/types/process'
import { NODE_TYPE_LABELS, isGatewayNode, isTaskNode } from '@/types/process'
import { orthogonalizePath } from '@/lib/edge-routing'
import { formatDuration } from '@/lib/bpmn-export'

/**
 * Наводка холста на фигуры, о которых говорит замечание проверки импорта.
 *
 * Замечание без адресата бесполезно: «Фигуры расширены под подпись: 226» не
 * подсказывает сотруднику, где смотреть. Клик по строке отчёта отправляет сюда
 * список фигур, холст подводит к ним камеру и подсвечивает их до тех пор, пока
 * сотрудник сам не закроет подсказку.
 */
export interface CanvasFocus {
  /** Фигуры замечания в порядке обхода. */
  nodeIds: string[]
  /**
   * Растёт при каждом клике по строке отчёта. Без него повторный клик по уже
   * наведённому замечанию ничего не делал бы: список фигур тот же самый.
   */
  nonce: number
  /** Само замечание — его текст показывается карточкой над холстом. */
  issue?: ProcessValidation
}

interface ProcessVisualizerProps {
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
  selectedNodeId?: string
  /** Замечание, на фигуры которого надо навести карту. */
  focus?: CanvasFocus
  /** Сотрудник закрыл подсказку: подсветку снимаем. */
  onClearFocus?: () => void
}

const GRID_MINOR = 10
const GRID_MAJOR = 100
const MIN_ZOOM = 0.08
const MAX_ZOOM = 3.0
const LANE_HEAD_DEFAULT = 44
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif'
/** Допуск выравнивания линии по осям на холсте, px. */
const CANVAS_SNAP = 7
/** Шаг от двух часов и дольше подсвечивается как узкое место. */
const SLOW_STEP_MINUTES = 120

type FilterId = 'all' | 'rpa' | 'bottlenecks'

const FILTERS: { id: FilterId; icon?: LucideIcon; label: string }[] = [
  { id: 'all', label: 'Все' },
  { id: 'rpa', icon: Cpu, label: 'RPA' },
  { id: 'bottlenecks', icon: AlertTriangle, label: 'SLA' },
]

/** Активный фильтр красится в цвет того, что он показывает. */
const FILTER_ACTIVE: Record<FilterId, string> = {
  all: 'bg-primary text-primary-foreground',
  rpa: 'bg-emerald-600 text-white',
  bottlenecks: 'bg-amber-500 text-white',
}

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

/**
 * Палитра холста.
 *
 * Фигуры рисуются атрибутами SVG, а не классами Tailwind, поэтому `dark:`
 * здесь не работает и цвет приходится выбирать в коде. Раньше палитра была
 * одна — всегда тёмная: в светлой теме карта оставалась чёрной плитой посреди
 * белого интерфейса, и на печати регламента получалась заливка на весь лист.
 *
 * Роли в обеих темах совпадают, различаются только значения, поэтому вся
 * остальная отрисовка ничего не знает о теме и просто берёт `C.<роль>`.
 */
type CanvasPalette = {
  canvas: string
  gridMinor: string
  gridMajor: string
  laneLine: string
  laneHead: string
  laneText: string
  taskFill: string
  taskStroke: string
  taskText: string
  rpaFill: string
  rpaStroke: string
  badFill: string
  badStroke: string
  edge: string
  /** Пунктир без собственного цвета и подписи шлюзов — тише основного потока. */
  edgeSoft: string
  edgeHi: string
  labelBg: string
  labelStroke: string
  gwStroke: string
  start: string
  endOk: string
  endNo: string
  /** Подпись под событием. */
  caption: string
  /** Второстепенная подпись: «ожидание …». */
  captionMuted: string
  storeFill: string
  storeStroke: string
  docFill: string
  docStroke: string
  timerStroke: string
  noteStroke: string
  /** Кольцо вокруг фигуры, на которую навела проверка импорта. */
  focusRing: string
}

const DARK_CANVAS: CanvasPalette = {
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
  edgeSoft: '#e8e8e8',
  edgeHi: '#7db7ff',
  labelBg: '#1a1a1a',
  labelStroke: '#3a3a3a',
  gwStroke: '#e6b422',
  start: '#f3f3f3',
  endOk: '#5ee08a',
  endNo: '#ff6b6b',
  caption: '#d8d8d8',
  captionMuted: '#9a9a9a',
  // Артефакты и промежуточные события (2-ILOVA)
  storeFill: '#10201c',
  storeStroke: '#4fd1c5',
  docFill: '#1b1a10',
  docStroke: '#d7c56a',
  timerStroke: '#c9a227',
  noteStroke: '#9aa0a6',
  focusRing: '#ff9f0a',
}

/**
 * Светлый холст держится ближе к тому, как та же схема выглядит в draw.io и
 * в PIX Процессной студии: белый лист, тёмный контур, цветом выделены только
 * роботы, узкие места и артефакты.
 */
const LIGHT_CANVAS: CanvasPalette = {
  canvas: '#ffffff',
  gridMinor: 'rgba(15,23,42,0.055)',
  gridMajor: 'rgba(15,23,42,0.13)',
  laneLine: '#94a3b8',
  laneHead: '#f1f5f9',
  laneText: '#0f172a',
  taskFill: '#ffffff',
  taskStroke: '#334155',
  taskText: '#0f172a',
  rpaFill: '#ecfdf5',
  rpaStroke: '#059669',
  badFill: '#fffbeb',
  badStroke: '#d97706',
  edge: '#475569',
  edgeSoft: '#64748b',
  edgeHi: '#2563eb',
  labelBg: '#ffffff',
  labelStroke: '#cbd5e1',
  gwStroke: '#b45309',
  start: '#0f172a',
  endOk: '#059669',
  endNo: '#dc2626',
  caption: '#334155',
  captionMuted: '#64748b',
  storeFill: '#ecfeff',
  storeStroke: '#0e7490',
  docFill: '#fefce8',
  docStroke: '#a16207',
  timerStroke: '#b45309',
  noteStroke: '#64748b',
  focusRing: '#ea580c',
}

type Box = { x: number; y: number; w: number; h: number; cx: number; cy: number }
type Pt = { x: number; y: number }

/**
 * Невидимая нулевая «фигура» в заданной точке.
 * Нужна для линий draw.io, у которых конец не привязан к фигуре: маршрут
 * строится теми же функциями, что и для обычных связей.
 */
function anchorStub(id: string, at: { x: number; y: number }): ProcessNode {
  return {
    id,
    name: '',
    type: 'textAnnotation',
    geometry: { x: at.x, y: at.y, width: 1, height: 1 },
    style: '',
  }
}

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
  return (
    n.type === 'startEvent' ||
    n.type === 'endEvent' ||
    n.type === 'intermediateTimerEvent' ||
    n.type === 'intermediateMessageEvent'
  )
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

function isGatewayShape(n: ProcessNode): boolean {
  return isGatewayNode(n.type)
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
      intersectBorder(srcBox, startToward, isEventNode(src), isGatewayShape(src))
  const end = isTgtLane
    ? {
        x: Math.max(tgtBox.x, Math.min(srcBox.cx, tgtBox.x + tgtBox.w)),
        y: srcBox.cy < tgtBox.cy ? tgtBox.y : tgtBox.y + tgtBox.h,
      }
    : constraintPoint(tgtBox, edge.entryX, edge.entryY) ||
      intersectBorder(tgtBox, endToward, isEventNode(tgt), isGatewayShape(tgt))

  let pts: Pt[]
  if (wp.length > 0) {
    // Изломы из draw.io нельзя соединять напрямую: редактор ведёт линию между
    // ними по осям, а прямое соединение даёт диагонали.
    pts = orthogonalizePath([start, ...wp, end], edge, CANVAS_SNAP)
  } else if (isOrthogonalEdge(edge)) {
    pts = buildOrthogonalPts(start, end, wp, edge, src, tgt)
  } else {
    const dx = end.x - start.x
    const dy = end.y - start.y
    if (Math.abs(dx) < 8 || Math.abs(dy) < 8) pts = [start, end]
    else pts = buildOrthogonalPts(start, end, wp, edge, src, tgt)
  }
  // Концы линии лежат на границе фигуры (у событий — на окружности), поэтому
  // финальное выравнивание по осям делаем с допуском в несколько пикселей.
  pts = orthogonalizePath(pts, edge, CANVAS_SNAP)
  if (pts.length < 2) pts = [start, end]

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

/**
 * Длительность у значка часов. Формат общий с выгрузкой (`bpmn-export`):
 * холст и файл, открытый в Процессной студии, обязаны показывать шагу одно
 * и то же время одними и теми же словами.
 */
function slaLabel(mins?: number): string {
  return formatDuration(mins)
}

/**
 * Значок внутри ромба шлюза — тот же, что рисует draw.io и ждёт bpmn.io.
 *
 * Раньше всё, кроме параллельного, помечалось крестиком, а сложный шлюз вообще
 * приходил на холст плюсом: аналитик рисовал звёздочку, а видел «И». Значок —
 * это и есть тип шлюза, подменять его нельзя.
 */
function gatewayMarker(type: NodeType, cx: number, cy: number, s: number, color: string): React.ReactNode {
  const r = s * 0.52
  const w = Math.max(1.6, s * 0.13)
  if (type === 'inclusiveGateway') {
    return <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={w * 1.2} />
  }
  if (type === 'complexGateway') {
    // Шестилучевая звёздочка: три отрезка через центр под 0°, 60° и 120°.
    return (
      <g stroke={color} strokeWidth={w} strokeLinecap="round">
        {[0, 60, 120].map((deg) => {
          const a = (deg * Math.PI) / 180
          const dx = Math.cos(a) * r
          const dy = Math.sin(a) * r
          return <line key={deg} x1={cx - dx} y1={cy - dy} x2={cx + dx} y2={cy + dy} />
        })}
      </g>
    )
  }
  if (type === 'parallelGateway') {
    return (
      <g stroke={color} strokeWidth={w} strokeLinecap="round">
        <line x1={cx - r} y1={cy} x2={cx + r} y2={cy} />
        <line x1={cx} y1={cy - r} x2={cx} y2={cy + r} />
      </g>
    )
  }
  const d = r * 0.78
  return (
    <g stroke={color} strokeWidth={w} strokeLinecap="round">
      <line x1={cx - d} y1={cy - d} x2={cx + d} y2={cy + d} />
      <line x1={cx - d} y1={cy + d} x2={cx + d} y2={cy - d} />
    </g>
  )
}

/** Значок и цвет уровня замечания в карточке наводки. */
const FOCUS_LEVEL_STYLE: Record<ProcessValidation['level'], { text: string; icon: React.ReactNode }> = {
  error:   { text: 'text-red-600 dark:text-red-400',   icon: <XCircle className="h-4 w-4" /> },
  warning: { text: 'text-amber-600 dark:text-amber-400', icon: <AlertTriangle className="h-4 w-4" /> },
  info:    { text: 'text-sky-600 dark:text-sky-400',   icon: <Info className="h-4 w-4" /> },
}

/** Максимальный масштаб, до которого холст «подъезжает» к фигуре замечания. */
const FOCUS_MAX_ZOOM = 1.1
/** Поля вокруг подсвеченной группы при наводке камеры, px экрана. */
const FOCUS_PADDING = 90
/** Больше этой доли холста карточка замечания занять не может. */
const FOCUS_CARD_MAX_SHARE = 0.45

export const ProcessVisualizer: React.FC<ProcessVisualizerProps> = ({
  process,
  onSelectNode,
  selectedNodeId,
  focus,
  onClearFocus,
}) => {
  const [zoom, setZoom]                 = useState(0.7)
  const [showGrid, setShowGrid]         = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [activeFilter, setActiveFilter] = useState<'all' | 'rpa' | 'bottlenecks'>('all')
  const [searchQuery, setSearchQuery]   = useState('')
  const [panPos, setPanPos]             = useState({ x: 24, y: 24 })
  const [isPanning, setIsPanning]       = useState(false)
  /** Какая по счёту фигура замечания сейчас в центре: по ней листает карточка. */
  const [focusIndex, setFocusIndex]     = useState(0)
  /** Сколько холста снизу занимает карточка замечания — меряем, а не угадываем. */
  const [cardHeight, setCardHeight]     = useState(0)
  const cardRef  = useRef<HTMLDivElement>(null)
  /** Рамка, к которой сейчас подведена камера: нужна при изменении размера. */
  const focusBoxRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  const panRef   = useRef({ ox: 0, oy: 0, px: 0, py: 0 })
  const wrapRef  = useRef<HTMLDivElement>(null)
  /**
   * Синхронная копия положения холста. Зум «к точке» должен пересчитать сдвиг
   * по тому масштабу, что виден прямо сейчас, а `zoom` из состояния во время
   * серии событий колеса отстаёт на кадр — карта уезжала бы из-под курсора.
   */
  const viewRef  = useRef({ zoom, x: panPos.x, y: panPos.y })
  /** Активные пальцы на холсте — для панорамы одним и щипка двумя. */
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const pinchRef = useRef<{ dist: number; zoom: number } | null>(null)

  const isDark = useIsDark()
  const C = isDark ? DARK_CANVAS : LIGHT_CANVAS

  /** Единственная точка, через которую меняется положение холста. */
  const setView = useCallback((next: { zoom: number; x: number; y: number }) => {
    viewRef.current = next
    setZoom(next.zoom)
    setPanPos({ x: next.x, y: next.y })
  }, [])

  /**
   * Масштабирование с якорем: точка под курсором (или под серединой щипка)
   * остаётся на месте. Без якоря карта при каждом повороте колеса уползает
   * к левому верхнему углу, и на большой схеме нужную область приходится
   * догонять перетаскиванием.
   */
  const zoomTo = useCallback(
    (nextZoom: number, clientX?: number, clientY?: number) => {
      const { zoom: z, x, y } = viewRef.current
      const next = +Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom)).toFixed(3)
      if (next === z) return
      const rect = wrapRef.current?.getBoundingClientRect()
      if (!rect || clientX == null || clientY == null) {
        // Без якоря сохраняем центр видимой области.
        const cx = (rect?.width ?? 0) / 2
        const cy = (rect?.height ?? 0) / 2
        setView({ zoom: next, x: cx - ((cx - x) * next) / z, y: cy - ((cy - y) * next) / z })
        return
      }
      const px = clientX - rect.left
      const py = clientY - rect.top
      setView({ zoom: next, x: px - ((px - x) * next) / z, y: py - ((py - y) * next) / z })
    },
    [setView],
  )

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
    if (activeFilter === 'bottlenecks') list = list.filter(n => (n.slaMinutes || 0) >= SLOW_STEP_MINUTES)
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
    setView({
      zoom: z,
      x: (width  - bounds.w * z) / 2 - bounds.minX * z,
      y: (height - bounds.h * z) / 2 - bounds.minY * z,
    })
  }, [bounds, setView])

  // ── Наводка на фигуры замечания ─────────────────────────────────────────
  /** Фигуры замечания, которые действительно есть на карте. */
  const focusNodes = useMemo(() => {
    if (!focus?.nodeIds?.length) return [] as ProcessNode[]
    const byId = new Map(process.nodes.map((n) => [n.id, n]))
    return focus.nodeIds.map((id) => byId.get(id)).filter((n): n is ProcessNode => Boolean(n))
  }, [focus, process.nodes])

  const focusIds = useMemo(() => new Set(focusNodes.map((n) => n.id)), [focusNodes])

  /**
   * Подводит камеру к прямоугольнику карты, не приближая сильнее обычного
   * чтения. Рамку запоминаем: когда панель отчёта схлопнется и холст станет
   * выше, наводку надо повторить — иначе фигура останется у самого края.
   */
  const centerOn = useCallback(
    (box: { x: number; y: number; w: number; h: number }) => {
      focusBoxRef.current = box
      const el = wrapRef.current
      if (!el) return
      const { width, height } = el.getBoundingClientRect()
      if (width < 40 || height < 40) return
      // Карточка замечания занимает низ холста: центрируем фигуру в том, что
      // от холста остаётся, иначе подсказка закрывает ровно то, на что навела.
      // Больше 45 % холста карточке не отдаём — на низком окне от него иначе
      // не остаётся ничего, и «подъезд» к фигуре превращался в отъезд.
      const usable = Math.max(height - cardHeight, height * (1 - FOCUS_CARD_MAX_SHARE))
      // Поля тоже пропорциональные: фиксированные 90 px на невысоком холсте
      // съедали всю доступную высоту, и масштаб схлопывался до минимума.
      const padX = Math.min(FOCUS_PADDING, width * 0.15)
      const padY = Math.min(FOCUS_PADDING, usable * 0.15)
      const fit = Math.min(
        (width - padX * 2) / Math.max(box.w, 1),
        (usable - padY * 2) / Math.max(box.h, 1),
      )
      const z = +Math.min(FOCUS_MAX_ZOOM, Math.max(MIN_ZOOM, fit)).toFixed(3)
      setView({
        zoom: z,
        x: width / 2 - (box.x + box.w / 2) * z,
        y: usable / 2 - (box.y + box.h / 2) * z,
      })
    },
    [setView, cardHeight],
  )

  /** Общая рамка нескольких фигур. */
  const unionBox = useCallback(
    (nodes: ProcessNode[]) => {
      if (!nodes.length) return null
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const n of nodes) {
        const b = nodeBoxes.get(n.id) ?? rawBox(n)
        minX = Math.min(minX, b.x)
        minY = Math.min(minY, b.y)
        maxX = Math.max(maxX, b.x + b.w)
        maxY = Math.max(maxY, b.y + b.h)
      }
      return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
    },
    [nodeBoxes],
  )

  // Новое замечание: снимаем фильтры (иначе нужная фигура останется погашенной)
  // и показываем всю группу целиком — сотрудник сперва видит масштаб проблемы,
  // а потом листает фигуры по одной.
  useEffect(() => {
    if (!focus || !focusNodes.length) return
    setActiveFilter('all')
    setSearchQuery('')
    setFocusIndex(0)
    const box = unionBox(focusNodes)
    if (box) centerOn(box)
    wrapRef.current?.focus({ preventScroll: true })
    // Наводка привязана к клику (nonce), а не к составу группы: повторный клик
    // по той же строке отчёта обязан вернуть камеру на место.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce])

  // Панель отчёта схлопывается в тот же клик, что и наводка: холст становится
  // выше уже после того, как камера встала. Пересчитываем наводку по факту
  // изменения размеров — заодно это чинит и обычное изменение окна.
  useEffect(() => {
    const el = wrapRef.current
    if (!el || !focus) {
      focusBoxRef.current = null
      return
    }
    const ro = new ResizeObserver(() => {
      const box = focusBoxRef.current
      if (box) centerOn(box)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [focus, centerOn])

  // Высота карточки зависит от длины замечания, поэтому её меряем.
  useEffect(() => {
    const el = cardRef.current
    if (!el) {
      setCardHeight(0)
      return
    }
    const ro = new ResizeObserver(() => setCardHeight(el.offsetHeight + 24))
    ro.observe(el)
    setCardHeight(el.offsetHeight + 24)
    return () => ro.disconnect()
  }, [focus, focusIndex])

  /** Показать одну фигуру группы крупным планом. */
  const goToFocusNode = useCallback(
    (index: number) => {
      const node = focusNodes[index]
      if (!node) return
      setFocusIndex(index)
      const b = nodeBoxes.get(node.id) ?? rawBox(node)
      centerOn(b)
    },
    [focusNodes, nodeBoxes, centerOn],
  )

  /** Расстояние между двумя пальцами — база для щипка. */
  const pinchDistance = () => {
    const [a, b] = [...pointers.current.values()]
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0
  }
  const pinchCenter = () => {
    const [a, b] = [...pointers.current.values()]
    return a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null
  }

  // Указатели вместо мыши: те же обработчики обслуживают и мышь, и палец, и
  // перо. На планшете карту нельзя было ни сдвинуть, ни масштабировать —
  // `onMouseDown` о касаниях не знает.
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // Панели поверх холста (карточка замечания, «ничего не найдено») живут
    // внутри той же области, что и карта. Без этой проверки холст забирает
    // указатель себе через setPointerCapture, и клик по кнопке панели до неё
    // просто не доходит.
    if ((e.target as HTMLElement).closest('.nb, [data-canvas-overlay]')) return
    if (e.pointerType === 'mouse' && e.button !== 0) return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    e.currentTarget.setPointerCapture(e.pointerId)
    if (pointers.current.size === 2) {
      pinchRef.current = { dist: pinchDistance(), zoom: viewRef.current.zoom }
      setIsPanning(false)
      return
    }
    setIsPanning(true)
    panRef.current = { ox: e.clientX, oy: e.clientY, px: viewRef.current.x, py: viewRef.current.y }
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(e.pointerId)) return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })

    if (pointers.current.size >= 2 && pinchRef.current) {
      const dist = pinchDistance()
      const center = pinchCenter()
      if (dist > 0 && pinchRef.current.dist > 0) {
        zoomTo(pinchRef.current.zoom * (dist / pinchRef.current.dist), center?.x, center?.y)
      }
      return
    }
    if (!isPanning) return
    setView({
      zoom: viewRef.current.zoom,
      x: panRef.current.px + e.clientX - panRef.current.ox,
      y: panRef.current.py + e.clientY - panRef.current.oy,
    })
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId)
    if (pointers.current.size < 2) pinchRef.current = null
    if (pointers.current.size === 0) setIsPanning(false)
  }

  /**
   * Клавиатура работает только когда холст в фокусе: тот же компонент открыт
   * и внутри окна просмотра выгрузки, и глобальный обработчик перехватывал бы
   * стрелки у остальной страницы.
   */
  const onCanvasKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 120 : 40
    const { zoom: z, x, y } = viewRef.current
    switch (e.key) {
      case '+':
      case '=':
        zoomTo(z + 0.15); break
      case '-':
      case '_':
        zoomTo(z - 0.15); break
      case '0':
        fitToScreen(); break
      case 'ArrowLeft':
        setView({ zoom: z, x: x + step, y }); break
      case 'ArrowRight':
        setView({ zoom: z, x: x - step, y }); break
      case 'ArrowUp':
        setView({ zoom: z, x, y: y + step }); break
      case 'ArrowDown':
        setView({ zoom: z, x, y: y - step }); break
      default:
        return
    }
    e.preventDefault()
  }

  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // Escape снимает сначала подсветку, и только потом выходит из полного
      // экрана: иначе сотрудник теряет весь контекст одним нажатием.
      if (focus && onClearFocus) onClearFocus()
      else setIsFullscreen(false)
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [focus, onClearFocus])

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
      // Множитель, а не слагаемое: на 15 % шаг колеса ощущается одинаково и на
      // мелком, и на крупном масштабе, тогда как фиксированные 0.08 у нижней
      // границы перепрыгивали половину диапазона.
      const factor = e.deltaY > 0 ? 1 / 1.12 : 1.12
      zoomTo(viewRef.current.zoom * factor, e.clientX, e.clientY)
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [zoomTo])

  const patternOffset = {
    x: ((panPos.x % (GRID_MAJOR * zoom)) + GRID_MAJOR * zoom) % (GRID_MAJOR * zoom),
    y: ((panPos.y % (GRID_MAJOR * zoom)) + GRID_MAJOR * zoom) % (GRID_MAJOR * zoom),
  }

  return (
    <div className={`flex flex-col overflow-hidden rounded-xl border bg-card shadow-sm transition-all duration-200 ${
      isFullscreen ? 'fixed inset-0 z-50 rounded-none border-none' : 'flex-1 min-h-0 h-full'
    }`}>
      {/* Панель холста.
          Собрана лентами, а не одной строкой с `flex-wrap`: на ноутбуке в один
          ряд помещается всё, ниже `md` фильтры и управление расходятся на две
          строки, и ни одна кнопка не выпадает за край. */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/40 px-2.5 py-2">
        <div className="no-scrollbar -mx-0.5 flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto px-0.5">
          <span className="hidden shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground lg:inline">
            Карта
          </span>
          <span className="hidden shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] tabular-nums text-muted-foreground sm:inline">
            {process.nodes.length} эл. · {process.lanes.length} дорожек
          </span>
          <span className="mx-0.5 hidden h-4 w-px shrink-0 bg-border sm:block" />
          {FILTERS.map(({ id, icon: FilterIcon, label }) => {
            const active = activeFilter === id
            const count =
              id === 'all'
                ? process.nodes.length
                : id === 'rpa'
                ? process.nodes.filter((n) => n.category === 'rpa_bot').length
                : process.nodes.filter((n) => (n.slaMinutes || 0) >= SLOW_STEP_MINUTES).length
            return (
              <button
                key={id}
                type="button"
                aria-pressed={active}
                onClick={() => setActiveFilter(id)}
                className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1 text-xs font-medium transition-colors ${
                  active
                    ? FILTER_ACTIVE[id]
                    : 'border border-transparent text-muted-foreground hover:bg-muted hover:text-foreground'
                }`}
              >
                {FilterIcon && <FilterIcon className="h-3 w-3 shrink-0" />}
                {label}
                <span className={`tabular-nums ${active ? 'opacity-80' : 'opacity-60'}`}>{count}</span>
              </button>
            )
          })}
        </div>

        <div className="flex w-full shrink-0 items-center gap-1.5 md:w-auto">
          <div className="relative min-w-0 flex-1 md:w-40 md:flex-none lg:w-52">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Поиск по шагам…"
              aria-label="Поиск по шагам карты"
              className="h-8 pl-8 pr-7 text-xs"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                aria-label="Очистить поиск"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Зум собран одним сегментом: три соседние кнопки без общей рамки
              читались как три независимых действия. Процент — кнопка «вписать
              в экран», а не подпись: возвращать масштаб приходится чаще всего. */}
          <div className="flex shrink-0 items-center rounded-lg border bg-background p-0.5">
            <Button
              variant="ghost" size="icon" className="h-7 w-7"
              onClick={() => zoomTo(viewRef.current.zoom - 0.15)}
              title="Отдалить (−)" aria-label="Отдалить"
            >
              <ZoomOut className="h-3.5 w-3.5" />
            </Button>
            <button
              type="button"
              onClick={fitToScreen}
              title="Вписать карту в экран (0)"
              aria-label="Вписать карту в экран"
              className="w-11 rounded px-1 py-1 text-[11px] font-medium tabular-nums text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {Math.round(zoom * 100)}%
            </button>
            <Button
              variant="ghost" size="icon" className="h-7 w-7"
              onClick={() => zoomTo(viewRef.current.zoom + 0.15)}
              title="Приблизить (+)" aria-label="Приблизить"
            >
              <ZoomIn className="h-3.5 w-3.5" />
            </Button>
          </div>

          <Button
            variant="ghost" size="icon"
            className={`h-8 w-8 shrink-0 ${showGrid ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}
            onClick={() => setShowGrid((v) => !v)}
            aria-pressed={showGrid}
            title="Сетка 10 px" aria-label="Показать сетку"
          >
            <Grid className="h-3.5 w-3.5" />
          </Button>

          <Button
            variant={isFullscreen ? 'secondary' : 'ghost'}
            size="sm"
            className="h-8 shrink-0 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setIsFullscreen((v) => !v)}
            title={isFullscreen ? 'Свернуть (Esc)' : 'Развернуть карту на весь экран'}
            aria-label={isFullscreen ? 'Свернуть карту' : 'Развернуть карту на весь экран'}
          >
            {isFullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            <span className="hidden lg:inline">{isFullscreen ? 'Свернуть' : 'На весь экран'}</span>
          </Button>
        </div>
      </div>

      <div
        ref={wrapRef}
        tabIndex={0}
        role="application"
        aria-label="Холст карты процесса: перетаскивание — панорама, колесо — масштаб, стрелки — сдвиг"
        // touch-none: без этого браузер забирает жест себе и страница
        // прокручивается вместо того, чтобы двигать карту.
        className={`relative flex-1 min-h-0 touch-none overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${
          isPanning ? 'cursor-grabbing' : 'cursor-grab'
        }`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onCanvasKeyDown}
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
              <polygon points="0 0,8 3,0 6" fill={C.edgeSoft} />
            </marker>
            <marker id="arr-dashed-red" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0,8 3,0 6" fill={C.endNo} />
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
              // Оформительская линия draw.io: конец не привязан к фигуре, а задан
              // точкой. Без этого такие линии просто исчезали с холста.
              if (!src && edge.sourcePoint) src = anchorStub(`${edge.id}-src`, edge.sourcePoint)
              if (!tgt && edge.targetPoint) tgt = anchorStub(`${edge.id}-tgt`, edge.targetPoint)
              if (!src || !tgt) return null
              if (activeFilter !== 'all' || searchQuery.trim()) {
                // Свободный конец и дорожка «видимы» всегда: фильтр отсеивает шаги.
                const endVisible = (id?: string) =>
                  !id || visibleIds.has(id) || process.lanes.some(l => l.id === id)
                if (!endVisible(edge.sourceId) || !endVisible(edge.targetId)) return null
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
              // Ассоциация к артефакту красится в цвет артефактов, а
              // оформительские разделители этапов приглушаются — иначе они
              // спорят с потоком управления за внимание.
              const isDecoration = edge.kind === 'annotationLine'
              const isAssociation = edge.kind === 'association'
              const baseColor = edge.strokeColor
                ? edge.strokeColor
                : isAssociation
                ? C.storeStroke
                : isDashed
                ? C.edgeSoft
                : C.edge
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
                  opacity={isDecoration ? 0.32 : 1}
                  markerEnd={markerId} />
              )
            })}

            {process.nodes.map(node => {
              // Подсвеченная фигура не гаснет под фильтром: иначе замечание
              // приводит сотрудника к пустому месту на холсте.
              const faded = !visibleIds.has(node.id) && !focusIds.has(node.id)
              const sel   = selectedNodeId === node.id
              const isRpa = node.category === 'rpa_bot'
              const isBad = (node.slaMinutes || 0) >= SLOW_STEP_MINUTES
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
                        fontSize={cap.fontSize} fill={C.caption}
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

              // Промежуточное событие (2-ILOVA: Oraliq hodisalar) — двойной круг
              // с маркером: таймер «Kutish vaqti» или конверт сообщения.
              if (node.type === 'intermediateTimerEvent' || node.type === 'intermediateMessageEvent') {
                const r = Math.max(8, Math.min(w, h) / 2 - 1)
                const isTimer = node.type === 'intermediateTimerEvent'
                const cap = fitCaption(node.name, 90, 2)
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <circle cx={cx} cy={cy} r={r + 6} fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <circle cx={cx} cy={cy} r={r} fill={C.canvas} stroke={sel ? C.edgeHi : C.timerStroke} strokeWidth={1.6} />
                    <circle cx={cx} cy={cy} r={Math.max(4, r - 3.5)} fill="none" stroke={sel ? C.edgeHi : C.timerStroke} strokeWidth={1.2} />
                    {isTimer ? (
                      <g stroke={C.timerStroke} strokeWidth={1.3} strokeLinecap="round" fill="none">
                        <line x1={cx} y1={cy - r * 0.45} x2={cx} y2={cy} />
                        <line x1={cx} y1={cy} x2={cx + r * 0.32} y2={cy + r * 0.22} />
                      </g>
                    ) : (
                      <g stroke={C.timerStroke} strokeWidth={1.2} fill="none">
                        <rect x={cx - r * 0.45} y={cy - r * 0.3} width={r * 0.9} height={r * 0.6} />
                        <path d={`M${cx - r * 0.45},${cy - r * 0.3} L${cx},${cy + r * 0.05} L${cx + r * 0.45},${cy - r * 0.3}`} />
                      </g>
                    )}
                    {cap.lines.map((line, i) => (
                      <text key={i} x={cx} y={y + h + 11 + i * (cap.fontSize + 2)}
                        textAnchor="middle" fontSize={cap.fontSize} fill={C.timerStroke}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              // Хранилище данных (IABS, EHA, EDO) — цилиндр, как в draw.io и PIX.
              if (node.type === 'dataStore') {
                const ry = Math.max(3, Math.min(8, h * 0.16))
                const cap = fitCaption(node.name, 80, 2)
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    <path
                      d={`M${x},${y + ry} a${w / 2},${ry} 0 0 1 ${w},0 L${x + w},${y + h - ry} a${w / 2},${ry} 0 0 1 ${-w},0 Z`}
                      fill={C.storeFill} stroke={sel ? C.edgeHi : C.storeStroke} strokeWidth={sel ? 2 : 1.4} />
                    <path d={`M${x},${y + ry} a${w / 2},${ry} 0 0 0 ${w},0`}
                      fill="none" stroke={sel ? C.edgeHi : C.storeStroke} strokeWidth={1.2} />
                    {cap.lines.map((line, i) => (
                      <text key={i} x={cx} y={cy + ry + i * (cap.fontSize + 1)}
                        textAnchor="middle" dominantBaseline="central"
                        fontSize={cap.fontSize} fill={C.storeStroke}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              // Объект данных (Dalolatnoma, Yig'ma jild) — лист с загнутым углом.
              if (node.type === 'dataObject') {
                const fold = Math.max(6, Math.min(14, w * 0.22))
                const cap = fitCaption(node.name, 80, 3)
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    <path
                      d={`M${x},${y} L${x + w - fold},${y} L${x + w},${y + fold} L${x + w},${y + h} L${x},${y + h} Z`}
                      fill={C.docFill} stroke={sel ? C.edgeHi : C.docStroke} strokeWidth={sel ? 2 : 1.4} />
                    <path d={`M${x + w - fold},${y} L${x + w - fold},${y + fold} L${x + w},${y + fold}`}
                      fill="none" stroke={sel ? C.edgeHi : C.docStroke} strokeWidth={1.2} />
                    {cap.lines.map((line, i) => (
                      <text key={i} x={cx} y={cy - (cap.lines.length - 1) * (cap.fontSize + 1) / 2 + i * (cap.fontSize + 1)}
                        textAnchor="middle" dominantBaseline="central"
                        fontSize={cap.fontSize} fill={C.docStroke}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              // Текстовое примечание — скобка слева, как в BPMN.
              if (node.type === 'textAnnotation') {
                const cap = fitCaption(node.name, 110, 4)
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    <path d={`M${x + 8},${y} L${x},${y} L${x},${y + h} L${x + 8},${y + h}`}
                      fill="none" stroke={sel ? C.edgeHi : C.noteStroke} strokeWidth={1.4} />
                    {cap.lines.map((line, i) => (
                      <text key={i} x={x + 12} y={y + 10 + i * (cap.fontSize + 2)}
                        textAnchor="start" fontSize={cap.fontSize} fill={C.noteStroke}
                        style={{ userSelect: 'none', fontFamily: FONT }}>
                        {line}
                      </text>
                    ))}
                  </g>
                )
              }

              if (isGatewayNode(node.type)) {
                const s = Math.max(12, Math.min(w, h) / 2)
                const cap = node.name && node.name !== 'Условие' ? fitCaption(node.name, 92, 2) : null
                return (
                  <g key={node.id} opacity={faded ? 0.18 : 1}
                    onClick={() => onSelectNode(node)} style={{ cursor: 'pointer' }} className="nb">
                    {sel && <polygon points={`${cx},${cy-s-6} ${cx+s+6},${cy} ${cx},${cy+s+6} ${cx-s-6},${cy}`}
                      fill="none" stroke={C.edgeHi} strokeWidth="1.2" strokeDasharray="4 3" />}
                    <polygon points={`${cx},${cy-s} ${cx+s},${cy} ${cx},${cy+s} ${cx-s},${cy}`}
                      fill={C.canvas} stroke={sel ? C.edgeHi : C.gwStroke} strokeWidth={1.8} />
                    {gatewayMarker(node.type, cx, cy, s, C.gwStroke)}
                    {cap && cap.lines.map((line, i) => (
                      <text key={i} x={cx} y={y - 6 - (cap.lines.length - 1 - i) * (cap.fontSize + 1)}
                        textAnchor="middle" fontSize={cap.fontSize} fill={C.caption}
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
                  {(sla || !!node.waitMinutes) && (() => {
                    // Бейдж длительности — как в draw.io: часы в правом нижнем
                    // углу шага и время под ними. Сама фигура-таймер в модель не
                    // попадает (её время уходит в ST шага), и без бейджа на карте
                    // терялось, сколько шаг занимает.
                    const bx = x + w - 15
                    const by = y + h - 15
                    return (
                      <g pointerEvents="none">
                        <circle cx={bx} cy={by} r={8} fill={C.canvas}
                          stroke={C.timerStroke} strokeWidth={1.2} />
                        <circle cx={bx} cy={by} r={5.6} fill="none"
                          stroke={C.timerStroke} strokeWidth={0.9} />
                        <path d={`M${bx},${by - 3.4} L${bx},${by} L${bx + 2.8},${by + 1.6}`}
                          fill="none" stroke={C.timerStroke} strokeWidth={1.1}
                          strokeLinecap="round" strokeLinejoin="round" />
                        {!!sla && (
                          <text x={bx} y={y + h + 12} textAnchor="middle"
                            fontSize="9.5" fill={C.timerStroke}
                            style={{ userSelect: 'none', fontFamily: FONT }}>
                            {sla}
                          </text>
                        )}
                        {!!node.waitMinutes && (
                          <text x={bx} y={y + h + (sla ? 23 : 12)} textAnchor="middle"
                            fontSize="9" fill={C.captionMuted}
                            style={{ userSelect: 'none', fontFamily: FONT }}>
                            {`ожидание ${slaLabel(node.waitMinutes)}`}
                          </text>
                        )}
                      </g>
                    )
                  })()}
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
                    fill={C.labelBg} stroke={C.labelStroke} strokeWidth="0.7" />
                  <text x={lb.lx} y={lb.ly + 3} textAnchor="middle"
                    fontSize={lb.cap.fontSize} fill={C.taskText}
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

            {/* Кольца вокруг фигур замечания. Рисуются последними: подсветка
                должна лежать поверх шага, а не под ним. Пульсация задана одной
                анимацией на группе — 200 отдельных SMIL-анимаций подвешивали
                вкладку на больших картах. */}
            {focusNodes.length > 0 && (
              <g className="sqb-focus-pulse" pointerEvents="none">
                {focusNodes.map((node, index) => {
                  const b = nodeBoxes.get(node.id) ?? rawBox(node)
                  const pad = 7
                  // Толщина задаётся в единицах карты, а группа масштабируется:
                  // делим на zoom, иначе на мелком масштабе кольцо исчезает.
                  const w = Math.max(1.5, 3 / zoom)
                  return (
                    <rect
                      key={node.id}
                      x={b.x - pad}
                      y={b.y - pad}
                      width={b.w + pad * 2}
                      height={b.h + pad * 2}
                      rx={Math.max(4, 8 / zoom)}
                      fill="none"
                      stroke={C.focusRing}
                      strokeWidth={index === focusIndex ? w * 1.8 : w}
                      strokeDasharray={index === focusIndex ? undefined : `${10 / zoom} ${6 / zoom}`}
                    />
                  )
                })}
              </g>
            )}
          </g>
        </svg>

        {/* Карточка замечания: что не так, с какой фигурой и что делать.
            Держится над холстом, пока сотрудник её не закроет — уведомление в
            углу исчезает раньше, чем он успевает найти фигуру глазами. */}
        {focus && focusNodes.length > 0 && (
          <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-3">
            <div
              ref={cardRef}
              data-canvas-overlay
              role="status"
              className="pointer-events-auto w-full max-w-xl rounded-xl border border-orange-500/40 bg-card/95 p-3 shadow-xl backdrop-blur"
            >
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 shrink-0 ${FOCUS_LEVEL_STYLE[focus.issue?.level ?? 'info'].text}`}>
                  {FOCUS_LEVEL_STYLE[focus.issue?.level ?? 'info'].icon}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold leading-snug">
                    {focus.issue?.message ?? 'Фигуры замечания на карте'}
                  </p>
                  {focus.issue?.hint && (
                    <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                      {focus.issue.hint}
                    </p>
                  )}
                  <div className="mt-2 rounded-lg border bg-muted/40 px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <Crosshair className="h-3.5 w-3.5 shrink-0 text-orange-500" />
                      <span className="min-w-0 truncate text-xs font-medium">
                        {focusNodes[focusIndex]?.name}
                      </span>
                      {focusNodes[focusIndex]?.code && (
                        <span className="shrink-0 rounded bg-background px-1 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                          {focusNodes[focusIndex].code}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {[
                        NODE_TYPE_LABELS[focusNodes[focusIndex].type],
                        focusNodes[focusIndex].laneName || focusNodes[focusIndex].role,
                        focusNodes[focusIndex].system,
                        isTaskNode(focusNodes[focusIndex].type)
                          ? slaLabel(focusNodes[focusIndex].slaMinutes)
                          : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Button
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => onSelectNode(focusNodes[focusIndex])}
                    >
                      Открыть карточку шага
                    </Button>
                    {focusNodes.length > 1 && (
                      <>
                        <Button
                          variant="outline" size="sm" className="h-7 w-7 p-0"
                          aria-label="Предыдущая фигура замечания"
                          onClick={() => goToFocusNode((focusIndex - 1 + focusNodes.length) % focusNodes.length)}
                        >
                          <ChevronLeft className="h-3.5 w-3.5" />
                        </Button>
                        <span className="text-[11px] tabular-nums text-muted-foreground">
                          {focusIndex + 1} из {focusNodes.length}
                        </span>
                        <Button
                          variant="outline" size="sm" className="h-7 w-7 p-0"
                          aria-label="Следующая фигура замечания"
                          onClick={() => goToFocusNode((focusIndex + 1) % focusNodes.length)}
                        >
                          <ChevronRight className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0"
                  aria-label="Снять подсветку"
                  onClick={onClearFocus}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Ничего не найдено — иначе после неудачного поиска остаётся пустой
            холст без объяснения, почему все фигуры погасли. */}
        {(searchQuery.trim() || activeFilter !== 'all') && visibleIds.size === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
            <div data-canvas-overlay className="pointer-events-auto max-w-xs rounded-xl border bg-card/95 p-4 text-center shadow-lg backdrop-blur">
              <Search className="mx-auto h-5 w-5 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">Шаги не найдены</p>
              <p className="mt-1 text-xs text-muted-foreground">
                По этому запросу и фильтру на карте нет ни одной фигуры.
              </p>
              <Button
                variant="outline" size="sm" className="mt-3 h-7 text-xs"
                onClick={() => { setSearchQuery(''); setActiveFilter('all') }}
              >
                Сбросить фильтры
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Легенда прокручивается вбок: шесть обозначений и подсказка не влезают
          в 400 px, а переносом строки они съедали высоту самой карты. */}
      <div className="flex shrink-0 items-center gap-3 border-t bg-muted/40 px-3 py-1.5 text-[10px] text-muted-foreground">
        <div className="no-scrollbar flex min-w-0 flex-1 items-center gap-3 overflow-x-auto whitespace-nowrap">
          <span className="flex shrink-0 items-center gap-1"><span className="inline-block h-3 w-3 rounded-full border-2 border-foreground/70" />Старт</span>
          <span className="flex shrink-0 items-center gap-1"><span className="inline-block h-3 w-3 rounded-full border-2 border-emerald-500" />Успех</span>
          <span className="flex shrink-0 items-center gap-1"><span className="inline-block h-3 w-3 rounded-full border-2 border-rose-500" />Отказ</span>
          <span className="flex shrink-0 items-center gap-1"><span className="inline-block h-3 w-3 rounded border border-emerald-500 bg-emerald-500/15" />PIX RPA</span>
          <span className="flex shrink-0 items-center gap-1"><span className="inline-block h-3 w-3 rotate-45 border border-amber-500" />Шлюз</span>
          <span className="flex shrink-0 items-center gap-1"><span className="inline-block h-3 w-3 rounded border border-amber-500 bg-amber-500/15" />SLA&nbsp;&gt;&nbsp;2&nbsp;ч</span>
        </div>
        <span className="hidden shrink-0 items-center gap-1 xl:flex">
          <Info className="h-3 w-3 text-sky-500" />
          Клик — детали · колесо — масштаб · перетаскивание — сдвиг
        </span>
      </div>
    </div>
  )
}
