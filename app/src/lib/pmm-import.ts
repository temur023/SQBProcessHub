import type {
  BusinessProcess,
  EdgeKind,
  NodeType,
  ProcessEdge,
  ProcessEdgePoint,
  ProcessNode,
} from '@/types/process'
import { isTaskNode } from '@/types/process'
import { classifyCategory, detectSystem } from './drawio'
import { assembleOpenedProcess } from './opened-process'
import { parseDurationText } from './bpmn-import'
import { readZip, ZipError, type ZipEntry } from './zip'

/**
 * Чтение нативного пакета PIX Процессной студии (`.pmm`) — для просмотра
 * выгрузки в системе.
 *
 * Пакет — это ZIP из `main.xml`, `pm/configuration.xml` и карты
 * `pm/maps/<имя>.xml`. Читаем только карту: каталог нотаций студии на то, как
 * выглядит схема, не влияет.
 *
 * Соглашения формата, обратные экспортёру (`backend/app/services/pmm_exporter.py`):
 *
 * - узлы дорожки (`horizontalRoad`) лежат в ОТНОСИТЕЛЬНЫХ координатах, сама
 *   дорожка — в абсолютных; здесь координаты снова разворачиваются в
 *   абсолютные, иначе вся карта сложилась бы в левый верхний угол;
 * - подпись связи хранится в атрибуте `Text`, а не `label`;
 * - `emptyPool` — заголовочная плашка карты, а не шаг: из неё берём имя
 *   процесса и на холст не выносим;
 * - мелкий `intermediate_event_catch_timer` без единой связи — это значок
 *   длительности у шага; его время возвращается в ST/WT ближайшего шага, и на
 *   холсте снова появляются часы в углу.
 */

const PIX_TYPES: Record<string, NodeType> = {
  start_event_none: 'startEvent',
  start_event_timer: 'startEvent',
  start_event_message: 'startEvent',
  end_event_none: 'endEvent',
  end_event_terminate: 'endEvent',
  end_event_message: 'endEvent',
  intermediate_event_catch_timer: 'intermediateTimerEvent',
  intermediate_event_catch_message: 'intermediateMessageEvent',
  gateway_xor: 'exclusiveGateway',
  gateway_parallel: 'parallelGateway',
  gateway_or: 'inclusiveGateway',
  gateway_complex: 'complexGateway',
  sub_process: 'subProcess',
  datastorage: 'dataStore',
  dataobject: 'dataObject',
  input: 'textAnnotation',
  servicetask: 'serviceTask',
  usertask: 'userTask',
  task: 'task',
}

/** Значок длительности не больше этого размера: настоящий таймер крупнее. */
const MARKER_MAX_SIDE = 32

/** На каком расстоянии значок ещё считается принадлежащим шагу, px. */
const MARKER_ATTACH_RADIUS = 40

function num(raw: string | null, fallback = 0): number {
  const value = Number(raw)
  return Number.isFinite(value) ? value : fallback
}

function pixNodeType(raw: string | null): NodeType {
  return PIX_TYPES[(raw || '').toLowerCase()] || 'task'
}

interface RawNode {
  el: Element
  type: NodeType
  pixType: string
  id: string
  label: string
  x: number
  y: number
  width: number
  height: number
}

function readNode(el: Element, originX: number, originY: number): RawNode {
  const width = num(el.getAttribute('width'), 120)
  const height = num(el.getAttribute('height'), 60)
  return {
    el,
    type: pixNodeType(el.getAttribute('type')),
    pixType: (el.getAttribute('type') || '').toLowerCase(),
    id: el.getAttribute('id') || `pix_${Math.random().toString(16).slice(2)}`,
    label: (el.getAttribute('label') || '').trim(),
    x: originX + num(el.getAttribute('x')),
    y: originY + num(el.getAttribute('y')),
    width,
    height,
  }
}

/** Часть пакета с картой: `pm/maps/*.xml`, либо любой XML с корнем `<Map>`. */
export function findMapPart(entries: ZipEntry[]): { name: string; xml: string } {
  const decoder = new TextDecoder('utf-8')
  const candidates = entries.filter((e) => /^pm\/maps\/.+\.xml$/i.test(e.name.replace(/^\//, '')))
  const pool = candidates.length ? candidates : entries.filter((e) => /\.xml$/i.test(e.name))
  for (const entry of pool) {
    const xml = decoder.decode(entry.data as BufferSource)
    if (/<Map[\s>]/.test(xml)) return { name: entry.name, xml }
  }
  throw new ZipError('В пакете .pmm нет карты: не найдено ни одного pm/maps/*.xml с корнем <Map>')
}

/**
 * Шаг, которому принадлежит значок длительности.
 *
 * Экспортёр сажает значок НА нижнюю грань шага, поэтому его квадрат
 * перекрывается с прямоугольником своего шага — по этому перекрытию шаг и
 * опознаётся. Сравнение по расстоянию между центрами на плотной карте банка
 * промахивалось: центр значка у края широкого шага может оказаться ближе к
 * центру соседнего, и время уезжало не туда, затирая чужое.
 *
 * Кандидаты ограничены дорожкой значка: в .pmm он лежит внутри того же
 * `horizontalRoad`, что и его шаг.
 */
function hostStep(marker: RawNode, steps: ProcessNode[]): ProcessNode | null {
  const laneId = marker.el.parentElement?.getAttribute('id') || ''
  const sameLane = laneId ? steps.filter((s) => s.laneId === laneId) : []
  const pool = sameLane.length ? sameLane : steps
  const [mx1, my1] = [marker.x, marker.y]
  const [mx2, my2] = [marker.x + marker.width, marker.y + marker.height]

  let host: ProcessNode | null = null
  let bestOverlap = 0
  let bestGap = MARKER_ATTACH_RADIUS
  for (const step of pool) {
    const g = step.geometry
    const overlap =
      Math.max(0, Math.min(mx2, g.x + g.width) - Math.max(mx1, g.x)) *
      Math.max(0, Math.min(my2, g.y + g.height) - Math.max(my1, g.y))
    if (overlap > bestOverlap) {
      bestOverlap = overlap
      host = step
      continue
    }
    if (bestOverlap > 0) continue
    // Значок, зажатый в границы дорожки, мог отойти от грани: берём зазор до
    // прямоугольника шага, а не расстояние между центрами.
    const gap = Math.hypot(
      Math.max(g.x - mx2, 0, mx1 - (g.x + g.width)),
      Math.max(g.y - my2, 0, my1 - (g.y + g.height)),
    )
    if (gap < bestGap) {
      bestGap = gap
      host = step
    }
  }
  return host
}

export function parsePmmMapXml(xmlText: string, fileName: string): BusinessProcess {
  const doc = new DOMParser().parseFromString(xmlText, 'application/xml')
  const failure = doc.querySelector('parsererror')
  if (failure) throw new Error(`Карту .pmm не разобрать: ${failure.textContent?.slice(0, 160)}`)
  const map = doc.getElementsByTagName('Map')[0]
  if (!map) throw new Error('В карте .pmm нет корневого элемента <Map>')

  const lanes: ProcessNode[] = []
  const nodes: ProcessNode[] = []
  const markers: RawNode[] = []
  let title = ''

  const makeNode = (raw: RawNode, laneOf?: ProcessNode): ProcessNode => {
    const node: ProcessNode = {
      id: raw.id,
      name: raw.label,
      type: raw.type,
      category: classifyCategory(raw.type, raw.label, ''),
      geometry: { x: raw.x, y: raw.y, width: raw.width, height: raw.height },
      style: '',
      slaMinutes: 0,
      waitMinutes: 0,
      laneId: laneOf?.id,
      laneName: laneOf?.name,
      role: laneOf?.name,
      system: detectSystem(raw.label, laneOf?.name || ''),
    }
    if (raw.pixType === 'intermediate_event_catch_timer') {
      node.slaMinutes = parseDurationText(raw.label)
    }
    return node
  }

  const collect = (raw: RawNode, laneOf?: ProcessNode): void => {
    // Мелкий таймер — кандидат в значок длительности. Значок он или настоящее
    // событие потока, видно только по связям, поэтому решение откладываем.
    if (
      raw.pixType === 'intermediate_event_catch_timer' &&
      raw.width <= MARKER_MAX_SIDE &&
      raw.height <= MARKER_MAX_SIDE
    ) {
      markers.push(raw)
      return
    }
    nodes.push(makeNode(raw, laneOf))
  }

  for (const child of Array.from(map.children)) {
    if (child.tagName !== 'node') continue
    const raw = readNode(child, 0, 0)
    if (raw.pixType === 'emptypool') {
      title = raw.label || title
      continue
    }
    if (raw.pixType === 'horizontalroad' || raw.pixType === 'verticalroad') {
      const lane: ProcessNode = {
        id: raw.id,
        name: raw.label || `Дорожка ${lanes.length + 1}`,
        type: 'lane',
        role: raw.label,
        geometry: { x: raw.x, y: raw.y, width: raw.width, height: raw.height },
        style: 'swimlane;horizontal=0;startSize=30;',
      }
      lanes.push(lane)
      for (const inner of Array.from(child.children)) {
        if (inner.tagName !== 'node') continue
        collect(readNode(inner, raw.x, raw.y), lane)
      }
      continue
    }
    collect(raw, undefined)
  }

  let stepIndex = 1
  for (const node of nodes) {
    if (isTaskNode(node.type)) node.code = `STEP-${String(stepIndex++).padStart(2, '0')}`
    else if (node.type === 'startEvent') node.code = 'START'
    else if (node.type === 'endEvent') node.code = 'END'
  }

  // ── Связи ──────────────────────────────────────────────────────────────
  // Кандидаты в значки тоже считаются известными: иначе их связи отсеются
  // раньше, чем мы поймём, значок это или настоящее событие потока.
  const knownIds = new Set([
    ...nodes.map((n) => n.id),
    ...lanes.map((l) => l.id),
    ...markers.map((m) => m.id),
  ])
  const edges: ProcessEdge[] = []
  for (const connector of Array.from(map.getElementsByTagName('connector'))) {
    const sourceId = connector.getAttribute('sourceNodeId') || undefined
    const targetId = connector.getAttribute('targetNodeId') || undefined
    if (!sourceId || !targetId || !knownIds.has(sourceId) || !knownIds.has(targetId)) continue
    const dotted =
      (connector.getAttribute('lineStyle') ||
        connector.getElementsByTagName('lineStyle')[0]?.textContent ||
        '')
        .trim()
        .toLowerCase() === 'dotted'
    const touchesLane = lanes.some((l) => l.id === sourceId || l.id === targetId)
    const kind: EdgeKind = dotted ? (touchesLane ? 'messageFlow' : 'association') : 'sequenceFlow'
    const points: ProcessEdgePoint[] = Array.from(connector.getElementsByTagName('waypoint'))
      .map((w) => ({
        index: num(w.getAttribute('index'), 0),
        x: num(w.getAttribute('x')),
        y: num(w.getAttribute('y')),
      }))
      .sort((a, b) => a.index - b.index)
      .map(({ x, y }) => ({ x, y }))
    edges.push({
      id: connector.getAttribute('id') || `conn_${edges.length}`,
      name: (connector.getAttribute('Text') || connector.getAttribute('label') || '').trim(),
      kind,
      sourceId,
      targetId,
      points,
      dashed: dotted,
    })
  }

  // ── Значки длительности возвращаются в ST/WT ближайшего шага ───────────
  const steps = nodes.filter((n) => isTaskNode(n.type))
  const connected = new Set(edges.flatMap((e) => [e.sourceId, e.targetId]))
  for (const marker of markers) {
    if (connected.has(marker.id)) {
      // Таймер, вписанный в поток связями, — событие карты, а не значок.
      nodes.push(makeNode(marker, lanes.find((l) => l.id === marker.el.parentElement?.getAttribute('id'))))
      continue
    }
    const waitMatch = /ожидани[ея]\s*(.+)$/i.exec(marker.label)
    const wait = parseDurationText(waitMatch?.[1])
    const work = parseDurationText(marker.label.replace(/[·|,;]?\s*ожидани[ея].*$/i, '').trim())
    if (!work && !wait) continue
    const host = hostStep(marker, steps)
    if (!host) continue
    if (work) host.slaMinutes = work
    if (wait) host.waitMinutes = wait
  }

  const processName = title || fileName.replace(/\.[^.]+$/, '') || map.getAttribute('name') || 'Карта PIX'
  return assembleOpenedProcess({
    fileName,
    processName,
    sourceLabel: 'PIX .pmm',
    nodes,
    lanes,
    edges,
  })
}

/** Открывает пакет `.pmm` целиком: распаковка плюс разбор карты. */
export async function parsePmmPackage(buffer: ArrayBuffer, fileName: string): Promise<BusinessProcess> {
  const entries = await readZip(buffer)
  const part = findMapPart(entries)
  return parsePmmMapXml(part.xml, fileName)
}
