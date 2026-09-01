import type { BusinessProcess, NodeType, ProcessEdge, ProcessNode } from '@/types/process'
import { isArtifactNode, isTaskNode } from '@/types/process'
import { messageFlowEndpoints, orthogonalWaypoints } from './edge-routing'
import type { Box } from './layout'
import {
  EXTERNAL_LABEL_TYPES,
  chooseLabelBox,
  edgeLabelCandidates,
  externalLabelCandidates,
  labelSize,
  nodeObstacles,
  segmentBoxes,
} from './layout'

/**
 * OMG BPMN 2.0 + BPMNDI для PIX Процессной студии / Processet / Camunda.
 *
 * Клиентский двойник `backend/app/services/bpmn_exporter.py`: используется
 * только когда FastAPI недоступен. Инварианты держатся те же, что на бэкенде:
 *
 * - стартовое событие не может иметь входящих переходов, конечное — исходящих;
 *   нарушители понижаются до промежуточных событий (нормализация);
 * - хранилища данных и документы выгружаются как `dataStoreReference` /
 *   `dataObjectReference` и соединяются `association`, а не `sequenceFlow`;
 * - порядок элементов внутри `bpmn:process` соблюдает XSD-последовательность
 *   `laneSet*, flowElement*, artifact*`;
 * - `flowNodeRef` дорожки перечисляет только узлы потока.
 */

const NCNAME = /^[A-Za-z_][A-Za-z0-9._-]*$/

const GATEWAY_TYPES: NodeType[] = ['exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway']

/** Эффективный тип узла в выгрузке: может отличаться от модельного после нормализации. */
type EffectiveType = NodeType | 'intermediateThrowEvent'

function escapeXml(str: string | number | undefined | null): string {
  if (str == null) return ''
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function safeId(raw: string, prefix: string, used: Map<string, string>, taken: Set<string>): string {
  const original = raw || ''
  const cached = used.get(original)
  if (cached) return cached
  let candidate = NCNAME.test(original) ? original : ''
  if (!candidate) {
    let cleaned = original.replace(/[^A-Za-z0-9._-]/g, '_')
    if (!cleaned || !/^[A-Za-z_]/.test(cleaned)) {
      cleaned = cleaned ? `${prefix}_${cleaned}` : prefix
    }
    candidate = cleaned
  }
  const base = candidate
  let n = 2
  while (taken.has(candidate)) {
    candidate = `${base}_${n}`
    n += 1
  }
  used.set(original, candidate)
  taken.add(candidate)
  return candidate
}

/** Минуты -> ISO-8601 длительность для timerEventDefinition. */
export function isoDuration(minutes: number | undefined): string {
  const value = Math.max(1, Math.round(minutes || 0) || 1)
  if (value >= 1440 && value % 1440 === 0) return `P${value / 1440}D`
  if (value >= 60 && value % 60 === 0) return `PT${value / 60}H`
  return `PT${value}M`
}

/** Диаметр значка события в BPMNDI: bpmn.io и Процессная студия рисуют 36 px. */
export const EVENT_SIDE = 36

/** Отступ значка длительности от правого края шага, px. */
const DURATION_INSET = 20

/** Человекочитаемая длительность: «45 мин», «2 ч», «1 ч 30 мин», «2 дн». */
export function formatDuration(minutes: number | undefined | null): string {
  const value = Math.round(minutes || 0)
  if (value <= 0) return ''
  if (value < 60) return `${value} мин`
  const hours = Math.floor(value / 60)
  const rest = value % 60
  if (rest) return `${hours} ч ${rest} мин`
  if (hours >= 24 && hours % 24 === 0) return `${hours / 24} дн`
  return `${hours} ч`
}

/** Подпись под часами у шага: время операции и, если есть, ожидание. */
export function stepDurationText(node: ProcessNode): string {
  const st = formatDuration(node.slaMinutes)
  const wt = formatDuration(node.waitMinutes)
  if (st && wt) return `${st} · ожидание ${wt}`
  if (wt) return `ожидание ${wt}`
  return st
}

/**
 * Значок часов у шага: граничный таймер плюс подпись со временем.
 *
 * На карте draw.io время операции нарисовано мелкой фигурой-таймером в углу
 * шага, и холст банка показывает его там же. В BPMN это время жило только в
 * `documentation`: в Процессной студии карта открывалась без единой цифры, и
 * сотруднику приходилось сверяться с регламентом отдельно.
 *
 * Некрывающий (`cancelActivity="false"`) граничный таймер — единственная
 * конструкция BPMN, которую импортёры рисуют ровно там, где значок стоит на
 * исходной карте: на границе фигуры. Поток она не меняет — у события нет
 * исходящих переходов, оно только помечает длительность.
 */
export interface DurationMarker {
  node: ProcessNode
  markerId: string
  text: string
  minutes: number
  cx: number
  cy: number
}

export function durationMarkers(
  flowNodes: ProcessNode[],
  idOf: Map<string, string>,
  used: Map<string, string>,
  taken: Set<string>,
): DurationMarker[] {
  const markers: DurationMarker[] = []
  for (const node of flowNodes) {
    // Граничное событие BPMN разрешено только у активности: события и шлюзы
    // значка не получают — их время уходит в собственный timerEventDefinition.
    if (!isTaskNode(node.type)) continue
    const text = stepDurationText(node)
    if (!text) continue
    const g = node.geometry
    // Узкий шаг значком не разрезать пополам: у него часы встают по центру
    // нижней грани, у обычного — в правом нижнем углу, как в draw.io.
    const offset = Math.max(g.width - DURATION_INSET, g.width / 2)
    markers.push({
      node,
      markerId: safeId(`Duration_${idOf.get(node.id)!}`, 'Duration', used, taken),
      text,
      minutes: Math.round(node.slaMinutes || node.waitMinutes || 0),
      cx: Math.round(g.x + offset),
      cy: Math.round(g.y + g.height),
    })
  }
  return markers
}

/** Рамка самого значка: круг события сидит на границе шага. */
export function markerBox(marker: DurationMarker): Box {
  const half = EVENT_SIDE / 2
  return { x: marker.cx - half, y: marker.cy - half, width: EVENT_SIDE, height: EVENT_SIDE }
}

/**
 * Куда положить время: под часами, затем правее, левее, выше и по углам.
 *
 * Четырёх сторон на плотной карте не хватает: если все они заняты, выбор
 * падает на «наименее конфликтную» позицию, и время печатается поверх текста
 * самого шага. Диагонали и вторая полка дают запас.
 */
function durationLabelCandidates(marker: DurationMarker): Box[] {
  const { width, height } = labelSize(marker.text)
  const half = EVENT_SIDE / 2
  const cx = marker.cx
  const cy = marker.cy
  const near = half + 4
  const far = half + 6 + height
  const mid = Math.round(cx - width / 2)
  const at = (x: number, y: number): Box => ({ x: Math.round(x), y: Math.round(y), width, height })
  return [
    at(mid, cy + half + 2),
    at(cx + near, cy - height / 2),
    at(cx - near - width, cy - height / 2),
    at(mid, cy - half - 2 - height),
    at(cx + near, cy + half + 2),
    at(cx - near - width, cy + half + 2),
    at(cx + near, cy - half - 2 - height),
    at(cx - near - width, cy - half - 2 - height),
    at(mid, cy + far),
    at(mid, cy - far - height),
  ]
}

/** Полоса с названием дорожки внутри пула — bpmn.io рисует её той же ширины. */
const LANE_HEADER = 30
/** Толщина рамки дорожки: подпись, севшая на разделитель, читается перечёркнутой. */
const LANE_BORDER = 4

/**
 * Заголовок дорожки и её рамка — места, куда подпись класть нельзя.
 *
 * В полосе слева bpmn.io печатает повёрнутое название дорожки, а по контуру
 * рисует линию. Подпись, попавшая туда, ложится либо поверх названия, либо
 * ровно на разделитель между дорожками — ровно то, что видно в выгрузке.
 */
function bandBoxes(x: number, y: number, width: number, height: number, header: number): Box[] {
  const b = LANE_BORDER
  return [
    { x, y, width: header, height },
    { x, y: y - b, width, height: 2 * b },
    { x, y: y + height - b, width, height: 2 * b },
  ]
}

function durationMarkerXml(marker: DurationMarker, attachedTo: string): string[] {
  return [
    `    <bpmn:boundaryEvent id="${escapeXml(marker.markerId)}"` +
      ` name="${escapeXml(marker.text)}"` +
      ` attachedToRef="${escapeXml(attachedTo)}" cancelActivity="false">`,
    '      <bpmn:timerEventDefinition>',
    `        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">${isoDuration(marker.minutes)}</bpmn:timeDuration>`,
    '      </bpmn:timerEventDefinition>',
    '    </bpmn:boundaryEvent>',
  ]
}

/**
 * Ортогональная ломаная — та же, что рисует draw.io. Раньше сюда шла только
 * пара «точка выхода — точка входа», и в bpmn.io схема выглядела диагональной
 * паутиной.
 */
function edgeWaypoints(edge: ProcessEdge, src?: ProcessNode, tgt?: ProcessNode): { x: number; y: number }[] {
  const route = orthogonalWaypoints(edge, src, tgt)
  if (route.length < 2) return [{ x: 100, y: 100 }, { x: 250, y: 100 }]
  return route.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }))
}

/**
 * Приводит степени событий к требованиям BPMN 2.0. Исходная модель не
 * меняется: понижение типа нужно только для выгрузки.
 */
export function normalizeEventTypes(
  flowNodes: ProcessNode[],
  edges: ProcessEdge[],
): Map<string, EffectiveType> {
  const incoming = new Set<string>()
  const outgoing = new Set<string>()
  for (const e of edges) {
    if ((e.kind ?? 'sequenceFlow') === 'association') continue
    if (e.targetId) incoming.add(e.targetId)
    if (e.sourceId) outgoing.add(e.sourceId)
  }
  const effective = new Map<string, EffectiveType>()
  for (const node of flowNodes) {
    let kind: EffectiveType = node.type
    if (node.type === 'startEvent' && incoming.has(node.id)) kind = 'intermediateThrowEvent'
    else if (node.type === 'endEvent' && outgoing.has(node.id)) kind = 'intermediateThrowEvent'
    effective.set(node.id, kind)
  }
  return effective
}

function nodeTag(node: ProcessNode, effective: EffectiveType): string {
  switch (effective) {
    case 'startEvent':
      return 'bpmn:startEvent'
    case 'endEvent':
      return 'bpmn:endEvent'
    case 'intermediateThrowEvent':
      return 'bpmn:intermediateThrowEvent'
    case 'intermediateTimerEvent':
    case 'intermediateMessageEvent':
      return 'bpmn:intermediateCatchEvent'
    case 'exclusiveGateway':
      return 'bpmn:exclusiveGateway'
    case 'parallelGateway':
      return 'bpmn:parallelGateway'
    case 'inclusiveGateway':
      return 'bpmn:inclusiveGateway'
    case 'complexGateway':
      return 'bpmn:complexGateway'
    case 'subProcess':
      return 'bpmn:subProcess'
    case 'dataStore':
      return 'bpmn:dataStoreReference'
    case 'dataObject':
      return 'bpmn:dataObjectReference'
    case 'textAnnotation':
      return 'bpmn:textAnnotation'
    case 'task':
      return 'bpmn:task'
    default:
      return effective === 'serviceTask' || node.category === 'rpa_bot' ? 'bpmn:serviceTask' : 'bpmn:userTask'
  }
}

function nodeDoc(node: ProcessNode): string {
  const bits: string[] = []
  if (node.code) bits.push(`Code: ${node.code}`)
  if (node.role) bits.push(`Role: ${node.role}`)
  if (node.laneName && node.laneName !== node.role) bits.push(`Lane: ${node.laneName}`)
  if (node.system) bits.push(`System: ${node.system}`)
  if (node.slaMinutes) bits.push(`ST: ${node.slaMinutes} min`)
  if (node.waitMinutes) bits.push(`WT: ${node.waitMinutes} min`)
  if (node.inputArtifacts?.length) bits.push(`In: ${node.inputArtifacts.join(', ')}`)
  if (node.outputArtifacts?.length) bits.push(`Out: ${node.outputArtifacts.join(', ')}`)
  if (node.category) bits.push(`Category: ${node.category}`)
  if (node.automationPotential) bits.push(`RPA potential: ${node.automationPotential}%`)
  return bits.join('; ')
}

/** Ширина заголовочной полосы пула в BPMNDI (bpmn.io рисует ровно 30 px). */
const POOL_HEADER = 30

/** Типы, которым BPMN разрешает быть концом messageFlow (InteractionNode). */
const INTERACTION_TYPES: NodeType[] = [
  'task', 'userTask', 'serviceTask', 'subProcess',
  'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
]

/**
 * Делит дорожки на полосы пула и внешних участников.
 *
 * Дорожка без единого шага — это не зона ответственности внутри организации,
 * а внешняя сторона (клиент, госорган): аналитик отводит ей полосу и тянет к
 * ней пунктир от шагов банка. В BPMN такая полоса обязана быть отдельным
 * участником-«чёрным ящиком», иначе импортёр (PIX Процессная студия) выбросит
 * дорожку без `flowNodeRef` — и с карты пропадает целая строка вместе со всеми
 * пунктирными связями к ней.
 *
 * Пустая дорожка, попадающая внутрь вертикального размаха заполненных,
 * участником не становится: вынести её из пула значило бы разорвать пул.
 */
export function splitExternalLanes(
  lanes: ProcessNode[],
  flowNodes: ProcessNode[],
): { inner: ProcessNode[]; external: ProcessNode[] } {
  const populated = lanes.filter((l) => flowNodes.some((n) => n.laneId === l.id))
  if (!populated.length) return { inner: lanes, external: [] }
  const top = Math.min(...populated.map((l) => l.geometry.y))
  const bottom = Math.max(...populated.map((l) => l.geometry.y + l.geometry.height))

  const inner: ProcessNode[] = []
  const external: ProcessNode[] = []
  for (const lane of lanes) {
    const g = lane.geometry
    const empty = !populated.includes(lane)
    const overlaps = g.y < bottom && g.y + g.height > top
    ;(empty && !overlaps ? external : inner).push(lane)
  }
  return { inner, external }
}

function unionBounds(nodes: ProcessNode[], header = POOL_HEADER): { x: number; y: number; width: number; height: number } {
  if (!nodes.length) return { x: 40, y: 40, width: 800, height: 200 }
  const minX = Math.min(...nodes.map((n) => n.geometry.x))
  const minY = Math.min(...nodes.map((n) => n.geometry.y))
  const maxX = Math.max(...nodes.map((n) => n.geometry.x + n.geometry.width))
  const maxY = Math.max(...nodes.map((n) => n.geometry.y + n.geometry.height))
  const x = minX - header
  return { x, y: minY, width: Math.max(maxX - x, 80), height: Math.max(maxY - minY, 80) }
}

export function generateBpmn2Xml(process: BusinessProcess): string {
  const used = new Map<string, string>()
  const taken = new Set<string>()
  const procId = safeId(
    `Process_${(process.passport.code || 'SQB').replace(/[^A-Za-z0-9_]/g, '_')}`,
    'Process',
    used,
    taken,
  )
  const defId = safeId(`Definitions_${process.id}`, 'Definitions', used, taken)
  const diagId = safeId(`Diagram_${procId}`, 'Diagram', used, taken)
  const planeId = safeId(`Plane_${procId}`, 'Plane', used, taken)
  const collabId = safeId(`Collaboration_${procId}`, 'Collaboration', used, taken)
  const participantId = safeId(`Participant_${procId}`, 'Participant', used, taken)
  const laneSetId = safeId(`LaneSet_${procId}`, 'LaneSet', used, taken)

  const allNodes = process.nodes.filter((n) => n.type !== 'lane')
  const flowNodes = allNodes.filter((n) => !isArtifactNode(n.type))
  const artifactNodes = allNodes.filter((n) => isArtifactNode(n.type))
  const { inner: lanes, external: externalLanes } = splitExternalLanes(process.lanes || [], flowNodes)
  const externalById = new Map(externalLanes.map((l) => [l.id, l]))
  const nodeById = new Map(allNodes.map((n) => [n.id, n]))

  // Точки контакта с внешним участником: один конец — его полоса, другой —
  // шаг или событие процесса. В BPMN это messageFlow внутри collaboration.
  const messageFlows = process.edges.filter((e) => {
    if (e.kind !== 'messageFlow' || !e.sourceId || !e.targetId) return false
    const laneIsSource = externalById.has(e.sourceId)
    const laneIsTarget = externalById.has(e.targetId)
    if (!laneIsSource && !laneIsTarget) return false
    const other = nodeById.get((laneIsSource ? e.targetId : e.sourceId) as string)
    return !!other && INTERACTION_TYPES.includes(other.type)
  })

  // Висячие связи и оформительские линии draw.io в схему не идут. Петля из
  // фигуры в саму себя тоже: спецификация её не допускает, а PIX Процессная
  // студия из-за одной такой линии отказывается открыть всю карту
  // («Connector source and target node cannot be the same»).
  const edges = process.edges.filter(
    (e) =>
      e.kind !== 'annotationLine' &&
      e.sourceId && e.targetId && nodeById.has(e.sourceId) && nodeById.has(e.targetId) &&
      e.sourceId !== e.targetId,
  )
  // messageFlow между двумя шагами одного пула спецификация запрещает: карта
  // SQB — один пул, поэтому такие связи выгружаются как поток управления.
  const sequenceEdges = edges.filter((e) => (e.kind ?? 'sequenceFlow') !== 'association')
  const associationEdges = edges.filter((e) => e.kind === 'association')

  const effectiveType = normalizeEventTypes(flowNodes, sequenceEdges)

  const idOf = new Map<string, string>()
  for (const n of flowNodes) idOf.set(n.id, safeId(n.id, 'Node', used, taken))
  for (const n of artifactNodes) idOf.set(n.id, safeId(n.id, 'Artifact', used, taken))
  for (const lane of lanes) idOf.set(lane.id, safeId(lane.id, 'Lane', used, taken))
  for (const lane of externalLanes) {
    idOf.set(lane.id, safeId(`Participant_${lane.id}`, 'Participant', used, taken))
  }
  for (const edge of edges) idOf.set(edge.id, safeId(edge.id, 'Flow', used, taken))
  for (const edge of messageFlows) idOf.set(edge.id, safeId(edge.id, 'MessageFlow', used, taken))

  const markers = durationMarkers(flowNodes, idOf, used, taken)
  const markerOf = new Map(markers.map((m) => [m.node.id, m]))

  const incoming: Record<string, string[]> = {}
  const outgoing: Record<string, string[]> = {}
  for (const n of flowNodes) {
    incoming[n.id] = []
    outgoing[n.id] = []
  }
  for (const edge of sequenceEdges) {
    if (edge.targetId && incoming[edge.targetId]) incoming[edge.targetId].push(edge.id)
    if (edge.sourceId && outgoing[edge.sourceId]) outgoing[edge.sourceId].push(edge.id)
  }

  const useCollab = lanes.length > 0 || externalLanes.length > 0
  const planeRef = useCollab ? collabId : procId
  const processName = escapeXml(process.passport.name || process.name || 'Business process')
  const xml: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
    '  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
    '  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
    '  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
    '  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
    `  id="${escapeXml(defId)}"`,
    '  targetNamespace="http://bpmn.io/schema/bpmn"',
    '  exporter="SQB Process Hub"',
    '  exporterVersion="2.0">',
    '',
    `  <!-- Process map: ${processName.replace(/--/g, '—')} -->`,
  ]

  if (useCollab) {
    xml.push(`  <bpmn:collaboration id="${escapeXml(collabId)}">`)
    xml.push(
      `    <bpmn:participant id="${escapeXml(participantId)}" name="${processName}" processRef="${escapeXml(procId)}" />`,
    )
    // Внешний участник — «чёрный ящик»: без processRef, только имя полосы.
    for (const lane of externalLanes) {
      xml.push(
        `    <bpmn:participant id="${escapeXml(idOf.get(lane.id)!)}" name="${escapeXml(lane.name)}" />`,
      )
    }
    for (const edge of messageFlows) {
      const name = (edge.name || edge.condition || '').trim()
      const nameAttr = name ? ` name="${escapeXml(name)}"` : ''
      xml.push(
        `    <bpmn:messageFlow id="${escapeXml(idOf.get(edge.id)!)}"${nameAttr}` +
          ` sourceRef="${escapeXml(idOf.get(edge.sourceId!)!)}"` +
          ` targetRef="${escapeXml(idOf.get(edge.targetId!)!)}" />`,
      )
    }
    xml.push('  </bpmn:collaboration>', '')
  }

  xml.push(`  <bpmn:process id="${escapeXml(procId)}" name="${processName}" isExecutable="true">`)

  // ── 1. laneSet (только узлы потока) ───────────────────────────────────────
  if (lanes.length) {
    xml.push(`    <bpmn:laneSet id="${escapeXml(laneSetId)}">`)
    for (const lane of lanes) {
      xml.push(`      <bpmn:lane id="${escapeXml(idOf.get(lane.id)!)}" name="${escapeXml(lane.name)}">`)
      for (const child of flowNodes) {
        if (child.laneId !== lane.id) continue
        xml.push(`        <bpmn:flowNodeRef>${escapeXml(idOf.get(child.id)!)}</bpmn:flowNodeRef>`)
        // Значок длительности — тоже узел потока: дорожка без ссылки на
        // него импортируется без часов у своих шагов.
        const marker = markerOf.get(child.id)
        if (marker) {
          xml.push(`        <bpmn:flowNodeRef>${escapeXml(marker.markerId)}</bpmn:flowNodeRef>`)
        }
      }
      xml.push('      </bpmn:lane>')
    }
    xml.push('    </bpmn:laneSet>')
  }

  // ── 2. Узлы потока ────────────────────────────────────────────────────────
  for (const node of flowNodes) {
    const kind = effectiveType.get(node.id)!
    const tag = nodeTag(node, kind)
    const nid = escapeXml(idOf.get(node.id)!)
    const outCount = (outgoing[node.id] || []).length
    const inCount = (incoming[node.id] || []).length
    let extra = ''
    if (GATEWAY_TYPES.includes(kind as NodeType)) {
      if (outCount > 1) extra = ' gatewayDirection="Diverging"'
      else if (inCount > 1) extra = ' gatewayDirection="Converging"'
    }
    const children: string[] = []
    const doc = nodeDoc(node)
    if (doc) children.push(`      <bpmn:documentation>${escapeXml(doc)}</bpmn:documentation>`)
    for (const edgeId of incoming[node.id] || []) {
      children.push(`      <bpmn:incoming>${escapeXml(idOf.get(edgeId)!)}</bpmn:incoming>`)
    }
    for (const edgeId of outgoing[node.id] || []) {
      children.push(`      <bpmn:outgoing>${escapeXml(idOf.get(edgeId)!)}</bpmn:outgoing>`)
    }
    if (kind === 'intermediateTimerEvent') {
      children.push('      <bpmn:timerEventDefinition>')
      children.push(
        `        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">${isoDuration(node.slaMinutes)}</bpmn:timeDuration>`,
      )
      children.push('      </bpmn:timerEventDefinition>')
    } else if (kind === 'intermediateMessageEvent') {
      children.push('      <bpmn:messageEventDefinition />')
    }
    if (children.length) {
      xml.push(`    <${tag} id="${nid}" name="${escapeXml(node.name)}"${extra}>`)
      xml.push(...children)
      xml.push(`    </${tag}>`)
    } else {
      xml.push(`    <${tag} id="${nid}" name="${escapeXml(node.name)}"${extra} />`)
    }

    const marker = markerOf.get(node.id)
    if (marker) xml.push(...durationMarkerXml(marker, nid))
  }

  // ── 3. Артефакты-элементы потока: хранилища и документы ───────────────────
  for (const node of artifactNodes) {
    if (node.type === 'textAnnotation') continue
    const tag = nodeTag(node, node.type)
    xml.push(`    <${tag} id="${escapeXml(idOf.get(node.id)!)}" name="${escapeXml(node.name)}" />`)
  }

  // ── 4. Переходы ───────────────────────────────────────────────────────────
  for (const edge of sequenceEdges) {
    const eid = escapeXml(idOf.get(edge.id)!)
    const name = edge.name || edge.condition || ''
    const nameAttr = name ? ` name="${escapeXml(name)}"` : ''
    const srcAttr = ` sourceRef="${escapeXml(idOf.get(edge.sourceId!)!)}"`
    const tgtAttr = ` targetRef="${escapeXml(idOf.get(edge.targetId!)!)}"`
    const expr = (edge.condition || edge.name || '').trim()
    if (expr) {
      xml.push(`    <bpmn:sequenceFlow id="${eid}"${nameAttr}${srcAttr}${tgtAttr}>`)
      xml.push(
        `      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">${escapeXml(expr)}</bpmn:conditionExpression>`,
      )
      xml.push('    </bpmn:sequenceFlow>')
    } else {
      xml.push(`    <bpmn:sequenceFlow id="${eid}"${nameAttr}${srcAttr}${tgtAttr} />`)
    }
  }

  // ── 5. Артефакты по XSD идут последними: примечания и ассоциации ──────────
  for (const node of artifactNodes) {
    if (node.type !== 'textAnnotation') continue
    xml.push(`    <bpmn:textAnnotation id="${escapeXml(idOf.get(node.id)!)}">`)
    xml.push(`      <bpmn:text>${escapeXml(node.name)}</bpmn:text>`)
    xml.push('    </bpmn:textAnnotation>')
  }

  for (const edge of associationEdges) {
    xml.push(
      `    <bpmn:association id="${escapeXml(idOf.get(edge.id)!)}"` +
        ` sourceRef="${escapeXml(idOf.get(edge.sourceId!)!)}"` +
        ` targetRef="${escapeXml(idOf.get(edge.targetId!)!)}"` +
        ' associationDirection="One" />',
    )
  }

  xml.push('  </bpmn:process>', '')

  // ── Диаграмма ─────────────────────────────────────────────────────────────
  xml.push(`  <bpmndi:BPMNDiagram id="${escapeXml(diagId)}">`)
  xml.push(`    <bpmndi:BPMNPlane id="${escapeXml(planeId)}" bpmnElement="${escapeXml(planeRef)}">`)

  const pool = unionBounds([...lanes, ...allNodes])
  if (useCollab) {
    // Пул охватывает дорожки и узлы; полосы внешних участников — отдельные
    // пулы и в его границы не входят.
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(participantId)}_di" bpmnElement="${escapeXml(participantId)}" isHorizontal="true">`,
    )
    xml.push(
      `        <dc:Bounds x="${pool.x}" y="${pool.y}" width="${pool.width}" height="${pool.height}" />`,
    )
    xml.push('      </bpmndi:BPMNShape>')
    for (const lane of externalLanes) {
      xml.push(
        `      <bpmndi:BPMNShape id="${escapeXml(idOf.get(lane.id)!)}_di" bpmnElement="${escapeXml(idOf.get(lane.id)!)}" isHorizontal="true">`,
      )
      xml.push(
        `        <dc:Bounds x="${lane.geometry.x}" y="${lane.geometry.y}" width="${lane.geometry.width}" height="${lane.geometry.height}" />`,
      )
      xml.push('      </bpmndi:BPMNShape>')
    }
  }

  for (const lane of lanes) {
    // Полосы обязаны тайлиться внутри пула: иначе импортёр рисует их уступами,
    // а часть карты оказывается за пределами дорожек.
    const laneX = useCollab ? pool.x + POOL_HEADER : lane.geometry.x
    const laneW = useCollab ? Math.max(pool.width - POOL_HEADER, 80) : lane.geometry.width
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(idOf.get(lane.id)!)}_di" bpmnElement="${escapeXml(idOf.get(lane.id)!)}" isHorizontal="true">`,
    )
    xml.push(
      `        <dc:Bounds x="${laneX}" y="${lane.geometry.y}" width="${laneW}" height="${lane.geometry.height}" />`,
    )
    xml.push('      </bpmndi:BPMNShape>')
  }

  // ── Подписи ──────────────────────────────────────────────────────────────
  // Позиции считаем ДО отрисовки: подпись шлюза, подпись связи и подпись
  // соседнего события претендуют на одно и то же место под фигурой, и
  // разводить их можно, только зная все занятые прямоугольники сразу.
  const routes: [ProcessEdge, { x: number; y: number }[]][] = edges.map((edge) => [
    edge,
    edgeWaypoints(edge, nodeById.get(edge.sourceId || ''), nodeById.get(edge.targetId || '')),
  ])
  for (const edge of messageFlows) {
    const laneIsSource = externalById.has(edge.sourceId!)
    const lane = externalById.get((laneIsSource ? edge.sourceId : edge.targetId) as string)!
    const peer = nodeById.get((laneIsSource ? edge.targetId : edge.sourceId) as string)!
    const [src, tgt] = messageFlowEndpoints(edge, peer, lane, laneIsSource)
    routes.push([edge, edgeWaypoints(edge, src, tgt)])
  }

  const takenBoxes: Box[] = nodeObstacles(allNodes)
  // Заголовки дорожек и пулов — тоже занятое место: в них печатается
  // повёрнутое название, и подпись, попавшая туда, ложится прямо на него.
  if (useCollab) {
    takenBoxes.push({ x: pool.x, y: pool.y, width: POOL_HEADER, height: pool.height })
    for (const lane of externalLanes) {
      const g = lane.geometry
      takenBoxes.push(...bandBoxes(g.x, g.y, g.width, g.height, POOL_HEADER))
    }
  }
  for (const lane of lanes) {
    const g = lane.geometry
    const laneX = useCollab ? pool.x + POOL_HEADER : g.x
    const laneW = useCollab ? Math.max(pool.width - POOL_HEADER, 80) : g.width
    takenBoxes.push(...bandBoxes(laneX, g.y, laneW, g.height, LANE_HEADER))
  }
  // Часы у шага занимают место на карте так же, как фигура: подпись связи,
  // положенная на них, скрывает цифру.
  for (const m of markers) takenBoxes.push(markerBox(m))
  // Линии связей — тоже препятствие: подпись, положенная на связь, читается
  // как перечёркнутая.
  for (const [, route] of routes) takenBoxes.push(...segmentBoxes(route))

  const edgeLabelOf = new Map<string, Box>()
  for (const [edge, route] of routes) {
    const text = (edge.name || edge.condition || '').trim()
    if (!text) continue
    const box = chooseLabelBox(edgeLabelCandidates(route, text), takenBoxes)
    edgeLabelOf.set(edge.id, box)
    takenBoxes.push(box)
  }

  const nodeLabelOf = new Map<string, Box>()
  for (const node of allNodes) {
    if (!EXTERNAL_LABEL_TYPES.includes(node.type) || !(node.name || '').trim()) continue
    const own = node.geometry
    const obstacles = takenBoxes.filter(
      (b) => !(b.x === own.x && b.y === own.y && b.width === own.width && b.height === own.height),
    )
    const box = chooseLabelBox(externalLabelCandidates(node), obstacles)
    nodeLabelOf.set(node.id, box)
    takenBoxes.push(box)
  }

  // Время под часами кладём последним: оно уступает место подписям связей и
  // событий, а не наоборот — цифру читают, подойдя к конкретному шагу.
  const markerLabelOf = new Map<string, Box>()
  for (const marker of markers) {
    const box = chooseLabelBox(durationLabelCandidates(marker), takenBoxes)
    markerLabelOf.set(marker.markerId, box)
    takenBoxes.push(box)
  }

  for (const node of allNodes) {
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(idOf.get(node.id)!)}_di" bpmnElement="${escapeXml(idOf.get(node.id)!)}">`,
    )
    xml.push(
      `        <dc:Bounds x="${node.geometry.x}" y="${node.geometry.y}" width="${node.geometry.width}" height="${node.geometry.height}" />`,
    )
    // Подпись события, шлюза и артефакта импортёр рисует вне фигуры и переносит
    // по рамке в 90 px. Без явных границ длинное имя шлюза превращается в
    // столбец, накрывающий соседние шаги и подписи связей.
    const nodeBox = nodeLabelOf.get(node.id)
    if (nodeBox) {
      xml.push(
        '        <bpmndi:BPMNLabel>',
        `          <dc:Bounds x="${nodeBox.x}" y="${nodeBox.y}" width="${nodeBox.width}" height="${nodeBox.height}" />`,
        '        </bpmndi:BPMNLabel>',
      )
    }
    xml.push('      </bpmndi:BPMNShape>')

    const marker = markerOf.get(node.id)
    if (marker) {
      const mb = markerBox(marker)
      const lb = markerLabelOf.get(marker.markerId)!
      xml.push(
        `      <bpmndi:BPMNShape id="${escapeXml(marker.markerId)}_di" bpmnElement="${escapeXml(marker.markerId)}">`,
        `        <dc:Bounds x="${mb.x}" y="${mb.y}" width="${mb.width}" height="${mb.height}" />`,
        '        <bpmndi:BPMNLabel>',
        `          <dc:Bounds x="${lb.x}" y="${lb.y}" width="${lb.width}" height="${lb.height}" />`,
        '        </bpmndi:BPMNLabel>',
        '      </bpmndi:BPMNShape>',
      )
    }
  }

  const labelXml = (box: Box, indent: string) => [
    `${indent}<bpmndi:BPMNLabel>`,
    `${indent}  <dc:Bounds x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" />`,
    `${indent}</bpmndi:BPMNLabel>`,
  ]

  for (const [edge, route] of routes) {
    xml.push(
      `      <bpmndi:BPMNEdge id="${escapeXml(idOf.get(edge.id)!)}_di" bpmnElement="${escapeXml(idOf.get(edge.id)!)}">`,
    )
    for (const p of route) xml.push(`        <di:waypoint x="${p.x}" y="${p.y}" />`)
    const box = edgeLabelOf.get(edge.id)
    if (box) xml.push(...labelXml(box, '        '))
    xml.push('      </bpmndi:BPMNEdge>')
  }

  xml.push('    </bpmndi:BPMNPlane>')
  xml.push('  </bpmndi:BPMNDiagram>')
  xml.push('</bpmn:definitions>')
  return xml.join('\n')
}
