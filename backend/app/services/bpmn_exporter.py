"""OMG BPMN 2.0 + BPMNDI для PIX Процессной студии / Processet / Camunda.

Экспортёр держит инвариант валидности схемы, а не просто перекладывает узлы:

* стартовое событие не может иметь входящих переходов, конечное — исходящих;
  узлы, нарушающие это, понижаются до промежуточных событий (нормализация);
* хранилища данных и документы (2-ILOVA: Artefaktlar) выгружаются как
  ``dataStoreReference`` / ``dataObjectReference`` и соединяются
  ``association``, а не ``sequenceFlow`` — по спецификации поток управления
  к артефакту вести нельзя;
* порядок элементов внутри ``bpmn:process`` соблюдает XSD-последовательность
  ``laneSet* , flowElement* , artifact*``;
* ``flowNodeRef`` дорожки перечисляет только узлы потока: артефакты
  элементами потока не являются;
* дорожка без шагов — это внешняя сторона (клиент), а не зона ответственности:
  она выгружается отдельным участником-«чёрным ящиком», а пунктир к ней —
  ``messageFlow``. Дорожка без ``flowNodeRef`` импортёру не нужна, и раньше
  такая строка вместе со всеми связями к ней пропадала из схемы;
* дорожки замощают пул по вертикали и делят с ним ширину: импортёр рисует
  геометрию как есть, и разрыв между полосами виден на схеме.
"""
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    GATEWAY_NODE_TYPES,
    TASK_NODE_TYPES,
    BusinessProcess,
    ProcessEdge,
    ProcessNode,
)
from app.services.diagnostics import is_generated_name
from app.services.map_layout import pool_bounds, stack_lanes
from app.services.edge_routing import (
    Obstacles,
    build_obstacles,
    message_flow_endpoints,
    orthogonal_waypoints,
)
from app.services.layout import (
    EXTERNAL_LABEL_TYPES,
    Box,
    choose_label_box,
    edge_label_candidates,
    external_label_candidates,
    label_size,
    node_obstacles,
    segment_boxes,
)

_NCNAME = re.compile(r'^[A-Za-z_][A-Za-z0-9._-]*$')

_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')


def escape_xml(value) -> str:
    if value is None:
        return ''
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )


def _safe_id(raw: str, prefix: str, used: Dict[str, str], taken: set) -> str:
    """Выдаёт уникальный xsd:ID и запоминает, во что превратился исходный.

    Раньше функция начиналась с возврата уже выданного значения по исходной
    строке. Для одной и той же сущности это было безобидно, но каждая сущность
    и так спрашивает идентификатор ровно один раз, а совпадение исходных строк
    у РАЗНЫХ сущностей возвращало им общий id. Так рождались дубликаты, из-за
    которых студия отвергала файл целиком:

    * маркер длительности шага ``A`` просит ``Duration_A`` — и получает id
      фигуры, которая в исходнике уже называлась ``Duration_A``;
    * две фигуры с одинаковым ``id`` в склеенном draw.io.

    Поэтому кэш больше не отдаёт готовый ответ, а только ведёт журнал
    соответствий: каждый вызов проходит через ``taken`` и получает своё имя.
    """
    original = raw or ''
    candidate = original if _NCNAME.match(original) else ''
    if not candidate:
        cleaned = re.sub(r'[^A-Za-z0-9._-]', '_', original)
        if not cleaned or not re.match(r'^[A-Za-z_]', cleaned):
            cleaned = f'{prefix}_{cleaned}' if cleaned else prefix
        candidate = cleaned
    base = candidate
    n = 2
    while candidate in taken:
        candidate = f'{base}_{n}'
        n += 1
    used[original] = candidate
    taken.add(candidate)
    return candidate


def iso_duration(minutes: Optional[int]) -> str:
    """Минуты -> ISO-8601 длительность для timerEventDefinition."""
    value = max(1, int(minutes or 0) or 1)
    if value >= 1440 and value % 1440 == 0:
        return f'P{value // 1440}D'
    if value >= 60 and value % 60 == 0:
        return f'PT{value // 60}H'
    return f'PT{value}M'


#: Диаметр значка события в BPMNDI: bpmn.io и Процессная студия рисуют 36 px.
EVENT_SIDE = 36

#: Отступ значка длительности от правого края шага, px.
_DURATION_INSET = 20


def duration_label(minutes: Optional[float]) -> str:
    """Человекочитаемая длительность: «45 мин», «2 ч», «1 ч 30 мин», «2 дн»."""
    value = int(round(minutes or 0))
    if value <= 0:
        return ''
    if value < 60:
        return f'{value} мин'
    hours, rest = divmod(value, 60)
    if rest:
        return f'{hours} ч {rest} мин'
    if hours >= 24 and hours % 24 == 0:
        return f'{hours // 24} дн'
    return f'{hours} ч'


def step_duration_text(node: ProcessNode) -> str:
    """Подпись под часами у шага: время операции и, если есть, ожидание.

    Пусто, если время не снято с карты. Импорт подставляет правдоподобную
    длительность по категории шага, когда в подписи её нет, — для расчёта SLA
    это нужно, но нарисовать её значком часов значит выдать догадку за замер.
    В draw.io у такого шага часов нет, и на карте студии их быть не должно.
    """
    if not node.slaMeasured:
        return ''
    st = duration_label(node.slaMinutes)
    wt = duration_label(node.waitMinutes)
    if st and wt:
        return f'{st} · ожидание {wt}'
    if wt:
        return f'ожидание {wt}'
    return st


def map_label(node: ProcessNode) -> str:
    """Подпись фигуры на карте студии. Эталон — то, как она выглядит в draw.io.

    Отличий от ``node.name`` два, и оба взяты с исходной карты.

    Подставленное платформой имя на карту не идёт. Безымянный шлюз аналитик
    рисует пустым ромбом — вопрос стоит на ветках («Ha» / «Yo'q»), — а мы
    писали в него «Условие». На карте банка таких ромбов 71 из 220: семь
    десятков надписей, которых в эталоне нет и которые в студии ложатся
    поверх соседних фигур. У задач подпись оставляем: пустой прямоугольник
    хуже условного имени, по нему шаг не найти ни в регламенте, ни в отчёте.

    Событию-ожиданию возвращаем время. В draw.io подпись набрана в две строки
    («Kutish vaqti» и «15 min»), импорт разбирает вторую строку в минуты и
    убирает из имени — в студии оставалось голое «Kutish vaqti» без единой
    цифры. На карту пишем так же, как нарисовано в эталоне.
    """
    name = (node.name or '').strip()
    if node.type in GATEWAY_NODE_TYPES and is_generated_name(name):
        return ''
    if node.type == 'intermediateTimerEvent' and name and not any(c.isdigit() for c in name):
        minutes = duration_label(node.slaMinutes or node.waitMinutes)
        if minutes:
            # В draw.io время стоит второй строкой, и от первой её нередко
            # отделяет двоеточие. Склеив в одну строку, получили бы
            # «Kutish vaqti : 30 мин» — с висящим знаком посреди подписи.
            return f'{name.rstrip(" :-—·")} {minutes}'
    return name


class DurationMarker(NamedTuple):
    """Значок часов у шага: граничный таймер плюс подпись со временем.

    На карте draw.io время операции нарисовано мелкой фигурой-таймером в углу
    шага, и холст банка показывает его там же. В BPMN это время жило только в
    ``documentation``: в Процессной студии карта открывалась без единой цифры,
    и сотруднику приходилось сверяться с регламентом отдельно.

    Некрывающий (``cancelActivity="false"``) граничный таймер — единственная
    конструкция BPMN, которую импортёры рисуют ровно там, где значок стоит на
    исходной карте: на границе фигуры. Поток она не меняет — у события нет
    исходящих переходов, оно только помечает длительность.
    """

    node: ProcessNode
    marker_id: str
    text: str
    minutes: int
    cx: int
    cy: int


def duration_markers(
    flow_nodes: List[ProcessNode],
    id_of: Dict[str, str],
    used: Dict[str, str],
    taken: set,
) -> List[DurationMarker]:
    """Значки длительности для шагов, где проставлено ST или WT.

    Граничное событие BPMN разрешено только у активности, поэтому события и
    шлюзы значка не получают: их время и так уходит в ``timerEventDefinition``
    самого события.
    """
    markers: List[DurationMarker] = []
    for node in flow_nodes:
        if node.type not in TASK_NODE_TYPES:
            continue
        text = step_duration_text(node)
        if not text:
            continue
        geo = node.geometry
        # Узкий шаг значком не разрезать пополам: у него часы встают по центру
        # нижней грани, у обычного — в правом нижнем углу, как в draw.io.
        offset = max(geo.width - _DURATION_INSET, geo.width / 2)
        markers.append(DurationMarker(
            node=node,
            marker_id=_safe_id(f'Duration_{id_of[node.id]}', 'Duration', used, taken),
            text=text,
            minutes=int(node.slaMinutes or node.waitMinutes or 0),
            cx=int(round(geo.x + offset)),
            cy=int(geo.y + geo.height),
        ))
    return markers


def marker_box(marker: DurationMarker) -> Box:
    """Рамка самого значка: круг события сидит на границе шага."""
    half = EVENT_SIDE // 2
    return (marker.cx - half, marker.cy - half, EVENT_SIDE, EVENT_SIDE)


def _duration_label_candidates(marker: DurationMarker) -> List[Box]:
    """Куда положить время: под часами, затем правее, левее, выше и по углам.

    Четырёх сторон на плотной карте не хватает: если все они заняты, выбор
    падает на «наименее конфликтную» позицию, и время печатается поверх текста
    самого шага. Диагонали и вторая полка дают запас.
    """
    width, height = label_size(marker.text)
    half = EVENT_SIDE // 2
    cx, cy = marker.cx, marker.cy
    near = half + 4
    far = half + 6 + height
    return [
        (cx - width // 2, cy + half + 2, width, height),
        (cx + near, cy - height // 2, width, height),
        (cx - near - width, cy - height // 2, width, height),
        (cx - width // 2, cy - half - 2 - height, width, height),
        (cx + near, cy + half + 2, width, height),
        (cx - near - width, cy + half + 2, width, height),
        (cx + near, cy - half - 2 - height, width, height),
        (cx - near - width, cy - half - 2 - height, width, height),
        (cx - width // 2, cy + far, width, height),
        (cx - width // 2, cy - far - height, width, height),
    ]


def _band_boxes(x: int, y: int, width: int, height: int, header: int) -> List[Box]:
    """Заголовок дорожки и её рамка — места, куда подпись класть нельзя.

    В полосе слева bpmn.io печатает повёрнутое название дорожки, а по контуру
    рисует линию. Подпись, попавшая туда, ложится либо поверх названия, либо
    ровно на разделитель между дорожками — ровно то, что видно в выгрузке.
    """
    b = LANE_BORDER
    return [
        (x, y, header, height),
        (x, y - b, width, 2 * b),
        (x, y + height - b, width, 2 * b),
    ]


def _duration_marker_xml(marker: DurationMarker, attached_to: str) -> List[str]:
    return [
        f'    <bpmn:boundaryEvent id="{escape_xml(marker.marker_id)}"'
        f' name="{escape_xml(marker.text)}"'
        f' attachedToRef="{escape_xml(attached_to)}" cancelActivity="false">',
        '      <bpmn:timerEventDefinition>',
        '        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">'
        f'{iso_duration(marker.minutes)}</bpmn:timeDuration>',
        '      </bpmn:timerEventDefinition>',
        '    </bpmn:boundaryEvent>',
    ]


def _edge_waypoints(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    obstacles: Optional[Obstacles] = None,
) -> List[Tuple[int, int]]:
    """Ортогональная ломаная — та же, что рисует draw.io.

    Раньше сюда шла только пара «точка выхода — точка входа», и в bpmn.io
    схема выглядела диагональной паутиной. ``obstacles`` даёт трассировщику
    список чужих фигур: без него колено ставится посередине и линия уходит
    сквозь то, что там стоит.
    """
    route = orthogonal_waypoints(edge, src, tgt, None, obstacles)
    if len(route) < 2:
        return [(100, 100), (250, 100)]
    return [(int(round(x)), int(round(y))) for x, y in route]


def normalize_event_types(
    flow_nodes: List[ProcessNode],
    edges: List[ProcessEdge],
) -> Dict[str, str]:
    """Приводит степени событий к требованиям BPMN 2.0.

    Возвращает карту ``id -> эффективный тип``. Исходная модель не меняется:
    понижение типа нужно только для выгрузки.

    * startEvent с входящим переходом -> intermediateThrowEvent;
    * endEvent с исходящим переходом  -> intermediateThrowEvent.
    """
    incoming: Set[str] = set()
    outgoing: Set[str] = set()
    for e in edges:
        if e.kind != 'sequenceFlow':
            continue
        if e.targetId:
            incoming.add(e.targetId)
        if e.sourceId:
            outgoing.add(e.sourceId)

    effective: Dict[str, str] = {}
    for node in flow_nodes:
        kind = node.type
        if kind == 'startEvent' and node.id in incoming:
            kind = 'intermediateThrowEvent'
        elif kind == 'endEvent' and node.id in outgoing:
            kind = 'intermediateThrowEvent'
        effective[node.id] = kind
    return effective


def _node_tag(node: ProcessNode, effective_type: str) -> str:
    if effective_type == 'startEvent':
        return 'bpmn:startEvent'
    if effective_type == 'endEvent':
        return 'bpmn:endEvent'
    if effective_type == 'intermediateThrowEvent':
        return 'bpmn:intermediateThrowEvent'
    if effective_type in ('intermediateTimerEvent', 'intermediateMessageEvent'):
        return 'bpmn:intermediateCatchEvent'
    if effective_type == 'exclusiveGateway':
        return 'bpmn:exclusiveGateway'
    if effective_type == 'parallelGateway':
        return 'bpmn:parallelGateway'
    if effective_type == 'inclusiveGateway':
        return 'bpmn:inclusiveGateway'
    if effective_type == 'complexGateway':
        return 'bpmn:complexGateway'
    if effective_type == 'subProcess':
        return 'bpmn:subProcess'
    if effective_type == 'dataStore':
        return 'bpmn:dataStoreReference'
    if effective_type == 'dataObject':
        return 'bpmn:dataObjectReference'
    if effective_type == 'textAnnotation':
        return 'bpmn:textAnnotation'
    if effective_type == 'serviceTask' or node.category == 'rpa_bot':
        return 'bpmn:serviceTask'
    if effective_type == 'task':
        return 'bpmn:task'
    return 'bpmn:userTask'


def _node_documentation(node: ProcessNode) -> str:
    bits: List[str] = []
    if node.code:
        bits.append(f'Code: {node.code}')
    if node.role:
        bits.append(f'Role: {node.role}')
    if node.laneName and node.laneName != node.role:
        bits.append(f'Lane: {node.laneName}')
    if node.system:
        bits.append(f'System: {node.system}')
    if node.slaMinutes:
        bits.append(f'ST: {node.slaMinutes} min')
    if node.waitMinutes:
        bits.append(f'WT: {node.waitMinutes} min')
    if node.inputArtifacts:
        bits.append(f"In: {', '.join(node.inputArtifacts)}")
    if node.outputArtifacts:
        bits.append(f"Out: {', '.join(node.outputArtifacts)}")
    if node.category:
        bits.append(f'Category: {node.category}')
    if node.automationPotential:
        bits.append(f'RPA potential: {node.automationPotential}%')
    return '; '.join(bits)


#: Ширина заголовочной полосы пула в BPMNDI (bpmn.io рисует ровно 30 px).
POOL_HEADER = 30
#: Полоса с названием дорожки внутри пула — bpmn.io рисует её той же ширины.
LANE_HEADER = 30
#: Толщина рамки дорожки: подпись, севшая на разделитель, читается перечёркнутой.
LANE_BORDER = 4

#: Типы, которым BPMN разрешает быть концом messageFlow (InteractionNode).
_INTERACTION_TYPES = (
    'task', 'userTask', 'serviceTask', 'subProcess',
    'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
)


def split_external_lanes(
    lanes: List[ProcessNode],
    flow_nodes: List[ProcessNode],
) -> Tuple[List[ProcessNode], List[ProcessNode]]:
    """Делит дорожки на полосы пула и внешних участников.

    Дорожка без единого шага — это не зона ответственности внутри организации,
    а внешняя сторона (клиент, госорган): аналитик отводит ей полосу и тянет к
    ней пунктир от шагов банка. В BPMN такая полоса обязана быть отдельным
    участником-«чёрным ящиком», иначе импортёр (PIX Процессная студия) выбросит
    дорожку без ``flowNodeRef`` — и с карты пропадает целая строка вместе со
    всеми пунктирными связями к ней.

    Пустая дорожка, попадающая внутрь вертикального размаха заполненных,
    участником не становится: вынести её из пула значило бы разорвать пул.
    """
    populated = [l for l in lanes if any(n.laneId == l.id for n in flow_nodes)]
    if not populated:
        return lanes, []
    top = min(l.geometry.y for l in populated)
    bottom = max(l.geometry.y + l.geometry.height for l in populated)

    inner: List[ProcessNode] = []
    external: List[ProcessNode] = []
    for lane in lanes:
        g = lane.geometry
        empty = lane not in populated
        overlaps = g.y < bottom and g.y + g.height > top
        (external if empty and not overlaps else inner).append(lane)
    return inner, external


def _union_bounds(nodes: Iterable[ProcessNode], header: int = POOL_HEADER) -> Tuple[int, int, int, int]:
    items = list(nodes)
    if not items:
        return (40, 40, 800, 200)
    min_x = min(n.geometry.x for n in items)
    min_y = min(n.geometry.y for n in items)
    max_x = max(n.geometry.x + n.geometry.width for n in items)
    max_y = max(n.geometry.y + n.geometry.height for n in items)
    x = min_x - header
    y = min_y
    return (x, y, max(max_x - x, 80), max(max_y - y, 80))


def generate_bpmn_xml(process: BusinessProcess) -> str:
    # Дорожки укладываются в сплошную стопку ДО всего остального, вместе со
    # своим содержимым. По спецификации дорожка — раздел пула, а не отдельный
    # прямоугольник на холсте: щель или нахлёст между полосами импортёр
    # вынужден исправлять сам, и в Процессной студии меньшие дорожки уходят
    # под большие. См. ``map_layout.stack_lanes``.
    process = stack_lanes(process)

    used: Dict[str, str] = {}
    taken: set = set()

    proc_id = _safe_id(
        f"Process_{re.sub(r'[^A-Za-z0-9_]', '_', process.passport.code or 'SQB')}",
        'Process',
        used,
        taken,
    )
    def_id = _safe_id(f"Definitions_{process.id}", 'Definitions', used, taken)
    diag_id = _safe_id(f'Diagram_{proc_id}', 'Diagram', used, taken)
    plane_id = _safe_id(f'Plane_{proc_id}', 'Plane', used, taken)
    collab_id = _safe_id(f'Collaboration_{proc_id}', 'Collaboration', used, taken)
    participant_id = _safe_id(f'Participant_{proc_id}', 'Participant', used, taken)
    lane_set_id = _safe_id(f'LaneSet_{proc_id}', 'LaneSet', used, taken)

    all_nodes = [n for n in process.nodes if n.type != 'lane']
    flow_nodes = [n for n in all_nodes if n.type not in ARTIFACT_NODE_TYPES]
    artifact_nodes = [n for n in all_nodes if n.type in ARTIFACT_NODE_TYPES]
    lanes, external_lanes = split_external_lanes(list(process.lanes or []), flow_nodes)
    external_by_id = {lane.id: lane for lane in external_lanes}
    node_by_id = {n.id: n for n in all_nodes}
    valid_ids = set(node_by_id)

    # Точки контакта с внешним участником: один конец — его полоса, другой —
    # шаг или событие процесса. В BPMN это messageFlow внутри collaboration.
    message_flows = [
        e for e in process.edges
        if e.kind == 'messageFlow'
        and (
            (e.sourceId in external_by_id and node_by_id.get(e.targetId or '') is not None
             and node_by_id[e.targetId].type in _INTERACTION_TYPES)
            or (e.targetId in external_by_id and node_by_id.get(e.sourceId or '') is not None
                and node_by_id[e.sourceId].type in _INTERACTION_TYPES)
        )
    ]

    # Висячие связи и оформительские линии draw.io в схему не идут: у
    # annotationLine хотя бы один конец не опирается на шаг процесса.
    # Петля из фигуры в саму себя тоже не идёт: спецификация её не допускает,
    # а PIX Процессная студия из-за одной такой линии отказывается открыть всю
    # карту («Connector source and target node cannot be the same»).
    edges = [
        e for e in process.edges
        if e.kind != 'annotationLine'
        and e.sourceId in valid_ids and e.targetId in valid_ids
        and e.sourceId != e.targetId
    ]
    # messageFlow между двумя шагами одного пула спецификация запрещает: карта
    # SQB — один пул, поэтому такие связи выгружаются как поток управления.
    sequence_edges = [e for e in edges if e.kind != 'association']
    association_edges = [e for e in edges if e.kind == 'association']

    # ── Связи с данными отделяются от связей с примечаниями ────────────────
    #
    # ``bpmn:association`` по спецификации соединяет АРТЕФАКТ (текстовое
    # примечание, группу) с чем угодно. Хранилище данных и документ артефактами
    # не являются: это ItemAwareElement, и подключаются они к активности через
    # ``dataInputAssociation`` / ``dataOutputAssociation`` ВНУТРИ неё. Разница
    # не формальная — Процессная студия обычную ассоциацию к хранилищу просто
    # не рисует, и на импортированной карте связи «шаг ↔ база» пропадали.
    #
    # Данные разрешено привязывать только к активности. Если на другом конце
    # шлюз или событие, связь остаётся ассоциацией: спецификация не даёт таким
    # узлам ни ioSpecification, ни dataInputAssociation.
    _data_types = ('dataStore', 'dataObject')
    node_type_of = {n.id: n.type for n in all_nodes}
    data_edges: List[ProcessEdge] = []
    plain_associations: List[ProcessEdge] = []
    for edge in association_edges:
        s_type = node_type_of.get(edge.sourceId or '')
        t_type = node_type_of.get(edge.targetId or '')
        data_to_task = s_type in _data_types and t_type in TASK_NODE_TYPES
        task_to_data = s_type in TASK_NODE_TYPES and t_type in _data_types
        (data_edges if data_to_task or task_to_data else plain_associations).append(edge)

    #: Шаг -> связи с данными, которые надо перечислить внутри него.
    data_in_of: Dict[str, List[ProcessEdge]] = {}
    data_out_of: Dict[str, List[ProcessEdge]] = {}
    for edge in data_edges:
        if node_type_of.get(edge.sourceId or '') in _data_types:
            data_in_of.setdefault(edge.targetId, []).append(edge)
        else:
            data_out_of.setdefault(edge.sourceId, []).append(edge)

    effective_type = normalize_event_types(flow_nodes, sequence_edges)

    id_of: Dict[str, str] = {}
    for n in flow_nodes:
        id_of[n.id] = _safe_id(n.id, 'Node', used, taken)
    for n in artifact_nodes:
        id_of[n.id] = _safe_id(n.id, 'Artifact', used, taken)
    for lane in lanes:
        id_of[lane.id] = _safe_id(lane.id, 'Lane', used, taken)
    external_process_of: Dict[str, str] = {}
    for lane in external_lanes:
        id_of[lane.id] = _safe_id(f'Participant_{lane.id}', 'Participant', used, taken)
        external_process_of[lane.id] = _safe_id(
            f'Process_{lane.id}', 'Process', used, taken
        )
    for edge in edges:
        id_of[edge.id] = _safe_id(edge.id, 'Flow', used, taken)
    for edge in message_flows:
        id_of[edge.id] = _safe_id(edge.id, 'MessageFlow', used, taken)

    markers = duration_markers(flow_nodes, id_of, used, taken)
    marker_of = {m.node.id: m for m in markers}

    incoming_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    outgoing_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    for edge in sequence_edges:
        if edge.targetId in incoming_by_node:
            incoming_by_node[edge.targetId].append(edge.id)
        if edge.sourceId in outgoing_by_node:
            outgoing_by_node[edge.sourceId].append(edge.id)

    use_collab = bool(lanes) or bool(external_lanes)
    plane_ref = collab_id if use_collab else proc_id
    process_name = escape_xml(process.passport.name or process.name or 'Business process')
    comment = process_name.replace('--', '—')

    xml: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        '  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
        '  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
        '  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
        f'  id="{escape_xml(def_id)}"',
        '  targetNamespace="http://bpmn.io/schema/bpmn"',
        '  exporter="SQB Process Hub"',
        '  exporterVersion="2.0">',
        '',
        f'  <!-- Process map: {comment} -->',
    ]

    if use_collab:
        pool_name = escape_xml(process.passport.name or process.name or 'Pool')
        xml.append(f'  <bpmn:collaboration id="{escape_xml(collab_id)}">')
        xml.append(
            f'    <bpmn:participant id="{escape_xml(participant_id)}" '
            f'name="{pool_name}" processRef="{escape_xml(proc_id)}" />'
        )
        # Внешний участник ссылается на собственный пустой процесс. Без
        # processRef импортёр считает пул свёрнутым и печатает имя по центру
        # полосы: на карте шириной 4620 px подпись уезжала за экран, и строка
        # выглядела безымянной. С процессом пул раскрыт, и имя стоит в шапке
        # слева — там же, где имена дорожек.
        for lane in external_lanes:
            xml.append(
                f'    <bpmn:participant id="{escape_xml(id_of[lane.id])}" '
                f'name="{escape_xml(lane.name)}" '
                f'processRef="{escape_xml(external_process_of[lane.id])}" />'
            )
        for edge in message_flows:
            src_id = id_of[edge.sourceId]
            tgt_id = id_of[edge.targetId]
            name = (edge.name or edge.condition or '').strip()
            name_attr = f' name="{escape_xml(name)}"' if name else ''
            xml.append(
                f'    <bpmn:messageFlow id="{escape_xml(id_of[edge.id])}"{name_attr}'
                f' sourceRef="{escape_xml(src_id)}" targetRef="{escape_xml(tgt_id)}" />'
            )
        xml.append('  </bpmn:collaboration>')
        xml.append('')

    xml.append(
        # processType и isExecutable проставлены явно: значение по умолчанию
        # («None») стандарт разрешает опустить, но профиль выгрузки для PIX
        # требует обоих атрибутов в тексте файла. Основной процесс — Private:
        # это внутренняя оркестровка банка, а не публичный интерфейс.
        f'  <bpmn:process id="{escape_xml(proc_id)}" name="{process_name}"'
        f' processType="Private" isExecutable="true">'
    )

    # ── 1. laneSet (только узлы потока) ─────────────────────────────────────
    if lanes:
        xml.append(f'    <bpmn:laneSet id="{escape_xml(lane_set_id)}">')
        for lane in lanes:
            xml.append(
                f'      <bpmn:lane id="{escape_xml(id_of[lane.id])}" name="{escape_xml(lane.name)}">'
            )
            for child in flow_nodes:
                if child.laneId != lane.id:
                    continue
                xml.append(
                    f'        <bpmn:flowNodeRef>{escape_xml(id_of[child.id])}</bpmn:flowNodeRef>'
                )
                # Значок длительности — тоже узел потока: дорожка без
                # ссылки на него импортируется без часов у своих шагов.
                marker = marker_of.get(child.id)
                if marker is not None:
                    xml.append(
                        '        <bpmn:flowNodeRef>'
                        f'{escape_xml(marker.marker_id)}</bpmn:flowNodeRef>'
                    )
            xml.append('      </bpmn:lane>')
        xml.append('    </bpmn:laneSet>')

    # ── 2. Узлы потока ──────────────────────────────────────────────────────
    for node in flow_nodes:
        nid = escape_xml(id_of[node.id])
        kind = effective_type[node.id]
        tag = _node_tag(node, kind)
        name_attr = f' name="{escape_xml(map_label(node))}"'
        extras: List[str] = []
        if kind in _GATEWAY_TYPES:
            if len(outgoing_by_node.get(node.id, [])) > 1:
                extras.append(' gatewayDirection="Diverging"')
            elif len(incoming_by_node.get(node.id, [])) > 1:
                extras.append(' gatewayDirection="Converging"')

        children: List[str] = []
        doc = _node_documentation(node)
        if doc:
            children.append(f'      <bpmn:documentation>{escape_xml(doc)}</bpmn:documentation>')
        for edge_id in incoming_by_node.get(node.id, []):
            children.append(f'      <bpmn:incoming>{escape_xml(id_of[edge_id])}</bpmn:incoming>')
        for edge_id in outgoing_by_node.get(node.id, []):
            children.append(f'      <bpmn:outgoing>{escape_xml(id_of[edge_id])}</bpmn:outgoing>')
        # Порядок внутри активности задан XSD: сначала incoming/outgoing из
        # tFlowNode, затем property и dataInput/OutputAssociation из tActivity.
        # Перепутав его, файл получаем невалидный по схеме.
        for edge in data_in_of.get(node.id, []):
            eid = escape_xml(id_of[edge.id])
            # targetRef обязан указывать на ItemAwareElement самой активности,
            # а не на неё саму. Стандартный приём (так делает и bpmn.io) —
            # property-заглушка: она объявляет «вход» шага, к которому и
            # привязывается хранилище.
            prop_id = escape_xml(f'{eid}_target')
            children.append(
                f'      <bpmn:property id="{prop_id}" name="__targetRef_placeholder" />')
            children.append(f'      <bpmn:dataInputAssociation id="{eid}">')
            children.append(
                f'        <bpmn:sourceRef>{escape_xml(id_of[edge.sourceId])}</bpmn:sourceRef>')
            children.append(f'        <bpmn:targetRef>{prop_id}</bpmn:targetRef>')
            children.append('      </bpmn:dataInputAssociation>')
        for edge in data_out_of.get(node.id, []):
            eid = escape_xml(id_of[edge.id])
            # У выходной связи источник — сама активность, поэтому sourceRef
            # не пишется: спецификация выводит его из места объявления.
            children.append(f'      <bpmn:dataOutputAssociation id="{eid}">')
            children.append(
                f'        <bpmn:targetRef>{escape_xml(id_of[edge.targetId])}</bpmn:targetRef>')
            children.append('      </bpmn:dataOutputAssociation>')

        if kind == 'intermediateTimerEvent':
            children.append('      <bpmn:timerEventDefinition>')
            children.append(
                '        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">'
                f'{iso_duration(node.slaMinutes)}</bpmn:timeDuration>'
            )
            children.append('      </bpmn:timerEventDefinition>')
        elif kind == 'intermediateMessageEvent':
            children.append('      <bpmn:messageEventDefinition />')

        extra = ''.join(extras)
        if children:
            xml.append(f'    <{tag} id="{nid}"{name_attr}{extra}>')
            xml.extend(children)
            xml.append(f'    </{tag}>')
        else:
            xml.append(f'    <{tag} id="{nid}"{name_attr}{extra} />')

        marker = marker_of.get(node.id)
        if marker is not None:
            xml.extend(_duration_marker_xml(marker, nid))

    # ── 3. Артефакты-элементы потока: хранилища и документы ─────────────────
    for node in artifact_nodes:
        if node.type == 'textAnnotation':
            continue
        tag = _node_tag(node, node.type)
        xml.append(
            f'    <{tag} id="{escape_xml(id_of[node.id])}" name="{escape_xml(node.name)}" />'
        )

    # ── 4. Переходы ─────────────────────────────────────────────────────────
    for edge in sequence_edges:
        eid = escape_xml(id_of[edge.id])
        name = edge.name or edge.condition or ''
        name_attr = f' name="{escape_xml(name)}"' if name else ''
        src_attr = f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
        tgt_attr = f' targetRef="{escape_xml(id_of[edge.targetId])}"'
        expr = (edge.condition or edge.name or '').strip()
        if expr:
            xml.append(f'    <bpmn:sequenceFlow id="{eid}"{name_attr}{src_attr}{tgt_attr}>')
            xml.append(
                '      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
                f'{escape_xml(expr)}</bpmn:conditionExpression>'
            )
            xml.append('    </bpmn:sequenceFlow>')
        else:
            xml.append(f'    <bpmn:sequenceFlow id="{eid}"{name_attr}{src_attr}{tgt_attr} />')

    # ── 5. Артефакты по XSD идут последними: примечания и ассоциации ────────
    for node in artifact_nodes:
        if node.type != 'textAnnotation':
            continue
        xml.append(f'    <bpmn:textAnnotation id="{escape_xml(id_of[node.id])}">')
        xml.append(f'      <bpmn:text>{escape_xml(node.name)}</bpmn:text>')
        xml.append('    </bpmn:textAnnotation>')

    for edge in plain_associations:
        xml.append(
            f'    <bpmn:association id="{escape_xml(id_of[edge.id])}"'
            f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
            f' targetRef="{escape_xml(id_of[edge.targetId])}"'
            ' associationDirection="One" />'
        )

    xml.append('  </bpmn:process>')
    xml.append('')

    for lane in external_lanes:
        xml.append(
            # Внешняя сторона — «чёрный ящик»: поведения у неё в файле нет,
            # видна только граница взаимодействия, поэтому Public.
            f'  <bpmn:process id="{escape_xml(external_process_of[lane.id])}" '
            f'name="{escape_xml(lane.name)}" processType="Public" isExecutable="false" />'
        )
    if external_lanes:
        xml.append('')

    # ── Диаграмма ───────────────────────────────────────────────────────────
    xml.append(f'  <bpmndi:BPMNDiagram id="{escape_xml(diag_id)}">')
    xml.append(
        f'    <bpmndi:BPMNPlane id="{escape_xml(plane_id)}" bpmnElement="{escape_xml(plane_ref)}">'
    )

    if use_collab:
        # Пул охватывает дорожки и узлы; полосы внешних участников — отдельные
        # пулы и в его границы не входят.
        # Пул — ровно объединение своих дорожек. Раньше сюда входили ещё и
        # узлы, и если хоть один стоял выше первой дорожки, пул начинался
        # на 120-260 px выше неё: сверху оставалась полоса, не принадлежащая
        # ни одной дорожке, и импортёр раскладывал полосы по-своему.
        stacked = pool_bounds(lanes)
        px, py, pw, ph = stacked if stacked else _union_bounds(lanes + all_nodes)
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(participant_id)}_di" '
            f'bpmnElement="{escape_xml(participant_id)}" isHorizontal="true">'
        )
        xml.append(f'        <dc:Bounds x="{px}" y="{py}" width="{pw}" height="{ph}" />')
        xml.append('      </bpmndi:BPMNShape>')
        for lane in external_lanes:
            xml.append(
                f'      <bpmndi:BPMNShape id="{escape_xml(id_of[lane.id])}_di" '
                f'bpmnElement="{escape_xml(id_of[lane.id])}" isHorizontal="true">'
            )
            xml.append(
                f'        <dc:Bounds x="{lane.geometry.x}" y="{lane.geometry.y}" '
                f'width="{lane.geometry.width}" height="{lane.geometry.height}" />'
            )
            xml.append('      </bpmndi:BPMNShape>')
    else:
        px, pw = 0, 0

    for lane in lanes:
        # Полосы обязаны тайлиться внутри пула: иначе импортёр рисует их
        # уступами, а часть карты оказывается за пределами дорожек. По X и
        # ширине полоса притягивается к пулу, по Y и высоте берётся из стопки,
        # уложенной в ``map_layout.stack_lanes``, — там они уже сплошные.
        lane_x = px + POOL_HEADER if use_collab else lane.geometry.x
        lane_w = max(pw - POOL_HEADER, 80) if use_collab else lane.geometry.width
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[lane.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[lane.id])}" isHorizontal="true">'
        )
        xml.append(
            f'        <dc:Bounds x="{lane_x}" y="{lane.geometry.y}" '
            f'width="{lane_w}" height="{lane.geometry.height}" />'
        )
        xml.append('      </bpmndi:BPMNShape>')

    # ── Подписи ────────────────────────────────────────────────────────────
    # Позиции считаем ДО отрисовки: подпись шлюза, подпись связи и подпись
    # соседнего события претендуют на одно и то же место под фигурой, и
    # разводить их можно, только зная все занятые прямоугольники сразу.
    # Препятствия считаются один раз на схему: дорожки и пулы в них не входят,
    # связь между дорожками обязана их пересекать.
    # Значок длительности рисует сам экспортёр, в модели его нет. Пока
    # трассировщик о нём не знал, линии шли прямо по часам — в PMM на это
    # приходилась половина всех пересечений. Хозяин у рамки свой шаг: связь,
    # идущая в этот шаг, проходит рядом со значком по праву.
    route_obstacles = build_obstacles(
        all_nodes,
        extra=[(marker.marker_id, marker.node.id, marker_box(marker))
               for marker in markers],
    )
    routes: List[Tuple[ProcessEdge, List[Tuple[int, int]]]] = []
    for edge in edges:
        routes.append((
            edge,
            _edge_waypoints(
                edge,
                node_by_id.get(edge.sourceId or ''),
                node_by_id.get(edge.targetId or ''),
                route_obstacles,
            ),
        ))
    for edge in message_flows:
        lane_is_source = edge.sourceId in external_by_id
        lane = external_by_id[edge.sourceId if lane_is_source else edge.targetId]
        peer = node_by_id[edge.targetId if lane_is_source else edge.sourceId]
        src_node, tgt_node = message_flow_endpoints(edge, peer, lane, lane_is_source)
        routes.append((edge, _edge_waypoints(edge, src_node, tgt_node, route_obstacles)))

    taken_boxes: List[Tuple[int, int, int, int]] = node_obstacles(all_nodes)
    # Заголовки дорожек и пулов — не просто занятое место, а запретное: в них
    # уже напечатано повёрнутое название, и вторая надпись поверх складывается
    # с ним в нечитаемую кашу. Держим их отдельным списком с большим весом.
    forbidden_boxes: List[Tuple[int, int, int, int]] = []
    if use_collab:
        forbidden_boxes.append((px, py, POOL_HEADER, ph))
        for lane in external_lanes:
            g = lane.geometry
            forbidden_boxes.extend(_band_boxes(g.x, g.y, g.width, g.height, POOL_HEADER))
    for lane in lanes:
        g = lane.geometry
        lane_x = px + POOL_HEADER if use_collab else g.x
        lane_w = max(pw - POOL_HEADER, 80) if use_collab else g.width
        forbidden_boxes.extend(_band_boxes(lane_x, g.y, lane_w, g.height, LANE_HEADER))
    # Часы у шага занимают место на карте так же, как фигура: подпись связи,
    # положенная на них, скрывает цифру.
    taken_boxes.extend(marker_box(m) for m in markers)
    # Линии связей — тоже препятствие: подпись, положенная на связь, читается
    # как перечёркнутая.
    for _, route in routes:
        taken_boxes.extend(segment_boxes(route))
    edge_label_of: Dict[str, Tuple[int, int, int, int]] = {}
    for edge, route in routes:
        text = (edge.name or edge.condition or '').strip()
        if not text:
            continue
        box = choose_label_box(
            edge_label_candidates(route, text), taken_boxes, forbidden_boxes)
        edge_label_of[edge.id] = box
        taken_boxes.append(box)

    node_label_of: Dict[str, Tuple[int, int, int, int]] = {}
    for node in all_nodes:
        # Смотрим на подпись, которая реально уедет в схему: у безымянного
        # шлюза она пустая, и место под неё резервировать не за чем.
        if node.type not in EXTERNAL_LABEL_TYPES or not map_label(node).strip():
            continue
        # Сама фигура своей подписи не мешает: подпись события печатается
        # вплотную к нему и обязана иметь право стоять рядом.
        around = [b for b in taken_boxes if b != (
            node.geometry.x, node.geometry.y, node.geometry.width, node.geometry.height
        )]
        box = choose_label_box(external_label_candidates(node), around, forbidden_boxes)
        node_label_of[node.id] = box
        taken_boxes.append(box)

    # Время под часами кладём последним: оно уступает место подписям связей
    # и событий, а не наоборот — цифру читают, подойдя к конкретному шагу.
    marker_label_of: Dict[str, Box] = {}
    for marker in markers:
        box = choose_label_box(_duration_label_candidates(marker), taken_boxes, forbidden_boxes)
        marker_label_of[marker.marker_id] = box
        taken_boxes.append(box)

    def _label_xml(box: Tuple[int, int, int, int], indent: str) -> List[str]:
        lx, ly, lw, lh = box
        return [
            f'{indent}<bpmndi:BPMNLabel>',
            f'{indent}  <dc:Bounds x="{lx}" y="{ly}" width="{lw}" height="{lh}" />',
            f'{indent}</bpmndi:BPMNLabel>',
        ]

    for node in all_nodes:
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[node.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[node.id])}">'
        )
        xml.append(
            f'        <dc:Bounds x="{node.geometry.x}" y="{node.geometry.y}" '
            f'width="{node.geometry.width}" height="{node.geometry.height}" />'
        )
        # Подпись события, шлюза и артефакта импортёр рисует вне фигуры и
        # переносит по рамке в 90 px. Без явных границ длинное имя шлюза
        # превращается в столбец, накрывающий соседние шаги и подписи связей.
        if node.id in node_label_of:
            xml.extend(_label_xml(node_label_of[node.id], '        '))
        xml.append('      </bpmndi:BPMNShape>')

        marker = marker_of.get(node.id)
        if marker is not None:
            mx, my, mw, mh = marker_box(marker)
            xml.append(
                f'      <bpmndi:BPMNShape id="{escape_xml(marker.marker_id)}_di" '
                f'bpmnElement="{escape_xml(marker.marker_id)}">'
            )
            xml.append(f'        <dc:Bounds x="{mx}" y="{my}" width="{mw}" height="{mh}" />')
            xml.extend(_label_xml(marker_label_of[marker.marker_id], '        '))
            xml.append('      </bpmndi:BPMNShape>')

    for edge, route in routes:
        xml.append(
            f'      <bpmndi:BPMNEdge id="{escape_xml(id_of[edge.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[edge.id])}">'
        )
        for x, y in route:
            xml.append(f'        <di:waypoint x="{x}" y="{y}" />')
        if edge.id in edge_label_of:
            xml.extend(_label_xml(edge_label_of[edge.id], '        '))
        xml.append('      </bpmndi:BPMNEdge>')

    xml.append('    </bpmndi:BPMNPlane>')
    xml.append('  </bpmndi:BPMNDiagram>')
    xml.append('</bpmn:definitions>')
    return '\n'.join(xml)
