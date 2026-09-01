import type { ProcessNode } from '@/types/process'
import { ARTIFACT_NODE_TYPES, TASK_NODE_TYPES } from '@/types/process'

/**
 * Приведение геометрии карты к читаемому виду после импорта draw.io.
 *
 * Клиентский двойник `backend/app/services/layout.py`.
 *
 * draw.io прощает то, чего не прощают BPMN-редакторы и PIX Процессная студия:
 *
 * - фигура события объявлена `aspect=fixed` — редактор рисует круг по меньшей
 *   стороне, а рамка может быть прямоугольной (80×50). Импортёр рисует эллипс,
 *   и значок таймера («часы») внутри него растягивается и вылезает за круг;
 * - подпись шага в draw.io свободно выходит за рамку, а bpmn.io и студия
 *   обрезают её по фигуре и кладут поверх маркера задачи в левом верхнем углу;
 * - хранилище данных может лежать поверх шага: в draw.io оно уходит на задний
 *   план, в выгрузке — перекрывает подпись.
 */

/** Кегль подписи фигуры в bpmn.io и в Процессной студии. */
export const FONT_SIZE = 12
/** Межстрочный интервал bpmn.io (1.2 кегля). */
export const LINE_HEIGHT = FONT_SIZE * 1.2
const PAD_X = 12
const PAD_Y = 10
/**
 * Маркер задачи (человечек/шестерёнка) bpmn.io рисует в полосе 12–26 px от
 * верхней грани. Подпись центрируется по высоте и по ширине, поэтому увести
 * первую строку из-под маркера можно только запасом высоты — по 22 px сверху и
 * снизу от текстового блока.
 */
const MARKER_BAND = 44

const GAP_X = 24
const GAP_Y = 16

const MAX_TASK_WIDTH = 260
const MAX_TASK_HEIGHT = 220
const MAX_NOTE_WIDTH = 340

const MIN_EVENT_SIDE = 36
const MAX_EVENT_SIDE = 56
const MIN_GATEWAY_SIDE = 40
const MAX_GATEWAY_SIDE = 60

const EVENT_TYPES = [
  'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
] as const
const GATEWAY_TYPES = ['exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway'] as const

const NARROW = new Set(" iljtfrI.,:;|!'`()[]{}/\\-".split(''))
const WIDE = new Set('mwMWШЩЮЫФ@%'.split(''))

/** Ширина строки в пикселях — оценка без обращения к движку шрифтов. */
export function textWidth(text: string, fontSize = FONT_SIZE): number {
  let total = 0
  for (const ch of text || '') {
    if (ch === ' ') total += 0.28
    else if (NARROW.has(ch)) total += 0.32
    else if (WIDE.has(ch)) total += 0.86
    else if (ch >= '0' && ch <= '9') total += 0.56
    else if (ch !== ch.toLowerCase() && ch === ch.toUpperCase()) total += 0.68
    else total += 0.55
  }
  return total * fontSize
}

/** Сколько строк займёт подпись при переносе по словам. */
export function wrappedLineCount(text: string, availablePx: number, fontSize = FONT_SIZE): number {
  const words = (text || '').split(/\s+/).filter(Boolean)
  if (!words.length) return 1
  if (availablePx <= 0) return words.length
  const space = textWidth(' ', fontSize)
  let lines = 1
  let cursor = 0
  for (const word of words) {
    let w = textWidth(word, fontSize)
    if (cursor && cursor + space + w > availablePx) {
      lines += 1
      cursor = w
    } else {
      cursor += (cursor ? space : 0) + w
    }
    while (cursor > availablePx && w > availablePx) {
      lines += 1
      cursor -= availablePx
      w -= availablePx
    }
  }
  return lines
}

/** Высота, которой хватит подписи внутри фигуры заданной ширины. */
export function labelHeight(text: string, boxWidth: number, marker: boolean): number {
  return wrappedLineCount(text, boxWidth - PAD_X) * LINE_HEIGHT + PAD_Y + (marker ? MARKER_BAND : 0)
}

function overlaps(a: ProcessNode, b: ProcessNode): boolean {
  const ag = a.geometry
  const bg = b.geometry
  return (
    ag.x < bg.x + bg.width && bg.x < ag.x + ag.width &&
    ag.y < bg.y + bg.height && bg.y < ag.y + ag.height
  )
}

/**
 * Свободное место слева/справа (`axis='x'`) или сверху/снизу от фигуры.
 * Соседями считаются только фигуры, перекрывающиеся по другой оси.
 */
function freeSpace(
  node: ProcessNode,
  others: ProcessNode[],
  lane: ProcessNode | undefined,
  axis: 'x' | 'y',
): [number, number] {
  const g = node.geometry
  const horizontal = axis === 'x'
  const low = horizontal ? g.x : g.y
  const high = horizontal ? g.x + g.width : g.y + g.height
  const crossLow = horizontal ? g.y : g.x
  const crossHigh = horizontal ? g.y + g.height : g.x + g.width
  const gap = horizontal ? GAP_X : GAP_Y

  let before = Number.POSITIVE_INFINITY
  let after = Number.POSITIVE_INFINITY
  for (const other of others) {
    if (other.id === node.id) continue
    const og = other.geometry
    const oLow = horizontal ? og.x : og.y
    const oHigh = horizontal ? og.x + og.width : og.y + og.height
    const oCrossLow = horizontal ? og.y : og.x
    const oCrossHigh = horizontal ? og.y + og.height : og.x + og.width
    // Мешает росту только тот, кто перекрывается по ДРУГОЙ оси: соседа,
    // разведённого по перпендикуляру, рост вдоль этой оси не задевает.
    if (oCrossHigh <= crossLow || oCrossLow >= crossHigh) continue
    if (oHigh <= low) before = Math.min(before, low - oHigh - gap)
    else if (oLow >= high) after = Math.min(after, oLow - high - gap)
    else {
      before = Math.min(before, 0)
      after = Math.min(after, 0)
    }
  }
  if (lane) {
    const lg = lane.geometry
    if (horizontal) {
      before = Math.min(before, low - lg.x)
      after = Math.min(after, lg.x + lg.width - high)
    } else {
      before = Math.min(before, low - lg.y)
      after = Math.min(after, lg.y + lg.height - high)
    }
  }
  return [Math.max(before, 0), Math.max(after, 0)]
}

function grow(node: ProcessNode, extra: number, axis: 'x' | 'y', before: number, after: number): void {
  let takeBefore = Math.min(extra / 2, before)
  const takeAfter = Math.min(extra - takeBefore, after)
  takeBefore = Math.min(extra - takeAfter, before)
  const g = node.geometry
  if (axis === 'x') {
    g.x = Math.round(g.x - takeBefore)
    g.width = Math.round(g.width + takeBefore + takeAfter)
  } else {
    g.y = Math.round(g.y - takeBefore)
    g.height = Math.round(g.height + takeBefore + takeAfter)
  }
}

/**
 * Событиям и шлюзам возвращает квадратную рамку: в draw.io они объявлены
 * `aspect=fixed` и рисуются по меньшей стороне, а импортёр растягивает круг в
 * эллипс, и значок внутри вылезает за контур.
 */
export function squareUpEvents(nodes: ProcessNode[]): string[] {
  const touched: string[] = []
  for (const node of nodes) {
    let lo: number
    let hi: number
    if ((EVENT_TYPES as readonly string[]).includes(node.type)) {
      lo = MIN_EVENT_SIDE
      hi = MAX_EVENT_SIDE
    } else if ((GATEWAY_TYPES as readonly string[]).includes(node.type)) {
      lo = MIN_GATEWAY_SIDE
      hi = MAX_GATEWAY_SIDE
    } else continue
    const g = node.geometry
    const side = Math.round(Math.min(Math.max(Math.min(g.width, g.height), lo), hi))
    if (side === g.width && side === g.height) continue
    g.x = Math.round(g.x + (g.width - side) / 2)
    g.y = Math.round(g.y + (g.height - side) / 2)
    g.width = side
    g.height = side
    touched.push(node.id)
  }
  return touched
}

/** Расширяет шаги и примечания так, чтобы подпись помещалась в рамку. */
export function fitLabels(nodes: ProcessNode[], lanes: ProcessNode[]): string[] {
  const laneById = new Map(lanes.map((l) => [l.id, l]))
  const fitted: string[] = []
  const targets = nodes
    .filter((n) => (TASK_NODE_TYPES as readonly string[]).includes(n.type) || n.type === 'textAnnotation')
    .sort((a, b) => (b.name || '').length - (a.name || '').length)

  for (const node of targets) {
    const isNote = node.type === 'textAnnotation'
    const marker = !isNote
    const lane = node.laneId ? laneById.get(node.laneId) : undefined
    const g = node.geometry
    if (labelHeight(node.name, g.width, marker) <= g.height) continue

    const maxWidth = isNote ? MAX_NOTE_WIDTH : MAX_TASK_WIDTH
    const [beforeX, afterX] = freeSpace(node, nodes, lane, 'x')
    const roomX = Math.min(beforeX + afterX, Math.max(maxWidth - g.width, 0))

    // Расширяем, только пока это убирает строку: подпись в одну строку от
    // лишней ширины не выигрывает, а фигура зря съедает место соседей.
    let lines = wrappedLineCount(node.name, g.width - PAD_X)
    let addX = 0
    let probe = 0
    while (probe < roomX && lines > 1) {
      probe = Math.min(probe + 10, roomX)
      const probeLines = wrappedLineCount(node.name, g.width + probe - PAD_X)
      if (probeLines >= lines) continue
      addX = probe
      lines = probeLines
      if (labelHeight(node.name, g.width + addX, marker) <= g.height) break
    }
    if (addX > 0) grow(node, addX, 'x', beforeX, afterX)

    const needH = labelHeight(node.name, g.width, marker)
    if (needH > g.height) {
      const [beforeY, afterY] = freeSpace(node, nodes, lane, 'y')
      const roomY = Math.min(beforeY + afterY, Math.max(MAX_TASK_HEIGHT - g.height, 0))
      grow(node, Math.min(needH - g.height, roomY), 'y', beforeY, afterY)
    }
    fitted.push(node.id)
  }
  return fitted
}

/**
 * Убирает наложение артефакта на шаг: в draw.io цилиндр уходит на задний план,
 * в выгрузке он ложится поверх подписи.
 */
export function separateArtifacts(nodes: ProcessNode[], lanes: ProcessNode[]): string[] {
  const laneById = new Map(lanes.map((l) => [l.id, l]))
  const flow = nodes.filter(
    (n) => !(ARTIFACT_NODE_TYPES as readonly string[]).includes(n.type) && n.type !== 'lane',
  )
  const moved: string[] = []
  for (const artifact of nodes) {
    if (!(ARTIFACT_NODE_TYPES as readonly string[]).includes(artifact.type)) continue
    if (artifact.type === 'textAnnotation') continue
    for (const step of flow) {
      if (!overlaps(artifact, step)) continue
      const ag = artifact.geometry
      const sg = step.geometry
      const overlapX = Math.min(ag.x + ag.width, sg.x + sg.width) - Math.max(ag.x, sg.x)
      const overlapY = Math.min(ag.y + ag.height, sg.y + sg.height) - Math.max(ag.y, sg.y)
      const lane = artifact.laneId ? laneById.get(artifact.laneId) : undefined
      if (overlapY <= overlapX) {
        const shift = overlapY + 8
        const up = ag.y - shift
        const down = ag.y + shift
        if (lane && up < lane.geometry.y) ag.y = down
        else ag.y = ag.y <= sg.y ? up : down
      } else {
        const shift = overlapX + 8
        ag.x = ag.x <= sg.x ? ag.x - shift : ag.x + shift
      }
      moved.push(artifact.id)
      break
    }
  }
  return moved
}

/**
 * Полный проход нормализации. Артефакты разводим дважды: до подгонки подписей —
 * чтобы наложение не считалось «нет свободного места», и после — потому что
 * подросший шаг может задеть соседний цилиндр.
 */
export function normalizeLayout(
  nodes: ProcessNode[],
  lanes: ProcessNode[],
): { squared: string[]; fitted: string[]; moved: string[] } {
  const squared = squareUpEvents(nodes)
  const moved = separateArtifacts(nodes, lanes)
  const fitted = fitLabels(nodes, lanes)
  for (const id of separateArtifacts(nodes, lanes)) {
    if (!moved.includes(id)) moved.push(id)
  }
  return { squared, fitted, moved }
}

/* ─── Разведение подписей в BPMNDI ────────────────────────────────────────── */

/**
 * Подписи событий, шлюзов и артефактов bpmn.io рисует ВНЕ фигуры и переносит
 * по узкой рамке в 90 px. Длинное имя шлюза превращалось в столбец из семи
 * строк, который ложился на соседние шаги и на подписи связей.
 */
const MIN_EXTERNAL_LABEL_WIDTH = 90
const MAX_EXTERNAL_LABEL_WIDTH = 220
const EXTERNAL_LABEL_TARGET_LINES = 2
const EXTERNAL_LABEL_GAP = 6
/** Толщина «коридора» линии связи при разведении подписей. */
const SEGMENT_THICKNESS = 3

/** Типы, подпись которых импортёр выносит за пределы фигуры. */
export const EXTERNAL_LABEL_TYPES: readonly string[] = [
  ...EVENT_TYPES, ...GATEWAY_TYPES, 'dataStore', 'dataObject',
]

export type Box = { x: number; y: number; width: number; height: number }

function styleMap(style: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of (style || '').split(';')) {
    const i = part.indexOf('=')
    if (i > 0) out[part.slice(0, i).trim().toLowerCase()] = part.slice(i + 1).trim().toLowerCase()
  }
  return out
}

function overlapArea(a: Box, b: Box): number {
  const dx = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)
  const dy = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y)
  return dx > 0 && dy > 0 ? dx * dy : 0
}

/**
 * Первая позиция подписи, которая ни на что не наезжает. Если свободной нет —
 * наименее конфликтная: подпись всё равно должна где-то стоять.
 */
export function chooseLabelBox(candidates: Box[], obstacles: Box[]): Box {
  let best: Box | null = null
  let bestArea = Number.POSITIVE_INFINITY
  for (const box of candidates) {
    // Как только позиция проиграла лучшей из уже проверенных, досчитывать её
    // перекрытия незачем: на карте в сотни фигур перебор «каждый кандидат
    // против каждого препятствия» занимал секунды.
    let area = 0
    for (const o of obstacles) {
      area += overlapArea(box, o)
      if (area >= bestArea) break
    }
    if (area === 0) return box
    if (area < bestArea) {
      best = box
      bestArea = area
    }
  }
  return best ?? candidates[0]
}

/** Ширина и высота рамки подписи: не уже 90 px и не длиннее пары строк. */
/**
 * Рамка подписи: по тексту, а не по фиксированной ширине.
 *
 * Короткому «To'liq» рамка в 90 px не нужна — на плотной карте лишние 55 px
 * ровно и приводят к тому, что подпись ветки ложится на соседний шаг.
 */
export function labelSize(text: string): { width: number; height: number } {
  const snug = Math.round(textWidth(text) + 10)
  if (snug <= MAX_EXTERNAL_LABEL_WIDTH) {
    return { width: Math.max(snug, 32), height: Math.round(LINE_HEIGHT + 4) }
  }
  let width = MIN_EXTERNAL_LABEL_WIDTH
  while (
    width < MAX_EXTERNAL_LABEL_WIDTH &&
    wrappedLineCount(text, width) > EXTERNAL_LABEL_TARGET_LINES
  ) width += 10
  return { width, height: Math.round(wrappedLineCount(text, width) * LINE_HEIGHT + 4) }
}

/**
 * Варианты рамки подписи — от широкой к узкой. Если на карте не нашлось места
 * под привычную двухстрочную рамку, подпись лучше сложить в три-четыре строки,
 * чем положить поверх соседней фигуры.
 */
export function labelSizeVariants(text: string): { width: number; height: number }[] {
  const variants = [labelSize(text)]
  for (const target of [3, 4]) {
    let width = MIN_EXTERNAL_LABEL_WIDTH
    while (width < MAX_EXTERNAL_LABEL_WIDTH && wrappedLineCount(text, width) > target) width += 10
    const candidate = {
      width,
      height: Math.round(wrappedLineCount(text, width) * LINE_HEIGHT + 4),
    }
    const last = variants[variants.length - 1]
    if (candidate.width < last.width) variants.push(candidate)
  }
  return variants
}

/**
 * Позиции выносной подписи в порядке предпочтения. Первой идёт та, которую
 * выбрал аналитик в draw.io: стиль хранит `labelPosition` и
 * `verticalLabelPosition`, и на карте банка они расставлены не случайно —
 * подпись таймера уведена влево, чтобы не лечь на вертикальную связь, а
 * подпись хранилища данных поднята над цилиндром.
 */
export function externalLabelCandidates(node: ProcessNode): Box[] {
  const g = node.geometry
  const gap = EXTERNAL_LABEL_GAP
  const smap = styleMap(node.style)
  const horizontal = smap.labelposition ?? 'center'
  const vertical = smap.verticallabelposition ?? 'bottom'

  const order: Box[] = []
  for (const { width, height } of labelSizeVariants(node.name || '')) {
    const centerX = Math.round(g.x + g.width / 2 - width / 2)
    const middleY = Math.round(g.y + g.height / 2 - height / 2)
    const below = { x: centerX, y: Math.round(g.y + g.height + gap), width, height }
    const above = { x: centerX, y: Math.round(g.y - gap - height), width, height }
    const left = { x: Math.round(g.x - gap - width), y: middleY, width, height }
    const right = { x: Math.round(g.x + g.width + gap), y: middleY, width, height }

    let preferred = below
    if (horizontal === 'left') preferred = left
    else if (horizontal === 'right') preferred = right
    else if (vertical === 'top') preferred = above

    const far = Math.round(gap + height + 4)
    const side = Math.round(gap + width / 2)
    order.push(
      preferred, below, above, right, left,
      { x: centerX - side, y: below.y, width, height },
      { x: centerX + side, y: below.y, width, height },
      { x: centerX - side, y: above.y, width, height },
      { x: centerX + side, y: above.y, width, height },
      { x: centerX, y: below.y + far, width, height },
      { x: centerX, y: above.y - far, width, height },
    )
  }
  const seen: Box[] = []
  for (const box of order) {
    if (!seen.some((b) => b.x === box.x && b.y === box.y && b.width === box.width)) seen.push(box)
  }
  return seen
}

export function nodeObstacles(nodes: ProcessNode[], skipId = ''): Box[] {
  return nodes
    .filter((n) => n.id !== skipId && n.type !== 'lane')
    .map((n) => ({ x: n.geometry.x, y: n.geometry.y, width: n.geometry.width, height: n.geometry.height }))
}

/**
 * Доли длины ломаной, около которых можно поставить подпись связи. Середина
 * предпочтительна, но на плотной карте там бывает занято — тогда подпись
 * сдвигается вдоль своей же линии, а не садится на чужую фигуру.
 */
/** Отступы подписи от линии связи: вплотную, затем в стороне. */
const EDGE_LABEL_GAPS = [4, 26]

const EDGE_LABEL_FRACTIONS = [0.5, 0.4, 0.6, 0.28, 0.72, 0.15, 0.85]

/** Точка на ломаной по доле её длины и ориентация отрезка в этом месте. */
function pointAt(
  route: { x: number; y: number }[],
  fraction: number,
): { x: number; y: number; vertical: boolean } {
  const lengths: number[] = []
  for (let i = 1; i < route.length; i += 1) {
    lengths.push(Math.abs(route[i].x - route[i - 1].x) + Math.abs(route[i].y - route[i - 1].y))
  }
  const total = lengths.reduce((a, b) => a + b, 0)
  if (total <= 0) return { x: route[0].x, y: route[0].y, vertical: false }
  let target = total * fraction
  for (let i = 0; i < lengths.length; i += 1) {
    const seg = lengths[i]
    if (seg <= 0) continue
    const a = route[i]
    const b = route[i + 1]
    if (target <= seg) {
      const t = target / seg
      return {
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
        vertical: Math.abs(b.y - a.y) > Math.abs(b.x - a.x),
      }
    }
    target -= seg
  }
  const a = route[route.length - 2]
  const b = route[route.length - 1]
  return { x: b.x, y: b.y, vertical: Math.abs(b.y - a.y) > Math.abs(b.x - a.x) }
}

/**
 * Позиции подписи связи вдоль ломаной. Порядок зависит от того, как идёт линия:
 * подпись у вертикального отрезка уводим вбок, у горизонтального — вверх.
 * Иначе линия проходит ровно через текст.
 */
export function edgeLabelCandidates(route: { x: number; y: number }[], text: string): Box[] {
  const sizes = labelSizeVariants(text)
  let points = route
  if (!points.length) {
    const { width, height } = sizes[0]
    return [{ x: 0, y: 0, width, height }]
  }
  if (points.length === 1) points = [points[0], points[0]]

  const out: Box[] = []
  for (const size of sizes) {
    for (const fraction of EDGE_LABEL_FRACTIONS) {
      const { x: cx, y: cy, vertical } = pointAt(points, fraction)
      const x = Math.round(cx - size.width / 2)
      const y = Math.round(cy - size.height / 2)
      // Два кольца отступов: сперва вплотную к линии, потом на ширину фигуры
      // в стороне. На тесной карте место у самой линии занято соседним шагом,
      // и подпись садилась ему прямо на текст.
      for (const gap of EDGE_LABEL_GAPS) {
        const above = { x, y: Math.round(cy - size.height - gap), ...size }
        const below = { x, y: Math.round(cy + gap), ...size }
        const right = { x: Math.round(cx + gap + 4), y, ...size }
        const left = { x: Math.round(cx - gap - 4 - size.width), y, ...size }
        out.push(...(vertical ? [right, left, above, below] : [above, below, right, left]))
      }
    }
  }
  const seen: Box[] = []
  for (const box of out) {
    if (!seen.some((b) => b.x === box.x && b.y === box.y && b.width === box.width)) seen.push(box)
  }
  return seen
}

/** Отрезки ломаной как узкие прямоугольники — препятствия для подписей. */
export function segmentBoxes(route: { x: number; y: number }[]): Box[] {
  const boxes: Box[] = []
  const half = SEGMENT_THICKNESS
  for (let i = 1; i < route.length; i += 1) {
    const a = route[i - 1]
    const b = route[i]
    const left = Math.min(a.x, b.x)
    const right = Math.max(a.x, b.x)
    const top = Math.min(a.y, b.y)
    const bottom = Math.max(a.y, b.y)
    boxes.push({
      x: left - half, y: top - half,
      width: right - left + 2 * half, height: bottom - top + 2 * half,
    })
  }
  return boxes
}
