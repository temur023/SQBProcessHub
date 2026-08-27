import type { ProcessEdge, ProcessNode } from '@/types/process'

/**
 * Ортогональная трассировка связей — как рисует draw.io.
 *
 * Клиентский двойник `backend/app/services/edge_routing.py`.
 *
 * draw.io ведёт связи стилем `edgeStyle=orthogonalEdgeStyle`: линия выходит из
 * фигуры перпендикулярно грани и идёт только по осям. В файле при этом хранятся
 * лишь изломы, подвинутые руками, — остальное редактор достраивает сам. Если
 * выгружать в BPMN только «точка выхода → точка входа», схема в bpmn.io
 * превращается в паутину диагоналей.
 */

export type Point = { x: number; y: number }
export type Direction = { dx: number; dy: number }

/** Длина перпендикулярного «уса» от грани фигуры, px. */
const STUB = 20
const EPS = 0.5

type Placed = Map<string, Point> | undefined

function origin(node: ProcessNode, placed: Placed): Point {
  const at = placed?.get(node.id)
  return at ? { x: at.x, y: at.y } : { x: node.geometry.x, y: node.geometry.y }
}

export function anchorPoint(node: ProcessNode, fracX: number, fracY: number, placed?: Placed): Point {
  const o = origin(node, placed)
  return { x: o.x + node.geometry.width * fracX, y: o.y + node.geometry.height * fracY }
}

function center(node: ProcessNode, placed: Placed): Point {
  return anchorPoint(node, 0.5, 0.5, placed)
}

function dominantAxis(src: ProcessNode, tgt: ProcessNode, placed: Placed): Direction {
  const s = center(src, placed)
  const t = center(tgt, placed)
  if (Math.abs(t.x - s.x) >= Math.abs(t.y - s.y)) return { dx: t.x >= s.x ? 1 : -1, dy: 0 }
  return { dx: 0, dy: t.y >= s.y ? 1 : -1 }
}

/** Внешняя нормаль грани, на которой сидит якорь draw.io. */
function sideDirection(fracX?: number, fracY?: number): Direction | null {
  if (fracX != null) {
    if (fracX <= 0) return { dx: -1, dy: 0 }
    if (fracX >= 1) return { dx: 1, dy: 0 }
  }
  if (fracY != null) {
    if (fracY <= 0) return { dx: 0, dy: -1 }
    if (fracY >= 1) return { dx: 0, dy: 1 }
  }
  return null
}

export function exitDirection(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode, placed?: Placed): Direction {
  const side = sideDirection(edge.exitX, edge.exitY)
  if (side) return side
  if (edge.points?.length) {
    const s = center(src, placed)
    const p = edge.points[0]
    if (Math.abs(p.x - s.x) >= Math.abs(p.y - s.y)) return { dx: p.x >= s.x ? 1 : -1, dy: 0 }
    return { dx: 0, dy: p.y >= s.y ? 1 : -1 }
  }
  return dominantAxis(src, tgt, placed)
}

/** Направление ДВИЖЕНИЯ линии в момент входа в целевую фигуру. */
export function entryDirection(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode, placed?: Placed): Direction {
  const side = sideDirection(edge.entryX, edge.entryY)
  if (side) return { dx: -side.dx, dy: -side.dy }
  if (edge.points?.length) {
    const t = center(tgt, placed)
    const p = edge.points[edge.points.length - 1]
    if (Math.abs(t.x - p.x) >= Math.abs(t.y - p.y)) return { dx: t.x >= p.x ? 1 : -1, dy: 0 }
    return { dx: 0, dy: t.y >= p.y ? 1 : -1 }
  }
  return dominantAxis(src, tgt, placed)
}

/** Убирает дубли и точки, лежащие внутри прямого отрезка. */
function simplify(points: Point[]): Point[] {
  const out: Point[] = []
  for (const pt of points) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - pt.x) < EPS && Math.abs(last.y - pt.y) < EPS) continue
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

/** Классическая ломаная draw.io: ус — колено — ус. */
function routeWithoutBends(start: Point, d0: Direction, end: Point, d1: Direction): Point[] {
  const a = { x: start.x + d0.dx * STUB, y: start.y + d0.dy * STUB }
  const b = { x: end.x - d1.dx * STUB, y: end.y - d1.dy * STUB }
  const horizontalExit = d0.dx !== 0
  const horizontalEntry = d1.dx !== 0

  let middle: Point[]
  if (horizontalExit && horizontalEntry) {
    const midX = (a.x + b.x) / 2
    middle = [{ x: midX, y: a.y }, { x: midX, y: b.y }]
  } else if (!horizontalExit && !horizontalEntry) {
    const midY = (a.y + b.y) / 2
    middle = [{ x: a.x, y: midY }, { x: b.x, y: midY }]
  } else if (horizontalExit) {
    middle = [{ x: b.x, y: a.y }]
  } else {
    middle = [{ x: a.x, y: b.y }]
  }
  return simplify([start, a, ...middle, b, end])
}

/** Проводит ортогональную ломаную через изломы, заданные в draw.io. */
function routeThroughBends(points: Point[], d0: Direction, d1: Direction): Point[] {
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
      elbow = d1.dx !== 0 ? { x: prev.x, y: cur.y } : { x: cur.x, y: prev.y }
    } else if (i === 1) {
      elbow = d0.dx !== 0 ? { x: cur.x, y: prev.y } : { x: prev.x, y: cur.y }
    } else {
      const prev2 = out[out.length - 2]
      const wasHorizontal = Math.abs(prev2.y - prev.y) < EPS
      elbow = wasHorizontal ? { x: cur.x, y: prev.y } : { x: prev.x, y: cur.y }
    }
    out.push(elbow)
    out.push(cur)
  }
  return simplify(out)
}

/**
 * Округляет ломаную к целым и добивает выравнивание по осям.
 * Ортогональность считалась в дробных координатах; после округления соседние
 * точки могут разойтись на пиксель, и отрезок становится «почти
 * горизонтальным» — в bpmn.io это видно как едва заметный скос.
 */
function snapToPixelGrid(points: Point[], tolerance = 1): Point[] {
  const snapped = points.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }))
  for (let i = 1; i < snapped.length; i++) {
    const prev = snapped[i - 1]
    const cur = snapped[i]
    const dx = Math.abs(cur.x - prev.x)
    const dy = Math.abs(cur.y - prev.y)
    if (dx === 0 || dy === 0) continue
    if (dy <= tolerance) snapped[i] = { x: cur.x, y: prev.y }
    else if (dx <= tolerance) snapped[i] = { x: prev.x, y: cur.y }
  }
  return simplify(snapped)
}

/**
 * Делает ортогональной уже посчитанную ломаную.
 *
 * Нужна холсту: там концы линии считаются по границе фигуры (и по границе
 * дорожки для линий-разделителей), а изломы берутся из draw.io — соединять их
 * напрямую нельзя, иначе появляются диагонали.
 *
 * `tolerance` — на сколько пикселей допустимо подвинуть точку ради выравнивания
 * по оси. Для холста порог больше единицы: конец линии лежит на окружности
 * события, и его дробная координата даёт едва заметный скос последнего отрезка.
 */
export function orthogonalizePath(points: Point[], edge?: ProcessEdge, tolerance = 1): Point[] {
  if (points.length < 2) return points
  const first = points[0]
  const second = points[1]
  const beforeLast = points[points.length - 2]
  const last = points[points.length - 1]

  const d0 =
    sideDirection(edge?.exitX, edge?.exitY) ??
    (Math.abs(second.x - first.x) >= Math.abs(second.y - first.y)
      ? { dx: second.x >= first.x ? 1 : -1, dy: 0 }
      : { dx: 0, dy: second.y >= first.y ? 1 : -1 })
  const entrySide = sideDirection(edge?.entryX, edge?.entryY)
  const d1 = entrySide
    ? { dx: -entrySide.dx, dy: -entrySide.dy }
    : Math.abs(last.x - beforeLast.x) >= Math.abs(last.y - beforeLast.y)
    ? { dx: last.x >= beforeLast.x ? 1 : -1, dy: 0 }
    : { dx: 0, dy: last.y >= beforeLast.y ? 1 : -1 }

  return snapToPixelGrid(routeThroughBends(points, d0, d1), tolerance)
}

/** Полная ломаная связи в абсолютных координатах карты. */
export function orthogonalWaypoints(
  edge: ProcessEdge,
  src?: ProcessNode,
  tgt?: ProcessNode,
  placed?: Placed,
): Point[] {
  if (!src || !tgt) return []

  const d0 = exitDirection(edge, src, tgt, placed)
  const d1 = entryDirection(edge, src, tgt, placed)

  // Если якорь не задан в стиле, берём середину грани, из которой выходим.
  const exitX = edge.exitX ?? 0.5 + d0.dx * 0.5
  const exitY = edge.exitY ?? 0.5 + d0.dy * 0.5
  const entryX = edge.entryX ?? 0.5 - d1.dx * 0.5
  const entryY = edge.entryY ?? 0.5 - d1.dy * 0.5

  const start = anchorPoint(src, exitX, exitY, placed)
  const end = anchorPoint(tgt, entryX, entryY, placed)

  if (edge.points?.length) {
    return snapToPixelGrid(
      routeThroughBends([start, ...edge.points.map((p) => ({ x: p.x, y: p.y })), end], d0, d1),
    )
  }
  return snapToPixelGrid(routeWithoutBends(start, d0, end, d1))
}
