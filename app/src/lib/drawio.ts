import type {
  BusinessProcess,
  NodeType,
  ProcessEdge,
  ProcessEdgePoint,
  ProcessNode,
  ProcessValidation,
  StepCategory,
  ProcessPassport,
  PixRegistrySchema,
} from '@/types/process'
import { analyzeProcessConformance } from './conformance'

/** Decodes HTML entities and tags in draw.io labels */
function cleanLabel(raw: string | null): string {
  if (!raw) return ''
  const div = document.createElement('div')
  div.innerHTML = raw
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/<br\s*[\/]?>/gi, ' ')
    .replace(/<\/?[^>]+(>|$)/g, ' ')
  return (div.textContent ?? '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Robust decompressor for draw.io deflate-raw base64 format without crashing */
async function inflateDiagram(data: string): Promise<string> {
  const cleanData = data.trim().replace(/\s+/g, '')
  if (!cleanData) throw new Error('Пустое содержимое диаграммы')

  if (cleanData.startsWith('<') || cleanData.includes('<mxGraphModel') || cleanData.includes('<root>')) {
    return cleanData
  }

  let binaryString: string
  try {
    binaryString = atob(cleanData)
  } catch {
    throw new Error('Некорректный формат сжатых данных Draw.io')
  }

  const bytes = new Uint8Array(binaryString.length)
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i)
  }

  if (typeof DecompressionStream !== 'undefined') {
    try {
      const ds = new DecompressionStream('deflate-raw')
      const writer = ds.writable.getWriter()
      writer.write(bytes).catch(() => {})
      writer.close().catch(() => {})

      const reader = ds.readable.getReader()
      const chunks: Uint8Array[] = []
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (value) chunks.push(value)
      }

      const totalLen = chunks.reduce((acc, c) => acc + c.length, 0)
      const combined = new Uint8Array(totalLen)
      let offset = 0
      for (const chunk of chunks) {
        combined.set(chunk, offset)
        offset += chunk.length
      }

      const decoded = new TextDecoder('utf-8').decode(combined)
      try {
        return decodeURIComponent(decoded)
      } catch {
        return decoded
      }
    } catch {
      // Fallback
    }
  }

  try {
    return decodeURIComponent(binaryString)
  } catch {
    return binaryString
  }
}

async function extractGraphXml(text: string): Promise<{ xml: string; isBpmn: boolean }> {
  const trimmed = text.trim()

  // 1. Check if BPMN 2.0 XML
  if (
    trimmed.includes('<definitions') ||
    trimmed.includes('<bpmn:definitions') ||
    trimmed.includes('<bpmn2:definitions') ||
    trimmed.includes('<bpmn:process')
  ) {
    return { xml: trimmed, isBpmn: true }
  }

  // 2. Direct mxGraphModel
  if (trimmed.startsWith('<mxGraphModel') || trimmed.includes('<mxGraphModel')) {
    const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
    const model = doc.querySelector('mxGraphModel')
    if (model) {
      return { xml: new XMLSerializer().serializeToString(model), isBpmn: false }
    }
  }

  // 3. mxfile container
  if (trimmed.includes('<mxfile') || trimmed.includes('<diagram')) {
    const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
    const parserError = doc.querySelector('parsererror')
    if (parserError) {
      throw new Error(`Ошибка XML: ${parserError.textContent?.slice(0, 80)}`)
    }

    const diagram = doc.querySelector('diagram')
    if (!diagram) throw new Error('В файле draw.io не найдено ни одной диаграммы (<diagram>)')

    const model = diagram.querySelector('mxGraphModel')
    if (model) {
      return { xml: new XMLSerializer().serializeToString(model), isBpmn: false }
    }

    const rootEl = diagram.querySelector('root')
    if (rootEl) {
      return {
        xml: `<mxGraphModel>${new XMLSerializer().serializeToString(rootEl)}</mxGraphModel>`,
        isBpmn: false,
      }
    }

    const innerText = diagram.textContent?.trim() ?? ''
    if (innerText) {
      if (innerText.startsWith('<mxGraphModel') || innerText.includes('<mxGraphModel')) {
        return { xml: innerText, isBpmn: false }
      }
      const decompressed = await inflateDiagram(innerText)
      return { xml: decompressed, isBpmn: false }
    }
  }

  // 4. SVG with embedded drawio
  if (trimmed.includes('<svg') && (trimmed.includes('content="') || trimmed.includes('data-diagram="'))) {
    const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
    const svg = doc.querySelector('svg')
    const content = svg?.getAttribute('content') || svg?.getAttribute('data-diagram')
    if (content) {
      return extractGraphXml(decodeURIComponent(content))
    }
  }

  throw new Error('Файл не распознан. Поддерживаются .drawio, .xml, .bpmn и схемы BPMN 2.0')
}

function getStyle(cell: Element): string {
  return cell.getAttribute('style') ?? ''
}

function classifyVertex(
  style: string,
  label: string,
  hasIncoming: boolean,
  hasOutgoing: boolean,
  id: string,
): NodeType {
  const s = style.toLowerCase()
  const l = label.toLowerCase()
  const i = id.toLowerCase()

  if (s.includes('swimlane') || s.includes('pool;') || s.includes('shape=pool') || s.includes('horizontal=0'))
    return 'lane'

  if (s.includes('rhombus') || s.includes('gateway') || s.includes('shape=rhombus') || i.includes('gw') || l.includes('?')) {
    if (s.includes('outline=plus') || s.includes('parallel') || s.includes('plus') || l.includes('+'))
      return 'parallelGateway'
    if (s.includes('inclusive') || s.includes('circle')) return 'inclusiveGateway'
    return 'exclusiveGateway'
  }

  if (
    s.includes('ellipse') ||
    s.includes('bpmn.shape') ||
    s.includes('shape=ellipse') ||
    i.includes('start') ||
    i.includes('end') ||
    i.includes('reject')
  ) {
    // Check if start
    if (
      i.includes('start') ||
      l.includes('старт') ||
      l.includes('поступлен') ||
      s.includes('fillcolor=#10b981') ||
      s.includes('fillcolor=#22c55e') ||
      s.includes('fillcolor=#059669')
    ) {
      return 'startEvent'
    }

    // Check if end
    if (
      i.includes('end') ||
      i.includes('reject') ||
      l.includes('заверш') ||
      l.includes('конец') ||
      l.includes('выдан') ||
      l.includes('отказ') ||
      s.includes('fillcolor=#ef4444') ||
      s.includes('fillcolor=#e11d48') ||
      s.includes('fillcolor=#be123c') ||
      s.includes('outline=double') ||
      s.includes('outline=end')
    ) {
      return 'endEvent'
    }

    if (!hasIncoming && hasOutgoing) return 'startEvent'
    if (hasIncoming && !hasOutgoing) return 'endEvent'
    return 'startEvent'
  }

  if (
    s.includes('robot') ||
    s.includes('rpa') ||
    s.includes('service') ||
    s.includes('fillcolor=#dcfce7') ||
    s.includes('fillcolor=#d5e8d4') ||
    l.includes('rpa') ||
    l.includes('робот') ||
    l.includes('авто-')
  ) {
    return 'serviceTask'
  }

  return 'userTask'
}

function classifyCategory(type: NodeType, name: string, style: string): StepCategory {
  const lower = (name + ' ' + style).toLowerCase()
  if (type === 'startEvent' || type === 'endEvent') return 'notification'
  if (type === 'serviceTask' || lower.includes('rpa') || lower.includes('робот') || lower.includes('авто-') || lower.includes('генерация'))
    return 'rpa_bot'
  if (lower.includes('согласован') || lower.includes('комитет') || lower.includes('утвержд') || lower.includes('подпис') || lower.includes('голос'))
    return 'approval'
  if (lower.includes('проверк') || lower.includes('валидац') || lower.includes('скоринг') || lower.includes('скор') || lower.includes('андеррайт') || lower.includes('риск'))
    return 'validation'
  if (lower.includes('api') || lower.includes('абс') || lower.includes('сервис') || lower.includes('цфт') || lower.includes('didox'))
    return 'api_service'
  return 'manual'
}

function detectSystem(name: string, laneName: string): string {
  const lower = (name + ' ' + laneName).toLowerCase()
  if (lower.includes('rpa') || lower.includes('робот')) return 'PIX RPA'
  if (lower.includes('абс') || lower.includes('счет') || lower.includes('проводк') || lower.includes('цфт') || lower.includes('транш'))
    return 'АБС ЦФТ-Банк'
  if (lower.includes('гнк') || lower.includes('налог') || lower.includes('soliq')) return 'API Soliq (ГНК)'
  if (lower.includes('катм') || lower.includes('katm') || lower.includes('бюро')) return 'API KATM'
  if (lower.includes('епигу') || lower.includes('egrpo') || lower.includes('егрпо')) return 'ЕПИГУ / ЕГРПО'
  if (lower.includes('didox') || lower.includes('эдо') || lower.includes('эцп')) return 'Didox (ЭДО)'
  if (lower.includes('swift') || lower.includes('свифт')) return 'SWIFT Alliance'
  return 'SQB CRM / Core'
}

function estimateSla(category: StepCategory, type: NodeType): number {
  if (type === 'startEvent' || type === 'endEvent') return 5
  switch (category) {
    case 'rpa_bot':
      return 3
    case 'api_service':
      return 2
    case 'validation':
      return 45
    case 'approval':
      return 180
    default:
      return 60
  }
}

/** Parses BPMN 2.0 XML files */
function parseBpmnXml(xmlText: string, fileName: string): BusinessProcess {
  const doc = new DOMParser().parseFromString(xmlText, 'text/xml')
  const processEl = doc.querySelector('process') || doc.querySelector('bpmn\\:process') || doc.querySelector('bpmn2\\:process')
  const processName = processEl?.getAttribute('name') || fileName.replace(/\.(bpmn|xml)$/i, '')
  const processId = processEl?.getAttribute('id') || `PRC-SQB-${Math.floor(100 + Math.random() * 900)}`

  const nodes: ProcessNode[] = []
  const edges: ProcessEdge[] = []
  const lanes: ProcessNode[] = []

  const laneEls = Array.from(doc.querySelectorAll('lane, bpmn\\:lane, bpmn2\\:lane'))
  laneEls.forEach((l, idx) => {
    const id = l.getAttribute('id') ?? `lane_${idx}`
    const name = l.getAttribute('name') ?? `Подразделение ${idx + 1}`
    lanes.push({
      id,
      name,
      type: 'lane',
      role: name,
      geometry: { x: 50, y: 50 + idx * 180, width: 1400, height: 180 },
      style: 'swimlane;',
    })
  })

  const boundsMap = new Map<string, { x: number; y: number; width: number; height: number }>()
  const shapeEls = Array.from(doc.querySelectorAll('BPMNShape, bpmndi\\:BPMNShape'))
  shapeEls.forEach((s) => {
    const bpmnElement = s.getAttribute('bpmnElement')
    const bounds = s.querySelector('Bounds, dc\\:Bounds')
    if (bpmnElement && bounds) {
      boundsMap.set(bpmnElement, {
        x: Number(bounds.getAttribute('x') ?? 100),
        y: Number(bounds.getAttribute('y') ?? 100),
        width: Number(bounds.getAttribute('width') ?? 120),
        height: Number(bounds.getAttribute('height') ?? 60),
      })
    }
  })

  const allElements = Array.from(doc.querySelectorAll('*'))
  let stepIndex = 1

  allElements.forEach((el) => {
    const tagName = el.localName.toLowerCase()
    let type: NodeType | null = null

    if (tagName === 'startevent') type = 'startEvent'
    else if (tagName === 'endevent') type = 'endEvent'
    else if (tagName === 'usertask' || tagName === 'task') type = 'userTask'
    else if (tagName === 'servicetask') type = 'serviceTask'
    else if (tagName === 'exclusivegateway') type = 'exclusiveGateway'
    else if (tagName === 'parallelgateway') type = 'parallelGateway'
    else if (tagName === 'inclusivegateway') type = 'inclusiveGateway'

    if (type) {
      const id = el.getAttribute('id') ?? `node_${crypto.randomUUID()}`
      const name = el.getAttribute('name') ?? (type === 'startEvent' ? 'Старт' : type === 'endEvent' ? 'Завершение' : `Шаг ${stepIndex}`)
      const geometry = boundsMap.get(id) || { x: 100 + stepIndex * 150, y: 100, width: 140, height: 70 }
      const category = classifyCategory(type, name, '')
      const isTask = type === 'userTask' || type === 'serviceTask'
      const code = type === 'startEvent' ? 'START' : type === 'endEvent' ? 'END' : isTask ? `STEP-${String(stepIndex++).padStart(2, '0')}` : undefined

      nodes.push({
        id,
        name,
        type,
        category,
        code,
        geometry,
        style: '',
        slaMinutes: estimateSla(category, type),
        costPerExecution: category === 'rpa_bot' ? 800 : 25000,
        automationPotential: category === 'rpa_bot' ? 95 : 60,
        system: detectSystem(name, ''),
      })
    }
  })

  const flowEls = Array.from(doc.querySelectorAll('sequenceFlow, bpmn\\:sequenceFlow, bpmn2\\:sequenceFlow'))
  flowEls.forEach((f) => {
    edges.push({
      id: f.getAttribute('id') ?? `edge_${crypto.randomUUID()}`,
      name: f.getAttribute('name') ?? '',
      sourceId: f.getAttribute('sourceRef') ?? undefined,
      targetId: f.getAttribute('targetRef') ?? undefined,
      points: [],
    })
  })

  const passport: ProcessPassport = {
    code: processId.startsWith('PRC-') ? processId : `PRC-SQB-${Math.floor(100 + Math.random() * 900)}`,
    name: processName,
    version: '1.0',
    status: 'draft',
    owner: 'Департамент бизнес-процессов АКБ «Узпромстройбанк»',
    department: 'Операционный блок',
    category: 'Банковские процессы',
    targetSlaHours: Math.round(nodes.reduce((acc, n) => acc + (n.slaMinutes || 0), 0) / 60) || 8,
    description: `Импортирован из файла BPMN: ${fileName}.`,
    createdDate: new Date().toISOString().split('T')[0],
    updatedDate: new Date().toISOString().split('T')[0],
  }

  const registry: PixRegistrySchema = {
    id: `reg-${crypto.randomUUID()}`,
    code: `REG_${passport.code.replace(/[^a-zA-Z0-9_]/g, '_')}`,
    name: `Реестр: ${processName}`,
    description: `Реестр заявок по процессу ${processName}`,
    fields: [
      { id: 'f1', code: 'case_number', name: 'Номер заявки', type: 'string', required: true },
      { id: 'f2', code: 'client_inn', name: 'ИНН Клиента', type: 'string', required: true },
      { id: 'f3', code: 'client_title', name: 'Компания', type: 'string', required: true },
      { id: 'f4', code: 'status', name: 'Статус', type: 'select', required: true, options: ['В работе', 'Одобрено', 'Отклонено'] },
    ],
    records: [
      {
        id: 'rec-1',
        caseId: 'SQB-2026-BPM01',
        createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
        status: 'in_progress',
        currentStepId: nodes[1]?.id || nodes[0]?.id || 'step-1',
        currentStepName: nodes[1]?.name || 'Первичный шаг',
        assignedTo: nodes[1]?.role || 'Сотрудник банка',
        elapsedMinutes: 15,
        data: {
          case_number: 'SQB-2026-BPM01',
          client_inn: '309819284',
          client_title: 'OOO "GLOBAL AGRO"',
          status: 'В работе',
        },
      },
    ],
  }

  const proc: BusinessProcess = {
    id: `proc_${crypto.randomUUID()}`,
    name: processName,
    fileName,
    passport,
    nodes,
    edges,
    lanes,
    validation: validate(nodes, edges),
    registry,
    miningMetrics: {
      totalCases: 100,
      conformanceRate: 80,
      avgLeadTimeHours: passport.targetSlaHours * 1.3,
      targetLeadTimeHours: passport.targetSlaHours,
      slaBreachRate: 20,
      reworkRate: 15,
      potentialRpaSavingsUzs: 50000000,
      deviations: [],
    },
  }
  proc.miningMetrics = analyzeProcessConformance(proc)
  return proc
}

export async function parseDrawio(text: string, fileName: string): Promise<BusinessProcess> {
  const { xml, isBpmn } = await extractGraphXml(text)

  if (isBpmn) {
    return parseBpmnXml(xml, fileName)
  }

  const doc = new DOMParser().parseFromString(xml, 'text/xml')
  const root = doc.querySelector('root')
  if (!root) throw new Error('Не удалось найти <root> в модели диаграммы draw.io')

  const cells = Array.from(root.querySelectorAll('mxCell'))
  const cellMap = new Map<string, Element>()
  cells.forEach((c) => {
    const id = c.getAttribute('id')
    if (id) cellMap.set(id, c)
  })

  // First pass: identify label overlay elements to attach their text to parent shapes
  const labelMap = new Map<string, string>()
  const labelCellIds = new Set<string>()

  cells.forEach((c) => {
    const id = c.getAttribute('id') ?? ''
    const style = getStyle(c).toLowerCase()
    const rawVal = c.getAttribute('value')
    const cleaned = cleanLabel(rawVal)

    // Detect if this is an overlay text label cell
    const isLabel =
      id.endsWith('_label') ||
      (style.includes('text;') && !style.includes('swimlane') && (style.includes('strokecolor=none') || style.includes('fillcolor=none') || !rawVal || cleaned.length < 35))

    if (isLabel && (id.endsWith('_label') || c.getAttribute('vertex') === '1')) {
      labelCellIds.add(id)
      const baseId = id.replace(/_label$/, '')
      if (baseId && cleaned) {
        labelMap.set(baseId, cleaned)
      }
    }
  })

  // Detect which cells are actual swimlanes vs outer pool container
  const swimlaneCells = cells.filter((c) => {
    const style = getStyle(c).toLowerCase()
    return c.getAttribute('vertex') === '1' && (style.includes('swimlane') || style.includes('shape=pool'))
  })

  // Outer pool is a swimlane that contains other swimlanes as children
  const poolIds = new Set<string>()
  swimlaneCells.forEach((sw) => {
    const swId = sw.getAttribute('id') ?? ''
    const hasChildLanes = swimlaneCells.some((other) => other.getAttribute('parent') === swId)
    if (hasChildLanes || getStyle(sw).includes('stackLayout')) {
      poolIds.add(swId)
    }
  })

  const rawVertices = cells.filter((c) => {
    const id = c.getAttribute('id') ?? ''
    const isVertex = c.getAttribute('vertex') === '1'
    if (!isVertex) return false
    if (labelCellIds.has(id)) return false
    if (poolIds.has(id)) return false // Pool is handled as overarching container
    return true
  })

  const rawEdges = cells.filter((c) => c.getAttribute('edge') === '1')

  // Connections for event classification
  const incoming = new Set<string>()
  const outgoing = new Set<string>()
  for (const e of rawEdges) {
    const t = e.getAttribute('target')
    const s = e.getAttribute('source')
    if (t) incoming.add(t)
    if (s) outgoing.add(s)
  }

  const nodes: ProcessNode[] = []
  let stepIndex = 1

  for (const cell of rawVertices) {
    const id = cell.getAttribute('id') ?? `node_${crypto.randomUUID()}`
    const style = getStyle(cell)
    const rawValue = cell.getAttribute('value')
    let rawCleaned = cleanLabel(rawValue)

    // Fallback to labelMap if cell value is empty or generic
    if (!rawCleaned && labelMap.has(id)) {
      rawCleaned = labelMap.get(id)!
    }

    const parentId = cell.getAttribute('parent') ?? undefined

    // Resolve absolute coordinates relative to parent pools/lanes
    const geo = cell.querySelector('mxGeometry')
    let x = Number(geo?.getAttribute('x') ?? 100)
    let y = Number(geo?.getAttribute('y') ?? 100)
    let width = Number(geo?.getAttribute('width') ?? 120)
    let height = Number(geo?.getAttribute('height') ?? 60)

    let curParent = parentId
    while (curParent && curParent !== '0' && curParent !== '1') {
      const parentEl = cellMap.get(curParent)
      if (!parentEl) break
      const parentGeo = parentEl.querySelector('mxGeometry')
      if (parentGeo) {
        x += Number(parentGeo.getAttribute('x') ?? 0)
        y += Number(parentGeo.getAttribute('y') ?? 0)
      }
      curParent = parentEl.getAttribute('parent') ?? undefined
    }

    const type = classifyVertex(style, rawCleaned, incoming.has(id), outgoing.has(id), id)
    const category = classifyCategory(type, rawCleaned, style)
    const isTask = type === 'task' || type === 'userTask' || type === 'serviceTask'

    // Extract explicit code if present in label (e.g. STEP-01)
    let code: string | undefined = undefined
    const codeMatch = (rawValue || rawCleaned).match(/\b(STEP[-_ ]?\d+|START|END|GW[-_ ]?\w+)\b/i)
    if (codeMatch) {
      code = codeMatch[1].toUpperCase().replace(/_/g, '-')
    } else if (type === 'startEvent') {
      code = 'START'
    } else if (type === 'endEvent') {
      code = 'END'
    } else if (isTask) {
      code = `STEP-${String(stepIndex++).padStart(2, '0')}`
    }

    // Clean human-friendly name (strip [PIX RPA], STEP-01:, etc.)
    let cleanName = rawCleaned
      .replace(/^\[.*?\]\s*/gi, '')
      .replace(/^STEP[-_ ]?\d+[:\s-]*/gi, '')
      .replace(/^[0-9]+[.)]\s*/gi, '')
      .trim()

    if (!cleanName) {
      cleanName =
        type === 'startEvent'
          ? 'Старт'
          : type === 'endEvent'
          ? 'Завершение'
          : type.includes('Gateway')
          ? 'Условие'
          : `Операция ${code || id}`
    }

    // Standardize BPMN dimensions for uniform rendering
    if (type === 'startEvent' || type === 'endEvent') {
      width = 48
      height = 48
    } else if (type.includes('Gateway')) {
      width = 46
      height = 46
    } else if (type === 'lane') {
      width = Math.max(width, 1400)
      height = Math.max(height, 160)
    } else {
      width = Math.max(width, 160)
      height = Math.max(height, 70)
    }

    nodes.push({
      id,
      name: cleanName,
      type,
      category,
      code,
      geometry: {
        x: Math.round(x),
        y: Math.round(y),
        width: Math.round(width),
        height: Math.round(height),
      },
      style,
      laneId: parentId,
      slaMinutes: estimateSla(category, type),
      costPerExecution: category === 'rpa_bot' ? 800 : 25000,
      automationPotential: category === 'rpa_bot' ? 95 : category === 'manual' ? 60 : 35,
    })
  }

  // Identify lanes / swimlanes (excluding outer pool)
  const lanes = nodes.filter((n) => n.type === 'lane')
  const laneIds = new Set(lanes.map((l) => l.id))
  const flowNodes = nodes.filter((n) => n.type !== 'lane')

  for (const n of flowNodes) {
    if (n.laneId && !laneIds.has(n.laneId)) n.laneId = undefined
  }

  // Geometry-based lane assignment fallback
  for (const n of flowNodes) {
    if (n.laneId) continue
    const hit = lanes.find(
      (l) =>
        n.geometry.x >= l.geometry.x - 50 &&
        n.geometry.x <= l.geometry.x + l.geometry.width + 50 &&
        n.geometry.y >= l.geometry.y &&
        n.geometry.y < l.geometry.y + l.geometry.height,
    )
    if (hit) {
      n.laneId = hit.id
      n.laneName = hit.name
      n.role = hit.name
    }
  }

  // Populate laneName and IT system
  for (const n of flowNodes) {
    if (n.laneId) {
      const parentLane = lanes.find((l) => l.id === n.laneId)
      if (parentLane) {
        n.laneName = parentLane.name
        n.role = n.role || parentLane.name
      }
    }
    n.system = detectSystem(n.name, n.laneName || '')
  }

  const edges: ProcessEdge[] = rawEdges.map((cell) => {
    const points: ProcessEdgePoint[] = []
    cell.querySelectorAll('mxPoint').forEach((p) => {
      points.push({ x: Number(p.getAttribute('x') ?? 0), y: Number(p.getAttribute('y') ?? 0) })
    })
    return {
      id: cell.getAttribute('id') ?? `edge_${crypto.randomUUID()}`,
      name: cleanLabel(cell.getAttribute('value')),
      sourceId: cell.getAttribute('source') ?? undefined,
      targetId: cell.getAttribute('target') ?? undefined,
      points,
    }
  })

  const validation = validate(flowNodes, edges)

  let diagramName = ''
  try {
    diagramName =
      new DOMParser()
        .parseFromString(text.trim().startsWith('<mxfile') ? text : '<mxfile/>', 'text/xml')
        .querySelector('diagram')
        ?.getAttribute('name') ?? ''
  } catch {
    // ignore
  }

  const cleanTitle = diagramName || fileName.replace(/\.(drawio|xml)$/i, '')

  const passport: ProcessPassport = {
    code: `PRC-SQB-${Math.floor(100 + Math.random() * 900)}`,
    name: cleanTitle,
    version: '1.0',
    status: 'draft',
    owner: 'Департамент бизнес-процессов АКБ «Узпромстройбанк»',
    department: lanes[0]?.name || 'Операционный блок',
    category: 'Банковские процессы',
    targetSlaHours: Math.round(flowNodes.reduce((acc, n) => acc + (n.slaMinutes || 0), 0) / 60) || 8,
    description: `Импортирован из файла drawio: ${fileName}. Создан автоматический паспорт и реестр PIX.`,
    createdDate: new Date().toISOString().split('T')[0],
    updatedDate: new Date().toISOString().split('T')[0],
  }

  const registry: PixRegistrySchema = {
    id: `reg-${crypto.randomUUID()}`,
    code: `REG_${passport.code.replace(/[^a-zA-Z0-9_]/g, '_')}`,
    name: `Реестр: ${cleanTitle}`,
    description: `Операционный реестр заявок по процессу ${cleanTitle}`,
    fields: [
      { id: 'f1', code: 'case_number', name: 'Номер заявки', type: 'string', required: true },
      { id: 'f2', code: 'client_inn', name: 'ИНН Клиента', type: 'string', required: true },
      { id: 'f3', code: 'client_title', name: 'Наименование клиента', type: 'string', required: true },
      { id: 'f4', code: 'status', name: 'Статус', type: 'select', required: true, options: ['В работе', 'Одобрено', 'Отклонено'] },
    ],
    records: [
      {
        id: 'rec-imp-1',
        caseId: 'SQB-2026-IMP01',
        createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
        status: 'in_progress',
        currentStepId: flowNodes[1]?.id || flowNodes[0]?.id || 'step-1',
        currentStepName: flowNodes[1]?.name || 'Первичный шаг',
        assignedTo: flowNodes[1]?.role || 'Сотрудник банка',
        elapsedMinutes: 25,
        data: {
          case_number: 'SQB-2026-IMP01',
          client_inn: '309819284',
          client_title: 'OOO "ORIENT TRADE"',
          status: 'В работе',
        },
      },
    ],
  }

  const initialProcess: BusinessProcess = {
    id: `proc_${crypto.randomUUID()}`,
    name: cleanTitle,
    fileName,
    passport,
    nodes: flowNodes,
    edges,
    lanes,
    validation,
    registry,
    miningMetrics: {
      totalCases: 100,
      conformanceRate: 80,
      avgLeadTimeHours: passport.targetSlaHours * 1.3,
      targetLeadTimeHours: passport.targetSlaHours,
      slaBreachRate: 20,
      reworkRate: 15,
      potentialRpaSavingsUzs: 50000000,
      deviations: [],
    },
  }

  initialProcess.miningMetrics = analyzeProcessConformance(initialProcess)
  return initialProcess
}

function validate(nodes: ProcessNode[], edges: ProcessEdge[]): ProcessValidation[] {
  const issues: ProcessValidation[] = []
  const starts = nodes.filter((n) => n.type === 'startEvent')
  const ends = nodes.filter((n) => n.type === 'endEvent')
  if (starts.length === 0)
    issues.push({ level: 'error', message: 'Отсутствует стартовое событие процесса' })
  if (starts.length > 1)
    issues.push({ level: 'warning', message: `Найдено ${starts.length} стартовых событий` })
  if (ends.length === 0)
    issues.push({ level: 'warning', message: 'Отсутствует событие успешного завершения' })

  for (const n of nodes) {
    const inE = edges.filter((e) => e.targetId === n.id)
    const outE = edges.filter((e) => e.sourceId === n.id)
    if (n.type !== 'startEvent' && n.type !== 'lane' && inE.length === 0)
      issues.push({ level: 'error', message: `Шаг «${n.name || n.id}» не имеет входящих переходов (тупик)`, nodeId: n.id })
    if (n.type !== 'endEvent' && n.type !== 'lane' && outE.length === 0)
      issues.push({ level: 'warning', message: `Шаг «${n.name || n.id}» не имеет исходящих переходов`, nodeId: n.id })
  }
  return issues
}
