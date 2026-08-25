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
    } catch (e) {
      throw new Error(`Не удалось распаковать сжатый draw.io: ${e instanceof Error ? e.message : String(e)}. Сохраните диаграмму как несжатый XML (File → Export)` )
    }
  }

  throw new Error('Браузер не поддерживает DecompressionStream для сжатых draw.io. Сохраните диаграмму как несжатый XML или откройте в современном браузере.')
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

  // 3. mxfile container — поддерживаем несколько diagram, объединяем
  if (trimmed.includes('<mxfile') || trimmed.includes('<diagram')) {
    const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
    const parserError = doc.querySelector('parsererror')
    if (parserError) {
      throw new Error(`Ошибка XML: ${parserError.textContent?.slice(0, 80)}`)
    }

    const diagrams = Array.from(doc.querySelectorAll('diagram')) as Element[]
    if (diagrams.length === 0) throw new Error('В файле draw.io не найдено ни одной диаграммы (<diagram>)')

    const models: string[] = []
    for (const diagram of diagrams) {
      const model = diagram.querySelector('mxGraphModel')
      if (model) {
        models.push(new XMLSerializer().serializeToString(model))
        continue
      }
      const rootEl = diagram.querySelector('root')
      if (rootEl) {
        models.push(`<mxGraphModel>${new XMLSerializer().serializeToString(rootEl)}</mxGraphModel>`)
        continue
      }
      const innerText = diagram.textContent?.trim() ?? ''
      if (innerText) {
        if (innerText.startsWith('<mxGraphModel') || innerText.includes('<mxGraphModel')) {
          models.push(innerText)
        } else {
          try {
            const decompressed = await inflateDiagram(innerText)
            models.push(decompressed)
          } catch {
            // пропускаем битую диаграмму
          }
        }
      }
    }
    if (models.length === 0) throw new Error('Не удалось извлечь ни одной диаграммы')
    if (models.length === 1) return { xml: models[0], isBpmn: false }
    // объединяем несколько диаграмм с вертикальным offset
    try {
      const ser = new XMLSerializer()
      let yOffset = 0
      const combinedRoot = doc.createElement('root')
      const c0 = doc.createElement('mxCell'); c0.setAttribute('id', '0'); combinedRoot.appendChild(c0)
      const c1 = doc.createElement('mxCell'); c1.setAttribute('id', '1'); c1.setAttribute('parent', '0'); combinedRoot.appendChild(c1)
      for (const mXml of models) {
        const mDoc = new DOMParser().parseFromString(mXml, 'text/xml')
        const root = mDoc.querySelector('root')
        if (!root) continue
        let maxY = 0
        Array.from(root.querySelectorAll('mxCell')).forEach((c) => {
          const geo = c.querySelector('mxGeometry')
          if (geo) {
            const y = Number(geo.getAttribute('y') ?? 0)
            const h = Number(geo.getAttribute('height') ?? 0)
            maxY = Math.max(maxY, y + h)
          }
        })
        Array.from(root.querySelectorAll('mxCell')).forEach((c) => {
          const cid = c.getAttribute('id')
          if (cid === '0' || cid === '1') return
          const clone = doc.createElement('mxCell')
          Array.from(c.attributes).forEach((a) => clone.setAttribute(a.name, a.value))
          const geo = c.querySelector('mxGeometry')
          if (geo) {
            const ng = doc.createElement('mxGeometry')
            Array.from(geo.attributes).forEach((a) => ng.setAttribute(a.name, a.value))
            if (yOffset !== 0 && geo.getAttribute('relative') !== '1') {
              const origY = Number(geo.getAttribute('y') ?? 0)
              ng.setAttribute('y', String(origY + yOffset))
            }
            Array.from(geo.children).forEach((ch) => ng.appendChild(ch.cloneNode(true)))
            clone.appendChild(ng)
          }
          Array.from(c.children).forEach((ch) => {
            if ((ch as Element).tagName !== 'mxGeometry') clone.appendChild(ch.cloneNode(true))
          })
          combinedRoot.appendChild(clone)
        })
        yOffset += maxY + 100
      }
      const wrapper = doc.createElement('mxGraphModel')
      wrapper.appendChild(combinedRoot)
      return { xml: ser.serializeToString(wrapper), isBpmn: false }
    } catch {
      return { xml: models[0], isBpmn: false }
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

function parseStyleMap(style: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of (style || '').split(';')) {
    if (!part) continue
    const eq = part.indexOf('=')
    if (eq < 0) {
      out[part.trim().toLowerCase()] = '1'
      continue
    }
    out[part.slice(0, eq).trim().toLowerCase()] = part.slice(eq + 1).trim()
  }
  return out
}

function styleFloat(map: Record<string, string>, key: string): number | undefined {
  const raw = map[key]
  if (raw == null || raw === '') return undefined
  const n = Number(raw)
  return Number.isFinite(n) ? n : undefined
}

/**
 * Absolute origin of a cell (own x/y plus every non-relative ancestor).
 * mxGraph semantics: children of a swimlane are relative to the swimlane's
 * FULL origin (including title/startSize area) — no extra startSize shift.
 */
function parentOrigin(
  cellId: string | null | undefined,
  cellMap: Map<string, Element>,
  cache: Map<string, { x: number; y: number }>,
): { x: number; y: number } {
  if (!cellId || cellId === '0' || cellId === '1') return { x: 0, y: 0 }
  const hit = cache.get(cellId)
  if (hit) return hit
  const cell = cellMap.get(cellId)
  if (!cell) {
    cache.set(cellId, { x: 0, y: 0 })
    return { x: 0, y: 0 }
  }
  const parent = parentOrigin(cell.getAttribute('parent'), cellMap, cache)
  const geo = cell.querySelector(':scope > mxGeometry') as Element | null
  let x = parent.x
  let y = parent.y
  if (geo && geo.getAttribute('relative') !== '1') {
    x += Number(geo.getAttribute('x') ?? 0)
    y += Number(geo.getAttribute('y') ?? 0)
  }
  const origin = { x, y }
  cache.set(cellId, origin)
  return origin
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
  "ha", "yo'q", "yo`q", "yo’q", "да", "нет", "yes", "no",
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
    s.includes('symbol=clock') ||
    s.includes('shape=datastore') ||
    s.includes('shape=mxgraph.bpmn.datastore') ||
    s.includes('kind=datastore') ||
    s.includes('shape=mxgraph.signs') ||
    s.includes('shape=mxgraph.bpmn.dataobject') ||
    s.includes('shape=note') ||
    s.includes('shape=mxgraph.bpmn.annotation')
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
    idHasToken(id, 'start') ||
    idHasToken(id, 'end') ||
    idHasToken(id, 'reject')
  ) {
    // Explicit Reject / Declined End Event (Red)
    if (
      l.includes('rad etildi') ||
      l.includes('rad javob') ||
      l.includes('otkaz') ||
      l.includes('отказ') ||
      l.includes('bekor') ||
      l.includes('отклон') ||
      idHasToken(id, 'reject') ||
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
  const edgeLabelGeo = new Map<string, { x?: number; y?: number }>()
  const ignoreCellIds = new Set<string>()
  const originCache = new Map<string, { x: number; y: number }>()
  const orphanConditionLabels: { text: string; x: number; y: number }[] = []

  const rememberLabelGeo = (edgeId: string, geo: Element | null) => {
    if (!geo || edgeLabelGeo.has(edgeId)) return
    const rawX = geo.getAttribute('x')
    const rawY = geo.getAttribute('y')
    let x = rawX != null && rawX !== '' ? Number(rawX) : undefined
    let y = rawY != null && rawY !== '' ? Number(rawY) : undefined
    const offset = Array.from(geo.querySelectorAll('mxPoint')).find((p) => p.getAttribute('as') === 'offset')
    if (offset) {
      const ox = Number(offset.getAttribute('x') ?? 0)
      const oy = Number(offset.getAttribute('y') ?? 0)
      y = y == null ? oy : y + oy
      if (ox !== 0) {
        if (x == null) x = ox * 0.005
        else x += ox * 0.005
      }
    }
    if (x != null || y != null) edgeLabelGeo.set(edgeId, { x, y })
  }

  cells.forEach((c) => {
    const id = c.getAttribute('id') ?? ''
    const parentId = c.getAttribute('parent') ?? ''
    const style = getStyle(c).toLowerCase()
    const rawVal = c.getAttribute('value')
    const cleaned = cleanLabel(rawVal)
    const geo = c.querySelector('mxGeometry')
    const isRelative = geo?.getAttribute('relative') === '1'
    const isConnectable0 = c.getAttribute('connectable') === '0'

    // Condition 1: Child of an edge (relative label)
    if (edgeIdSet.has(parentId)) {
      ignoreCellIds.add(id)
      if (cleaned) labelMap.set(parentId, cleaned)
      rememberLabelGeo(parentId, geo)
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
      // Сохраняем условия Yo'q/Ha/To'liq как отдельные метки для привязки к ближайшему ребру
      if (CONDITION_TAGS.has(cleaned.toLowerCase()) && cleaned) {
        const geo2 = c.querySelector('mxGeometry')
        if (geo2) {
          const o = parentOrigin(parentId, cellMap, originCache)
          const lx = Number(geo2.getAttribute('x') ?? 0) + o.x + Number(geo2.getAttribute('width') ?? 40) / 2
          const ly = Number(geo2.getAttribute('y') ?? 0) + o.y + Number(geo2.getAttribute('height') ?? 20) / 2
          orphanConditionLabels.push({ text: cleaned, x: lx, y: ly })
        }
      }
      return
    }

    // Condition 4: Diagram title banner (e.g. "Kredit shartnomasi muddatini o'zgartirish (AS IS)")
    if (style.includes('text;') && isNonTaskLabel(cleaned)) {
      ignoreCellIds.add(id)
      if (CONDITION_TAGS.has(cleaned.toLowerCase()) && cleaned) {
        const geo2 = c.querySelector('mxGeometry')
        if (geo2) {
          const o = parentOrigin(parentId, cellMap, originCache)
          const lx = Number(geo2.getAttribute('x') ?? 0) + o.x + Number(geo2.getAttribute('width') ?? 40) / 2
          const ly = Number(geo2.getAttribute('y') ?? 0) + o.y + Number(geo2.getAttribute('height') ?? 20) / 2
          orphanConditionLabels.push({ text: cleaned, x: lx, y: ly })
        }
      }
      return
    }

    // Condition 5: Non-task system tags, artifacts, conditions with no sequence connections
    if (isNonTaskLabel(cleaned) && !incoming.has(id) && !outgoing.has(id)) {
      ignoreCellIds.add(id)
      if (CONDITION_TAGS.has(cleaned.toLowerCase()) && cleaned) {
        const geo2 = c.querySelector('mxGeometry')
        if (geo2) {
          const o = parentOrigin(parentId, cellMap, originCache)
          const lx = Number(geo2.getAttribute('x') ?? 0) + o.x + Number(geo2.getAttribute('width') ?? 40) / 2
          const ly = Number(geo2.getAttribute('y') ?? 0) + o.y + Number(geo2.getAttribute('height') ?? 20) / 2
          orphanConditionLabels.push({ text: cleaned, x: lx, y: ly })
        }
      }
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
    if (hasChildLanes || getStyle(sw).toLowerCase().includes('stacklayout')) {
      poolIds.add(swId)
    }
  })

  const swimlaneIds = new Set(swimlaneCells.map((c) => c.getAttribute('id') || '').filter(Boolean))
  const containerIds = new Set<string>([...poolIds, ...swimlaneIds, '0', '1'])

  const isArtifactShape = (s: string) => s.includes('datastore') || s.includes('dataobject') || s.includes('shape=note') || s.includes('shape=mxgraph.signs') || s.includes('shape=mxgraph.bpmn.annotation')

  const rawVertices = cells.filter((c) => {
    const id = c.getAttribute('id') ?? ''
    const isVertex = c.getAttribute('vertex') === '1'
    if (!isVertex) return false
    if (ignoreCellIds.has(id)) return false
    if (poolIds.has(id)) return false
    const style = getStyle(c).toLowerCase()
    // Datastore/Note/Annotation — показываем только если участвует в потоке (иначе это легенда)
    if (isArtifactShape(style) && !incoming.has(id) && !outgoing.has(id)) return false
    const parentId = c.getAttribute('parent') ?? ''
    const parentEl = cellMap.get(parentId)
    if (parentEl && parentEl.getAttribute('vertex') === '1' && !containerIds.has(parentId)) {
      // Clock / icon nested inside a task — not a flow node
      return false
    }
    const geo = c.querySelector(':scope > mxGeometry') as Element | null
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

    // AbsoluteX = LocalX + ParentX + GrandparentX + ...
    // В draw.io отсутствующий x/y означает 0 (не 100!)
    const geo = cell.querySelector(':scope > mxGeometry') as Element | null
    const localX = Number(geo?.getAttribute('x') ?? 0)
    const localY = Number(geo?.getAttribute('y') ?? 0)
    let width = Number(geo?.getAttribute('width') ?? 120)
    let height = Number(geo?.getAttribute('height') ?? 60)
    const origin = parentOrigin(parentId, cellMap, originCache)
    const x = localX + origin.x
    const y = localY + origin.y

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

    // Guard against invisible boxes: разные минимумы по типу
    if (type === 'lane') {
      width = Math.max(Math.round(width || 40), 40)
      height = Math.max(Math.round(height || 40), 40)
    } else if (type === 'startEvent' || type === 'endEvent') {
      width = Math.max(Math.round(width || 44), 32)
      height = Math.max(Math.round(height || 44), 32)
    } else if (type.includes('Gateway')) {
      width = Math.max(Math.round(width || 48), 32)
      height = Math.max(Math.round(height || 48), 32)
    } else {
      width = Math.max(Math.round(width || 120), 80)
      height = Math.max(Math.round(height || 60), 40)
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
        width,
        height,
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

  // Geometry-based lane assignment fallback — учитываем ширину заголовка дорожки (44px)
  const LANE_HEAD_WIDTH = 44
  for (const n of flowNodes) {
    if (!n.laneId) {
      const hit = lanes.find(
        (l) =>
          n.geometry.x >= l.geometry.x + LANE_HEAD_WIDTH - 10 &&
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

  const validNodeIdSet = new Set(flowNodes.map((n) => n.id))
  const validLaneIdSet = new Set(lanes.map((l) => l.id))

  const edges: ProcessEdge[] = rawEdges
    .filter((cell) => {
      const s = cell.getAttribute('source')
      const t = cell.getAttribute('target')
      if (!s || !t) return false
      const sOk = validNodeIdSet.has(s) || validLaneIdSet.has(s)
      const tOk = validNodeIdSet.has(t) || validLaneIdSet.has(t)
      return sOk && tOk
    })
    .map((cell) => {
      const edgeId = cell.getAttribute('id') ?? `edge_${crypto.randomUUID()}`
      const rawVal = cell.getAttribute('value')
      let edgeName = cleanLabel(rawVal)
      if (!edgeName && labelMap.has(edgeId)) {
        edgeName = labelMap.get(edgeId)!
      }

      const rawStyle = getStyle(cell)
      const smap = parseStyleMap(rawStyle)
      const origin = parentOrigin(cell.getAttribute('parent'), cellMap, originCache)
      const points: ProcessEdgePoint[] = []
      cell.querySelectorAll('Array[as="points"] > mxPoint').forEach((p) => {
        points.push({
          x: Math.round(Number(p.getAttribute('x') ?? 0) + origin.x),
          y: Math.round(Number(p.getAttribute('y') ?? 0) + origin.y),
        })
      })

      const stored = edgeLabelGeo.get(edgeId)
      const edgeGeo = cell.querySelector('mxGeometry')
      const rawLX = edgeGeo?.getAttribute('x')
      const rawLY = edgeGeo?.getAttribute('y')
      const labelX = stored?.x ?? (rawLX != null && rawLX !== '' ? Number(rawLX) : undefined)
      const labelY = stored?.y ?? (rawLY != null && rawLY !== '' ? Number(rawLY) : undefined)

      const lowerStyle = rawStyle.toLowerCase()
      const dashed = lowerStyle.includes('dashed=1') || lowerStyle.includes('dashed = 1')
      const dashPattern = smap['dashpattern']
      const edgeStyle = smap['edgestyle']
      const strokeColor = smap['strokecolor']
      const sw = styleFloat(smap, 'strokewidth')

      return {
        id: edgeId,
        name: edgeName,
        sourceId: cell.getAttribute('source') ?? undefined,
        targetId: cell.getAttribute('target') ?? undefined,
        points,
        exitX: styleFloat(smap, 'exitx'),
        exitY: styleFloat(smap, 'exity'),
        entryX: styleFloat(smap, 'entryx'),
        entryY: styleFloat(smap, 'entryy'),
        labelX,
        labelY,
        style: rawStyle,
        dashed: dashed || undefined,
        dashPattern,
        edgeStyle,
        strokeColor,
        strokeWidth: sw,
      }
    })

  // Привязываем висячие метки Yo'q/Ha/To'liq к ближайшему безымянному ребру (обычно от шлюза)
  if (orphanConditionLabels.length > 0) {
    const nodeById = new Map<string, ProcessNode>()
    for (const n of [...flowNodes, ...lanes]) nodeById.set(n.id, n)
    for (const lbl of orphanConditionLabels) {
      let best: ProcessEdge | null = null
      let bestDist = Infinity
      for (const e of edges) {
        if (e.name) continue
        const s = nodeById.get(e.sourceId || '')
        const t = nodeById.get(e.targetId || '')
        if (!s || !t) continue
        const isGw = s.type === 'exclusiveGateway' || s.type === 'parallelGateway' || s.type === 'inclusiveGateway'
        // центр ребра (с учётом waypoints)
        let cx: number, cy: number
        if (e.points.length > 0) {
          let sx = s.geometry.x + s.geometry.width / 2
          let sy = s.geometry.y + s.geometry.height / 2
          let ex = t.geometry.x + t.geometry.width / 2
          let ey = t.geometry.y + t.geometry.height / 2
          const pts = [{ x: sx, y: sy }, ...e.points, { x: ex, y: ey }]
          let tx = 0, ty = 0
          for (const p of pts) { tx += p.x; ty += p.y }
          cx = tx / pts.length; cy = ty / pts.length
        } else {
          cx = (s.geometry.x + s.geometry.width / 2 + t.geometry.x + t.geometry.width / 2) / 2
          cy = (s.geometry.y + s.geometry.height / 2 + t.geometry.y + t.geometry.height / 2) / 2
        }
        const d = Math.hypot(lbl.x - cx, lbl.y - cy)
        const penalty = isGw ? 0 : 35
        if (d + penalty < bestDist && d < 140) {
          bestDist = d + penalty
          best = e
        }
      }
      if (best) {
        best.name = lbl.text
      }
    }
  }

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
