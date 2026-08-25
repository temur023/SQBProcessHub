/**
 * Pure geometry + text layout helpers for the BPMN visualizer.
 * Extracted from ProcessVisualizer.tsx so the exact same code can be
 * exercised by unit tests and the QA geometry harness (no duplication).
 *
 * Everything here is deterministic and side-effect free: the component and
 * the QA harness both consume the same functions.
 */
import type { BusinessProcess, ProcessEdge, ProcessNode } from '@/types/process'

export type Box = { x: number; y: number; w: number; h: number; cx: number; cy: number }
export type Pt = { x: number; y: number }

export function rawBox(n: ProcessNode): Box {
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

export function isEventNode(n: ProcessNode): boolean {
  return n.type === 'startEvent' || n.type === 'endEvent'
}

export function parseStyleMap(style: string): Record<string, string> {
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

export function styleNum(map: Record<string, string>, key: string, fallback = 0): number {
  const n = Number(map[key])
  return Number.isFinite(n) ? n : fallback
}

/** Perimeter point from mxGraph exitX/exitY (0..1 of the box). */
export function constraintPoint(box: Box, fx?: number, fy?: number): Pt | null {
  if (fx == null || fy == null || Number.isNaN(fx) || Number.isNaN(fy)) return null
  return { x: box.x + fx * box.w, y: box.y + fy * box.h }
}

export function isGatewayNode(n: ProcessNode): boolean {
  return n.type === 'exclusiveGateway' || n.type === 'parallelGateway' || n.type === 'inclusiveGateway'
}

// Точный ромб-пересечение (gateway)
export function intersectRhombus(box: Box, toward: Pt): Pt {
  const dx = toward.x - box.cx
  const dy = toward.y - box.cy
  if (dx === 0 && dy === 0) return { x: box.x + box.w, y: box.cy }
  const hw = box.w / 2
  const hh = box.h / 2
  const t = 1 / (Math.abs(dx) / hw + Math.abs(dy) / hh || 1)
  return { x: box.cx + dx * t, y: box.cy + dy * t }
}

/** Intersection of the ray from the box centre toward `toward` with the shape border. */
export function intersectBorder(box: Box, toward: Pt, circular: boolean, isGateway = false): Pt {
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

export function labelT(x?: number): number {
  if (x == null || Number.isNaN(x)) return 0.5
  // mxGraph uses -1..1 (0 = midpoint); the spec also allows 0..1 along the path.
  if (x < 0 || x > 1) return Math.max(0, Math.min(1, (x + 1) / 2))
  return x
}

export function pointAlong(pts: Pt[], t: number, perp = 0): Pt {
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

export function isOrthogonalEdge(edge: ProcessEdge): boolean {
  const s = (edge.style || '').toLowerCase()
  const es = (edge.edgeStyle || '').toLowerCase()
  return s.includes('orthogonal') || es.includes('orthogonal')
}

/** Does segment [a,b] penetrate the interior of `box` by more than `minLen` px? */
export function segHitsBox(a: Pt, b: Pt, box: Box, minLen = 2): boolean {
  const dx = b.x - a.x
  const dy = b.y - a.y
  if (Math.hypot(dx, dy) < 1) return false
  let t0 = 0
  let t1 = 1
  const p = [-dx, dx, -dy, dy]
  const q = [a.x - box.x, box.x + box.w - a.x, a.y - box.y, box.y + box.h - a.y]
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return false
    } else {
      const r = q[i] / p[i]
      if (p[i] < 0) {
        if (r > t1) return false
        if (r > t0) t0 = r
      } else {
        if (r < t0) return false
        if (r < t1) t1 = r
      }
    }
  }
  return Math.hypot(dx, dy) * (t1 - t0) > minLen
}

function countHits(pts: Pt[], obstacles: Box[]): number {
  let hits = 0
  for (let i = 0; i < pts.length - 1; i++) {
    for (const o of obstacles) {
      if (segHitsBox(pts[i], pts[i + 1], o)) hits++
    }
  }
  return hits
}

function dedupePts(pts: Pt[]): Pt[] {
  return pts.filter((p, i, arr) => i === 0 || p.x !== arr[i - 1].x || p.y !== arr[i - 1].y)
}

/**
 * Obstacle-aware orthogonal routing.
 * Builds several candidate routes (L- and Z-shaped, plus the legacy
 * jetty/mid-lane variants) and picks the one with the fewest intersections
 * with obstacle boxes; among equals, the one with the fewest turns wins.
 */
export function buildOrthogonalPts(
  start: Pt,
  end: Pt,
  wp: Pt[],
  edge: ProcessEdge,
  _src?: ProcessNode,
  _tgt?: ProcessNode,
  obstacles: Box[] = [],
): Pt[] {
  if (wp.length > 0) return [start, ...wp, end]
  const dx = end.x - start.x
  const dy = end.y - start.y
  const adx = Math.abs(dx)
  const ady = Math.abs(dy)
  if (adx < 8 || ady < 8) return [start, end]

  const candidates: Pt[][] = []

  // L-образные маршруты
  candidates.push(dedupePts([start, { x: end.x, y: start.y }, end]))
  candidates.push(dedupePts([start, { x: start.x, y: end.y }, end]))

  // Z-образные через середину (поведение для разных дорожек)
  const midX = Math.round((start.x + end.x) / 2)
  const midY = Math.round((start.y + end.y) / 2)
  candidates.push(dedupePts([start, { x: start.x, y: midY }, { x: end.x, y: midY }, end]))
  candidates.push(dedupePts([start, { x: midX, y: start.y }, { x: midX, y: end.y }, end]))

  // Варианты с "причалом" (jetty) при явных exit/entry ограничениях
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
    let horizontalFirst = adx > ady * 0.7
    if (edge.exitX === 0 || edge.exitX === 1) horizontalFirst = true
    else if (edge.exitY === 0 || edge.exitY === 1) horizontalFirst = false
    else if (edge.entryX === 0 || edge.entryX === 1) horizontalFirst = true
    else if (edge.entryY === 0 || edge.entryY === 1) horizontalFirst = false
    const mid = horizontalFirst ? { x: ex, y: sy } : { x: sx, y: ey }
    const pts: Pt[] = [start]
    if (Math.hypot(sx - start.x, sy - start.y) > 2) pts.push({ x: sx, y: sy })
    pts.push(mid)
    if (Math.hypot(ex - mid.x, ey - mid.y) > 2) pts.push({ x: ex, y: ey })
    pts.push(end)
    candidates.unshift(dedupePts(pts))
  }

  // Выбираем маршрут: минимум пересечений, затем минимум поворотов
  let best = candidates[0]
  let bestScore = Infinity
  for (const c of candidates) {
    const score = countHits(c, obstacles) * 1000 + Math.max(0, c.length - 2)
    if (score < bestScore) {
      bestScore = score
      best = c
      if (score < 1000) break // маршрут без пересечений найден — достаточно
    }
  }
  return best
}

export interface EdgePathResult {
  d: string
  lx: number
  ly: number
  pts: Pt[]
}

export function edgePath(
  src: ProcessNode,
  tgt: ProcessNode,
  srcBox: Box,
  tgtBox: Box,
  edge: ProcessEdge,
  obstacles: Box[] = [],
): EdgePathResult {
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
    pts = buildOrthogonalPts(start, end, wp, edge, src, tgt, obstacles)
  } else {
    const dx = end.x - start.x
    const dy = end.y - start.y
    if (Math.abs(dx) < 8 || Math.abs(dy) < 8) pts = [start, end]
    else pts = buildOrthogonalPts(start, end, wp, edge, src, tgt, obstacles)
  }

  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const rawLabelY = edge.labelY
  const perp = rawLabelY != null ? rawLabelY : (pts.length > 2 ? -8 : -10)
  const label = pointAlong(pts, labelT(edge.labelX), perp)
  return { d, lx: label.x, ly: label.y, pts }
}

export function wrapText(text: string, maxChars: number): string[] {
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

export function fitBoxText(
  text: string,
  width: number,
  height: number,
  maxLines = 3,
): { lines: string[]; fontSize: number } {
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

export function fitCaption(
  text: string,
  maxWidth: number,
  maxLines = 2,
): { lines: string[]; fontSize: number } {
  for (let fs = 10; fs >= 8; fs--) {
    const maxChars = Math.max(6, Math.floor(maxWidth / (fs * 0.56)))
    const lines = wrapText(text, maxChars).slice(0, maxLines)
    if (lines.every((l) => l.length * fs * 0.56 <= maxWidth)) return { lines, fontSize: fs }
  }
  const fs = 8
  const maxChars = Math.max(6, Math.floor(maxWidth / (fs * 0.56)))
  return { lines: wrapText(text, maxChars).slice(0, maxLines), fontSize: fs }
}

export function fitLaneLabel(name: string, laneHeight: number): { lines: string[]; fontSize: number } {
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

export function slaLabel(mins?: number): string {
  if (mins == null) return ''
  if (mins < 1) return `${mins} min`
  if (mins % 60 === 0 && mins >= 60) return `${mins / 60} ч`
  return `${mins} min`
}

export function laneHeaderWidth(lane: ProcessNode): number {
  const m = parseStyleMap(lane.style || '')
  const raw = m['startsize']
  if (raw) {
    const n = Number(raw)
    if (Number.isFinite(n) && n > 0) return Math.max(18, Math.min(80, Math.round(n)))
  }
  return 44
}

const RED_STROKES = new Set([
  '#ff6b6b', '#ef4444', '#dc2626', '#b91c1c', '#e11d48', '#be123c',
  '#f87171', '#fca5a5', '#dc143c', '#991b1b', '#7f1d1d', '#c00000',
  'red', 'rose', 'crimson', 'darkred',
])

/** Red/rose stroke colours mark rejection paths → they must render dashed. */
export function isRedStrokeColor(color?: string | null): boolean {
  if (!color) return false
  return RED_STROKES.has(color.trim().toLowerCase())
}

/** Final dash array for an edge (never undefined for dashed edges). */
export function edgeDashArray(edge: ProcessEdge): string | undefined {
  const isDashed = Boolean(edge.dashed) || Boolean(edge.dashPattern) || isRedStrokeColor(edge.strokeColor)
  if (!isDashed) return undefined
  if (edge.dashPattern) {
    const cleaned = edge.dashPattern.replace(/;/g, ' ').replace(/,/g, ' ').trim()
    if (cleaned) return cleaned
  }
  return '8 8'
}

/** SVG-safe marker id derived from an edge id. */
export function markerIdFor(edge: ProcessEdge): string {
  return `arr-${(edge.id || 'e').replace(/[^A-Za-z0-9_-]/g, '_')}`
}

// ─────────────────────────── Text layout engine ───────────────────────────

export type PlacedTextKind = 'edge-label' | 'event-caption' | 'gw-caption' | 'sla'

export interface PlacedTextLine {
  text: string
  x: number
  y: number
}

export interface PlacedText {
  id: string
  kind: PlacedTextKind
  /** Pill background (null = bare text) */
  pill: { x: number; y: number; w: number; h: number } | null
  lines: PlacedTextLine[]
  fontSize: number
  /** 'auto' → caller decides by context */
  fill: string | 'auto'
  bold?: boolean
  anchor?: 'middle' | 'start' | 'end'
}

export interface LayoutColors {
  labelBg: string
  labelBorder: string
  labelText: string
  eventOk: string
  eventBad: string
  sla: string
  gwCaption: string
}

const DEFAULT_COLORS: LayoutColors = {
  labelBg: '#1a1a1a',
  labelBorder: '#3a3a3a',
  labelText: '#e8e8e8',
  eventOk: '#5ee08a',
  eventBad: '#ff6b6b',
  sla: '#9a9a9a',
  gwCaption: '#e8e8e8',
}

function isRejectLike(node: ProcessNode): boolean {
  const l = `${node.id} ${node.name}`.toLowerCase()
  return (
    l.includes('reject') ||
    l.includes('отказ') ||
    l.includes('rad etildi') ||
    l.includes('bekor') ||
    l.includes('отклон')
  )
}

interface BBoxR {
  x: number
  y: number
  x2: number
  y2: number
}

function bboxArea(b: BBoxR): number {
  return Math.max(0, b.x2 - b.x) * Math.max(0, b.y2 - b.y)
}

function bboxOverlapFrac(a: BBoxR, b: BBoxR): number {
  const w = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x, b.x))
  const h = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y, b.y))
  const area = w * h
  const minArea = Math.min(bboxArea(a), bboxArea(b))
  return minArea > 0 ? area / minArea : 0
}

function textW(text: string, fs: number): number {
  return text.length * fs * 0.56
}

/**
 * Computes the collision-free placement of every floating text on the map:
 * gateway captions, SLA hints, event captions and edge labels.
 * Mirrors what ProcessVisualizer renders, so the QA harness can validate
 * the exact same positions.
 */
export function layoutMapTexts(proc: BusinessProcess, colors: LayoutColors = DEFAULT_COLORS): PlacedText[] {
  const pieces: PlacedText[] = []
  const occupied: BBoxR[] = []

  const nodeById = new Map<string, ProcessNode>()
  for (const n of proc.nodes) nodeById.set(n.id, n)
  const laneById = new Map<string, ProcessNode>()
  for (const l of proc.lanes) laneById.set(l.id, l)
  const boxes = new Map<string, Box>()
  for (const n of [...proc.nodes, ...proc.lanes]) boxes.set(n.id, rawBox(n))
  const resolve = (id?: string) => (id ? nodeById.get(id) || laneById.get(id) : undefined)

  // все узлы (flow + lanes) — препятствия для линий и для текста
  const nodeBBoxes: BBoxR[] = proc.nodes.map((n) => {
    const b = boxes.get(n.id) || rawBox(n)
    return { x: b.x, y: b.y, x2: b.x + b.w, y2: b.y + b.h }
  })

  const overlapFrac = (bb: BBoxR): number => {
    let worst = 0
    for (const o of nodeBBoxes) worst = Math.max(worst, bboxOverlapFrac(bb, o))
    for (const o of occupied) worst = Math.max(worst, bboxOverlapFrac(bb, o))
    return worst
  }

  const pushPiece = (p: PlacedText, bb: BBoxR) => {
    pieces.push(p)
    occupied.push(bb)
  }

  const toRect = (b: BBoxR) => ({ x: b.x, y: b.y, w: b.x2 - b.x, h: b.y2 - b.y })

  // 1. Подписи шлюзов (над ромбом)
  for (const n of proc.nodes) {
    if (!isGatewayNode(n)) continue
    if (!n.name || n.name === 'Условие') continue
    const b = boxes.get(n.id) || rawBox(n)
    const cap = fitCaption(n.name, 92, 2)
    const lineH = cap.fontSize + 1
    const lines: PlacedTextLine[] = cap.lines.map((line, i) => ({
      text: line,
      x: b.cx,
      y: b.y - 6 - (cap.lines.length - 1 - i) * lineH,
    }))
    const w = Math.max(...cap.lines.map((l) => textW(l, cap.fontSize)))
    const bb: BBoxR = {
      x: b.cx - w / 2,
      y: lines[0].y - cap.fontSize * 0.8,
      x2: b.cx + w / 2,
      y2: lines[lines.length - 1].y + cap.fontSize * 0.25,
    }
    pushPiece(
      { id: `${n.id}#gwcap`, kind: 'gw-caption', pill: null, lines, fontSize: cap.fontSize, fill: colors.gwCaption, bold: true },
      bb,
    )
  }

  // 2. SLA-подсказки (под боксами задач)
  for (const n of proc.nodes) {
    if (n.type !== 'userTask' && n.type !== 'task' && n.type !== 'serviceTask') continue
    const sla = slaLabel(n.slaMinutes)
    if (!sla) continue
    const b = boxes.get(n.id) || rawBox(n)
    const w = textW(sla, 9)
    const y = b.y + b.h + 10
    const bb: BBoxR = { x: b.cx - w / 2, y: y - 4.5, x2: b.cx + w / 2, y2: y + 4.5 }
    pushPiece(
      { id: `${n.id}#sla`, kind: 'sla', pill: null, lines: [{ text: sla, x: b.cx, y }], fontSize: 9, fill: colors.sla },
      bb,
    )
  }

  // 3. Подписи событий — ищем свободное место: низ → верх → право → лево
  for (const n of proc.nodes) {
    if (!isEventNode(n)) continue
    if (!n.name) continue
    const b = boxes.get(n.id) || rawBox(n)
    const cap = fitCaption(n.name, 100, 3)
    const lineH = cap.fontSize + 2
    const w = Math.max(...cap.lines.map((l) => textW(l, cap.fontSize))) + 10
    const h = cap.lines.length * lineH + 4
    const fill =
      n.type === 'endEvent'
        ? isRejectLike(n)
          ? colors.eventBad
          : colors.eventOk
        : colors.labelText

    // Дорожка узла (для защиты от непрозрачного заголовка дорожки)
    const lane = proc.lanes.find(
      (l) =>
        b.cx >= l.geometry.x &&
        b.cx <= l.geometry.x + l.geometry.width &&
        b.cy >= l.geometry.y &&
        b.cy <= l.geometry.y + l.geometry.height,
    )
    const headRight = lane ? lane.geometry.x + laneHeaderWidth(lane) : -Infinity

    const makeCandidate = (side: 'bottom' | 'top' | 'right' | 'left'): { pill: BBoxR; lines: PlacedTextLine[] } => {
      const lines: PlacedTextLine[] = []
      if (side === 'bottom' || side === 'top') {
        const y0 = side === 'bottom' ? b.y + b.h + 5 : b.y - 5 - h
        // Не заезжаем под непрозрачный заголовок дорожки
        let px = b.cx - w / 2
        if (px < headRight + 2 && b.x >= headRight - 10) px = headRight + 2
        const yBase = y0 + 2 + lineH / 2
        for (let i = 0; i < cap.lines.length; i++) {
          lines.push({ text: cap.lines[i], x: b.cx, y: yBase + i * lineH })
        }
        return { pill: { x: px, y: y0, x2: px + w, y2: y0 + h }, lines }
      }
      if (side === 'right' || side === 'left') {
        const x0 = side === 'right' ? b.x + b.w + 6 : b.x - 6 - w
        const yBase = b.cy - (cap.lines.length - 1) * lineH / 2
        for (let i = 0; i < cap.lines.length; i++) {
          lines.push({ text: cap.lines[i], x: side === 'right' ? x0 + 5 : x0 + w - 5, y: yBase + i * lineH })
        }
        return { pill: { x: x0, y: b.cy - h / 2, x2: x0 + w, y2: b.cy + h / 2 }, lines }
      }
      return { pill: { x: 0, y: 0, x2: 0, y2: 0 }, lines }
    }

    let chosen: { pill: BBoxR; lines: PlacedTextLine[] } | null = null
    let chosenOverlap = Infinity
    for (const side of ['bottom', 'top', 'right', 'left'] as const) {
      const c = makeCandidate(side)
      const frac = overlapFrac(c.pill)
      if (frac <= 0.02) {
        chosen = c
        break
      }
      if (frac < chosenOverlap) {
        chosenOverlap = frac
        chosen = c
      }
    }
    if (chosen) {
      const anchor = chosen.lines[0].x === b.cx ? 'middle' : chosen.pill.x + 5 === chosen.lines[0].x ? 'start' : 'end'
      pushPiece(
        { id: `${n.id}#cap`, kind: 'event-caption', pill: toRect(chosen.pill), lines: chosen.lines, fontSize: cap.fontSize, fill, bold: n.type === 'endEvent', anchor },
        chosen.pill,
      )
    }
  }

  // 4. Метки рёбер — поиск по t (вдоль пути) и perp (перпендикуляр)
  for (const edge of proc.edges) {
    const raw = (edge.name || '').trim()
    if (!raw) continue
    const src = resolve(edge.sourceId)
    const tgt = resolve(edge.targetId)
    if (!src || !tgt) continue
    const sb = boxes.get(src.id) || rawBox(src)
    const tb = boxes.get(tgt.id) || rawBox(tgt)
    const obstacles: Box[] = []
    for (const n of proc.nodes) {
      if (n.id === src.id || n.id === tgt.id) continue
      const b = boxes.get(n.id) || rawBox(n)
      obstacles.push(b)
    }
    const path = edgePath(src, tgt, sb, tb, edge, obstacles)
    if (path.pts.length < 2) continue
    const totalLen = path.pts.reduce((acc, p, i, arr) => (i ? acc + Math.hypot(p.x - arr[i - 1].x, p.y - arr[i - 1].y) : 0), 0)
    if (totalLen < 4) continue

    const cap = fitCaption(raw, 130, 2)
    if (!cap || !cap.lines[0]) continue
    const fs = cap.fontSize
    const lw = Math.min(150, Math.max(28, Math.max(...cap.lines.map((l) => l.length)) * fs * 0.58 + 10))
    const lh = cap.lines.length * (fs + 2) + 4

    const basePerp = edge.labelY != null ? edge.labelY : (path.pts.length > 2 ? -8 : -10)
    const t0 = labelT(edge.labelX)

    // Кандидаты: базовые позиции, затем сетка t × perp
    const tCandidates = [t0, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8]
    const perpCandidates = [basePerp, -basePerp, 10, -10, 18, -18, 26, -26, 34, -34, 44, -44, 54, -54, 64, -64]
    const seen = new Set<string>()
    let bestPt: Pt | null = null
    let bestFrac = Infinity
    outer: for (const t of tCandidates) {
      for (const p of perpCandidates) {
        const key = `${t.toFixed(2)}|${p}`
        if (seen.has(key)) continue
        seen.add(key)
        const pt = pointAlong(path.pts, t, p)
        const bb: BBoxR = { x: pt.x - lw / 2, y: pt.y - lh / 2, x2: pt.x + lw / 2, y2: pt.y + lh / 2 }
        const frac = overlapFrac(bb)
        if (frac <= 0.02) {
          bestPt = pt
          bestFrac = 0
          break outer
        }
        if (frac < bestFrac) {
          bestFrac = frac
          bestPt = pt
        }
      }
    }

    const pt = bestPt || { x: path.lx, y: path.ly }
    const pill: BBoxR = { x: pt.x - lw / 2, y: pt.y - lh / 2, x2: pt.x + lw / 2, y2: pt.y + lh / 2 }
    const lineH = fs + 2
    const yBase = pt.y - (cap.lines.length - 1) * lineH / 2
    pushPiece(
      {
        id: `${edge.id}#lbl`,
        kind: 'edge-label',
        pill: toRect(pill),
        lines: cap.lines.map((text, i) => ({ text, x: pt.x, y: yBase + i * lineH })),
        fontSize: fs,
        fill: colors.labelText,
      },
      pill,
    )
  }

  return pieces
}
