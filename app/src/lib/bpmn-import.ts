import type {
  BusinessProcess,
  EdgeKind,
  NodeType,
  ProcessEdgePoint,
  ProcessNode,
  StepCategory,
} from '@/types/process'
import { isTaskNode } from '@/types/process'
import { classifyCategory, detectSystem } from './drawio'
import { assembleOpenedProcess } from './opened-process'

/**
 * Чтение OMG BPMN 2.0 обратно в модель — для просмотра выгрузки в системе.
 *
 * Это не импорт «как-нибудь», а сверка: сотрудник открывает тот самый файл,
 * который уедет в PIX Процессную студию, и должен увидеть ровно его. Поэтому
 * читатель ничего не достраивает и не переставляет:
 *
 * - геометрия берётся из BPMNDI как есть, ломаные связей — из `di:waypoint`;
 * - `documentation`, которую пишет наш экспортёр (`Code`, `Role`, `System`,
 *   `ST`, `WT`, `Category`), разбирается обратно в поля шага, а эвристика по
 *   названию включается только там, где в файле ничего нет;
 * - граничный таймер без исходящих переходов — это значок длительности у шага
 *   (см. `DurationMarker` в `bpmn-export`), и его время возвращается в ST/WT
 *   шага, а не превращается в отдельный узел. Именно так на карте снова
 *   появляются часы в углу.
 *
 * Отдельный от `drawio.parseBpmnXml` модуль: там BPMN читается как «ещё один
 * способ нарисовать карту» и время шага домысливается по категории, а здесь
 * важно показать файл без единой поправки.
 */

const NODE_TAGS: Record<string, NodeType> = {
  startevent: 'startEvent',
  endevent: 'endEvent',
  task: 'task',
  usertask: 'userTask',
  manualtask: 'userTask',
  servicetask: 'serviceTask',
  scripttask: 'serviceTask',
  sendtask: 'serviceTask',
  receivetask: 'serviceTask',
  businessruletask: 'serviceTask',
  subprocess: 'subProcess',
  transaction: 'subProcess',
  callactivity: 'subProcess',
  adhocsubprocess: 'subProcess',
  exclusivegateway: 'exclusiveGateway',
  parallelgateway: 'parallelGateway',
  inclusivegateway: 'inclusiveGateway',
  complexgateway: 'complexGateway',
  eventbasedgateway: 'exclusiveGateway',
  datastorereference: 'dataStore',
  dataobjectreference: 'dataObject',
  textannotation: 'textAnnotation',
}

/** Теги-события, тип которых зависит от вложенного eventDefinition. */
const EVENT_TAGS = new Set(['intermediatecatchevent', 'intermediatethrowevent'])

const EDGE_TAGS: Record<string, EdgeKind> = {
  sequenceflow: 'sequenceFlow',
  messageflow: 'messageFlow',
  association: 'association',
  dataassociation: 'association',
  datainputassociation: 'association',
  dataoutputassociation: 'association',
}

interface Bounds {
  x: number
  y: number
  width: number
  height: number
}

function byLocalName(root: ParentNode, names: string[]): Element[] {
  const want = new Set(names.map((n) => n.toLowerCase()))
  const scope = root as Document | Element
  if (!('getElementsByTagName' in scope)) return []
  return Array.from(scope.getElementsByTagName('*')).filter((el) =>
    want.has(el.localName.toLowerCase()),
  )
}

function childByLocalName(el: Element, name: string): Element | null {
  const want = name.toLowerCase()
  for (const child of Array.from(el.children)) {
    if (child.localName.toLowerCase() === want) return child
  }
  return null
}

function num(raw: string | null, fallback: number): number {
  const value = Number(raw)
  return Number.isFinite(value) ? value : fallback
}

/**
 * «1 ч 30 мин», «45 мин», «2 дн», «30 min» -> минуты. Ноль, если ничего не
 * распознано.
 *
 * Границу единицы проверяем просмотром вперёд, а не `\b`: в JavaScript `\b`
 * опирается на латиницу, и после кириллической «ч» границы не возникает — от
 * «1 ч 30 мин» оставалось 30 минут, а «1 ч» превращалось в ноль.
 */
export function parseDurationText(text: string | null | undefined): number {
  if (!text) return 0
  const re =
    /(\d+(?:[.,]\d+)?)\s*(дней|дня|день|дн|суток|сут|часов|часа|час|ч|минуты|минут|мин|days?|hours?|hrs?|min|d|h|m)(?![\p{L}\p{N}])/giu
  let total = 0
  let hit: RegExpExecArray | null
  while ((hit = re.exec(text)) !== null) {
    const value = Number(hit[1].replace(',', '.'))
    const unit = hit[2].toLowerCase()
    if (!Number.isFinite(value)) continue
    if (unit.startsWith('д') || unit.startsWith('с') || unit.startsWith('d')) total += value * 1440
    else if (unit.startsWith('ч') || unit.startsWith('h')) total += value * 60
    else total += value
  }
  return Math.round(total)
}

/** ISO-8601 длительность из `timeDuration` -> минуты. */
export function isoToMinutes(iso: string | null | undefined): number {
  const hit = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec((iso || '').trim())
  if (!hit) return 0
  return Number(hit[1] || 0) * 1440 + Number(hit[2] || 0) * 60 + Number(hit[3] || 0)
}

/** Поля шага из `documentation`, которую пишет наш экспортёр. */
function applyDocumentation(node: ProcessNode, doc: string): void {
  for (const chunk of doc.split(';')) {
    const at = chunk.indexOf(':')
    if (at < 0) continue
    const key = chunk.slice(0, at).trim().toLowerCase()
    const value = chunk.slice(at + 1).trim()
    if (!value) continue
    if (key === 'code') node.code = value
    else if (key === 'role') node.role = value
    else if (key === 'lane') node.laneName = node.laneName || value
    else if (key === 'system') node.system = value
    else if (key === 'st') node.slaMinutes = parseDurationText(value) || Number(value.replace(/\D+/g, '')) || 0
    else if (key === 'wt') node.waitMinutes = parseDurationText(value) || Number(value.replace(/\D+/g, '')) || 0
    else if (key === 'in') node.inputArtifacts = value.split(',').map((s) => s.trim()).filter(Boolean)
    else if (key === 'out') node.outputArtifacts = value.split(',').map((s) => s.trim()).filter(Boolean)
    else if (key === 'category') node.category = value as StepCategory
    else if (key === 'rpa potential') node.automationPotential = Number(value.replace(/\D+/g, '')) || undefined
  }
}

function eventType(el: Element): NodeType {
  return childByLocalName(el, 'timerEventDefinition') ? 'intermediateTimerEvent' : 'intermediateMessageEvent'
}

/** Длительность таймера события: `<timerEventDefinition><timeDuration>`. */
function timerMinutes(el: Element): number {
  const timer = childByLocalName(el, 'timerEventDefinition')
  if (!timer) return 0
  return isoToMinutes(childByLocalName(timer, 'timeDuration')?.textContent || '')
}

/**
 * Значки длительности: граничные таймеры без исходящих переходов.
 *
 * Граничное событие, из которого поток уходит дальше, — это настоящий таймаут
 * шага, и его мы оставляем узлом карты.
 */
function durationMarkerElements(doc: Document, outgoing: Set<string>): Element[] {
  return byLocalName(doc, ['boundaryEvent']).filter(
    (el) => !outgoing.has(el.getAttribute('id') || '') && !!childByLocalName(el, 'timerEventDefinition'),
  )
}

export function parseBpmnMap(xmlText: string, fileName: string): BusinessProcess {
  const doc = new DOMParser().parseFromString(xmlText, 'application/xml')
  const failure = doc.querySelector('parsererror')
  if (failure) throw new Error(`BPMN не разобрать: ${failure.textContent?.slice(0, 160)}`)
  if (!byLocalName(doc, ['definitions']).length) {
    throw new Error('В файле нет <definitions>: это не BPMN 2.0')
  }

  // ── Геометрия из BPMNDI ────────────────────────────────────────────────
  const boundsOf = new Map<string, Bounds>()
  for (const shape of byLocalName(doc, ['BPMNShape'])) {
    const ref = shape.getAttribute('bpmnElement')
    const bounds = childByLocalName(shape, 'Bounds')
    if (!ref || !bounds) continue
    boundsOf.set(ref, {
      x: num(bounds.getAttribute('x'), 0),
      y: num(bounds.getAttribute('y'), 0),
      width: num(bounds.getAttribute('width'), 120),
      height: num(bounds.getAttribute('height'), 60),
    })
  }
  const routeOf = new Map<string, ProcessEdgePoint[]>()
  for (const di of byLocalName(doc, ['BPMNEdge'])) {
    const ref = di.getAttribute('bpmnElement')
    if (!ref) continue
    const points = Array.from(di.children)
      .filter((c) => c.localName.toLowerCase() === 'waypoint')
      .map((c) => ({ x: num(c.getAttribute('x'), 0), y: num(c.getAttribute('y'), 0) }))
    if (points.length >= 2) routeOf.set(ref, points)
  }

  // ── Связи ──────────────────────────────────────────────────────────────
  const rawEdges: { el: Element; kind: EdgeKind }[] = []
  for (const el of Array.from(doc.getElementsByTagName('*'))) {
    const kind = EDGE_TAGS[el.localName.toLowerCase()]
    if (kind) rawEdges.push({ el, kind })
  }
  const outgoing = new Set(
    rawEdges.map(({ el }) => el.getAttribute('sourceRef') || '').filter(Boolean),
  )

  // ── Узлы ───────────────────────────────────────────────────────────────
  const markers = durationMarkerElements(doc, outgoing)
  const markerIds = new Set(markers.map((el) => el.getAttribute('id') || ''))
  const nodes: ProcessNode[] = []
  const byId = new Map<string, ProcessNode>()
  let shapeIndex = 1
  let stepIndex = 1

  const pushNode = (el: Element, type: NodeType): void => {
    const id = el.getAttribute('id')
    if (!id || byId.has(id)) return
    const name = (el.getAttribute('name') || childByLocalName(el, 'text')?.textContent || '').trim()
    const geometry = boundsOf.get(id) || { x: 60 + shapeIndex * 180, y: 60, width: 140, height: 80 }
    const node: ProcessNode = {
      id,
      name: name || defaultName(type, shapeIndex),
      type,
      category: classifyCategory(type, name, ''),
      geometry,
      style: '',
      slaMinutes: 0,
      waitMinutes: 0,
    }
    const documentation = childByLocalName(el, 'documentation')?.textContent || ''
    if (documentation.trim()) applyDocumentation(node, documentation)
    if (type === 'intermediateTimerEvent' && !node.slaMinutes) node.slaMinutes = timerMinutes(el)
    if (isTaskNode(type)) {
      if (!node.code) node.code = `STEP-${String(stepIndex).padStart(2, '0')}`
      stepIndex += 1
    }
    if (!node.system) node.system = detectSystem(node.name, node.laneName || '')
    shapeIndex += 1
    nodes.push(node)
    byId.set(id, node)
  }

  for (const el of Array.from(doc.getElementsByTagName('*'))) {
    const tag = el.localName.toLowerCase()
    if (tag === 'boundaryevent') {
      if (!markerIds.has(el.getAttribute('id') || '')) pushNode(el, eventType(el))
      continue
    }
    if (EVENT_TAGS.has(tag)) {
      pushNode(el, eventType(el))
      continue
    }
    const type = NODE_TAGS[tag]
    if (type) pushNode(el, type)
  }

  if (!nodes.length) throw new Error('В BPMN-файле не найдено ни одного элемента процесса')

  // ── Часы длительности возвращаются в ST/WT своего шага ─────────────────
  for (const el of markers) {
    const host = byId.get(el.getAttribute('attachedToRef') || '')
    if (!host) continue
    const label = el.getAttribute('name') || ''
    const waitPart = /ожидани[ея]\s*(.+)$/i.exec(label)
    const workPart = label.replace(/[·|,;]?\s*ожидани[ея].*$/i, '').trim()
    // Подпись значка точнее ISO-длительности: в ней стоят и ST, и WT.
    const work = parseDurationText(workPart) || (waitPart ? 0 : timerMinutes(el))
    const wait = parseDurationText(waitPart?.[1])
    if (!host.slaMinutes && work) host.slaMinutes = work
    if (!host.waitMinutes && wait) host.waitMinutes = wait
  }

  // ── Дорожки: полосы пула и внешние участники ───────────────────────────
  const lanes: ProcessNode[] = []
  const mainProcessIds = new Set(
    byLocalName(doc, ['process'])
      .filter((p) => Array.from(p.children).some((c) => c.localName.toLowerCase() !== 'documentation'))
      .map((p) => p.getAttribute('id') || ''),
  )
  const addLane = (id: string, name: string, index: number, fallbackWidth: number): ProcessNode => {
    const lane: ProcessNode = {
      id,
      name,
      type: 'lane',
      role: name,
      geometry: boundsOf.get(id) || { x: 40, y: 40 + index * 180, width: fallbackWidth, height: 180 },
      style: 'swimlane;horizontal=0;startSize=30;',
    }
    lanes.push(lane)
    return lane
  }

  byLocalName(doc, ['lane']).forEach((el, index) => {
    const id = el.getAttribute('id') || `lane_${index}`
    const lane = addLane(id, el.getAttribute('name') || `Дорожка ${index + 1}`, index, 1200)
    for (const ref of Array.from(el.getElementsByTagName('*'))) {
      if (ref.localName.toLowerCase() !== 'flownoderef') continue
      const child = byId.get((ref.textContent || '').trim())
      if (!child) continue
      child.laneId = lane.id
      child.laneName = lane.name
      child.role = child.role || lane.name
    }
  })

  // Пул участника с содержимым — это рамка карты, а не строка. Строкой
  // становится «чёрный ящик» внешней стороны: наш экспортёр выносит клиента
  // и госорган в отдельные пулы, и без этого шага пунктир к ним повис бы.
  byLocalName(doc, ['participant']).forEach((el, index) => {
    const id = el.getAttribute('id') || ''
    const ref = el.getAttribute('processRef') || ''
    if (!id || mainProcessIds.has(ref)) return
    addLane(id, el.getAttribute('name') || `Участник ${index + 1}`, lanes.length, 900)
  })

  // Шаг без дорожки по флагам — привязываем по геометрии, как на холсте.
  for (const node of nodes) {
    if (node.laneId) continue
    const host = lanes.find(
      (l) =>
        node.geometry.x + node.geometry.width / 2 >= l.geometry.x &&
        node.geometry.x + node.geometry.width / 2 <= l.geometry.x + l.geometry.width &&
        node.geometry.y + node.geometry.height / 2 >= l.geometry.y &&
        node.geometry.y + node.geometry.height / 2 <= l.geometry.y + l.geometry.height,
    )
    if (!host) continue
    node.laneId = host.id
    node.laneName = host.name
    node.role = node.role || host.name
  }

  // ── Сборка связей ──────────────────────────────────────────────────────
  const laneIds = new Set(lanes.map((l) => l.id))
  const edges = rawEdges
    .map(({ el, kind }) => {
      const id = el.getAttribute('id') || `edge_${Math.random().toString(16).slice(2)}`
      const sourceId = el.getAttribute('sourceRef') || undefined
      const targetId = el.getAttribute('targetRef') || undefined
      const known = (ref?: string) => !!ref && (byId.has(ref) || laneIds.has(ref))
      if (!known(sourceId) || !known(targetId)) return null
      if (markerIds.has(sourceId!) || markerIds.has(targetId!)) return null
      const condition = (childByLocalName(el, 'conditionExpression')?.textContent || '').trim()
      const name = el.getAttribute('name') || condition || ''
      return {
        id,
        name,
        kind,
        sourceId,
        targetId,
        condition: condition || undefined,
        points: routeOf.get(id) || [],
        dashed: kind !== 'sequenceFlow',
      }
    })
    .filter((e): e is NonNullable<typeof e> => e !== null)

  const definitions = byLocalName(doc, ['definitions'])[0]
  const mainProcess = byLocalName(doc, ['process']).find((p) => mainProcessIds.has(p.getAttribute('id') || ''))
  const collaboration = byLocalName(doc, ['collaboration'])[0]
  const poolName = collaboration
    ? byLocalName(collaboration, ['participant'])
        .find((p) => mainProcessIds.has(p.getAttribute('processRef') || ''))
        ?.getAttribute('name')
    : null
  const processName =
    mainProcess?.getAttribute('name') ||
    poolName ||
    definitions?.getAttribute('name') ||
    fileName.replace(/\.[^.]+$/, '')

  return assembleOpenedProcess({
    fileName,
    processName,
    passportCode: (mainProcess?.getAttribute('id') || '').replace(/^Process_/, '').replace(/_/g, '-'),
    sourceLabel: 'BPMN 2.0',
    nodes,
    lanes,
    edges,
  })
}

function defaultName(type: NodeType, index: number): string {
  switch (type) {
    case 'startEvent':
      return 'Старт'
    case 'endEvent':
      return 'Завершение'
    case 'intermediateTimerEvent':
      return 'Ожидание'
    case 'intermediateMessageEvent':
      return 'Событие-сообщение'
    case 'exclusiveGateway':
    case 'parallelGateway':
    case 'inclusiveGateway':
    case 'complexGateway':
      return 'Условие'
    case 'dataStore':
      return 'Информационная система'
    case 'dataObject':
      return 'Документ'
    case 'textAnnotation':
      return 'Примечание'
    default:
      return `Операция ${index}`
  }
}
