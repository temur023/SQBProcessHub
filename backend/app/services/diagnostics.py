"""Замечания к импортированной карте — то, что показывается сотруднику.

Импорт draw.io почти всегда что-то домысливает: подписи, недостающие ветки,
время шага, привязку линии к фигуре. Молча делать это нельзя — аналитик должен
увидеть, где карта неполна и что платформа достроила за него, иначе ошибка
дойдёт до регламента и до выгрузки в PIX.

Уровни:

* ``error``   — карта не соответствует нотации, выгрузка будет неполной;
* ``warning`` — модель собралась, но данные вызывают сомнение;
* ``info``    — платформа что-то достроила сама, просто сообщаем об этом.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.process import (
    NODE_TYPE_LABELS,
    ARTIFACT_NODE_TYPES,
    TASK_NODE_TYPES,
    ProcessEdge,
    ProcessNode,
    ProcessValidation,
)

_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')

#: Имена, которые платформа придумывает сама (см. ``fallback_node_name``).
_GENERATED_NAME_PREFIXES = (
    'Операция ', 'Условие', 'Ожидание', 'Событие-сообщение', 'Старт',
    'Завершение', 'Подпроцесс', 'Примечание', 'Информационная система', 'Документ',
)

#: Сколько однотипных замечаний показываем поимённо, прежде чем свернуть в счёт.
_MAX_PER_CODE = 8


class DiagnosticsCollector:
    """Накопитель замечаний: парсер докладывает сюда по ходу разбора."""

    def __init__(self) -> None:
        self.items: List[ProcessValidation] = []

    def add(
        self,
        level: str,
        code: str,
        message: str,
        hint: Optional[str] = None,
        node: Optional[ProcessNode] = None,
        node_ids: Optional[Sequence[str]] = None,
    ) -> None:
        # Одна фигура — тоже группа из одной: UI подсвечивает список целиком и
        # не должен знать, пришло замечание про шаг или про две сотни фигур.
        ids = list(node_ids) if node_ids else ([node.id] if node else [])
        self.items.append(ProcessValidation(
            level=level,  # type: ignore[arg-type]
            code=code,
            message=message,
            hint=hint,
            nodeId=node.id if node else (ids[0] if len(ids) == 1 else None),
            nodeName=node.name if node else None,
            nodeIds=ids or None,
        ))


def is_generated_name(name: Optional[str]) -> bool:
    """Имя, которое подставила платформа, а не написал аналитик.

    Нужно не только отчёту о качестве импорта: на карту для Процессной студии
    такие подписи не идут. В draw.io безымянный шлюз так и нарисован — пустым
    ромбом, вопрос стоит на ветках; подставленное «Условие» на карте студии
    оказывается лишней надписью, которой в эталоне нет.
    """
    return any((name or '').startswith(prefix) for prefix in _GENERATED_NAME_PREFIXES)


def _is_generated_name(name: str) -> bool:
    return is_generated_name(name)


def _cap(
    collector: DiagnosticsCollector,
    code: str,
    total: int,
    tail_message: str,
    tail_nodes: Sequence[ProcessNode] = (),
) -> None:
    """Свернуть хвост однотипных замечаний в одну строку со счётчиком.

    Свёрнутые фигуры остаются адресуемыми: строка «Ещё 12 шагов…» подсвечивает
    на карте те самые двенадцать, иначе её невозможно раскрыть.
    """
    if total > _MAX_PER_CODE:
        collector.add(
            'info', f'{code}_more',
            tail_message.format(count=total - _MAX_PER_CODE),
            node_ids=[n.id for n in tail_nodes] or None,
        )


def collect_import_diagnostics(
    flow_nodes: Sequence[ProcessNode],
    lanes: Sequence[ProcessNode],
    edges: Sequence[ProcessEdge],
    *,
    pages_skipped: Sequence[str] = (),
    page_used: str = '',
    snapped_edges: Sequence[str] = (),
    layout_report: Optional[Dict[str, List[str]]] = None,
    timed_step_ids: Optional[Sequence[str]] = None,
    unsupported_shapes: Optional[Dict[str, List[ProcessNode]]] = None,
    skipped_clipart: Sequence[str] = (),
    completed_branches: Sequence[Tuple[ProcessNode, ProcessEdge, str]] = (),
) -> List[ProcessValidation]:
    """Полный отчёт по импортированной карте."""
    collector = DiagnosticsCollector()
    steps = [n for n in flow_nodes if n.type in TASK_NODE_TYPES]
    sequence = [e for e in edges if e.kind == 'sequenceFlow']

    incoming: Dict[str, List[ProcessEdge]] = defaultdict(list)
    outgoing: Dict[str, List[ProcessEdge]] = defaultdict(list)
    for edge in sequence:
        if edge.targetId:
            incoming[edge.targetId].append(edge)
        if edge.sourceId:
            outgoing[edge.sourceId].append(edge)

    # ── Страницы файла ──────────────────────────────────────────────────────
    if pages_skipped:
        collector.add(
            'info', 'pages_skipped',
            f'В файле {len(pages_skipped) + 1} страницы, импортирована первая — «{page_used}».',
            'Остальные страницы (' + ', '.join(f'«{p}»' for p in pages_skipped)
            + ') загрузите отдельными файлами: это разные версии процесса, '
            'и объединять их в одну карту нельзя.',
        )

    # ── Целостность потока ──────────────────────────────────────────────────
    starts = [n for n in flow_nodes if n.type == 'startEvent']
    ends = [n for n in flow_nodes if n.type == 'endEvent']
    if not starts:
        collector.add(
            'error', 'no_start_event',
            'На карте нет стартового события.',
            'Добавьте кружок начала процесса — без него выгрузка в BPMN невалидна.',
        )
    elif len(starts) > 1:
        collector.add(
            'warning', 'many_start_events',
            f'Стартовых событий: {len(starts)}.',
            'В одном процессе должен быть один вход; лишние обычно оказываются '
            'промежуточными событиями.',
            node_ids=[n.id for n in starts],
        )
    if not ends:
        collector.add(
            'error', 'no_end_event',
            'На карте нет события завершения процесса.',
            'Добавьте кружок конца процесса хотя бы для успешного сценария.',
        )

    # Фигура, у которой нет НИ входящих, НИ исходящих связей, — отдельный
    # случай: это не разрыв цепочки, а забытая на холсте фигура. Раньше о ней
    # сообщалось дважды («нет входящих» и «нет исходящих»), и было неясно, что
    # соединять — на самом деле её надо либо вписать в поток, либо убрать.
    isolated = [
        n for n in flow_nodes
        if n.type not in ARTIFACT_NODE_TYPES and not incoming[n.id] and not outgoing[n.id]
    ]
    isolated_ids = {n.id for n in isolated}
    for node in isolated[:_MAX_PER_CODE]:
        collector.add(
            'error', 'isolated_node',
            f'Фигура «{node.name}» не соединена ни с чем: ни входящих связей, ни исходящих.',
            'Либо впишите её в поток — линия от предыдущего шага и линия к '
            'следующему, — либо уберите с карты: в BPMN и PIX такая фигура '
            'попадёт в регламент отдельной строкой, до которой процесс не доходит.',
            node,
        )
    _cap(collector, 'isolated_node', len(isolated),
         'Ещё {count} фигур не соединены ни с чем.', isolated[_MAX_PER_CODE:])

    dead_ends = [
        n for n in flow_nodes
        if n.type not in ARTIFACT_NODE_TYPES and n.type != 'startEvent'
        and not incoming[n.id] and n.id not in isolated_ids
    ]
    for node in dead_ends[:_MAX_PER_CODE]:
        collector.add(
            'error', 'no_incoming',
            f'В шаг «{node.name}» не входит ни одна связь.',
            'Шаг недостижим: соедините его с предыдущим шагом.',
            node,
        )
    _cap(collector, 'no_incoming', len(dead_ends), 'Ещё {count} шагов без входящих связей.',
         dead_ends[_MAX_PER_CODE:])

    # Несколько связей, входящих в один шаг, — «неявное слияние»: стандарт BPMN
    # его допускает, а Процессная студия считает ошибкой («У элемента должен
    # быть только один входящий поток управления») и отказывается считать по
    # такой карте показатели. Разойтись со студией молча нельзя, но и чинить за
    # аналитика тоже: слияние ветвей — решение о том, как устроен процесс, а не
    # оформление. Поэтому предупреждение с готовым ответом, что дорисовать.
    merges = [
        (n, len(incoming[n.id])) for n in flow_nodes
        if n.type in TASK_NODE_TYPES and len(incoming[n.id]) > 1
    ]
    merges.sort(key=lambda pair: -pair[1])
    for node, count in merges[:_MAX_PER_CODE]:
        collector.add(
            'warning', 'implicit_merge',
            f'В шаг «{node.name}» входит {count} связи: ветки сходятся прямо на шаге.',
            'Процессная студия PIX принимает только одну входящую связь на шаг. '
            'Поставьте перед шагом шлюз слияния и заведите ветки в него — на '
            'смысл процесса это не влияет, зато карта пройдёт проверку студии.',
            node,
        )
    _cap(collector, 'implicit_merge', len(merges),
         'Ещё в {count} шагов сходится больше одной связи.',
         [node for node, _ in merges[_MAX_PER_CODE:]])

    hanging = [
        n for n in flow_nodes
        if n.type not in ARTIFACT_NODE_TYPES and n.type != 'endEvent'
        and not outgoing[n.id] and n.id not in isolated_ids
    ]
    for node in hanging[:_MAX_PER_CODE]:
        collector.add(
            'warning', 'no_outgoing',
            f'Из шага «{node.name}» не выходит ни одна связь.',
            'Процесс обрывается: доведите ветку до следующего шага или до '
            'события завершения.',
            node,
        )
    _cap(collector, 'no_outgoing', len(hanging), 'Ещё {count} шагов без исходящих связей.',
         hanging[_MAX_PER_CODE:])

    # ── Шлюзы ───────────────────────────────────────────────────────────────
    for node in flow_nodes:
        if node.type not in _GATEWAY_TYPES:
            continue
        branches = outgoing[node.id]
        if len(branches) < 2 and len(incoming[node.id]) < 2:
            collector.add(
                'warning', 'gateway_single_branch',
                f'У шлюза «{node.name}» только одна ветка.',
                'Шлюз без развилки не нужен: либо добавьте вторую ветку, либо '
                'уберите шлюз с карты.',
                node,
            )
        unnamed = [e for e in branches if not (e.name or e.condition or '').strip()]
        if len(branches) > 1 and unnamed:
            collector.add(
                'error', 'gateway_branch_unlabeled',
                f'У шлюза «{node.name}» {len(unnamed)} из {len(branches)} веток без подписи условия.',
                'Подпишите ветки («Ha» / «Yo\'q»): без условия шаг регламента '
                'нельзя автоматизировать в PIX BPM.',
                node,
            )

    for gateway, _branch, condition in completed_branches:
        collector.add(
            'warning', 'gateway_branch_completed',
            f'У шлюза «{gateway.name}» вторая ветка была без подписи — подставлено «{condition}».',
            'Условие взято как противоположное подписанной ветке: без него PIX BPM '
            'не автоматизирует развилку. Подпишите ветку в draw.io, если смысл другой.',
            gateway,
        )

    # ── Подписи фигур ───────────────────────────────────────────────────────
    # У таймера подпись — это его длительность, и «Ожидание 10 мин» дефектом не
    # является. Спрашиваем только там, где имя несёт смысл: шаги, шлюзы, события
    # начала и конца.
    named_types = tuple(TASK_NODE_TYPES) + _GATEWAY_TYPES + ('startEvent', 'endEvent')
    generated = [
        n for n in flow_nodes
        if n.type in named_types and _is_generated_name(n.name)
    ]
    for node in generated[:_MAX_PER_CODE]:
        collector.add(
            'warning', 'generated_name',
            f'Фигура без подписи получила имя «{node.name}».',
            'Назовите фигуру в draw.io: сгенерированное имя попадёт в регламент '
            'и в выгрузку как есть.',
            node,
        )
    _cap(collector, 'generated_name', len(generated), 'Ещё {count} фигур без подписи.',
         generated[_MAX_PER_CODE:])

    duplicates = [
        name for name, count in Counter(n.name for n in steps).items()
        if count > 1 and name
    ]
    for name in duplicates[:_MAX_PER_CODE]:
        collector.add(
            'info', 'duplicate_step_name',
            f'Шаг «{name}» встречается на карте несколько раз.',
            'В регламенте такие строки не отличить друг от друга — уточните формулировки.',
            node_ids=[n.id for n in steps if n.name == name],
        )

    # ── Время выполнения ────────────────────────────────────────────────────
    measured = set(timed_step_ids or ())
    no_time = [n for n in steps if n.id not in measured]
    for node in no_time[:_MAX_PER_CODE]:
        collector.add(
            'warning', 'no_step_time',
            f'У шага «{node.name}» на карте нет времени выполнения — '
            f'подставлено {node.slaMinutes} мин по типу операции.',
            'Поставьте рядом с шагом бейдж-таймер («5 min»): по нему считается '
            'SLA процесса и экономия от роботизации.',
            node,
        )
    _cap(collector, 'no_step_time', len(no_time), 'Ещё {count} шагов без времени выполнения.',
         no_time[_MAX_PER_CODE:])

    # ── Дорожки и роли ──────────────────────────────────────────────────────
    homeless = [n for n in flow_nodes if not n.laneId and n.type not in ARTIFACT_NODE_TYPES]
    for node in homeless[:_MAX_PER_CODE]:
        collector.add(
            'warning', 'step_without_lane',
            f'Шаг «{node.name}» не лежит ни в одной дорожке.',
            'Без дорожки у шага нет исполнителя: перетащите фигуру внутрь дорожки '
            'подразделения.',
            node,
        )
    _cap(collector, 'step_without_lane', len(homeless), 'Ещё {count} шагов вне дорожек.',
         homeless[_MAX_PER_CODE:])

    if not lanes:
        collector.add(
            'warning', 'no_lanes',
            'На карте нет дорожек подразделений.',
            'Без дорожек в регламенте не заполнится колонка «Исполнитель».',
        )

    # ── Связи и артефакты ───────────────────────────────────────────────────
    decoration = [e for e in edges if e.kind == 'annotationLine']
    if decoration:
        collector.add(
            'warning', 'decoration_line',
            f'Линий, не опирающихся на фигуры: {len(decoration)}.',
            'Такие линии остаются на холсте, но в BPMN и PIX не выгружаются — '
            'привяжите оба конца к фигурам.',
        )

    # Связь фигуры с самой собой рисуется в draw.io без возражений, но ни BPMN,
    # ни PIX её не принимают: Процессная студия отказывается открыть карту
    # целиком («Connector source and target node cannot be the same»).
    by_id = {n.id: n for n in flow_nodes}
    loops = [
        e for e in edges
        if e.sourceId and e.sourceId == e.targetId and e.kind != 'annotationLine'
    ]
    for edge in loops[:_MAX_PER_CODE]:
        node = by_id.get(edge.sourceId or '')
        where = f'«{node.name}»' if node else 'фигуры'
        collector.add(
            'error', 'self_loop',
            f'Связь у {where} начинается и заканчивается на одной и той же фигуре.',
            'В выгрузку такая линия не идёт: BPMN и PIX её не принимают. '
            'Доведите линию до следующей фигуры или удалите её с карты.',
            node,
        )
    _cap(collector, 'self_loop', len(loops), 'Ещё {count} связей замкнуты на самих себя.',
         [by_id[e.sourceId] for e in loops[_MAX_PER_CODE:] if e.sourceId in by_id])

    if snapped_edges:
        collector.add(
            'info', 'snapped_endpoint',
            f'У {len(snapped_edges)} связей конец не был привязан к фигуре — '
            'платформа притянула его к ближайшей.',
            'Проверьте эти связи: привязка выбрана по расстоянию и может отличаться '
            'от задуманной.',
        )

    lonely_artifacts = [
        n for n in flow_nodes
        if n.type in ('dataStore', 'dataObject')
        and not any(
            e.kind == 'association' and n.id in (e.sourceId, e.targetId) for e in edges
        )
    ]
    for node in lonely_artifacts[:_MAX_PER_CODE]:
        collector.add(
            'info', 'artifact_not_linked',
            f'{"Система" if node.type == "dataStore" else "Документ"} «{node.name}» '
            'не связана ни с одним шагом.',
            'Проведите пунктирную линию от неё к шагу — иначе в регламенте не '
            'заполнится колонка системы или документа.',
            node,
        )
    _cap(collector, 'artifact_not_linked', len(lonely_artifacts),
         'Ещё {count} систем и документов без связи с шагом.',
         lonely_artifacts[_MAX_PER_CODE:])

    # ── Фигуры, которых платформа не знает ──────────────────────────────────
    # Незнакомую фигуру импортёр всё равно во что-то превращает, иначе карта
    # порвётся. Но подменять смысл молча нельзя: аналитик рисовал одно, а в
    # регламент и в PIX уедет другое, и заметит он это уже в Процессной студии.
    for description, nodes in sorted((unsupported_shapes or {}).items()):
        if not nodes:
            continue
        shown = ', '.join(f'«{n.name}»' for n in nodes[:3])
        tail = f' и ещё {len(nodes) - 3}' if len(nodes) > 3 else ''
        message = (
            f'Платформа не распознала {description}: «{nodes[0].name}».'
            if len(nodes) == 1
            else f'Платформа не распознала {description} — фигур на карте: {len(nodes)} ({shown}{tail}).'
        )
        collector.add(
            'warning', 'unsupported_shape',
            message,
            f'Платформа подставила ближайший тип ({NODE_TYPE_LABELS.get(nodes[0].type, nodes[0].type)}), '
            'и в регламент уйдёт именно он. Перерисуйте фигуру набором BPMN 2.0 '
            'из draw.io — «События», «Действия», «Шлюзы» — или проверьте шаг вручную.',
            node_ids=[n.id for n in nodes],
        )

    if skipped_clipart:
        collector.add(
            'info', 'clipart_skipped',
            f'Иконок из библиотеки draw.io пропущено: {len(skipped_clipart)}.',
            'Картинки (телефон, здание, транспорт) — оформление схемы, а не шаги '
            'процесса: на карту банка и в выгрузку они не идут.',
        )

    # ── Что платформа поправила сама ────────────────────────────────────────
    report = layout_report or {}
    if report.get('squared'):
        collector.add(
            'info', 'geometry_squared',
            f'Событиям и шлюзам вернули квадратную рамку: {len(report["squared"])} фигур.',
            'В draw.io они нарисованы с фиксированной пропорцией, а импортёр BPMN '
            'растянул бы круг в эллипс вместе со значком таймера.',
            node_ids=report['squared'],
        )
    if report.get('fitted'):
        collector.add(
            'info', 'geometry_fitted',
            f'Фигуры расширены под подпись: {len(report["fitted"])}.',
            'В draw.io текст свободно выходит за рамку, а bpmn.io и Процессная '
            'студия обрезают его по фигуре.',
            node_ids=report['fitted'],
        )
    if report.get('moved'):
        collector.add(
            'info', 'geometry_moved',
            f'Артефакты сдвинуты, чтобы не лежать поверх шагов: {len(report["moved"])}.',
            'На карте они перекрывали подпись шага.',
            node_ids=report['moved'],
        )

    return collector.items
