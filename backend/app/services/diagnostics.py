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
from typing import Dict, List, Optional, Sequence

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    TASK_NODE_TYPES,
    ProcessEdge,
    ProcessNode,
    ProcessValidation,
)

_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway')

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
    ) -> None:
        self.items.append(ProcessValidation(
            level=level,  # type: ignore[arg-type]
            code=code,
            message=message,
            hint=hint,
            nodeId=node.id if node else None,
            nodeName=node.name if node else None,
        ))


def _is_generated_name(name: str) -> bool:
    return any((name or '').startswith(prefix) for prefix in _GENERATED_NAME_PREFIXES)


def _cap(collector: DiagnosticsCollector, code: str, total: int, tail_message: str) -> None:
    """Свернуть хвост однотипных замечаний в одну строку со счётчиком."""
    if total > _MAX_PER_CODE:
        collector.add('info', f'{code}_more', tail_message.format(count=total - _MAX_PER_CODE))


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
        )
    if not ends:
        collector.add(
            'error', 'no_end_event',
            'На карте нет события завершения процесса.',
            'Добавьте кружок конца процесса хотя бы для успешного сценария.',
        )

    dead_ends = 0
    for node in flow_nodes:
        if node.type in ARTIFACT_NODE_TYPES:
            continue
        if node.type != 'startEvent' and not incoming[node.id]:
            dead_ends += 1
            if dead_ends <= _MAX_PER_CODE:
                collector.add(
                    'error', 'no_incoming',
                    f'В шаг «{node.name}» не входит ни одна связь.',
                    'Шаг недостижим: соедините его с предыдущим шагом.',
                    node,
                )
    _cap(collector, 'no_incoming', dead_ends, 'Ещё {count} шагов без входящих связей.')

    hanging = 0
    for node in flow_nodes:
        if node.type in ARTIFACT_NODE_TYPES or node.type == 'endEvent':
            continue
        if not outgoing[node.id]:
            hanging += 1
            if hanging <= _MAX_PER_CODE:
                collector.add(
                    'warning', 'no_outgoing',
                    f'Из шага «{node.name}» не выходит ни одна связь.',
                    'Процесс обрывается: доведите ветку до следующего шага или до '
                    'события завершения.',
                    node,
                )
    _cap(collector, 'no_outgoing', hanging, 'Ещё {count} шагов без исходящих связей.')

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
    _cap(collector, 'generated_name', len(generated), 'Ещё {count} фигур без подписи.')

    duplicates = [
        name for name, count in Counter(n.name for n in steps).items()
        if count > 1 and name
    ]
    for name in duplicates[:_MAX_PER_CODE]:
        collector.add(
            'info', 'duplicate_step_name',
            f'Шаг «{name}» встречается на карте несколько раз.',
            'В регламенте такие строки не отличить друг от друга — уточните формулировки.',
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
    _cap(collector, 'no_step_time', len(no_time), 'Ещё {count} шагов без времени выполнения.')

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
    _cap(collector, 'step_without_lane', len(homeless), 'Ещё {count} шагов вне дорожек.')

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

    # ── Что платформа поправила сама ────────────────────────────────────────
    report = layout_report or {}
    if report.get('squared'):
        collector.add(
            'info', 'geometry_squared',
            f'Событиям и шлюзам вернули квадратную рамку: {len(report["squared"])} фигур.',
            'В draw.io они нарисованы с фиксированной пропорцией, а импортёр BPMN '
            'растянул бы круг в эллипс вместе со значком таймера.',
        )
    if report.get('fitted'):
        collector.add(
            'info', 'geometry_fitted',
            f'Фигуры расширены под подпись: {len(report["fitted"])}.',
            'В draw.io текст свободно выходит за рамку, а bpmn.io и Процессная '
            'студия обрезают его по фигуре.',
        )
    if report.get('moved'):
        collector.add(
            'info', 'geometry_moved',
            f'Артефакты сдвинуты, чтобы не лежать поверх шагов: {len(report["moved"])}.',
            'На карте они перекрывали подпись шага.',
        )

    return collector.items
