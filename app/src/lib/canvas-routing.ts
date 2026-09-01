import type { ProcessEdge } from '@/types/process'

/**
 * Трассировка связей на холсте карты — «как в draw.io».
 *
 * Модуль `edge-routing.ts` рядом решает другую задачу: он клиентский двойник
 * серверного `edge_routing.py` и обслуживает выгрузку в BPMN, где ломаная
 * обязана совпасть с файлом. Здесь задача рисовальная — линия должна читаться
 * глазом: якорь садится на середину грани, лишние изломы убираются, а связи,
 * идущие одним коридором, разводятся по параллельным дорожкам.
 *
 * Порядок работы:
 *  1. Для каждого конца определяется грань — из стиля draw.io (`exitX`/`entryX`)
 *     либо по взаимному положению фигур.
 *  2. Если грани смотрят друг на друга, якоря съезжают в общий створ: связь
 *     становится одной прямой без изломов. Это главный источник «прямоты».
 *  3. Из полутора десятка заготовок маршрута выбирается та, что выходит и
 *     входит с нужных сторон, не режет фигуры и имеет меньше изломов.
 *  4. Общий проход разводит коридоры, в которые попали несколько связей.
 *
 * Связи, у которых в draw.io не задан `edgeStyle`, редактор рисует прямым
 * отрезком — их мы тоже ведём отрезком, иначе короткая подводка к значку
 * системы превращается в ступеньку из двух изломов.
 */

export type Point = { x: number; y: number }
export type Box = { x: number; y: number; w: number; h: number; cx: number; cy: number }
export type Side = 'left' | 'right' | 'top' | 'bottom'

/** Конец связи: фигура и, если он задан жёстко, точка вместе с гранью. */
export interface EdgeEnd {
  box: Box
  /** Идентификатор фигуры: по нему связи, севшие на одну грань, разводятся. */
  id?: string
  /**
   * У фигуры нет прямых граней — ромб шлюза, окружность события.
   * Якорь такой фигуры живёт строго в середине грани рамки, то есть в вершине
   * ромба или в крайней точке окружности: сдвинь его вдоль рамки — и линия
   * начнётся в пустоте рядом с фигурой.
   */
  centered?: boolean
  /** Точка привязана намертво: конец на дорожке или свободный конец draw.io. */
  pinned?: Point
  /** Грань, с которой связь обязана выйти (или в которую войти). */
  side?: Side
}

export interface EdgeRouteRequest {
  edge: ProcessEdge
  from: EdgeEnd
  to: EdgeEnd
}

/** Длина перпендикулярного «уса» от грани фигуры (jettySize у draw.io), px. */
const JETTY = 20
/** Зазор до фигуры, ближе которого обходной коридор не проводим, px. */
const CLEARANCE = 16
/** Насколько раздвигаются связи, попавшие в один коридор, px. */
const CORRIDOR_GAP = 10
/** Короче этого совпадение коридоров глазом не читается, px. */
const CORRIDOR_MIN_OVERLAP = 26
/** Меньшее перекрытие граней в общий створ не сводим, px. */
const ALIGN_MIN_OVERLAP = 12
/** На сколько можно подвинуть якорь, заданный автором в draw.io, px. */
const ALIGN_FIXED_SNAP = 6
/** Доля грани, в пределах которой держим съехавший якорь. */
const ANCHOR_SPREAD = 0.86
/**
 * То же для ромба и окружности.
 *
 * У них грань рамки касается фигуры единственной точкой — вершиной: якорь,
 * отъехавший от неё на четверть грани, повисает рядом с фигурой в пустоте.
 * Узкая полоса оставляет запас ровно на выравнивание в пару пикселей.
 */
const CENTERED_SPREAD = 0.2
/** Допуск выравнивания почти-осевого отрезка по оси, px. */
const AXIS_SNAP = 7
/** Короче этого излом считается паразитным и схлопывается, px. */
const JOG_SNAP = 9
/** Ширина «шпильки» — возврата линии по самой себе, которую схлопываем, px. */
const SPIKE_WIDTH = 16
/** Длиннее этого наклонный отрезок читается как перекос, а не как связка, px. */
const STRAIGHT_SPAN = 120
/** Ближе этого два якоря на одной грани сливаются в один, px. */
const ANCHOR_MIN_GAP = 15
/** Куда пробуем отодвинуть якорь, если его место занято, px. */
const ANCHOR_STEPS = [0, 18, -18, 36, -36, 54, -54]
const EPS = 0.5
/** Насколько далеко от границы фракция ещё считается серединой грани. */
const SIDE_TOLERANCE = 0.02

const NORMAL: Record<Side, Point> = {
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
  top: { x: 0, y: -1 },
  bottom: { x: 0, y: 1 },
}

const isHorizontalSide = (side: Side) => side === 'left' || side === 'right'

// ── Мелкая геометрия ────────────────────────────────────────────────────────

function same(a: Point, b: Point): boolean {
  return Math.abs(a.x - b.x) < EPS && Math.abs(a.y - b.y) < EPS
}

/** Убирает дубли и точки, лежащие внутри прямого отрезка. */
function simplify(points: Point[]): Point[] {
  const out: Point[] = []
  for (const pt of points) {
    const last = out[out.length - 1]
    if (last && same(last, pt)) continue
    out.push(pt)
  }
  return out.filter((pt, i) => {
    if (i === 0 || i === out.length - 1) return true
    const prev = out[i - 1]
    const next = out[i + 1]
    const sameX = Math.abs(prev.x - pt.x) < EPS && Math.abs(pt.x - next.x) < EPS
    const sameY = Math.abs(prev.y - pt.y) < EPS && Math.abs(pt.y - next.y) < EPS
    return !(sameX || sameY)
  })
}

function isOrthogonalChain(pts: Point[]): boolean {
  for (let i = 1; i < pts.length; i++) {
    if (Math.abs(pts[i].x - pts[i - 1].x) > EPS && Math.abs(pts[i].y - pts[i - 1].y) > EPS) return false
  }
  return true
}

/** Направление первого невырожденного отрезка. */
function leadDirection(pts: Point[]): Point | null {
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i].x - pts[i - 1].x
    const dy = pts[i].y - pts[i - 1].y
    if (Math.abs(dx) > EPS || Math.abs(dy) > EPS) return { x: Math.sign(dx), y: Math.sign(dy) }
  }
  return null
}

/** Направление последнего невырожденного отрезка. */
function tailDirection(pts: Point[]): Point | null {
  for (let i = pts.length - 1; i > 0; i--) {
    const dx = pts[i].x - pts[i - 1].x
    const dy = pts[i].y - pts[i - 1].y
    if (Math.abs(dx) > EPS || Math.abs(dy) > EPS) return { x: Math.sign(dx), y: Math.sign(dy) }
  }
  return null
}

/** Пересекает ли осевой отрезок внутренность фигуры. */
function crossesBox(a: Point, b: Point, box: Box, inset = 1.5): boolean {
  const x1 = Math.min(a.x, b.x)
  const x2 = Math.max(a.x, b.x)
  const y1 = Math.min(a.y, b.y)
  const y2 = Math.max(a.y, b.y)
  return (
    x2 > box.x + inset &&
    x1 < box.x + box.w - inset &&
    y2 > box.y + inset &&
    y1 < box.y + box.h - inset
  )
}

/** Держится ли точка за эту фигуру (лежит на грани или внутри). */
function touchesBox(box: Box, pt: Point, slack = 2): boolean {
  return (
    pt.x >= box.x - slack &&
    pt.x <= box.x + box.w + slack &&
    pt.y >= box.y - slack &&
    pt.y <= box.y + box.h + slack
  )
}

/** Точечная рамка вокруг якоря — для концов, прибитых к точке. */
function pointBox(at: Point): Box {
  return { x: at.x - 1, y: at.y - 1, w: 2, h: 2, cx: at.x, cy: at.y }
}

function pathLength(pts: Point[]): number {
  let total = 0
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y)
  return total
}

// ── Чистка ломаной ──────────────────────────────────────────────────────────

/**
 * Доводит почти-осевые отрезки до осевых.
 *
 * Концы линии лежат на границе фигуры (у события — на окружности), и дробная
 * координата даёт отрезку едва заметный скос: на холсте это читается как
 * кривая линия.
 */
function snapToAxes(points: Point[], tolerance = AXIS_SNAP): Point[] {
  const out = points.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }))
  for (let i = 1; i < out.length; i++) {
    const prev = out[i - 1]
    const cur = out[i]
    const dx = Math.abs(cur.x - prev.x)
    const dy = Math.abs(cur.y - prev.y)
    if (dx === 0 || dy === 0) continue
    if (dy <= tolerance) out[i] = { x: cur.x, y: prev.y }
    else if (dx <= tolerance) out[i] = { x: prev.x, y: cur.y }
  }
  return simplify(out)
}

/**
 * Схлопывает «шпильки» — узкие возвраты линии по самой себе.
 *
 * draw.io хранит в файле изломы, оставшиеся от прежних правок: связь уходит
 * на сотню пикселей вверх, сдвигается на пиксель вбок и возвращается обратно.
 * Редактор такую петлю не показывает, а холст рисовал её честно — над верхней
 * дорожкой торчал непонятный хвост.
 */
function collapseSpikes(points: Point[], width = SPIKE_WIDTH): Point[] {
  const pts = points.slice()
  for (let guard = 0; guard < pts.length; guard++) {
    let changed = false
    for (let i = 0; i + 3 < pts.length; i++) {
      const d0 = { x: pts[i + 1].x - pts[i].x, y: pts[i + 1].y - pts[i].y }
      const d2 = { x: pts[i + 3].x - pts[i + 2].x, y: pts[i + 3].y - pts[i + 2].y }
      const bridge = Math.hypot(pts[i + 2].x - pts[i + 1].x, pts[i + 2].y - pts[i + 1].y)
      if (bridge > width) continue
      // Ход туда и сразу обратно по той же оси — вся вылазка бессмысленна.
      if (d0.x * d2.x + d0.y * d2.y >= 0) continue
      pts.splice(i + 1, 2)
      changed = true
      break
    }
    if (!changed) break
  }
  return pts
}

/**
 * Убирает паразитные ступеньки в пару пикселей.
 *
 * Такие изломы берутся из расхождения якорей: автор поставил `exitY=0.5` и
 * `entryY=0.5` у фигур разной высоты, и линия делает ступеньку в один пиксель.
 */
function mergeMicroJogs(points: Point[], tolerance = JOG_SNAP): Point[] {
  const pts = points.map((p) => ({ ...p }))
  if (pts.length < 3) return pts

  // Ступенька у самого начала: прижимаем первую точку к оси второго отрезка.
  const headLen = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y)
  if (headLen <= tolerance && pts.length >= 3) {
    if (Math.abs(pts[2].y - pts[1].y) < EPS) pts[0] = { x: pts[0].x, y: pts[1].y }
    else pts[0] = { x: pts[1].x, y: pts[0].y }
  }
  const n = pts.length
  const tailLen = Math.hypot(pts[n - 1].x - pts[n - 2].x, pts[n - 1].y - pts[n - 2].y)
  if (tailLen <= tolerance && n >= 3) {
    if (Math.abs(pts[n - 3].y - pts[n - 2].y) < EPS) pts[n - 1] = { x: pts[n - 1].x, y: pts[n - 2].y }
    else pts[n - 1] = { x: pts[n - 2].x, y: pts[n - 1].y }
  }

  // Ступенька в середине: соседние отрезки параллельны, перемычка между ними
  // короче допуска — выкидываем её и выравниваем остаток по оси.
  for (let i = 1; i + 2 < pts.length; i++) {
    const len = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
    if (len > tolerance) continue
    const before = Math.abs(pts[i].y - pts[i - 1].y) < EPS
    const after = Math.abs(pts[i + 2].y - pts[i + 1].y) < EPS
    if (before !== after) continue
    pts.splice(i, 2)
    i--
  }
  return pts
}

/** Полная чистка ломаной: шпильки, ступеньки, выравнивание по осям. */
export function tidyPath(points: Point[], tolerance = AXIS_SNAP): Point[] {
  let pts = simplify(points)
  if (pts.length < 2) return pts
  pts = collapseSpikes(pts)
  pts = snapToAxes(pts, tolerance)
  pts = mergeMicroJogs(pts)
  pts = snapToAxes(pts, tolerance)
  return pts.length >= 2 ? pts : points.slice(0, 2)
}

// ── Якоря ───────────────────────────────────────────────────────────────────

/** Грань, на которой сидит якорь draw.io. `toward` разрешает угловые случаи. */
function sideFromFractions(
  fx: number | undefined,
  fy: number | undefined,
  box: Box,
  toward: Point,
): Side | null {
  const cands: Side[] = []
  if (fx != null && Number.isFinite(fx)) {
    if (fx <= SIDE_TOLERANCE) cands.push('left')
    if (fx >= 1 - SIDE_TOLERANCE) cands.push('right')
  }
  if (fy != null && Number.isFinite(fy)) {
    if (fy <= SIDE_TOLERANCE) cands.push('top')
    if (fy >= 1 - SIDE_TOLERANCE) cands.push('bottom')
  }
  if (!cands.length) return null
  if (cands.length === 1) return cands[0]
  const px = box.x + (fx ?? 0.5) * box.w
  const py = box.y + (fy ?? 0.5) * box.h
  let best = cands[0]
  let bestDot = -Infinity
  for (const s of cands) {
    const n = NORMAL[s]
    const dot = n.x * (toward.x - px) + n.y * (toward.y - py)
    if (dot > bestDot) {
      bestDot = dot
      best = s
    }
  }
  return best
}

/** Пара граней для связи без якорей в стиле — по взаимному положению фигур. */
function preferredSides(a: Box, b: Box): [Side, Side] {
  const vOverlap = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  const hOverlap = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  if (vOverlap > ALIGN_MIN_OVERLAP) {
    if (b.x - (a.x + a.w) > 0) return ['right', 'left']
    if (a.x - (b.x + b.w) > 0) return ['left', 'right']
  }
  if (hOverlap > ALIGN_MIN_OVERLAP) {
    if (b.y - (a.y + a.h) > 0) return ['bottom', 'top']
    if (a.y - (b.y + b.h) > 0) return ['top', 'bottom']
  }
  const dx = b.cx - a.cx
  const dy = b.cy - a.cy
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ['right', 'left'] : ['left', 'right']
  return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom']
}

/** Грань фигуры, смотрящая в сторону уже известного конца связи. */
function facingSide(box: Box, toward: Point): Side {
  const dx = toward.x - box.cx
  const dy = toward.y - box.cy
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left'
  return dy >= 0 ? 'bottom' : 'top'
}

/** Отрезок грани, вдоль которого якорь может ездить. */
function sideSpan(box: Box, side: Side): [number, number] {
  return isHorizontalSide(side) ? [box.y, box.y + box.h] : [box.x, box.x + box.w]
}

/** Тот же отрезок, но с полями: у самого угла якорь смотрится ошибкой. */
function sideSpread(box: Box, side: Side, rigid = false): [number, number] {
  const [lo, hi] = sideSpan(box, side)
  const mid = (lo + hi) / 2
  const half = ((hi - lo) / 2) * (rigid ? CENTERED_SPREAD : ANCHOR_SPREAD)
  return [mid - half, mid + half]
}

function anchorAt(box: Box, side: Side, along: number): Point {
  switch (side) {
    case 'left':
      return { x: box.x, y: along }
    case 'right':
      return { x: box.x + box.w, y: along }
    case 'top':
      return { x: along, y: box.y }
    default:
      return { x: along, y: box.y + box.h }
  }
}

function alongOf(point: Point, side: Side): number {
  return isHorizontalSide(side) ? point.y : point.x
}

// ── Маршрут ─────────────────────────────────────────────────────────────────

interface Candidate {
  pts: Point[]
  bends: number
  crossings: number
  length: number
}

function evaluate(
  raw: Point[],
  na: Point,
  nb: Point,
  guardA: Box | null,
  guardB: Box | null,
  obstacles: Box[],
): Candidate | null {
  const pts = simplify(raw)
  if (pts.length < 2) return null
  if (!isOrthogonalChain(pts)) return null
  const lead = leadDirection(pts)
  const tail = tailDirection(pts)
  if (!lead || lead.x !== na.x || lead.y !== na.y) return null
  if (!tail || tail.x !== -nb.x || tail.y !== -nb.y) return null

  let crossings = 0
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    if (guardA && crossesBox(a, b, guardA)) return null
    if (guardB && crossesBox(a, b, guardB)) return null
    for (const box of obstacles) {
      if (crossesBox(a, b, box)) crossings++
    }
  }
  return { pts, bends: pts.length - 2, crossings, length: pathLength(pts) }
}

/**
 * Заготовки маршрута между двумя якорями.
 *
 * Порядок важен: при равном счёте побеждает та, что встретилась раньше, а
 * первыми идут варианты, которые рисует сам draw.io, — колено и створ через
 * середину промежутка.
 */
function candidates(a: Point, na: Point, b: Point, nb: Point, boxA: Box, boxB: Box): Point[][] {
  const aj = { x: a.x + na.x * JETTY, y: a.y + na.y * JETTY }
  const bj = { x: b.x + nb.x * JETTY, y: b.y + nb.y * JETTY }
  const midX = (a.x + b.x) / 2
  const midY = (a.y + b.y) / 2
  const midXj = (aj.x + bj.x) / 2
  const midYj = (aj.y + bj.y) / 2
  const outer = {
    top: Math.min(boxA.y, boxB.y) - CLEARANCE,
    bottom: Math.max(boxA.y + boxA.h, boxB.y + boxB.h) + CLEARANCE,
    left: Math.min(boxA.x, boxB.x) - CLEARANCE,
    right: Math.max(boxA.x + boxA.w, boxB.x + boxB.w) + CLEARANCE,
  }

  const list: Point[][] = [
    [a, b],
    [a, { x: b.x, y: a.y }, b],
    [a, { x: a.x, y: b.y }, b],
    [a, { x: midXj, y: a.y }, { x: midXj, y: b.y }, b],
    [a, { x: a.x, y: midYj }, { x: b.x, y: midYj }, b],
    [a, { x: midX, y: a.y }, { x: midX, y: b.y }, b],
    [a, { x: a.x, y: midY }, { x: b.x, y: midY }, b],
    [a, aj, { x: aj.x, y: bj.y }, bj, b],
    [a, aj, { x: bj.x, y: aj.y }, bj, b],
  ]
  for (const y of [outer.top, outer.bottom]) {
    list.push([a, { x: a.x, y }, { x: b.x, y }, b])
    list.push([a, aj, { x: aj.x, y }, { x: bj.x, y }, bj, b])
  }
  for (const x of [outer.left, outer.right]) {
    list.push([a, { x, y: a.y }, { x, y: b.y }, b])
    list.push([a, aj, { x, y: aj.y }, { x, y: bj.y }, bj, b])
  }
  return list
}

function chooseRoute(
  a: Point,
  sideA: Side,
  b: Point,
  sideB: Side,
  boxA: Box,
  boxB: Box,
  obstacles: Box[],
  guardA: Box | null,
  guardB: Box | null,
): Point[] {
  const na = NORMAL[sideA]
  const nb = NORMAL[sideB]
  // Крюк вокруг всей схемы формально не режет ни одной фигуры, поэтому по
  // штрафам он обыгрывает честный короткий маршрут. Ограничиваем длину: связь
  // между соседними шагами не имеет права уходить на другой конец карты.
  const budget = (Math.abs(b.x - a.x) + Math.abs(b.y - a.y)) * 3 + JETTY * 10
  let best: Candidate | null = null
  for (const raw of candidates(a, na, b, nb, boxA, boxB)) {
    const cand = evaluate(raw, na, nb, guardA, guardB, obstacles)
    if (!cand || cand.length > budget) continue
    const score = cand.crossings * 1000 + cand.bends * 60 + cand.length * 0.05
    const bestScore = best ? best.crossings * 1000 + best.bends * 60 + best.length * 0.05 : Infinity
    if (score < bestScore - 0.001) best = cand
  }
  if (best) return best.pts
  // Ни одна заготовка не подошла (например, фигуры наложились друг на друга) —
  // ведём линию через усы: она заведомо ортогональна и выходит с нужных граней.
  const aj = { x: a.x + na.x * JETTY, y: a.y + na.y * JETTY }
  const bj = { x: b.x + nb.x * JETTY, y: b.y + nb.y * JETTY }
  const knee = isHorizontalSide(sideA) ? { x: bj.x, y: aj.y } : { x: aj.x, y: bj.y }
  return simplify([a, aj, knee, bj, b])
}

// ── Разведение коридоров ────────────────────────────────────────────────────

interface Corridor {
  /** Координата поперёк коридора: x у вертикального, y у горизонтального. */
  coord: number
  lo: number
  hi: number
}

function overlaps(a: Corridor, b: Corridor, gap: number): boolean {
  if (Math.abs(a.coord - b.coord) >= gap) return false
  return Math.min(a.hi, b.hi) - Math.max(a.lo, b.lo) > CORRIDOR_MIN_OVERLAP
}

/**
 * Разводит связи, попавшие в один коридор.
 *
 * Две линии, лёгшие на одну прямую, читаются как одна: на карте пропадает
 * половина потока. Двигаем только внутренние отрезки — те, у которых оба конца
 * являются изломами: сдвинуть отрезок у самой фигуры значит оторвать линию
 * от якоря.
 */
export function separateCorridors(routes: Point[][], gap = CORRIDOR_GAP): void {
  const vertical: Corridor[] = []
  const horizontal: Corridor[] = []
  const movable: { pts: Point[]; index: number; vertical: boolean }[] = []

  for (const pts of routes) {
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1]
      const b = pts[i]
      const isVertical = Math.abs(a.x - b.x) < EPS
      const isHorizontal = Math.abs(a.y - b.y) < EPS
      if (!isVertical && !isHorizontal) continue
      const corridor: Corridor = isVertical
        ? { coord: a.x, lo: Math.min(a.y, b.y), hi: Math.max(a.y, b.y) }
        : { coord: a.y, lo: Math.min(a.x, b.x), hi: Math.max(a.x, b.x) }
      // Отрезок с якорем на конце занимает коридор, но подвинуть его нельзя.
      if (i - 1 === 0 || i === pts.length - 1) {
        ;(isVertical ? vertical : horizontal).push(corridor)
      } else {
        movable.push({ pts, index: i, vertical: isVertical })
      }
    }
  }

  for (const item of movable) {
    const a = item.pts[item.index - 1]
    const b = item.pts[item.index]
    const taken = item.vertical ? vertical : horizontal
    const base = item.vertical ? a.x : a.y
    const span: [number, number] = item.vertical
      ? [Math.min(a.y, b.y), Math.max(a.y, b.y)]
      : [Math.min(a.x, b.x), Math.max(a.x, b.x)]

    let shift = 0
    for (const step of [0, gap, -gap, gap * 2, -gap * 2, gap * 3, -gap * 3]) {
      const probe: Corridor = { coord: base + step, lo: span[0], hi: span[1] }
      if (!taken.some((t) => overlaps(probe, t, gap))) {
        shift = step
        break
      }
    }
    if (shift !== 0) {
      if (item.vertical) {
        a.x += shift
        b.x += shift
      } else {
        a.y += shift
        b.y += shift
      }
    }
    taken.push({ coord: base + shift, lo: span[0], hi: span[1] })
  }
}

// ── Точка входа ─────────────────────────────────────────────────────────────

/**
 * Годится ли связь для прямого отрезка.
 *
 * Короткая наклонная связка — подводка к значку системы, привязка примечания —
 * выглядит опрятно и повторяет draw.io. Длинный пологий наклон через полкарты
 * читается уже не как связь, а как перекос: такую связь лучше провести по осям.
 */
function isCompactChain(points: Point[]): boolean {
  for (let i = 1; i < points.length; i++) {
    const dx = Math.abs(points[i].x - points[i - 1].x)
    const dy = Math.abs(points[i].y - points[i - 1].y)
    if (dx <= AXIS_SNAP || dy <= AXIS_SNAP) continue
    if (dx > STRAIGHT_SPAN || dy > STRAIGHT_SPAN) return false
  }
  return true
}

/** Стиль draw.io без `edgeStyle` — редактор рисует такую связь отрезком. */
function isStraightStyle(edge: ProcessEdge): boolean {
  const style = (edge.style || '').toLowerCase()
  const explicit = (edge.edgeStyle || '').toLowerCase()
  if (explicit) return !(explicit.includes('orthogonal') || explicit.includes('elbow'))
  // Карта, пришедшая не из draw.io (например, из BPMN), стиля не несёт —
  // для неё ортогональная трассировка и есть ожидаемый вид.
  if (!style) return false
  return !(style.includes('orthogonal') || style.includes('elbow'))
}

interface ResolvedEnd {
  point: Point
  side: Side
  /** Якорь задан автором в draw.io или прибит к точке — двигать нельзя. */
  fixed: boolean
  /** Якорь сидит в вершине ромба или окружности — вдоль грани не ездит. */
  rigid: boolean
  /** Якорь съехал по грани ради прямой линии — место за ним лучше сохранить. */
  aligned?: boolean
}

/** Якорь, который двигать нельзя: конец на дорожке или заданный в стиле. */
function fixedAnchor(
  end: EdgeEnd,
  fx: number | undefined,
  fy: number | undefined,
  toward: Point,
): ResolvedEnd | null {
  if (end.pinned) {
    return { point: { ...end.pinned }, side: end.side ?? facingSide(end.box, toward), fixed: true, rigid: false }
  }
  const side = sideFromFractions(fx, fy, end.box, toward)
  if (side && fx != null && fy != null) {
    return {
      point: { x: end.box.x + fx * end.box.w, y: end.box.y + fy * end.box.h },
      side,
      fixed: true,
      rigid: false,
    }
  }
  return null
}

/** Якорь по умолчанию — середина грани, как ставит его draw.io. */
function freeAnchor(end: EdgeEnd, side: Side): ResolvedEnd {
  const along = isHorizontalSide(side) ? end.box.cy : end.box.cx
  return { point: anchorAt(end.box, side, along), side, fixed: false, rigid: Boolean(end.centered) }
}

/**
 * Определяет обе грани разом.
 *
 * Разом — потому что грани должны смотреть навстречу друг другу: если считать
 * каждый конец отдельно, легко получить пару «вправо — вниз», из которой
 * прямая уже не выйдет ни при каком выравнивании якорей.
 */
function resolveEnds(
  edge: ProcessEdge,
  from: EdgeEnd,
  to: EdgeEnd,
  towardA: Point,
  towardB: Point,
): [ResolvedEnd, ResolvedEnd] {
  const fa = fixedAnchor(from, edge.exitX, edge.exitY, towardA)
  const fb = fixedAnchor(to, edge.entryX, edge.entryY, towardB)
  if (fa && fb) return [fa, fb]
  if (fa) return [fa, freeAnchor(to, to.side ?? facingSide(to.box, fa.point))]
  if (fb) return [freeAnchor(from, from.side ?? facingSide(from.box, fb.point)), fb]
  const [sideA, sideB] = preferredSides(from.box, to.box)
  return [freeAnchor(from, from.side ?? sideA), freeAnchor(to, to.side ?? sideB)]
}

/**
 * Сводит якоря в общий створ, чтобы связь стала одной прямой.
 *
 * Это и есть ответ на «линии должны быть прямыми»: пока каждый конец сидит
 * ровно посреди своей грани, любая пара фигур с разной высотой даёт лишнюю
 * ступеньку, хотя между ними есть общий створ.
 */
function alignAnchors(
  from: EdgeEnd,
  a: ResolvedEnd,
  to: EdgeEnd,
  b: ResolvedEnd,
  obstacles: Box[],
): void {
  const na = NORMAL[a.side]
  const nb = NORMAL[b.side]
  // Прямая возможна, только когда грани смотрят навстречу друг другу.
  if (na.x !== -nb.x || na.y !== -nb.y) return
  const horizontal = isHorizontalSide(a.side)
  const alongA = alongOf(a.point, a.side)
  const alongB = alongOf(b.point, b.side)
  if (Math.abs(alongA - alongB) < EPS) return

  const set = (end: ResolvedEnd, box: Box, value: number) => {
    end.point = anchorAt(box, end.side, value)
    end.aligned = true
  }

  if (a.fixed && b.fixed) {
    if (Math.abs(alongA - alongB) <= ALIGN_FIXED_SNAP) set(a, from.box, alongB)
    return
  }

  const clear = (value: number) => {
    const p1 = horizontal ? { x: a.point.x, y: value } : { x: value, y: a.point.y }
    const p2 = horizontal ? { x: b.point.x, y: value } : { x: value, y: b.point.y }
    return !obstacles.some(
      (box) =>
        !touchesBox(box, a.point) && !touchesBox(box, b.point) && crossesBox(p1, p2, box),
    )
  }

  /** Подводит подвижный конец под уже определившийся. */
  const slideTo = (free: ResolvedEnd, freeBox: Box, target: number) => {
    const [lo, hi] = sideSpread(freeBox, free.side, free.rigid)
    if (target >= lo && target <= hi && clear(target)) set(free, freeBox, target)
  }

  if (a.fixed !== b.fixed) {
    if (a.fixed) slideTo(b, to.box, alongA)
    else slideTo(a, from.box, alongB)
    return
  }

  // Вершина ромба или окружности с места почти не сходит, поэтому в створ к ней
  // подводим второй конец, а не ищем середину общего перекрытия: у жёсткого
  // якоря полоса хода уже, чем сам порог перекрытия, и створ бы не нашёлся.
  if (a.rigid !== b.rigid) {
    if (a.rigid) slideTo(b, to.box, alongA)
    else slideTo(a, from.box, alongB)
    return
  }

  const [aLo, aHi] = sideSpread(from.box, a.side, a.rigid)
  const [bLo, bHi] = sideSpread(to.box, b.side, b.rigid)
  const lo = Math.max(aLo, bLo)
  const hi = Math.min(aHi, bHi)
  if (hi - lo < ALIGN_MIN_OVERLAP) return
  const value = Math.round((lo + hi) / 2)
  if (!clear(value)) return
  set(a, from.box, value)
  set(b, to.box, value)
}

/**
 * Трассирует все связи карты разом.
 *
 * Разом — потому что развести коридоры можно, только видя маршруты целиком:
 * поодиночке каждая связь выглядит корректной, а вместе они ложатся друг
 * на друга.
 */
interface Prepared {
  edge: ProcessEdge
  from: EdgeEnd
  to: EdgeEnd
  a: ResolvedEnd
  b: ResolvedEnd
  waypoints: Point[]
}

/**
 * Разводит якоря, сошедшиеся в одной точке грани.
 *
 * Середина грани — самое естественное место для якоря, поэтому входящая и
 * исходящая связи шага садятся на неё обе и дальше идут по одной прямой: на
 * карте вместо двух связей видна одна. Право остаться на месте получают
 * сначала якоря, заданные автором, потом съехавшие ради прямой линии, и лишь
 * потом остальные.
 */
function spreadAnchors(prepared: Prepared[]): void {
  type Slot = { end: ResolvedEnd; box: Box; order: number }
  const groups = new Map<string, Slot[]>()
  const add = (end: EdgeEnd, resolved: ResolvedEnd) => {
    if (!end.id || end.pinned) return
    const key = `${end.id}|${resolved.side}`
    const list = groups.get(key)
    const order = resolved.fixed || resolved.rigid ? 0 : resolved.aligned ? 1 : 2
    // Жёсткий якорь всё равно почти не сдвинуть, поэтому место он занимает
    // первым — вместе с якорями, заданными автором.
    const slot: Slot = { end: resolved, box: end.box, order }
    if (list) list.push(slot)
    else groups.set(key, [slot])
  }
  for (const item of prepared) {
    add(item.from, item.a)
    add(item.to, item.b)
  }

  for (const slots of groups.values()) {
    if (slots.length < 2) continue
    slots.sort((x, y) => x.order - y.order)
    const taken: number[] = []
    for (const slot of slots) {
      const along = alongOf(slot.end.point, slot.end.side)
      if (slot.end.fixed) {
        taken.push(along)
        continue
      }
      const [lo, hi] = sideSpread(slot.box, slot.end.side, slot.end.rigid)
      let picked = along
      for (const step of ANCHOR_STEPS) {
        const probe = along + step
        if (probe < lo || probe > hi) continue
        if (taken.every((t) => Math.abs(t - probe) >= ANCHOR_MIN_GAP)) {
          picked = probe
          break
        }
      }
      slot.end.point = anchorAt(slot.box, slot.end.side, picked)
      taken.push(picked)
    }
  }
}

/**
 * Трассирует все связи карты разом.
 *
 * Разом — потому что и якоря на общей грани, и коридоры разводятся, только
 * когда схема видна целиком: поодиночке каждая связь выглядит правильной, а
 * вместе они ложатся друг на друга.
 */
export function routeEdges(requests: EdgeRouteRequest[], obstacles: Box[] = []): Map<string, Point[]> {
  const prepared: Prepared[] = []
  for (const { edge, from, to } of requests) {
    const waypoints = (edge.points || []).map((p) => ({ x: p.x, y: p.y }))
    const towardA = waypoints[0] ?? { x: to.box.cx, y: to.box.cy }
    const towardB = waypoints[waypoints.length - 1] ?? { x: from.box.cx, y: from.box.cy }
    const [a, b] = resolveEnds(edge, from, to, towardA, towardB)
    // Изломы автора уже задают ход линии — выравнивать под них якоря незачем.
    if (!waypoints.length) alignAnchors(from, a, to, b, obstacles)
    prepared.push({ edge, from, to, a, b, waypoints })
  }
  spreadAnchors(prepared)

  const result = new Map<string, Point[]>()
  const routed: Point[][] = []
  for (const { edge, from, to, a, b, waypoints } of prepared) {
    let pts: Point[]
    if (isStraightStyle(edge) && isCompactChain([a.point, ...waypoints, b.point])) {
      // draw.io ведёт такую связь отрезком — повторяем, только выравнивая
      // почти-осевые куски, чтобы они не выглядели случайно перекошенными.
      pts = snapToAxes([a.point, ...waypoints, b.point], AXIS_SNAP)
    } else if (waypoints.length) {
      // Изломы автора уважаем, но чистим наследие прежних правок.
      pts = tidyPath(routeThroughWaypoints(a, waypoints, b))
    } else {
      // Точка, прибитая к дорожке или к свободному концу, лежит внутри своей
      // рамки, а не на грани: сторожить такую рамку нельзя — иначе от неё не
      // уйдёт ни один маршрут.
      const guardA = from.pinned ? null : from.box
      const guardB = to.pinned ? null : to.box
      // Дорожка тянется на всю ширину карты: если считать обходные каналы по
      // её рамке, «обход вокруг фигуры» уводит связь за край схемы.
      const shapeA = from.pinned ? pointBox(a.point) : from.box
      const shapeB = to.pinned ? pointBox(b.point) : to.box
      pts = tidyPath(
        chooseRoute(a.point, a.side, b.point, b.side, shapeA, shapeB, obstacles, guardA, guardB),
      )
    }
    if (pts.length < 2) pts = [a.point, b.point]
    result.set(edge.id, pts)
    routed.push(pts)
  }

  separateCorridors(routed)
  for (const [id, pts] of result) result.set(id, simplify(pts))
  return result
}

/** Ортогональная ломаная через изломы, заданные в draw.io. */
function routeThroughWaypoints(a: ResolvedEnd, waypoints: Point[], b: ResolvedEnd): Point[] {
  const points = [a.point, ...waypoints, b.point]
  const na = NORMAL[a.side]
  const nb = NORMAL[b.side]
  const out: Point[] = [points[0]]
  const last = points.length - 1
  for (let i = 1; i < points.length; i++) {
    const prev = out[out.length - 1]
    const cur = points[i]
    if (Math.abs(prev.x - cur.x) < EPS || Math.abs(prev.y - cur.y) < EPS) {
      out.push(cur)
      continue
    }
    let elbow: Point
    if (i === last) {
      elbow = nb.x !== 0 ? { x: prev.x, y: cur.y } : { x: cur.x, y: prev.y }
    } else if (i === 1) {
      elbow = na.x !== 0 ? { x: cur.x, y: prev.y } : { x: prev.x, y: cur.y }
    } else {
      const prev2 = out[out.length - 2]
      const wasHorizontal = Math.abs(prev2.y - prev.y) < EPS
      elbow = wasHorizontal ? { x: cur.x, y: prev.y } : { x: prev.x, y: cur.y }
    }
    out.push(elbow)
    out.push(cur)
  }
  return out
}
