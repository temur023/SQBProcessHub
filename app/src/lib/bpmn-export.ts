import type { BusinessProcess, ProcessNode, ProcessEdge } from '@/types/process'

/**
 * Generates official BPMN 2.0 XML compliant with OMG BPMN 2.0 standard
 * and optimized for Infomaximum Processet Process Mining model import.
 */
export function generateBpmn2Xml(process: BusinessProcess): string {
  const processId = `Process_${process.passport.code.replace(/[^a-zA-Z0-9_]/g, '_') || 'SQB_BPM'}`
  const processName = escapeXml(process.passport.name || process.name || 'Бизнес-процесс SQB')

  // Map nodes to BPMN element tags
  const flowElementsXml = process.nodes
    .map((node) => {
      const id = escapeXml(node.id)
      const name = escapeXml(node.name || node.code || '')
      const incoming = process.edges
        .filter((e) => e.targetId === node.id)
        .map((e) => `<bpmn:incoming>${escapeXml(e.id)}</bpmn:incoming>`)
        .join('\n        ')
      const outgoing = process.edges
        .filter((e) => e.sourceId === node.id)
        .map((e) => `<bpmn:outgoing>${escapeXml(e.id)}</bpmn:outgoing>`)
        .join('\n        ')

      let tag = 'bpmn:task'
      let extraAttrs = ''

      switch (node.type) {
        case 'startEvent':
          tag = 'bpmn:startEvent'
          break
        case 'endEvent':
          tag = 'bpmn:endEvent'
          break
        case 'exclusiveGateway':
          tag = 'bpmn:exclusiveGateway'
          break
        case 'parallelGateway':
          tag = 'bpmn:parallelGateway'
          break
        case 'inclusiveGateway':
          tag = 'bpmn:inclusiveGateway'
          break
        case 'serviceTask':
          tag = 'bpmn:serviceTask'
          extraAttrs = ` implementation="##WebService"`
          break
        case 'userTask':
          tag = 'bpmn:userTask'
          break
        default:
          tag = node.category === 'rpa_bot' ? 'bpmn:serviceTask' : 'bpmn:userTask'
      }

      return `      <${tag} id="${id}" name="${name}"${extraAttrs}>
        ${incoming ? incoming + '\n        ' : ''}${outgoing ? outgoing + '\n      ' : ''}</${tag}>`
    })
    .join('\n')

  // Sequence flows
  const sequenceFlowsXml = process.edges
    .map((edge) => {
      const id = escapeXml(edge.id)
      const name = escapeXml(edge.name || '')
      const sourceRef = escapeXml(edge.sourceId || '')
      const targetRef = escapeXml(edge.targetId || '')
      const cond = edge.condition
        ? `\n        <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">${escapeXml(edge.condition)}</bpmn:conditionExpression>`
        : ''

      return `      <bpmn:sequenceFlow id="${id}" name="${name}" sourceRef="${sourceRef}" targetRef="${targetRef}"${cond ? '>' + cond + '\n      </bpmn:sequenceFlow>' : ' />'}`
    })
    .join('\n')

  // LaneSet if lanes exist
  let laneSetXml = ''
  if (process.lanes.length > 0) {
    const lanesList = process.lanes
      .map((lane) => {
        const laneId = escapeXml(lane.id)
        const laneName = escapeXml(lane.name || 'Подразделение')
        const memberNodes = process.nodes.filter((n) => n.laneId === lane.id)
        const nodeRefs = memberNodes
          .map((n) => `<bpmn:flowNodeRef>${escapeXml(n.id)}</bpmn:flowNodeRef>`)
          .join('\n          ')

        return `        <bpmn:lane id="${laneId}" name="${laneName}">
          ${nodeRefs}
        </bpmn:lane>`
      })
      .join('\n')

    laneSetXml = `      <bpmn:laneSet id="LaneSet_${processId}">
${lanesList}
      </bpmn:laneSet>`
  }

  // Diagram coordinates (BPMNDiagram)
  const shapesXml = [
    ...process.lanes.map((lane) => {
      return `      <bpmndi:BPMNShape id="${escapeXml(lane.id)}_di" bpmnElement="${escapeXml(lane.id)}" isHorizontal="true">
        <dc:Bounds x="${lane.geometry.x}" y="${lane.geometry.y}" width="${lane.geometry.width || 800}" height="${lane.geometry.height || 200}" />
        <bpmndi:BPMNLabel />
      </bpmndi:BPMNShape>`
    }),
    ...process.nodes.map((node) => {
      return `      <bpmndi:BPMNShape id="${escapeXml(node.id)}_di" bpmnElement="${escapeXml(node.id)}">
        <dc:Bounds x="${node.geometry.x}" y="${node.geometry.y}" width="${node.geometry.width || 120}" height="${node.geometry.height || 60}" />
        <bpmndi:BPMNLabel />
      </bpmndi:BPMNShape>`
    }),
  ].join('\n')

  const edgesXml = process.edges
    .map((edge) => {
      const points = edge.points.length > 0 ? edge.points : generateDefaultPoints(edge, process.nodes)
      const waypointsXml = points
        .map((p) => `<di:waypoint x="${p.x}" y="${p.y}" />`)
        .join('\n        ')

      return `      <bpmndi:BPMNEdge id="${escapeXml(edge.id)}_di" bpmnElement="${escapeXml(edge.id)}">
        ${waypointsXml}
      </bpmndi:BPMNEdge>`
    })
    .join('\n')

  return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Definitions_SQB_Processet"
                  targetNamespace="http://bpmn.io/schema/bpmn"
                  exporter="SQB Bank PIX-Processet Bridge"
                  exporterVersion="1.0">
  <bpmn:process id="${processId}" name="${processName}" isExecutable="true">
${laneSetXml ? laneSetXml + '\n' : ''}${flowElementsXml}
${sequenceFlowsXml}
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="${processId}">
${shapesXml}
${edgesXml}
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`
}

function escapeXml(str: string): string {
  if (!str) return ''
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function generateDefaultPoints(edge: ProcessEdge, nodes: ProcessNode[]): { x: number; y: number }[] {
  const src = nodes.find((n) => n.id === edge.sourceId)
  const tgt = nodes.find((n) => n.id === edge.targetId)
  if (!src || !tgt) {
    return [
      { x: 100, y: 100 },
      { x: 250, y: 100 },
    ]
  }

  const srcX = src.geometry.x + src.geometry.width
  const srcY = src.geometry.y + src.geometry.height / 2
  const tgtX = tgt.geometry.x
  const tgtY = tgt.geometry.y + tgt.geometry.height / 2

  return [
    { x: srcX, y: srcY },
    { x: tgtX, y: tgtY },
  ]
}
