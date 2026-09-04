import type { ProcessEdge, ProcessNode, ProcessValidation } from '@/types/process'
import { ARTIFACT_NODE_TYPES, NODE_TYPE_LABELS, TASK_NODE_TYPES } from '@/types/process'

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

/**
 * Устойчивый ключ строки отчёта: у замечаний нет собственных id, а UI должен
 * помнить, какое из них подсвечено на карте прямо сейчас.
 */
export function issueKey(issue: ProcessValidation, index: number): string {
  return `${issue.code ?? issue.level}-${index}`
}

/** Фигуры замечания: одиночное `nodeId` — та же группа, только из одной фигуры. */
export function issueNodeIds(issue: ProcessValidation): string[] {
  if (issue.nodeIds?.length) return issue.nodeIds
  return issue.nodeId ? [issue.nodeId] : []
}

const GATEWAY_TYPES = ['exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway']

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
  /** Фигуры, смысл которых импортёр не понял: описание -> подставленные узлы. */
  unsupportedShapes?: Map<string, ProcessNode[]>
  /** Иконки-украшения, снятые с карты. */
  skippedClipart?: string[]
  /** Развилки, вторую ветку которых платформа подписала сама. */
  completedBranches?: { gateway: ProcessNode; condition: string }[]
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
    nodeIds?: string[],
  ) => {
    // Одна фигура — тоже группа из одной: UI подсвечивает список целиком и не
    // должен знать, пришло замечание про шаг или про две сотни фигур.
    const ids = nodeIds?.length ? nodeIds : node ? [node.id] : []
    issues.push({
      level,
      code,
      message,
      hint,
      nodeId: node?.id ?? (ids.length === 1 ? ids[0] : undefined),
      nodeName: node?.name,
      nodeIds: ids.length ? ids : undefined,
    })
  }
  /**
   * Свернуть хвост однотипных замечаний в одну строку со счётчиком.
   * Свёрнутые фигуры остаются адресуемыми: строка «Ещё 12 шагов…» подсвечивает
   * на карте те самые двенадцать, иначе её невозможно раскрыть.
   */
  const cap = (code: string, total: number, tail: (n: number) => string, rest: ProcessNode[] = []) => {
    if (total > MAX_PER_CODE) {
      add('info', `${code}_more`, tail(total - MAX_PER_CODE), undefined, undefined, rest.map((n) => n.id))
    }
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
      'В одном процессе должен быть один вход; лишние обычно оказываются промежуточными событиями.',
      undefined, starts.map((n) => n.id))
  }
  if (!ends.length) {
    add('error', 'no_end_event', 'На карте нет события завершения процесса.',
      'Добавьте кружок конца процесса хотя бы для успешного сценария.')
  }

  // Фигура, у которой нет НИ входящих, НИ исходящих связей, — отдельный случай:
  // это не разрыв цепочки, а забытая на холсте фигура. Раньше о ней сообщалось
  // дважды («нет входящих» и «нет исходящих»), и было неясно, что соединять —
  // на самом деле её надо либо вписать в поток, либо убрать.
  const isolated = flowNodes.filter(
    (n) => !isArtifact(n) && !(incoming.get(n.id) ?? []).length && !(outgoing.get(n.id) ?? []).length,
  )
  const isolatedIds = new Set(isolated.map((n) => n.id))
  for (const node of isolated.slice(0, MAX_PER_CODE)) {
    add('error', 'isolated_node',
      `Фигура «${node.name}» не соединена ни с чем: ни входящих связей, ни исходящих.`,
      'Либо впишите её в поток — линия от предыдущего шага и линия к следующему, — ' +
        'либо уберите с карты: в BPMN и PIX такая фигура попадёт в регламент отдельной ' +
        'строкой, до которой процесс не доходит.',
      node)
  }
  cap('isolated_node', isolated.length, (n) => `Ещё ${n} фигур не соединены ни с чем.`,
    isolated.slice(MAX_PER_CODE))

  const deadEnds = flowNodes.filter(
    (n) => !isArtifact(n) && n.type !== 'startEvent' &&
      !(incoming.get(n.id) ?? []).length && !isolatedIds.has(n.id),
  )
  for (const node of deadEnds.slice(0, MAX_PER_CODE)) {
    add('error', 'no_incoming', `В шаг «${node.name}» не входит ни одна связь.`,
      'Шаг недостижим: соедините его с предыдущим шагом.', node)
  }
  cap('no_incoming', deadEnds.length, (n) => `Ещё ${n} шагов без входящих связей.`,
    deadEnds.slice(MAX_PER_CODE))

  // Несколько связей, входящих в один шаг, — «неявное слияние»: стандарт BPMN
  // его допускает, а Процессная студия считает ошибкой («У элемента должен быть
  // только один входящий поток управления»). Разойтись со студией молча нельзя,
  // но и чинить за аналитика тоже: слияние ветвей — решение о том, как устроен
  // процесс, а не оформление.
  const merges = flowNodes
    .filter((n) => (TASK_NODE_TYPES as readonly string[]).includes(n.type) &&
      (incoming.get(n.id) ?? []).length > 1)
    .sort((a, b) => (incoming.get(b.id) ?? []).length - (incoming.get(a.id) ?? []).length)
  for (const node of merges.slice(0, MAX_PER_CODE)) {
    add('warning', 'implicit_merge',
      `В шаг «${node.name}» входит ${(incoming.get(node.id) ?? []).length} связи: ` +
      'ветки сходятся прямо на шаге.',
      'Процессная студия PIX принимает только одну входящую связь на шаг. ' +
      'Поставьте перед шагом шлюз слияния и заведите ветки в него — на смысл ' +
      'процесса это не влияет, зато карта пройдёт проверку студии.', node)
  }
  cap('implicit_merge', merges.length,
    (n) => `Ещё в ${n} шагов сходится больше одной связи.`, merges.slice(MAX_PER_CODE))

  const hanging = flowNodes.filter(
    (n) => !isArtifact(n) && n.type !== 'endEvent' &&
      !(outgoing.get(n.id) ?? []).length && !isolatedIds.has(n.id),
  )
  for (const node of hanging.slice(0, MAX_PER_CODE)) {
    add('warning', 'no_outgoing', `Из шага «${node.name}» не выходит ни одна связь.`,
      'Процесс обрывается: доведите ветку до следующего шага или до события завершения.', node)
  }
  cap('no_outgoing', hanging.length, (n) => `Ещё ${n} шагов без исходящих связей.`,
    hanging.slice(MAX_PER_CODE))

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

  for (const { gateway, condition } of input.completedBranches ?? []) {
    add('warning', 'gateway_branch_completed',
      `У шлюза «${gateway.name}» вторая ветка была без подписи — подставлено «${condition}».`,
      'Условие взято как противоположное подписанной ветке: без него PIX BPM не ' +
        'автоматизирует развилку. Подпишите ветку в draw.io, если смысл другой.',
      gateway)
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
  cap('generated_name', generated.length, (n) => `Ещё ${n} фигур без подписи.`,
    generated.slice(MAX_PER_CODE))

  const seen = new Map<string, number>()
  for (const step of steps) seen.set(step.name, (seen.get(step.name) ?? 0) + 1)
  for (const [name, count] of seen) {
    if (count > 1 && name) {
      add('info', 'duplicate_step_name', `Шаг «${name}» встречается на карте несколько раз.`,
        'В регламенте такие строки не отличить друг от друга — уточните формулировки.',
        undefined, steps.filter((s) => s.name === name).map((s) => s.id))
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
  cap('no_step_time', noTime.length, (n) => `Ещё ${n} шагов без времени выполнения.`,
    noTime.slice(MAX_PER_CODE))

  // ── Дорожки и роли ────────────────────────────────────────────────────────
  const homeless = flowNodes.filter((n) => !n.laneId && !isArtifact(n))
  for (const node of homeless.slice(0, MAX_PER_CODE)) {
    add('warning', 'step_without_lane', `Шаг «${node.name}» не лежит ни в одной дорожке.`,
      'Без дорожки у шага нет исполнителя: перетащите фигуру внутрь дорожки подразделения.', node)
  }
  cap('step_without_lane', homeless.length, (n) => `Ещё ${n} шагов вне дорожек.`,
    homeless.slice(MAX_PER_CODE))
  if (!lanes.length) {
    add('warning', 'no_lanes', 'На карте нет дорожек подразделений.',
      'Без дорожек в регламенте не заполнится колонка «Исполнитель».')
  }

  // ── Связи и артефакты ─────────────────────────────────────────────────────
  // Связь фигуры с самой собой рисуется в draw.io без возражений, но ни BPMN,
  // ни PIX её не принимают: Процессная студия отказывается открыть карту
  // целиком («Connector source and target node cannot be the same»).
  const nodeById = new Map(flowNodes.map((n) => [n.id, n]))
  const loops = edges.filter(
    (e) => e.sourceId && e.sourceId === e.targetId && e.kind !== 'annotationLine',
  )
  for (const edge of loops.slice(0, MAX_PER_CODE)) {
    const node = nodeById.get(edge.sourceId!)
    add('error', 'self_loop',
      `Связь у ${node ? `«${node.name}»` : 'фигуры'} начинается и заканчивается на одной и той же фигуре.`,
      'В выгрузку такая линия не идёт: BPMN и PIX её не принимают. ' +
        'Доведите линию до следующей фигуры или удалите её с карты.',
      node)
  }
  cap('self_loop', loops.length, (n) => `Ещё ${n} связей замкнуты на самих себя.`,
    loops.slice(MAX_PER_CODE).map((e) => nodeById.get(e.sourceId!)).filter((n): n is ProcessNode => Boolean(n)))

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
  cap('artifact_not_linked', lonely.length,
    (n) => `Ещё ${n} систем и документов без связи с шагом.`, lonely.slice(MAX_PER_CODE))

  // ── Фигуры, которых платформа не знает ────────────────────────────────────
  // Незнакомую фигуру импортёр всё равно во что-то превращает, иначе карта
  // порвётся. Но подменять смысл молча нельзя: аналитик рисовал одно, а в
  // регламент и в PIX уедет другое, и заметит он это уже в Процессной студии.
  const unsupportedEntries: [string, ProcessNode[]][] = [...(input.unsupportedShapes ?? new Map())]
  unsupportedEntries.sort((a, b) => a[0].localeCompare(b[0]))
  for (const [description, unknownNodes] of unsupportedEntries) {
    if (!unknownNodes.length) continue
    const shown = unknownNodes.slice(0, 3).map((n) => `«${n.name}»`).join(', ')
    const tail = unknownNodes.length > 3 ? ` и ещё ${unknownNodes.length - 3}` : ''
    const message =
      unknownNodes.length === 1
        ? `Платформа не распознала ${description}: «${unknownNodes[0].name}».`
        : `Платформа не распознала ${description} — фигур на карте: ${unknownNodes.length} (${shown}${tail}).`
    add('warning', 'unsupported_shape', message,
      `Платформа подставила ближайший тип (${NODE_TYPE_LABELS[unknownNodes[0].type]}), и в регламент ` +
        'уйдёт именно он. Перерисуйте фигуру набором BPMN 2.0 из draw.io — «События», ' +
        '«Действия», «Шлюзы» — или проверьте шаг вручную.',
      undefined, unknownNodes.map((n) => n.id))
  }

  const clipart = input.skippedClipart ?? []
  if (clipart.length) {
    add('info', 'clipart_skipped', `Иконок из библиотеки draw.io пропущено: ${clipart.length}.`,
      'Картинки (телефон, здание, транспорт) — оформление схемы, а не шаги процесса: ' +
        'на карту банка и в выгрузку они не идут.')
  }

  // ── Что платформа поправила сама ──────────────────────────────────────────
  const report = input.layoutReport
  if (report?.squared.length) {
    add('info', 'geometry_squared',
      `Событиям и шлюзам вернули квадратную рамку: ${report.squared.length} фигур.`,
      'В draw.io они нарисованы с фиксированной пропорцией, а импортёр BPMN растянул бы круг в эллипс вместе со значком таймера.',
      undefined, report.squared)
  }
  if (report?.fitted.length) {
    add('info', 'geometry_fitted', `Фигуры расширены под подпись: ${report.fitted.length}.`,
      'В draw.io текст свободно выходит за рамку, а bpmn.io и Процессная студия обрезают его по фигуре.',
      undefined, report.fitted)
  }
  if (report?.moved.length) {
    add('info', 'geometry_moved',
      `Артефакты сдвинуты, чтобы не лежать поверх шагов: ${report.moved.length}.`,
      'На карте они перекрывали подпись шага.',
      undefined, report.moved)
  }

  return issues
}
