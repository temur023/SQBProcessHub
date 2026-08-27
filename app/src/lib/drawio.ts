import type {
  BusinessProcess,
  EdgeKind,
  NodeType,
  ProcessEdge,
  ProcessEdgePoint,
  ProcessNode,
  StepCategory,
  ProcessPassport,
  PixRegistrySchema,
} from '@/types/process'
import { isArtifactNode, isTaskNode } from '@/types/process'
import { analyzeProcessConformance } from './conformance'
import { normalizeLayout } from './layout'
import { collectImportDiagnostics } from './diagnostics'

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

/** Сколько фигур на странице draw.io: пустые страницы пропускаем. */
function modelVertexCount(modelXml: string): number {
  const doc = new DOMParser().parseFromString(modelXml, 'text/xml')
  if (doc.querySelector('parsererror')) return 0
  return Array.from(doc.querySelectorAll('mxCell')).filter(
    (c) => c.getAttribute('vertex') === '1' && !['0', '1'].includes(c.getAttribute('id') ?? ''),
  ).length
}

/**
 * XML одной карты процесса + признак «это BPMN 2.0, а не draw.io».
 *
 * Порядок проверок важен: файл `.drawio` — это `<mxfile>` со страницами, и
 * внутри него тоже встречается подстрока `<mxGraphModel`. Если сначала искать
 * модель, многостраничный файл разбирается как его первая страница случайно,
 * а сжатый — попадает в другую ветку и разбирается иначе.
 *
 * Страницы НЕ объединяются: в картах банка это варианты одного процесса
 * (AS-IS, AS-IS с изменениями, TO-BE). Склейка накладывала их друг на друга —
 * получалась одна нечитаемая схема с дублями шагов и пересечениями связей.
 * Берём первую непустую страницу — ту же, что draw.io открывает по умолчанию.
 */
/**
 * Имя импортированной страницы и имена пропущенных.
 *
 * Нужно, чтобы сотрудник увидел: файл многостраничный, а в работу взята одна
 * страница. Молча брать первую нельзя — на второй обычно лежит TO-BE, и её
 * отсутствие выглядит как потеря данных.
 */
export function pageReport(text: string): { used: string; skipped: string[] } {
  const trimmed = (text || '').trim()
  if (!trimmed.includes('<mxfile') && !trimmed.includes('<diagram')) return { used: '', skipped: [] }
  const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
  if (doc.querySelector('parsererror')) return { used: '', skipped: [] }
  const diagrams = Array.from(doc.querySelectorAll('diagram'))
  if (diagrams.length < 2) {
    return { used: diagrams[0]?.getAttribute('name') ?? '', skipped: [] }
  }
  const names = diagrams.map((d, i) => d.getAttribute('name') || `Страница ${i + 1}`)
  const usedIndex = Math.max(
    0,
    diagrams.findIndex((d) =>
      Array.from(d.querySelectorAll('mxCell')).some(
        (c) => c.getAttribute('vertex') === '1' && !['0', '1'].includes(c.getAttribute('id') ?? ''),
      ),
    ),
  )
  return { used: names[usedIndex], skipped: names.filter((_, i) => i !== usedIndex) }
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

  // 2. mxfile container — страницы диаграммы
  if (trimmed.includes('<mxfile') || trimmed.includes('<diagram')) {
    const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
    const parserError = doc.querySelector('parsererror')
    if (parserError) {
      throw new Error(`Ошибка XML: ${parserError.textContent?.slice(0, 80)}`)
    }

    const diagrams = Array.from(doc.querySelectorAll('diagram')) as Element[]
    if (diagrams.length > 0) {
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
        if (!innerText) continue
        if (innerText.includes('<mxGraphModel')) {
          models.push(innerText)
        } else {
          try {
            models.push(await inflateDiagram(innerText))
          } catch {
            // пропускаем битую страницу
          }
        }
      }
      if (models.length === 0) throw new Error('Не удалось извлечь ни одной диаграммы')
      return { xml: models.find((m) => modelVertexCount(m) > 0) ?? models[0], isBpmn: false }
    }
  }

  // 3. Direct mxGraphModel
  if (trimmed.includes('<mxGraphModel')) {
    const doc = new DOMParser().parseFromString(trimmed, 'text/xml')
    const model = doc.querySelector('mxGraphModel')
    if (model) {
      return { xml: new XMLSerializer().serializeToString(model), isBpmn: false }
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

/** «5 min», «0.5 daq», «120 мин», «Kutish vaqti 30 min» — длительность в подписи фигуры. */
const DURATION_RE = /(\d+(?:[.,]\d+)?)\s*(?:min|daq|мин)[a-zа-я]*/i
/** Подпись, помечающая время ОЖИДАНИЯ (WT), а не выполнения (ST). */
const WAIT_RE = /kutish\s+vaqti|время\s+ожидания|wait/i

export function durationMinutes(text: string | null | undefined): number | null {
  if (!text) return null
  const m = text.match(DURATION_RE)
  if (!m) return null
  const value = parseFloat(m[1].replace(',', '.'))
  return Number.isFinite(value) ? value : null
}

export function isWaitLabel(text: string | null | undefined): boolean {
  return !!text && WAIT_RE.test(text)
}

/**
 * Максимальное расстояние от бейджа длительности до центра шага, px.
 * На картах SQB бейдж лежит в 56-90 px от своего шага, а до соседнего — не
 * ближе ~190 px, поэтому порог однозначен.
 */
const BADGE_ATTACH_RADIUS = 130

/** Точка в абсолютных координатах карты. */
type Pt = { x: number; y: number }

/** Насколько близко свободный конец линии должен подойти к фигуре, px. */
const FREE_ENDPOINT_SNAP = 30

function distanceToBox(px: number, py: number, node: ProcessNode): number {
  const g = node.geometry
  const dx = Math.max(g.x - px, 0, px - (g.x + g.width))
  const dy = Math.max(g.y - py, 0, py - (g.y + g.height))
  return Math.sqrt(dx * dx + dy * dy)
}

/**
 * Фигура под свободным концом линии draw.io.
 *
 * В draw.io конец связи может быть не привязан к фигуре, а задан точкой
 * (`mxPoint as="sourcePoint"`). Редактор всё равно рисует линию, а мы раньше
 * выбрасывали её целиком — на карте пропадали и потоки, и пунктирные
 * ассоциации к хранилищам данных.
 */
function resolveFreeEndpoint(point: Pt | undefined, candidates: ProcessNode[]): string | undefined {
  if (!point || candidates.length === 0) return undefined
  let bestId: string | undefined
  let bestDist = FREE_ENDPOINT_SNAP
  let bestArea = Infinity
  for (const node of candidates) {
    const dist = distanceToBox(point.x, point.y, node)
    if (dist > FREE_ENDPOINT_SNAP) continue
    // При равном расстоянии выигрывает меньшая фигура: точка внутри шага
    // лежит и внутри его дорожки, но связать её надо с шагом.
    const area = node.geometry.width * node.geometry.height
    if (dist < bestDist || (dist === bestDist && area < bestArea)) {
      bestDist = dist
      bestArea = area
      bestId = node.id
    }
  }
  return bestId
}

/** Типы, которым BPMN разрешает быть концом messageFlow (InteractionNode). */
const INTERACTION_TYPES: NodeType[] = [
  'task', 'userTask', 'serviceTask', 'subProcess',
  'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
]

/** Вид соединения: связь с артефактом по BPMN не может быть потоком управления. */
function edgeKind(
  sourceType?: NodeType,
  targetType?: NodeType,
  dashed?: boolean,
  laneEnd?: { external: boolean; otherType?: NodeType },
): EdgeKind {
  if (laneEnd) {
    // Пунктир «шаг банка ↔ полоса клиента» — это обмен сообщениями с внешним
    // участником, а не оформление: в выгрузке он обязан остаться, иначе с
    // карты пропадают точки контакта с клиентом. Линия, упирающаяся в дорожку
    // с шагами, — по-прежнему разделитель этапов.
    if (laneEnd.external && laneEnd.otherType && INTERACTION_TYPES.includes(laneEnd.otherType))
      return 'messageFlow'
    return 'annotationLine'
  }
  if ((sourceType && isArtifactNode(sourceType)) || (targetType && isArtifactNode(targetType)))
    return 'association'
  if (dashed && (sourceType === 'intermediateMessageEvent' || targetType === 'intermediateMessageEvent'))
    return 'messageFlow'
  return 'sequenceFlow'
}

/**
 * Убирает безымянные дорожки-баннеры и даёт позиционные имена остальным.
 * Настоящая безымянная дорожка содержит шаги; пустая рамка без подписи —
 * это оформление схемы, а не зона ответственности.
 */
function nameAndPruneLanes(lanes: ProcessNode[], flowNodes: ProcessNode[]): void {
  const populated = new Set(flowNodes.map((n) => n.laneId).filter(Boolean) as string[])
  const doomed = new Set(
    lanes.filter((l) => !(l.name || '').trim() && !populated.has(l.id)).map((l) => l.id),
  )
  if (doomed.size > 0) {
    const kept = lanes.filter((l) => !doomed.has(l.id))
    lanes.length = 0
    lanes.push(...kept)
    for (const node of flowNodes) {
      if (node.laneId && doomed.has(node.laneId)) {
        node.laneId = undefined
        node.laneName = undefined
      }
    }
  }
  const ordered = [...lanes].sort((a, b) => a.geometry.y - b.geometry.y || a.geometry.x - b.geometry.x)
  ordered.forEach((lane, index) => {
    if (!(lane.name || '').trim()) {
      lane.name = `Дорожка ${index + 1}`
      lane.role = lane.name
    }
  })
}

/**
 * Переносит ST/WT из фигур-таймеров в ближайший шаг процесса (4-ILOVA).
 * Возвращает шаги, которым время проставил реальный бейдж с карты: остальным
 * оно досталось от эвристики по категории, и об этом надо сказать аналитику.
 */
function applyDurationBadges(
  flowNodes: ProcessNode[],
  badges: { x: number; y: number; minutes: number; isWait: boolean }[],
): Set<string> {
  const stSeen = new Set<string>()
  const tasks = flowNodes.filter((n) => isTaskNode(n.type))
  if (tasks.length === 0 || badges.length === 0) return stSeen
  for (const badge of badges) {
    let best: ProcessNode | null = null
    let bestDist = BADGE_ATTACH_RADIUS
    for (const t of tasks) {
      const dx = badge.x - (t.geometry.x + t.geometry.width / 2)
      const dy = badge.y - (t.geometry.y + t.geometry.height / 2)
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < bestDist) {
        bestDist = dist
        best = t
      }
    }
    if (!best) continue
    const value = Math.max(1, Math.round(badge.minutes))
    if (badge.isWait) {
      best.waitMinutes = (best.waitMinutes || 0) + value
    } else if (stSeen.has(best.id)) {
      best.slaMinutes = (best.slaMinutes || 0) + value
    } else {
      best.slaMinutes = value
      stSeen.add(best.id)
    }
    if (best.category !== 'rpa_bot') best.costPerExecution = (best.slaMinutes || 0) * 1932
  }
  return stSeen
}

/** Системы и документы шага — из реальных ассоциаций карты, а не из эвристик. */
function resolveArtifactLinks(flowNodes: ProcessNode[], edges: ProcessEdge[]): void {
  const byId = new Map(flowNodes.map((n) => [n.id, n]))
  const systems = new Map<string, string[]>()
  const inputs = new Map<string, string[]>()
  const outputs = new Map<string, string[]>()

  const add = (bucket: Map<string, string[]>, key: string, value: string) => {
    if (!value) return
    const items = bucket.get(key) ?? []
    if (!items.includes(value)) items.push(value)
    bucket.set(key, items)
  }

  for (const e of edges) {
    if (e.kind !== 'association') continue
    const src = byId.get(e.sourceId || '')
    const tgt = byId.get(e.targetId || '')
    if (!src || !tgt) continue
    for (const [artifact, step, incomingDir] of [[src, tgt, true], [tgt, src, false]] as const) {
      if (!isTaskNode(step.type)) continue
      if (artifact.type === 'dataStore') add(systems, step.id, artifact.name)
      else if (artifact.type === 'dataObject') add(incomingDir ? inputs : outputs, step.id, artifact.name)
    }
  }

  for (const node of flowNodes) {
    const linked = systems.get(node.id)
    if (linked?.length) node.system = linked.join(', ')
    if (inputs.get(node.id)?.length) node.inputArtifacts = inputs.get(node.id)
    if (outputs.get(node.id)?.length) node.outputArtifacts = outputs.get(node.id)
  }
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

/** Короткая подпись рядом с фигурой — это бейдж, а не примечание к процессу. */
const TEXT_NOTE_MIN_LEN = 12

/**
 * Обрамлённая текстовая врезка draw.io — примечание к процессу.
 *
 * На картах SQB перечень требуемых документов оформлен как `text`-фигура с
 * рамкой (`strokeColor` задан). Такие врезки раньше отбрасывались вместе с
 * подписями-накладками, и содержательный текст пропадал из выгрузки. Заголовок
 * схемы и подписи связей рамки не имеют (`strokeColor=none`) — они по-прежнему
 * остаются оформлением.
 */
function isTextNote(style: string, label: string): boolean {
  const s = (style || '').toLowerCase()
  if (!s.includes('text;') || s.includes('swimlane') || s.includes('edgelabel')) return false
  const text = (label || '').trim()
  if (text.length < TEXT_NOTE_MIN_LEN || isNonTaskLabel(text)) return false
  if (durationMinutes(text) !== null) return false
  const stroke = parseStyleMap(s).strokecolor ?? 'none'
  return stroke !== 'none' && stroke !== ''
}

function formatMinutes(minutes: number): string {
  return Number.isInteger(minutes) ? String(minutes) : String(minutes)
}

/**
 * Осмысленное имя фигуры без подписи — никогда не идентификатор ячейки.
 *
 * Раньше на карту и в выгрузку попадали заголовки вида «Операция
 * G9DXMv3N_W9X6-3aXuzq-1»: у промежуточного таймера вся подпись — это
 * длительность («10 min»), а её снимает нормализация имени шага. Возвращаем
 * длительность в имя события и подписываем остальные фигуры по их роли.
 */
export function fallbackNodeName(type: NodeType, code?: string | null, rawText = ''): string {
  if (type === 'intermediateTimerEvent') {
    const minutes = durationMinutes(rawText)
    return minutes !== null ? `Ожидание ${formatMinutes(minutes)} мин` : 'Ожидание'
  }
  if (type === 'intermediateMessageEvent') return 'Событие-сообщение'
  if (type === 'startEvent') return 'Старт'
  if (type === 'endEvent') return 'Завершение'
  if (type.includes('Gateway')) return 'Условие'
  if (type === 'dataStore') return 'Информационная система'
  if (type === 'dataObject') return 'Документ'
  if (type === 'textAnnotation') return 'Примечание'
  if (type === 'subProcess') return code ? `Подпроцесс ${code}` : 'Подпроцесс'
  return code ? `Операция ${code}` : 'Операция'
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
  const smap = parseStyleMap(style)
  const shape = (smap.shape || '').toLowerCase()

  if (s.includes('swimlane') || s.includes('pool;') || s.includes('shape=pool'))
    return 'lane'

  // ── Артефакты (2-ILOVA: Artefaktlar) ──────────────────────────────────────
  // Хранилище данных: IABS, EHA, EDO, Korporativ pochta.
  if (s.includes('shape=datastore') || s.includes('mxgraph.bpmn.datastore') || s.includes('kind=datastore'))
    return 'dataStore'
  // Объект данных: Dalolatnoma, Yig'ma jild, Hujjatlar ro'yxati.
  if (s.includes('mxgraph.bpmn.data2') || shape.endsWith('bpmn.data') || s.includes('shape=dataobject'))
    return 'dataObject'
  if (s.includes('shape=note') || s.includes('mxgraph.bpmn.annotation') || s.includes('shape=mxgraph.flowchart.annotation'))
    return 'textAnnotation'
  if (isTextNote(style, label)) return 'textAnnotation'

  if (s.includes('mxgraph.bpmn.gateway') || shape.endsWith('gateway2') || smap.gwtype) {
    const gw = (smap.gwtype || smap.symbol || '').toLowerCase()
    if (gw === 'parallel' || gw === 'and' || gw === 'complex' || s.includes('outline=plus') || s.includes('parallel'))
      return 'parallelGateway'
    if (gw === 'inclusive' || gw === 'or' || s.includes('inclusive') || s.includes('outline=circle'))
      return 'inclusiveGateway'
    return 'exclusiveGateway'
  }

  if (s.includes('mxgraph.bpmn.event') || shape.endsWith('.event')) {
    const outline = (smap.outline || '').toLowerCase()
    const symbol = (smap.symbol || '').toLowerCase()
    // Таймер внутри потока — «Kutish vaqti»: промежуточное событие-обработчик.
    // Одиночные бейджи длительности сюда не доходят: их снимает
    // collectDurationBadges() и переносит в ST/WT ближайшего шага.
    if (symbol === 'timer' && hasIncoming && hasOutgoing) return 'intermediateTimerEvent'
    if (symbol === 'message' && hasIncoming && hasOutgoing) return 'intermediateMessageEvent'
    if (outline === 'end' || outline === 'terminate' || s.includes('outline=end') || s.includes('outline=double'))
      return 'endEvent'
    if (outline === 'catching' && hasIncoming && hasOutgoing)
      return symbol === 'timer' ? 'intermediateTimerEvent' : 'intermediateMessageEvent'
    if (!hasIncoming && hasOutgoing) return 'startEvent'
    if (hasIncoming && !hasOutgoing) return 'endEvent'
    return 'startEvent'
  }

  if (s.includes('mxgraph.bpmn.task')) {
    const marker = (smap.taskmarker || smap.symbol || '').toLowerCase()
    if (marker === 'sub' || marker === 'subprocess' || s.includes('issubprocess=1') || s.includes('mxgraph.bpmn.transaction'))
      return 'subProcess'
    if (
      marker === 'service' ||
      marker === 'script' ||
      marker === 'send' ||
      marker === 'receive' ||
      marker === 'businessrule' ||
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

export function classifyCategory(type: NodeType, name: string, style: string): StepCategory {
  const lower = (name + ' ' + style).toLowerCase()
  if (isArtifactNode(type)) return type === 'dataStore' ? 'api_service' : 'manual'
  if (type === 'startEvent' || type === 'endEvent' || type === 'intermediateTimerEvent' || type === 'intermediateMessageEvent')
    return 'notification'
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

export function detectSystem(name: string, laneName: string): string {
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
  if (isArtifactNode(type)) return 0
  if (type === 'startEvent' || type === 'endEvent') return 5

  if (type === 'intermediateTimerEvent' || type === 'intermediateMessageEvent') {
    // Событие ожидания: длительность — это и есть его подпись.
    const minutes = durationMinutes(rawText)
    return minutes ? Math.max(1, Math.round(minutes)) : 30
  }

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
    const condEl = elementsByLocalName(f, ['conditionExpression'])[0]
    const condText = (condEl?.textContent || '').trim()
    const edgeName = f.getAttribute('name') || condText || ''
    edges.push({
      id: f.getAttribute('id') ?? `edge_${crypto.randomUUID()}`,
      name: edgeName,
      sourceId,
      targetId,
      condition: condText || edgeName || undefined,
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
    validation: collectImportDiagnostics(nodes, lanes, edges),
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
  const durationBadges: { x: number; y: number; minutes: number; isWait: boolean }[] = []

  // ── Бейджи ST/WT ─────────────────────────────────────────────────────────
  // По Методике (4-ILOVA) время операции проставляется отдельной мелкой
  // фигурой-таймером рядом с шагом, а не в подписи самого шага. Такие фигуры
  // не соединены рёбрами: снимаем их с карты и переносим в ST/WT шага.
  for (const c of cells) {
    if (c.getAttribute('vertex') !== '1') continue
    const id = c.getAttribute('id') ?? ''
    if (!id || incoming.has(id) || outgoing.has(id)) continue
    const styleLower = getStyle(c).toLowerCase()
    if (styleLower.includes('swimlane')) continue
    const text = cleanLabel(c.getAttribute('value'))
    const minutes = durationMinutes(text)
    if (minutes == null) continue
    const residual = text.replace(DURATION_RE, '').replace(/^[\s.,:;-]+|[\s.,:;-]+$/g, '')
    const isTimerShape = styleLower.includes('symbol=timer') || styleLower.includes('shape=mxgraph.bpmn.timer')
    if (!isTimerShape && (styleLower.includes('mxgraph.bpmn') || residual.length > 32)) continue
    const geoBadge = c.querySelector(':scope > mxGeometry') as Element | null
    if (!geoBadge) continue
    const originBadge = parentOrigin(c.getAttribute('parent') ?? undefined, cellMap, originCache)
    durationBadges.push({
      x: Number(geoBadge.getAttribute('x') ?? 0) + originBadge.x + Number(geoBadge.getAttribute('width') ?? 20) / 2,
      y: Number(geoBadge.getAttribute('y') ?? 0) + originBadge.y + Number(geoBadge.getAttribute('height') ?? 20) / 2,
      minutes,
      isWait: isWaitLabel(text),
    })
    ignoreCellIds.add(id)
  }

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

    // Condition 2.5: обрамлённая текстовая врезка — примечание, а не оформление.
    if (isTextNote(style, cleaned)) return

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
    // Дорожки исключены: у безымянной дорожки подпись пустая, но это не мусор.
    if (style.includes('swimlane') || style.includes('shape=pool')) return
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

  /** Шаги, чьё время взято с карты, а не из эвристики по категории. */
  const timedStepIds = new Set<string>()

  for (const cell of rawVertices) {
    const id = cell.getAttribute('id') ?? `node_${crypto.randomUUID()}`
    const style = getStyle(cell)
    const rawValue = cell.getAttribute('value')
    let rawCleaned = cleanLabel(rawValue)

    // labelMap хранит подписи детей (условия «Ha»/«Yo'q») — для безымянной
    // дорожки они бы стали её именем.
    if (!rawCleaned && labelMap.has(id) && !style.toLowerCase().includes('swimlane')) {
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

    const rawText = `${rawValue || ''} ${rawCleaned}`
    const slaMin = extractSlaMinutes(rawText, category, type)
    if (durationMinutes(rawText) !== null) timedStepIds.add(id)

    // Clean human-friendly name (strip [PIX RPA], numbers, minutes)
    let cleanName = rawCleaned
      .replace(/^\[.*?\]\s*/gi, '')
      .replace(/^STEP[-_ ]?\d+[:\s-]*/gi, '')
      .replace(/^[0-9]+[.)]\s*/gi, '')
      .replace(/\b\d+(?:\.\d+)?\s*(?:min|daq|минут|мин)\b.*$/gi, '')
      .trim()

    if (!cleanName && type === 'lane') {
      // безымянная дорожка: имя присвоим позиционно после разбора
    } else if (!cleanName) {
      cleanName = fallbackNodeName(type, code, `${rawValue || ''} ${rawCleaned}`)
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

  nameAndPruneLanes(lanes, flowNodes)

  // Geometry-based lane assignment fallback — учитываем ширину заголовка дорожки (44px)
  const laneById = new Map(lanes.map((l) => [l.id, l]))
  /** Дорожка, внутри которой лежит центр фигуры. */
  const laneUnder = (node: ProcessNode): ProcessNode | undefined => {
    const cx = node.geometry.x + node.geometry.width / 2
    const cy = node.geometry.y + node.geometry.height / 2
    return lanes.find(
      (l) =>
        l.geometry.x <= cx && cx <= l.geometry.x + l.geometry.width &&
        l.geometry.y <= cy && cy < l.geometry.y + l.geometry.height,
    )
  }

  for (const n of flowNodes) {
    // Родитель ячейки в draw.io — не то же самое, что дорожка на рисунке:
    // фигуру можно утащить из дорожки, и редактор сохранит прежнего родителя.
    // Исполнителя шага определяем по тому, где фигура лежит на самом деле,
    // иначе роль в регламенте берётся от чужого подразделения.
    const declared = n.laneId ? laneById.get(n.laneId) : undefined
    const actual = laneUnder(n)
    if (actual && actual.id !== declared?.id) {
      n.laneId = actual.id
      n.role = undefined
    } else if (declared && !actual) {
      n.laneId = undefined
      n.laneName = undefined
      n.role = undefined
    }

    const parentLane = n.laneId ? laneById.get(n.laneId) : undefined
    if (parentLane) {
      n.laneName = parentLane.name
      n.role = n.role || parentLane.name
    }
    n.system = detectSystem(n.name, n.laneName || '')
  }

  const validNodeIdSet = new Set(flowNodes.map((n) => n.id))
  const validLaneIdSet = new Set(lanes.map((l) => l.id))
  const typeById = new Map<string, NodeType>(flowNodes.map((n) => [n.id, n.type]))
  // Дорожка без единого шага — это внешний участник (клиент, госорган):
  // аналитик отводит ему полосу и тянет к ней пунктир от шагов банка.
  const populatedLaneIds = new Set(flowNodes.map((n) => n.laneId).filter(Boolean) as string[])
  const externalLaneIds = new Set([...validLaneIdSet].filter((id) => !populatedLaneIds.has(id)))

  const snapTargets = [...flowNodes, ...lanes]
  const isKnown = (id?: string | null): boolean =>
    !!id && (validNodeIdSet.has(id) || validLaneIdSet.has(id))

  /** Свободные концы связи в абсолютных координатах карты. */
  const freeEndsOf = (cell: Element): { sourcePoint?: Pt; targetPoint?: Pt } => {
    const geo = cell.querySelector(':scope > mxGeometry')
    if (!geo) return {}
    const origin = parentOrigin(cell.getAttribute('parent') ?? undefined, cellMap, originCache)
    const out: { sourcePoint?: Pt; targetPoint?: Pt } = {}
    for (const mx of Array.from(geo.querySelectorAll(':scope > mxPoint'))) {
      const role = mx.getAttribute('as')
      if (role !== 'sourcePoint' && role !== 'targetPoint') continue
      out[role] = {
        x: Number(mx.getAttribute('x') ?? 0) + origin.x,
        y: Number(mx.getAttribute('y') ?? 0) + origin.y,
      }
    }
    return out
  }

  const resolvedEnds = new Map<Element, { s?: string; t?: string; free: { sourcePoint?: Pt; targetPoint?: Pt } }>()

  const edges: ProcessEdge[] = rawEdges
    .filter((cell) => {
      const free = freeEndsOf(cell)
      let s: string | undefined = cell.getAttribute('source') ?? undefined
      let t: string | undefined = cell.getAttribute('target') ?? undefined
      if (!s) s = resolveFreeEndpoint(free.sourcePoint, snapTargets)
      if (!t) t = resolveFreeEndpoint(free.targetPoint, snapTargets)
      const sOk = isKnown(s)
      const tOk = isKnown(t)
      // Линия без единой опоры на фигуру — мусор; с одной опорой — оформление.
      if (!sOk && !tOk) return false
      if ((!sOk || !tOk) && !free.sourcePoint && !free.targetPoint) return false
      resolvedEnds.set(cell, { s: sOk ? s : undefined, t: tOk ? t : undefined, free })
      return true
    })
    .map((cell) => {
      const ends = resolvedEnds.get(cell)!
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

      const srcId = ends.s
      const tgtId = ends.t
      const isAnnotationLine = !srcId || !tgtId
      const laneIsSource = !!srcId && validLaneIdSet.has(srcId)
      const laneIsTarget = !!tgtId && validLaneIdSet.has(tgtId)
      const laneEnd = laneIsSource || laneIsTarget
        ? {
            external: externalLaneIds.has((laneIsSource ? srcId : tgtId) as string),
            otherType: typeById.get((laneIsSource ? tgtId : srcId) ?? ''),
          }
        : undefined

      return {
        id: edgeId,
        name: edgeName,
        kind: isAnnotationLine
          ? ('annotationLine' as EdgeKind)
          : edgeKind(typeById.get(srcId ?? ''), typeById.get(tgtId ?? ''), dashed, laneEnd),
        sourceId: srcId,
        targetId: tgtId,
        sourcePoint: ends.free.sourcePoint
          ? { x: Math.round(ends.free.sourcePoint.x), y: Math.round(ends.free.sourcePoint.y) }
          : undefined,
        targetPoint: ends.free.targetPoint
          ? { x: Math.round(ends.free.targetPoint.x), y: Math.round(ends.free.targetPoint.y) }
          : undefined,
        condition: edgeName || undefined,
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
        best.condition = lbl.text
      }
    }
  }

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

  for (const id of applyDurationBadges(flowNodes, durationBadges)) timedStepIds.add(id)
  resolveArtifactLinks(flowNodes, edges)
  // Геометрию правим до сбора замечаний: часть из них про размеры фигур.
  const layoutReport = normalizeLayout(flowNodes, lanes)

  const pages = pageReport(text)
  const validation = collectImportDiagnostics(flowNodes, lanes, edges, {
    pagesSkipped: pages.skipped,
    pageUsed: pages.used,
    timedStepIds,
    layoutReport,
  })

  const cleanTitle = diagramName || fileName.replace(/\.(drawio|xml)$/i, '')
  const taskNodes = flowNodes.filter((n) => isTaskNode(n.type))
  const totalHours =
    roundHours(taskNodes.reduce((acc, n) => acc + (n.slaMinutes || 0) + (n.waitMinutes || 0), 0) / 60) || 8

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

