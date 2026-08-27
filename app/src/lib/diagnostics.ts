import type { ProcessEdge, ProcessNode, ProcessValidation } from '@/types/process'
import { ARTIFACT_NODE_TYPES, TASK_NODE_TYPES } from '@/types/process'

/**
 * Замечания к импортированной карте — то, что показывается сотруднику.
 *
 * Клиентский двойник `backend/app/services/diagnostics.py`; используется, когда
 * FastAPI недоступен и карту разбирает браузер.
 *
 * Импорт draw.io почти всегда что-то домысливает: подписи, недостающие ветки,
 * время шага, привязку линии к фигуре. Молча делать это нельзя — аналитик
 * должен увидеть, где карта неполна и что платформа достроила за него.
 */

const GATEWAY_TYPES = ['exclusiveGateway', 'parallelGateway', 'inclusiveGateway']

/** Имена, которые платформа придумывает сама (см. `fallbackNodeName`). */
const GENERATED_NAME_PREFIXES = [
  'Операция ', 'Условие', 'Ожидание', 'Событие-сообщение', 'Старт',
  'Завершение', 'Подпроцесс', 'Примечание', 'Информационная система', 'Документ',
]

/** Сколько однотипных замечаний показываем поимённо, прежде чем свернуть в счёт. */
const MAX_PER_CODE = 8

export interface ImportDiagnosticsInput {
  pagesSkipped?: string[]
  pageUsed?: string
  /** Шаги, чьё время взято с карты, а не из эвристики по категории. */
  timedStepIds?: Set<string>
  layoutReport?: { squared: string[]; fitted: string[]; moved: string[] }
}

function isGeneratedName(name: string): boolean {
  return GENERATED_NAME_PREFIXES.some((prefix) => (name || '').startsWith(prefix))
}

export function collectImportDiagnostics(
  flowNodes: ProcessNode[],
  lanes: ProcessNode[],
  edges: ProcessEdge[],
  input: ImportDiagnosticsInput = {},
): ProcessValidation[] {
  const issues: ProcessValidation[] = []
  const add = (
    level: ProcessValidation['level'],
    code: string,
    message: string,
    hint?: string,
    node?: ProcessNode,
  ) => {
    issues.push({ level, code, message, hint, nodeId: node?.id, nodeName: node?.name })
  }
  const cap = (code: string, total: number, tail: (n: number) => string) => {
    if (total > MAX_PER_CODE) add('info', `${code}_more`, tail(total - MAX_PER_CODE))
  }

  const steps = flowNodes.filter((n) => (TASK_NODE_TYPES as readonly string[]).includes(n.type))
  const sequence = edges.filter((e) => (e.kind ?? 'sequenceFlow') === 'sequenceFlow')
  const incoming = new Map<string, ProcessEdge[]>()
  const outgoing = new Map<string, ProcessEdge[]>()
  for (const edge of sequence) {
    if (edge.targetId) incoming.set(edge.targetId, [...(incoming.get(edge.targetId) ?? []), edge])
    if (edge.sourceId) outgoing.set(edge.sourceId, [...(outgoing.get(edge.sourceId) ?? []), edge])
  }
  const isArtifact = (n: ProcessNode) => (ARTIFACT_NODE_TYPES as readonly string[]).includes(n.type)

  // ── Страницы файла ────────────────────────────────────────────────────────
  const skipped = input.pagesSkipped ?? []
  if (skipped.length) {
    add(
      'info', 'pages_skipped',
      `В файле ${skipped.length + 1} страницы, импортирована первая — «${input.pageUsed ?? ''}».`,
      `Остальные страницы (${skipped.map((p) => `«${p}»`).join(', ')}) загрузите отдельными ` +
        'файлами: это разные версии процесса, и объединять их в одну карту нельзя.',
    )
  }

  // ── Целостность потока ────────────────────────────────────────────────────
  const starts = flowNodes.filter((n) => n.type === 'startEvent')
  const ends = flowNodes.filter((n) => n.type === 'endEvent')
  if (!starts.length) {
    add('error', 'no_start_event', 'На карте нет стартового события.',
      'Добавьте кружок начала процесса — без него выгрузка в BPMN невалидна.')
  } else if (starts.length > 1) {
    add('warning', 'many_start_events', `Стартовых событий: ${starts.length}.`,
      'В одном процессе должен быть один вход; лишние обычно оказываются промежуточными событиями.')
  }
  if (!ends.length) {
    add('error', 'no_end_event', 'На карте нет события завершения процесса.',
      'Добавьте кружок конца процесса хотя бы для успешного сценария.')
  }

  let deadEnds = 0
  for (const node of flowNodes) {
    if (isArtifact(node) || node.type === 'startEvent') continue
    if (!(incoming.get(node.id) ?? []).length) {
      deadEnds += 1
      if (deadEnds <= MAX_PER_CODE) {
        add('error', 'no_incoming', `В шаг «${node.name}» не входит ни одна связь.`,
          'Шаг недостижим: соедините его с предыдущим шагом.', node)
      }
    }
  }
  cap('no_incoming', deadEnds, (n) => `Ещё ${n} шагов без входящих связей.`)

  let hanging = 0
  for (const node of flowNodes) {
    if (isArtifact(node) || node.type === 'endEvent') continue
    if (!(outgoing.get(node.id) ?? []).length) {
      hanging += 1
      if (hanging <= MAX_PER_CODE) {
        add('warning', 'no_outgoing', `Из шага «${node.name}» не выходит ни одна связь.`,
          'Процесс обрывается: доведите ветку до следующего шага или до события завершения.', node)
      }
    }
  }
  cap('no_outgoing', hanging, (n) => `Ещё ${n} шагов без исходящих связей.`)

  // ── Шлюзы ─────────────────────────────────────────────────────────────────
  for (const node of flowNodes) {
    if (!GATEWAY_TYPES.includes(node.type)) continue
    const branches = outgoing.get(node.id) ?? []
    if (branches.length < 2 && (incoming.get(node.id) ?? []).length < 2) {
      add('warning', 'gateway_single_branch', `У шлюза «${node.name}» только одна ветка.`,
        'Шлюз без развилки не нужен: либо добавьте вторую ветку, либо уберите шлюз с карты.', node)
    }
    const unnamed = branches.filter((e) => !(e.name || e.condition || '').trim())
    if (branches.length > 1 && unnamed.length) {
      add('error', 'gateway_branch_unlabeled',
        `У шлюза «${node.name}» ${unnamed.length} из ${branches.length} веток без подписи условия.`,
        'Подпишите ветки («Ha» / «Yo\'q»): без условия шаг регламента нельзя автоматизировать в PIX BPM.',
        node)
    }
  }

  // ── Подписи фигур ─────────────────────────────────────────────────────────
  // У таймера подпись — это его длительность, и «Ожидание 10 мин» дефектом не
  // является. Спрашиваем только там, где имя несёт смысл.
  const namedTypes = [...TASK_NODE_TYPES, ...GATEWAY_TYPES, 'startEvent', 'endEvent'] as string[]
  const generated = flowNodes.filter((n) => namedTypes.includes(n.type) && isGeneratedName(n.name))
  for (const node of generated.slice(0, MAX_PER_CODE)) {
    add('warning', 'generated_name', `Фигура без подписи получила имя «${node.name}».`,
      'Назовите фигуру в draw.io: сгенерированное имя попадёт в регламент и в выгрузку как есть.',
      node)
  }
  cap('generated_name', generated.length, (n) => `Ещё ${n} фигур без подписи.`)

  const seen = new Map<string, number>()
  for (const step of steps) seen.set(step.name, (seen.get(step.name) ?? 0) + 1)
  for (const [name, count] of seen) {
    if (count > 1 && name) {
      add('info', 'duplicate_step_name', `Шаг «${name}» встречается на карте несколько раз.`,
        'В регламенте такие строки не отличить друг от друга — уточните формулировки.')
    }
  }

  // ── Время выполнения ──────────────────────────────────────────────────────
  const measured = input.timedStepIds ?? new Set<string>()
  const noTime = steps.filter((n) => !measured.has(n.id))
  for (const node of noTime.slice(0, MAX_PER_CODE)) {
    add('warning', 'no_step_time',
      `У шага «${node.name}» на карте нет времени выполнения — подставлено ${node.slaMinutes} мин по типу операции.`,
      'Поставьте рядом с шагом бейдж-таймер («5 min»): по нему считается SLA процесса и экономия от роботизации.',
      node)
  }
  cap('no_step_time', noTime.length, (n) => `Ещё ${n} шагов без времени выполнения.`)

  // ── Дорожки и роли ────────────────────────────────────────────────────────
  const homeless = flowNodes.filter((n) => !n.laneId && !isArtifact(n))
  for (const node of homeless.slice(0, MAX_PER_CODE)) {
    add('warning', 'step_without_lane', `Шаг «${node.name}» не лежит ни в одной дорожке.`,
      'Без дорожки у шага нет исполнителя: перетащите фигуру внутрь дорожки подразделения.', node)
  }
  cap('step_without_lane', homeless.length, (n) => `Ещё ${n} шагов вне дорожек.`)
  if (!lanes.length) {
    add('warning', 'no_lanes', 'На карте нет дорожек подразделений.',
      'Без дорожек в регламенте не заполнится колонка «Исполнитель».')
  }

  // ── Связи и артефакты ─────────────────────────────────────────────────────
  const decoration = edges.filter((e) => e.kind === 'annotationLine')
  if (decoration.length) {
    add('warning', 'decoration_line', `Линий, не опирающихся на фигуры: ${decoration.length}.`,
      'Такие линии остаются на холсте, но в BPMN и PIX не выгружаются — привяжите оба конца к фигурам.')
  }

  const lonely = flowNodes.filter(
    (n) => (n.type === 'dataStore' || n.type === 'dataObject') &&
      !edges.some((e) => e.kind === 'association' && (e.sourceId === n.id || e.targetId === n.id)),
  )
  for (const node of lonely.slice(0, MAX_PER_CODE)) {
    add('info', 'artifact_not_linked',
      `${node.type === 'dataStore' ? 'Система' : 'Документ'} «${node.name}» не связана ни с одним шагом.`,
      'Проведите пунктирную линию от неё к шагу — иначе в регламенте не заполнится колонка системы или документа.',
      node)
  }

  // ── Что платформа поправила сама ──────────────────────────────────────────
  const report = input.layoutReport
  if (report?.squared.length) {
    add('info', 'geometry_squared',
      `Событиям и шлюзам вернули квадратную рамку: ${report.squared.length} фигур.`,
      'В draw.io они нарисованы с фиксированной пропорцией, а импортёр BPMN растянул бы круг в эллипс вместе со значком таймера.')
  }
  if (report?.fitted.length) {
    add('info', 'geometry_fitted', `Фигуры расширены под подпись: ${report.fitted.length}.`,
      'В draw.io текст свободно выходит за рамку, а bpmn.io и Процессная студия обрезают его по фигуре.')
  }
  if (report?.moved.length) {
    add('info', 'geometry_moved',
      `Артефакты сдвинуты, чтобы не лежать поверх шагов: ${report.moved.length}.`,
      'На карте они перекрывали подпись шага.')
  }

  return issues
}
