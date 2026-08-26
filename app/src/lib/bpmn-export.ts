import type { BusinessProcess, ProcessEdge, ProcessNode } from '@/types/process'

const NCNAME = /^[A-Za-z_][A-Za-z0-9._-]*$/

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

function anchor(node: ProcessNode, fracX: number, fracY: number): { x: number; y: number } {
  return {
    x: Math.round(node.geometry.x + node.geometry.width * fracX),
    y: Math.round(node.geometry.y + node.geometry.height * fracY),
  }
}

function edgeWaypoints(edge: ProcessEdge, src?: ProcessNode, tgt?: ProcessNode): { x: number; y: number }[] {
  if (!src || !tgt) return [{ x: 100, y: 100 }, { x: 250, y: 100 }]
  const pts = [
    anchor(src, edge.exitX ?? 1, edge.exitY ?? 0.5),
    ...edge.points.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) })),
    anchor(tgt, edge.entryX ?? 0, edge.entryY ?? 0.5),
  ]
  const out: { x: number; y: number }[] = []
  for (const pt of pts) {
    const last = out[out.length - 1]
    if (!last || last.x !== pt.x || last.y !== pt.y) out.push(pt)
  }
  return out.length >= 2 ? out : [pts[0], pts[pts.length - 1]]
}

function nodeTag(node: ProcessNode): string {
  if (node.type === 'startEvent') return 'bpmn:startEvent'
  if (node.type === 'endEvent') return 'bpmn:endEvent'
  if (node.type === 'exclusiveGateway') return 'bpmn:exclusiveGateway'
  if (node.type === 'parallelGateway') return 'bpmn:parallelGateway'
  if (node.type === 'inclusiveGateway') return 'bpmn:inclusiveGateway'
  if (node.type === 'serviceTask' || node.category === 'rpa_bot') return 'bpmn:serviceTask'
  return 'bpmn:userTask'
}

function nodeDoc(node: ProcessNode): string {
  const bits: string[] = []
  if (node.code) bits.push(`Code: ${node.code}`)
  if (node.role) bits.push(`Role: ${node.role}`)
  if (node.laneName && node.laneName !== node.role) bits.push(`Lane: ${node.laneName}`)
  if (node.system) bits.push(`System: ${node.system}`)
  if (node.slaMinutes) bits.push(`SLA: ${node.slaMinutes} min`)
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

/**
 * OMG BPMN 2.0 + BPMNDI for PIX Process Studio / Processet / Camunda.
 */
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

  const flowNodes = process.nodes.filter((n) => n.type !== 'lane')
  const lanes = process.lanes || []
  const nodeById = new Map(flowNodes.map((n) => [n.id, n]))

  const idOf = new Map<string, string>()
  for (const n of flowNodes) idOf.set(n.id, safeId(n.id, 'Node', used, taken))
  for (const lane of lanes) idOf.set(lane.id, safeId(lane.id, 'Lane', used, taken))
  for (const edge of process.edges) idOf.set(edge.id, safeId(edge.id, 'Flow', used, taken))

  const incoming: Record<string, string[]> = {}
  const outgoing: Record<string, string[]> = {}
  for (const n of flowNodes) {
    incoming[n.id] = []
    outgoing[n.id] = []
  }
  for (const edge of process.edges) {
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
    '  exporterVersion="1.1">',
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

  for (const node of flowNodes) {
    const tag = nodeTag(node)
    const nid = escapeXml(idOf.get(node.id)!)
    const outCount = (outgoing[node.id] || []).length
    const inCount = (incoming[node.id] || []).length
    let extra = ''
    if (node.type.includes('Gateway') && outCount > 1) extra = ' gatewayDirection="Diverging"'
    else if (node.type.includes('Gateway') && inCount > 1) extra = ' gatewayDirection="Converging"'
    const children: string[] = []
    const doc = nodeDoc(node)
    if (doc) children.push(`      <bpmn:documentation>${escapeXml(doc)}</bpmn:documentation>`)
    for (const edgeId of incoming[node.id] || []) {
      children.push(`      <bpmn:incoming>${escapeXml(idOf.get(edgeId)!)}</bpmn:incoming>`)
    }
    for (const edgeId of outgoing[node.id] || []) {
      children.push(`      <bpmn:outgoing>${escapeXml(idOf.get(edgeId)!)}</bpmn:outgoing>`)
    }
    if (children.length) {
      xml.push(`    <${tag} id="${nid}" name="${escapeXml(node.name)}"${extra}>`)
      xml.push(...children)
      xml.push(`    </${tag}>`)
    } else {
      xml.push(`    <${tag} id="${nid}" name="${escapeXml(node.name)}"${extra} />`)
    }
  }

  for (const edge of process.edges) {
    const eid = escapeXml(idOf.get(edge.id)!)
    const name = edge.name || edge.condition || ''
    const nameAttr = name ? ` name="${escapeXml(name)}"` : ''
    const src = edge.sourceId && idOf.has(edge.sourceId) ? escapeXml(idOf.get(edge.sourceId)!) : ''
    const tgt = edge.targetId && idOf.has(edge.targetId) ? escapeXml(idOf.get(edge.targetId)!) : ''
    const srcAttr = src ? ` sourceRef="${src}"` : ''
    const tgtAttr = tgt ? ` targetRef="${tgt}"` : ''
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

  xml.push('  </bpmn:process>', '')
  xml.push(`  <bpmndi:BPMNDiagram id="${escapeXml(diagId)}">`)
  xml.push(`    <bpmndi:BPMNPlane id="${escapeXml(planeId)}" bpmnElement="${escapeXml(planeRef)}">`)

  if (useCollab) {
    const b = unionBounds(lanes)
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

  for (const node of flowNodes) {
    xml.push(
      `      <bpmndi:BPMNShape id="${escapeXml(idOf.get(node.id)!)}_di" bpmnElement="${escapeXml(idOf.get(node.id)!)}">`,
    )
    xml.push(
      `        <dc:Bounds x="${node.geometry.x}" y="${node.geometry.y}" width="${node.geometry.width}" height="${node.geometry.height}" />`,
    )
    xml.push('      </bpmndi:BPMNShape>')
  }

  for (const edge of process.edges) {
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
