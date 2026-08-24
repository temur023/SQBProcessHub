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

/** Decodes HTML entities and tags in draw.io labels without executing markup */
function cleanLabel(raw: string | null): string {
  if (!raw) return ''
  const stripped = raw
    .replace(/<br\s*[\/]?>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
  const textarea = document.createElement('textarea')
  textarea.innerHTML = stripped
  return (textarea.value || '')
    .replace(/\s+/g, ' ')
    .trim()
}

function idHasToken(id: string, token: string): boolean {
  const i = id.toLowerCase()
  const t = token.toLowerCase()
  if (i === t || i.startsWith(t + '_') || i.startsWith(t + '-') || i.endsWith('_' + t) || i.endsWith('-' + t)) {
    return true
  }
  return i.split(/[-_]/).includes(t)
}

function elementsByLocalName(root: ParentNode, names: string[]): Element[] {
  const want = new Set(names.map((n) => n.toLowerCase()))
  const scope = root as Document | Element
  if (!('getElementsByTagName' in scope)) return []
  return Array.from(scope.getElementsByTagName('*')).filter((el) => want.has(el.localName.toLowerCase()))
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

// Lists of non-task strings in Uzbek/Russian banking drawio diagrams
const SYSTEM_TAGS = new Set([
  'iabs', 'iabs / crm', 'iabs / eha', 'eha', 'edo', 'zoom', 'crobs', 'excell rmr',
  'dragle bi', 'nibbd', 'soliq', 'katm', 'didox', 'myorg.uz', 'ihamkor', 'orginfo',
  'registr.stat.uz', 'jira', 'e-baholash.uz', 'garov.uz', 'davreestr.uz', 'korporativ pochta',
  'tsoyat', 'intranet', 'emv service', 'sqb crm', 'internet saytlari', 'crobs, internet saytlari',
  'internet saytlari, tsoyat, crobs', 'internet saytlari va iabs', 'internet saytlari tsoyat',
  'iabs, regstr.uz', 'iabs, garov.uz, davreestr.uz', 'iabs, garov.uz, davrestr.uz', 'iabs, crobs'
])

const ARTIFACT_TAGS = new Set([
  'dalolatnoma', 'chek-list', "yig'ma jild", 'yig‘majild', 'yig‘ma jild', 'asoslantiruvchi xat',
  'fotosuratlar', 'fotosuratlar va hujjatlar', "hujjatlar ro'yxati", 'hujjatlar',
  'xulosa', 'loyiha hujjatlari', "ko'chirma", 'kuzatuv kengash bayonnomasi',
  'yirik bitimlar bayonnomasi', 'kredit/sug‘urta/kafillik', 'kredit/sug\'urta/kafillik',
  'qo‘shimcha kelishuv', "qo'shimcha kelishuv", 'shartnoma', 'baholash dalolatnomasi',
  'garov xulosasi', "yig'ilish bayonnomasi", 'yuriskonsult xulosasi', 'qaror loyihasi',
  'asoslantirilgan xat', 'moliyaviy hisobotlar', 'skaner', 'kredit/garov/kafillik shartnomasi',
  'kredit/kafillik/sug\'urta shartnomasi', 'hukumat qarori', 'tegishli qaror', "ma'lumotnoma",
  'mijoz murojaati, ta`sischilar qarori', 'ta`sischilar qarori'
])

const CONDITION_TAGS = new Set([
  "ha", "yo'q", "yo`q", "yo’q", "yo'q ", "ha ", "да", "нет", "yes", "no",
  "to'liq", "to'liq emas", "to`liq", "to`liq emas",
  "mos keldi", "mos kelmaydi", "mos kelmadi", "to'liq mos keladi",
  "manba aniqlandi", "qabul qilindi", "rad etildi", "rad javob berildi",
  "asoslantirilgan rad javob berildi", "mulkiy", "nomulkiy", "o'rganildi", "bajarildi",
  "nazorat uchun", "ijobiy", "salbiy", "kamchilik mavjudmi", "kamchiliklar mavjudmi",
  "kamchilik mavjudmi?", "kamchiliklar mavjudmi?", "to'g'ri rasmiylashtirilganmi?",
  "tog'ri rasmiylashtirilganmi?", "barcha ma'lumotlar to'g'ri kiritilganmi?",
  "barcha hujjatlar mavjudmi", "hujjatlar to'liqmi?", "hujjatlar to'plami to'liqmi?",
  "resurs mablag'lari mavjudmi?", "muzokara ijobiymi?", "muqobil resurs aniqlandimi ?",
  "vakolatli organ qarori ijobiymi?", "qo'mita qarori ijobiymi?", "qaror qabul qilish qo'mita vakolatidami?",
  "kredit maqsadli ishlatilganmi?", "mijoz talabi kredit mahsuloti shartlariga muvofiqmi?",
  "garov obyekti qiymati mustaqil baholovchining hisoboti bilan mosligini o'rganish"
])

function isDecorationStyle(style: string): boolean {
  const s = style.toLowerCase()
  return (
    s.includes('timer') ||
    s.includes('clock') ||
    s.includes('mxgraph.bpmn.icon') ||
    s.includes('shape=mxgraph.bpmn.timer') ||
    s.includes('eventicon') ||
    s.includes('symbol=timer') ||
    s.includes('symbol=clock')
  )
}

function isNonTaskLabel(val: string): boolean {
  const v = val.toLowerCase().trim()
  if (v.length === 0) return true
  if (CONDITION_TAGS.has(v)) return true
  if (SYSTEM_TAGS.has(v)) return true
  if (ARTIFACT_TAGS.has(v)) return true
  if (v.startsWith('kutish vaqti') || v.startsWith("o'rtacha kutish vaqti")) return true
  if (v.includes('(as is)') || v.includes('(to be)') || v.includes('(as-is)') || v.includes('(to-be)')) return true
  return false
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

  if (s.includes('swimlane') || s.includes('pool;') || s.includes('shape=pool'))
    return 'lane'

  const isGatewayShape =
    s.includes('rhombus') ||
    s.includes('shape=rhombus') ||
    s.includes('gateway') ||
    i.startsWith('gw') ||
    i.startsWith('gateway') ||
    i.includes('-gw-') ||
    i.includes('_gw_')

  if (isGatewayShape) {
    if (s.includes('outline=plus') || s.includes('parallel') || l.trim() === '+' || l.trim() === 'and' || l.trim() === 'и')
      return 'parallelGateway'
    if (s.includes('inclusive') || s.includes('outline=circle')) return 'inclusiveGateway'
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
    // Explicit Reject / Declined End Event (Red)
    if (
      l.includes('rad etildi') ||
      l.includes('rad javob') ||
      l.includes('otkaz') ||
      l.includes('отказ') ||
      l.includes('bekor') ||
      l.includes('отклон') ||
      i.includes('reject') ||
      s.includes('fillcolor=#ef4444') ||
      s.includes('fillcolor=#e11d48') ||
      s.includes('fillcolor=#be123c') ||
      s.includes('fillcolor=#dc2626') ||
      s.includes('fillcolor=#b91c1c')
    ) {
      return 'endEvent'
    }

    const isEnd =
      idHasToken(id, 'end') ||
      idHasToken(id, 'finish') ||
      l.includes('заверш') ||
      l.includes('конец') ||
      l.includes('выдан') ||
      l.includes('ochildi') ||
      l.includes('tugashi') ||
      l.includes('bajarildi') ||
      l.includes('активирован') ||
      s.includes('outline=double') ||
      s.includes('outline=end')

    const isStart =
      idHasToken(id, 'start') ||
      idHasToken(id, 'begin') ||
      l.includes('старт') ||
      l.includes('поступлен') ||
      l.includes('tashrif') ||
      l.includes('boshlanish')

    if (isEnd) return 'endEvent'
    if (isStart) return 'startEvent'

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
    l.includes('авто-') ||
    l.includes('avtomat') ||
    l.includes('sms')
  ) {
    return 'serviceTask'
  }

  return 'userTask'
}

function classifyCategory(type: NodeType, name: string, style: string): StepCategory {
  const lower = (name + ' ' + style).toLowerCase()
  if (type === 'startEvent' || type === 'endEvent') return 'notification'
  if (type === 'serviceTask' || lower.includes('rpa') || lower.includes('робот') || lower.includes('авто-') || lower.includes('avtomat') || lower.includes('генерация') || lower.includes('sms'))
    return 'rpa_bot'
  if (lower.includes('согласован') || lower.includes('комитет') || lower.includes('утвержд') || lower.includes('подпис') || lower.includes('голос') || lower.includes('imzo') || lower.includes('vizo') || lower.includes('tasdiq') || lower.includes('himoya'))
    return 'approval'
  if (lower.includes('проверк') || lower.includes('валидац') || lower.includes('скоринг') || lower.includes('скор') || lower.includes('андеррайт') || lower.includes('риск') || lower.includes('tekshirish') || lower.includes('solishtirish') || lower.includes('identifikatsiya') || lower.includes('o\'rganish'))
    return 'validation'
  if (lower.includes('api') || lower.includes('абс') || lower.includes('сервис') || lower.includes('цфт') || lower.includes('didox') || lower.includes('iabs') || lower.includes('eha') || lower.includes('edo') || lower.includes('nibbd'))
    return 'api_service'
  return 'manual'
}

function detectSystem(name: string, laneName: string): string {
  const lower = (name + ' ' + laneName).toLowerCase()
  if (lower.includes('rpa') || lower.includes('робот') || lower.includes('avtomat sms')) return 'PIX RPA'
  if (lower.includes('nibbd')) return 'NIBBD / ЦБ РУз'
  if (lower.includes('eha') || lower.includes('еха')) return 'EHA Dasturi'
  if (lower.includes('edo') || lower.includes('эдо') || lower.includes('didox') || lower.includes('эцп')) return 'EDO / Didox (ЭЦП)'
  if (lower.includes('aml') || lower.includes('komplayens')) return 'AML/CFT Moduli'
  if (lower.includes('iabs') || lower.includes('абс') || lower.includes('счет') || lower.includes('проводк') || lower.includes('цфт') || lower.includes('клиенты и счета') || lower.includes('комиссия') || lower.includes('транш'))
    return 'iABS (ЦФТ-Банк)'
  if (lower.includes('гнк') || lower.includes('налог') || lower.includes('soliq')) return 'API Soliq (ГНК)'
  if (lower.includes('катм') || lower.includes('katm') || lower.includes('бюро')) return 'API KATM'
  if (lower.includes('епигу') || lower.includes('egrpo') || lower.includes('егрпо')) return 'ЕПИГУ / ЕГРПО'
  if (lower.includes('dragle')) return 'Dragle BI'
  if (lower.includes('crobs')) return 'CROBS Risk Engine'
  if (lower.includes('zoom')) return 'Zoom Video Conf'
  if (lower.includes('swift') || lower.includes('свифт')) return 'SWIFT Alliance'
  return 'SQB CRM / Core'
}

function extractSlaMinutes(rawText: string, category: StepCategory, type: NodeType): number {
  if (type === 'startEvent' || type === 'endEvent') return 5

  const match = rawText.match(/(\d+(?:\.\d+)?)\s*(?:min|daq|минут|мин|m\b)/i)
  if (match) {
    const val = parseFloat(match[1])
    return Math.max(1, Math.round(val))
  }

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

function parseBpmnXml(xmlText: string, fileName: string): BusinessProcess {
  const doc = new DOMParser().parseFromString(xmlText, 'text/xml')
  const parserError = doc.querySelector('parsererror')
  if (parserError) {
    throw new Error(`Ошибка BPMN XML: ${parserError.textContent?.slice(0, 80)}`)
  }

  const processEl = elementsByLocalName(doc, ['process'])[0]
  const processName = processEl?.getAttribute('name') || fileName.replace(/\.(bpmn|xml)$/i, '')
  const processId = processEl?.getAttribute('id') || `PRC-SQB-${crypto.randomUUID().slice(0, 8).toUpperCase()}`

  const nodes: ProcessNode[] = []
  const edges: ProcessEdge[] = []
  const lanes: ProcessNode[] = []

  const boundsMap = new Map<string, { x: number; y: number; width: number; height: number }>()
  elementsByLocalName(doc, ['BPMNShape']).forEach((s) => {
    const bpmnElement = s.getAttribute('bpmnElement')
    const bounds = elementsByLocalName(s, ['Bounds'])[0]
    if (bpmnElement && bounds) {
      boundsMap.set(bpmnElement, {
        x: Number(bounds.getAttribute('x') ?? 100),
        y: Number(bounds.getAttribute('y') ?? 100),
        width: Number(bounds.getAttribute('width') ?? 120),
        height: Number(bounds.getAttribute('height') ?? 60),
      })
    }
  })

  const allElements = Array.from(doc.getElementsByTagName('*'))
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
      const sla = extractSlaMinutes(name, category, type)

      nodes.push({
        id,
        name,
        type,
        category,
        code,
        geometry,
        style: '',
        slaMinutes: sla,
        costPerExecution: category === 'rpa_bot' ? 800 : (sla * 1932),
        automationPotential: category === 'rpa_bot' ? 95 : 60,
        system: detectSystem(name, ''),
      })
    }
  })

  if (nodes.length === 0) {
    throw new Error('В BPMN-файле не найдено ни одного элемента процесса')
  }

  elementsByLocalName(doc, ['lane']).forEach((l, idx) => {
    const id = l.getAttribute('id') ?? `lane_${idx}`
    const name = l.getAttribute('name') ?? `Подразделение ${idx + 1}`
    const geometry = boundsMap.get(id) || { x: 50, y: 50 + idx * 180, width: 1400, height: 180 }
    lanes.push({
      id,
      name,
      type: 'lane',
      role: name,
      geometry,
      style: 'swimlane;',
    })
    elementsByLocalName(l, ['flowNodeRef']).forEach((ref) => {
      const nodeId = (ref.textContent || '').trim()
      const node = nodes.find((n) => n.id === nodeId)
      if (node) {
        node.laneId = id
        node.laneName = name
        node.role = node.role || name
        node.system = detectSystem(node.name, name)
      }
    })
  })

  const nodeIdSet = new Set(nodes.map((n) => n.id))
  elementsByLocalName(doc, ['sequenceFlow']).forEach((f) => {
    const sourceId = f.getAttribute('sourceRef') ?? undefined
    const targetId = f.getAttribute('targetRef') ?? undefined
    if (!sourceId || !targetId || !nodeIdSet.has(sourceId) || !nodeIdSet.has(targetId)) return
    edges.push({
      id: f.getAttribute('id') ?? `edge_${crypto.randomUUID()}`,
      name: f.getAttribute('name') ?? '',
      sourceId,
      targetId,
      points: [],
    })
  })

  const firstTask = nodes.find((n) => n.type === 'userTask' || n.type === 'serviceTask' || n.type === 'task') || nodes[0]

  const passport: ProcessPassport = {
    code: processId.startsWith('PRC-') ? processId : `PRC-SQB-${crypto.randomUUID().slice(0, 8).toUpperCase()}`,
    name: processName,
    version: '1.0',
    status: 'draft',
    owner: 'Департамент бизнес-процессов АКБ «Узпромстройбанк»',
    department: lanes[0]?.name || 'Операционный блок',
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
        currentStepId: firstTask?.id || 'step-1',
        currentStepName: firstTask?.name || 'Первичный шаг',
        assignedTo: firstTask?.role || 'Сотрудник банка',
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

  // 1. Collect all Edges
  const rawEdges = cells.filter((c) => c.getAttribute('edge') === '1')
  const edgeIdSet = new Set(rawEdges.map((e) => e.getAttribute('id')).filter(Boolean))

  const incoming = new Set<string>()
  const outgoing = new Set<string>()
  for (const e of rawEdges) {
    const t = e.getAttribute('target')
    const s = e.getAttribute('source')
    if (t) incoming.add(t)
    if (s) outgoing.add(s)
  }

  // 2. Classify and filter cells
  const labelMap = new Map<string, string>()
  const ignoreCellIds = new Set<string>()

  cells.forEach((c) => {
    const id = c.getAttribute('id') ?? ''
    const parentId = c.getAttribute('parent') ?? ''
    const style = getStyle(c).toLowerCase()
    const rawVal = c.getAttribute('value')
    const cleaned = cleanLabel(rawVal)
    const geo = c.querySelector('mxGeometry')
    const isRelative = geo?.getAttribute('relative') === '1'
    const isConnectable0 = c.getAttribute('connectable') === '0'

    // Condition 1: Child of an edge
    if (edgeIdSet.has(parentId)) {
      ignoreCellIds.add(id)
      if (cleaned) labelMap.set(parentId, cleaned)
      return
    }

    // Condition 2: Explicit edgeLabel / connectable="0" / relative="1"
    if (style.includes('edgelabel') || isConnectable0 || isRelative) {
      ignoreCellIds.add(id)
      if (cleaned && parentId) labelMap.set(parentId, cleaned)
      return
    }

    // Condition 3: Text label overlay (e.g. node_start_label, gw_risk_label)
    if (id.endsWith('_label') || (style.includes('text;') && !style.includes('swimlane') && (style.includes('strokecolor=none') || style.includes('fillcolor=none') || isNonTaskLabel(cleaned) || cleaned.length < 2))) {
      ignoreCellIds.add(id)
      const baseId = id.replace(/_label$/, '')
      if (baseId && cleaned) labelMap.set(baseId, cleaned)
      return
    }

    // Condition 4: Diagram title banner (e.g. "Kredit shartnomasi muddatini o'zgartirish (AS IS)")
    if (style.includes('text;') && isNonTaskLabel(cleaned)) {
      ignoreCellIds.add(id)
      return
    }

    // Condition 5: Non-task system tags, artifacts, conditions with no sequence connections
    if (isNonTaskLabel(cleaned) && !incoming.has(id) && !outgoing.has(id)) {
      ignoreCellIds.add(id)
      return
    }
  })

  // 3. Detect Swimlanes vs Outer Pool Container
  const swimlaneCells = cells.filter((c) => {
    const style = getStyle(c).toLowerCase()
    return c.getAttribute('vertex') === '1' && (style.includes('swimlane') || style.includes('shape=pool'))
  })

  const poolIds = new Set<string>()
  swimlaneCells.forEach((sw) => {
    const swId = sw.getAttribute('id') ?? ''
    const hasChildLanes = swimlaneCells.some((other) => other.getAttribute('parent') === swId)
    if (hasChildLanes || getStyle(sw).includes('stackLayout')) {
      poolIds.add(swId)
    }
  })

  const swimlaneIds = new Set(swimlaneCells.map((c) => c.getAttribute('id') || '').filter(Boolean))
  const containerIds = new Set<string>([...poolIds, ...swimlaneIds, '0', '1'])

  const rawVertices = cells.filter((c) => {
    const id = c.getAttribute('id') ?? ''
    const isVertex = c.getAttribute('vertex') === '1'
    if (!isVertex) return false
    if (ignoreCellIds.has(id)) return false
    if (poolIds.has(id)) return false
    const style = getStyle(c).toLowerCase()
    const parentId = c.getAttribute('parent') ?? ''
    const parentEl = cellMap.get(parentId)
    if (parentEl && parentEl.getAttribute('vertex') === '1' && !containerIds.has(parentId)) {
      // Clock / icon nested inside a task — not a flow node
      return false
    }
    const geo = c.querySelector('mxGeometry')
    const w = Number(geo?.getAttribute('width') ?? 0)
    const h = Number(geo?.getAttribute('height') ?? 0)
    const unlabeled = !cleanLabel(c.getAttribute('value')) && !labelMap.has(id)
    const tiny = w > 0 && h > 0 && w <= 32 && h <= 32
    if (isDecorationStyle(style) && (unlabeled || tiny) && !incoming.has(id) && !outgoing.has(id)) {
      return false
    }
    if (unlabeled && !incoming.has(id) && !outgoing.has(id) && tiny) {
      return false
    }
    return true
  })

  const nodes: ProcessNode[] = []
  let stepIndex = 1

  for (const cell of rawVertices) {
    const id = cell.getAttribute('id') ?? `node_${crypto.randomUUID()}`
    const style = getStyle(cell)
    const rawValue = cell.getAttribute('value')
    let rawCleaned = cleanLabel(rawValue)

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

    // Extract explicit code or number (e.g. '1. Mijozni kutib olish' -> 'STEP-01', 'STEP-02')
    let code: string | undefined = undefined
    const codeMatch = (rawValue || rawCleaned).match(/\b(STEP[-_ ]?\d+|START|END|GW[-_ ]?\w+)\b/i)
    const numPrefix = rawCleaned.match(/^(\d+)[.)]\s*/)

    if (codeMatch) {
      code = codeMatch[1].toUpperCase().replace(/_/g, '-')
    } else if (numPrefix && isTask) {
      code = `STEP-${String(numPrefix[1]).padStart(2, '0')}`
    } else if (type === 'startEvent') {
      code = 'START'
    } else if (type === 'endEvent') {
      code = 'END'
    } else if (isTask) {
      code = `STEP-${String(stepIndex++).padStart(2, '0')}`
    }

    const slaMin = extractSlaMinutes(`${rawValue || ''} ${rawCleaned}`, category, type)

    // Clean human-friendly name (strip [PIX RPA], numbers, minutes)
    let cleanName = rawCleaned
      .replace(/^\[.*?\]\s*/gi, '')
      .replace(/^STEP[-_ ]?\d+[:\s-]*/gi, '')
      .replace(/^[0-9]+[.)]\s*/gi, '')
      .replace(/\b\d+(?:\.\d+)?\s*(?:min|daq|минут|мин)\b.*$/gi, '')
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

    if (type === 'startEvent' || type === 'endEvent') {
      width = Math.max(width || 32, 28)
      height = Math.max(height || 32, 28)
    } else if (type.includes('Gateway')) {
      width = Math.max(width || 36, 28)
      height = Math.max(height || 36, 28)
    } else if (type === 'lane') {
      width = Math.max(width, 80)
      height = Math.max(height, 40)
    } else {
      width = Math.max(width, 40)
      height = Math.max(height, 24)
    }

    const fotCost = category !== 'rpa_bot' ? slaMin * 1932 : 800

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
      slaMinutes: slaMin,
      costPerExecution: fotCost,
      automationPotential: category === 'rpa_bot' ? 95 : category === 'manual' ? 65 : 40,
    })
  }

  // Identify lanes / swimlanes
  const lanes = nodes.filter((n) => n.type === 'lane')
  const laneIds = new Set(lanes.map((l) => l.id))
  const flowNodes = nodes.filter((n) => n.type !== 'lane')

  for (const n of flowNodes) {
    if (n.laneId && !laneIds.has(n.laneId)) n.laneId = undefined
  }

  // Geometry-based lane assignment fallback
  for (const n of flowNodes) {
    if (!n.laneId) {
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

    if (n.laneId) {
      const parentLane = lanes.find((l) => l.id === n.laneId)
      if (parentLane) {
        n.laneName = parentLane.name
        n.role = n.role || parentLane.name
      }
    }
    n.system = detectSystem(n.name, n.laneName || '')
  }

  // Draw.io Grid Snap & Layout Spacing (10px grid unit)
  const GRID_SIZE = 10
  const SNAP = (v: number) => Math.round(v / GRID_SIZE) * GRID_SIZE

  // Keep original draw.io coordinates — do not shove shapes sideways.
  lanes.forEach((lane) => {
    lane.geometry.x = SNAP(lane.geometry.x)
    lane.geometry.y = SNAP(lane.geometry.y)
    lane.geometry.width = Math.max(SNAP(lane.geometry.width), 80)
    lane.geometry.height = Math.max(SNAP(lane.geometry.height), 40)
  })

  flowNodes.forEach((node) => {
    node.geometry.x = SNAP(node.geometry.x)
    node.geometry.y = SNAP(node.geometry.y)
    node.geometry.width = Math.max(SNAP(node.geometry.width), 24)
    node.geometry.height = Math.max(SNAP(node.geometry.height), 24)
  })

  const validNodeIdSet = new Set(flowNodes.map((n) => n.id))

  const edges: ProcessEdge[] = rawEdges
    .filter((cell) => {
      const s = cell.getAttribute('source')
      const t = cell.getAttribute('target')
      return Boolean(s && t && validNodeIdSet.has(s) && validNodeIdSet.has(t))
    })
    .map((cell) => {
      const edgeId = cell.getAttribute('id') ?? `edge_${crypto.randomUUID()}`
      const rawVal = cell.getAttribute('value')
      let edgeName = cleanLabel(rawVal)
      if (!edgeName && labelMap.has(edgeId)) {
        edgeName = labelMap.get(edgeId)!
      }

      const points: ProcessEdgePoint[] = []
      cell.querySelectorAll('Array[as="points"] > mxPoint').forEach((p) => {
        points.push({ x: Number(p.getAttribute('x') ?? 0), y: Number(p.getAttribute('y') ?? 0) })
      })

      return {
        id: edgeId,
        name: edgeName,
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
  const totalHours = roundHours(flowNodes.reduce((acc, n) => acc + (n.slaMinutes || 0), 0) / 60) || 8

  const passport: ProcessPassport = {
    code: `PRC-SQB-${crypto.randomUUID().slice(0, 8).toUpperCase()}`,
    name: cleanTitle,
    version: '1.0',
    status: 'draft',
    owner: 'Департамент бизнес-процессов АКБ «Узпромстройбанк»',
    department: lanes[0]?.name || 'Операционный блок',
    category: 'Банковские процессы (Методика SQB)',
    targetSlaHours: totalHours,
    description: `Импортирован из файла draw.io: ${fileName}. Сформирован регламент по Методологии АКБ «Узпромстройбанк» (1-ILOVA / 4-ILOVA).`,
    createdDate: new Date().toISOString().split('T')[0],
    updatedDate: new Date().toISOString().split('T')[0],
  }

  const registry: PixRegistrySchema = {
    id: `reg-${crypto.randomUUID()}`,
    code: `REG_${passport.code.replace(/[^a-zA-Z0-9_]/g, '_')}`,
    name: `Реестр: ${cleanTitle}`,
    description: `Операционный реестр заявок по процессу ${cleanTitle} (PIX BPM)`,
    fields: [
      { id: 'f1', code: 'case_number', name: 'Номер заявки', type: 'string', required: true },
      { id: 'f2', code: 'client_inn', name: 'ИНН Клиента', type: 'string', required: true },
      { id: 'f3', code: 'client_title', name: 'Наименование компании', type: 'string', required: true },
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

function roundHours(val: number): number {
  return Math.round(val * 10) / 10
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
