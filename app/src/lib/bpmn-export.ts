import type { BusinessProcess, NodeType, ProcessEdge, ProcessNode } from '@/types/process'
import { isArtifactNode } from '@/types/process'
import { orthogonalWaypoints } from './edge-routing'

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

const GATEWAY_TYPES: NodeType[] = ['exclusiveGateway', 'parallelGateway', 'inclusiveGateway']

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

function unionBounds(nodes: ProcessNode[], header = 30): { x: number; y: number; width: number; height: number } {
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
  const lanes = process.lanes || []
  const nodeById = new Map(allNodes.map((n) => [n.id, n]))

  // Висячие связи и оформительские линии draw.io в схему не идут.
  const edges = process.edges.filter(
    (e) =>
      e.kind !== 'annotationLine' &&
      e.sourceId && e.targetId && nodeById.has(e.sourceId) && nodeById.has(e.targetId),
  )
  // messageFlow допустим только между пулами; карта SQB — один пул, поэтому
  // такие связи выгружаются как обычный поток управления.
  const sequenceEdges = edges.filter((e) => (e.kind ?? 'sequenceFlow') !== 'association')
  const associationEdges = edges.filter((e) => e.kind === 'association')

  const effectiveType = normalizeEventTypes(flowNodes, sequenceEdges)

  const idOf = new Map<string, string>()
  for (const n of flowNodes) idOf.set(n.id, safeId(n.id, 'Node', used, taken))
  for (const n of artifactNodes) idOf.set(n.id, safeId(n.id, 'Artifact', used, taken))
  for (const lane of lanes) idOf.set(lane.id, safeId(lane.id, 'Lane', used, taken))
  for (const edge of edges) idOf.set(edge.id, safeId(edge.id, 'Flow', used, taken))

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

  const useCollab = lanes.length > 0
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
    xml.push('  </bpmn:collaboration>', '')
  }

  xml.push(`  <bpmn:process id="${escapeXml(procId)}" name="${processName}" isExecutable="true">`)

  // ── 1. laneSet (только узлы потока) ───────────────────────────────────────
  if (lanes.length) {
    xml.push(`    <bpmn:laneSet id="${escapeXml(laneSetId)}">`)
    for (const lane of lanes) {
      xml.push(`      <bpmn:lane id="${escapeXml(idOf.get(lane.id)!)}" name="${escapeXml(lane.name)}">`)
      for (const child of flowNodes) {
        if (child.laneId === lane.id) {
          xml.push(`        <bpmn:flowNodeRef>${escapeXml(idOf.get(child.id)!)}</bpmn:flowNodeRef>`)
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

  if (useCollab) {
    // Пул охватывает и дорожки, и узлы: иначе часть карты окажется вне пула.
    const b = unionBounds([...lanes, ...allNodes])
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(participantId)}_di" bpmnElement="${escapeXml(participantId)}" isHorizontal="true">`,
    )
    xml.push(`        <dc:Bounds x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" />`)
    xml.push('      </bpmndi:BPMNShape>')
  }

  for (const lane of lanes) {
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(idOf.get(lane.id)!)}_di" bpmnElement="${escapeXml(idOf.get(lane.id)!)}" isHorizontal="true">`,
    )
    xml.push(
      `        <dc:Bounds x="${lane.geometry.x}" y="${lane.geometry.y}" width="${lane.geometry.width}" height="${lane.geometry.height}" />`,
    )
    xml.push('      </bpmndi:BPMNShape>')
  }

  for (const node of allNodes) {
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(idOf.get(node.id)!)}_di" bpmnElement="${escapeXml(idOf.get(node.id)!)}">`,
    )
    xml.push(
      `        <dc:Bounds x="${node.geometry.x}" y="${node.geometry.y}" width="${node.geometry.width}" height="${node.geometry.height}" />`,
    )
    xml.push('      </bpmndi:BPMNShape>')
  }

  for (const edge of edges) {
    const src = nodeById.get(edge.sourceId || '')
    const tgt = nodeById.get(edge.targetId || '')
    xml.push(
      `      <bpmndi:BPMNEdge id="${escapeXml(idOf.get(edge.id)!)}_di" bpmnElement="${escapeXml(idOf.get(edge.id)!)}">`,
    )
    for (const p of edgeWaypoints(edge, src, tgt)) {
      xml.push(`        <di:waypoint x="${p.x}" y="${p.y}" />`)
    }
    xml.push('      </bpmndi:BPMNEdge>')
  }

  xml.push('    </bpmndi:BPMNPlane>')
  xml.push('  </bpmndi:BPMNDiagram>')
  xml.push('</bpmn:definitions>')
  return xml.join('\n')
}
