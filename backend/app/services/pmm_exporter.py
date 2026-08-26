"""PIX Process Studio native package (.pmm = ZIP of three XML parts)."""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from typing import Dict, List, Tuple

from app.models.process import BusinessProcess, ProcessNode

_NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_NS_XSD = 'http://www.w3.org/2001/XMLSchema'
_PIX_NS = uuid.UUID('8b2e0c5a-4d71-4f3a-9c1e-6a7f0d2b9e11')


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


def _pix_id(raw: str) -> str:
    return str(uuid.uuid5(_PIX_NS, raw or 'node'))


def _map_slug(process: BusinessProcess) -> str:
    raw = process.passport.code or process.name or 'map'
    slug = re.sub(r'[^A-Za-z0-9_-]+', '_', raw).strip('_')[:40]
    return slug or 'map'


def _pix_type(node: ProcessNode) -> str:
    if node.type == 'startEvent':
        return 'start_event_none'
    if node.type == 'endEvent':
        return 'end_event_none'
    if node.type == 'exclusiveGateway':
        return 'gateway_xor'
    if node.type == 'parallelGateway':
        return 'gateway_parallel'
    if node.type == 'inclusiveGateway':
        return 'gateway_or'
    if node.type == 'serviceTask' or node.category == 'rpa_bot':
        return 'serviceTask'
    if node.type == 'userTask':
        return 'userTask'
    return 'task'


def _attr(name: str, value, skip_empty: bool = True) -> str:
    if value is None:
        return ''
    text = str(value)
    if skip_empty and text == '':
        return ''
    return f' {name}="{escape_xml(text)}"'


def _node_xml(
    pix_type: str,
    nid: str,
    label: str,
    x: int,
    y: int,
    w: int,
    h: int,
    extra: str = '',
    fill: str = 'var(--bg-accent-node)',
) -> str:
    bits = (
        f'    <node type="{escape_xml(pix_type)}" id="{escape_xml(nid)}"'
        f'{_attr("label", label, skip_empty=False)} number="0"'
        f' x="{int(x)}" y="{int(y)}" width="{int(max(w, 8))}" height="{int(max(h, 8))}"'
        f' fill_color="{escape_xml(fill)}"{extra} />'
    )
    return bits


def _rel(child: ProcessNode, parent: ProcessNode) -> Tuple[int, int]:
    return (child.geometry.x - parent.geometry.x, child.geometry.y - parent.geometry.y)


def generate_map_xml(process: BusinessProcess) -> Tuple[str, str]:
    slug = _map_slug(process)
    id_map: Dict[str, str] = {}
    flow = [n for n in process.nodes if n.type != 'lane']
    lanes = list(process.lanes or [])
    for n in flow + lanes:
        id_map[n.id] = _pix_id(n.id)

    lines: List[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        (
            f'<Map xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}" '
            f'name="{escape_xml(slug)}" notation="bpmn" paperEnabled="false" paperType="0">'
        ),
    ]

    title = process.passport.name or process.name or slug
    bounds_nodes = lanes or flow
    if bounds_nodes:
        min_y = min(n.geometry.y for n in bounds_nodes)
        min_x = min(n.geometry.x for n in bounds_nodes)
        max_x = max(n.geometry.x + n.geometry.width for n in bounds_nodes)
    else:
        min_x, min_y, max_x = 0, 120, 400
    lines.append(
        _node_xml(
            'emptyPool',
            _pix_id(f'title:{process.id}'),
            title,
            min_x,
            min_y - 120,
            max(max_x - min_x, 400),
            90,
            extra=' font_size="28"',
        )
    )

    assigned = set()
    for lane in lanes:
        lid = id_map[lane.id]
        children = [n for n in flow if n.laneId == lane.id]
        for n in children:
            assigned.add(n.id)
        open_tag = (
            f'  <node type="horizontalRoad" id="{escape_xml(lid)}"'
            f' label="{escape_xml(lane.name)}" number="0"'
            f' x="{lane.geometry.x}" y="{lane.geometry.y}"'
            f' width="{max(lane.geometry.width, 80)}" height="{max(lane.geometry.height, 80)}"'
            f' fill_color="var(--bg-accent-road-node)">'
        )
        lines.append(open_tag)
        for n in children:
            rx, ry = _rel(n, lane)
            extra = ''
            if 'Gateway' in n.type:
                extra = ' labelPlacement="Left" font_size="16"'
            lines.append(
                '  ' + _node_xml(_pix_type(n), id_map[n.id], n.name, rx, ry, n.geometry.width, n.geometry.height, extra)
            )
        lines.append('  </node>')

    for n in flow:
        if n.id in assigned:
            continue
        extra = ' labelPlacement="Left" font_size="16"' if 'Gateway' in n.type else ''
        lines.append(
            _node_xml(
                _pix_type(n),
                id_map[n.id],
                n.name,
                n.geometry.x,
                n.geometry.y,
                n.geometry.width,
                n.geometry.height,
                extra,
            )
        )

    lane_ids = {l.id for l in lanes}
    for edge in process.edges:
        if not edge.sourceId or not edge.targetId:
            continue
        if edge.sourceId not in id_map or edge.targetId not in id_map:
            continue
        if edge.sourceId in lane_ids or edge.targetId in lane_ids:
            continue
        dashed = bool(edge.dashed)
        line_style = 'dotted' if dashed else 'solid'
        marker_end = 'arrowLine' if dashed else 'arrowclosed'
        cid = _pix_id(edge.id)
        label = (edge.name or edge.condition or '').strip()
        label_attr = _attr('label', label)
        lines.append(
            f'  <connector id="{escape_xml(cid)}" type="step" lineStyle="{line_style}"'
            f' sourceNodeId="{escape_xml(id_map[edge.sourceId])}"'
            f' targetNodeId="{escape_xml(id_map[edge.targetId])}"'
            f' targetPoint="6"{label_attr}>'
        )
        lines.append('    <MarkerStart>line</MarkerStart>')
        lines.append('    <MarkerMiddle />')
        lines.append(f'    <MarkerEnd>{marker_end}</MarkerEnd>')
        for i, pt in enumerate(edge.points or []):
            lines.append(f'    <waypoint x="{pt.x}" y="{pt.y}" index="{i}" />')
        lines.append('    <labelPosition>50</labelPosition>')
        lines.append('    <color>var(--fg-gray-primary)</color>')
        lines.append('    <fontSize>12</fontSize>')
        lines.append('    <fontBold>false</fontBold>')
        lines.append('    <fontItalic>false</fontItalic>')
        lines.append('    <fontUnderline>false</fontUnderline>')
        lines.append('    <fontStrikethrough>false</fontStrikethrough>')
        lines.append('  </connector>')

    lines.append('</Map>')
    return slug, '\n'.join(lines) + '\n'


def _el(name: str, display: str, extra: str = '') -> str:
    return f'    <element name="{escape_xml(name)}" displayName="{escape_xml(display)}"{extra} />'


def generate_configuration_xml() -> str:
    """Property catalog + notation registry from a real PIX PMM sample."""
    props = [
        (4, 'system_description', 'Описание', ' visible="false" defaultProperty="true"', 1, False),
        (5, 'system_technology', 'Технология', ' visible="false" defaultProperty="true"', 1, False),
        (12, 'vladelets_protsessa', 'Владелец процесса', '', 21, False),
        (16, 'prioritet', 'Приоритет', '', 26, False),
        (9, 'vremya_ozhidaniya', 'Время ожидания', '', 7, False),
        (17, 'menezher_etapa', 'Менеджер этапа', '', 21, False),
        (7, 'uroven_avtomatizatsii1', 'уровень автоматизации1', ' isRequired="true"', 22, False),
        (1, 'attached_files', 'Прикрепленные файлы', ' isMulti="true"', 9, False),
        (2, 'document', 'Документ', '', 9, False),
        (3, 'process_avtomatization_lvl', 'Уровень автоматизации', '', 16, False),
        (14, 'khranilishcha_failov', 'Хранилища файлов', ' isMulti="true"', 9, False),
        (18, 'attached_files', 'Прикрепленные файлы', ' defaultProperty="true"', 9, False),
        (13, 'informatsionnaya_sistema', 'Информационная система', '', 25, False),
        (19, 'document', 'Документ', ' defaultProperty="true"', 9, False),
        (8, 'vremya_protsessa', 'Время процесса', '', 7, False),
        (20, 'system_probability', 'Вероятность', ' visible="false" defaultProperty="true"', 3, False),
        (15, 'podtverzhdayushchie_dokumenti', 'Подтверждающие документы', ' isMulti="true"', 9, False),
        (21, 'system_process_time', 'Время процесса', ' visible="false" defaultProperty="true"', 7, False),
        (11, 'ispolnitel', 'Исполнитель', ' isRequired="true"', 21, False),
        (10, 'zarplata', 'Зарплата', ' isRequired="true"', 21, False),
        (6, 'otvetsvennii', 'ответственный', ' isRequired="true"', 21, False),
    ]
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<configuration xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}">',
    ]
    for pid, name, display, extra, type_id, _ in props:
        lines.append(
            f'  <propertyTemplate id="{pid}" name="{name}" displayName="{escape_xml(display)}"'
            f'{extra} group="main" typeId="{type_id}">'
        )
        lines.append('    <column />')
        lines.append('  </propertyTemplate>')

    bpmn = [
        ('input', 'Текст', ''),
        ('dataStorage', 'Хранилище данных', ' type="Артефакты"'),
        ('verticalRoad', 'Вертикальный пул', ' type="Участники"'),
        ('horizontalRoad', 'Горизонтальный пул', ' type="Участники"'),
        ('emptyPool', 'Пустой пул', ' type="Участники"'),
        ('task', 'Задача', ' type="Задачи"'),
        ('userTask', 'Пользовательская задача', ' type="Задачи"'),
        ('serviceTask', 'Сервисная задача', ' type="Задачи"'),
        ('manualTask', 'Ручная задача', ' type="Задачи"'),
        ('scriptTask', 'Сценарий', ' type="Задачи"'),
        ('businessRuleTask', 'Бизнес-правило', ' type="Задачи"'),
        ('receiving_message_activity', 'Получение сообщения', ' type="Задачи"'),
        ('sending_message_activity', 'Отправка сообщения', ' type="Задачи"'),
        ('gateway_xor', 'Эксклюзивный шлюз', ' type="Шлюзы"'),
        ('gateway_parallel', 'Параллельный шлюз', ' type="Шлюзы"'),
        ('gateway_or', 'Неэксклюзивный шлюз', ' type="Шлюзы"'),
        ('gateway_complex', 'Комплексный шлюз', ' type="Шлюзы"'),
        ('gateway_eventbased', 'Событийный шлюз', ' type="Шлюзы"'),
        ('start_event_none', 'Стартовое событие', ' type="События" subType="Стартовые события"'),
        ('start_event_timer', 'Стартовое событие - таймер', ' type="События" subType="Стартовые события"'),
        ('end_event_none', 'Конечное событие', ' type="События" subType="Конечные события"'),
        ('end_event_terminate', 'Конечное событие - завершение', ' type="События" subType="Конечные события"'),
        ('intermediate_event_catch_timer', 'Промежуточное событие-обработчик – таймер', ' type="События" subType="Промежуточные события"'),
        ('intermediate_event_catch_none', 'Промежуточное событие', ' type="События" subType="Промежуточные события"'),
        ('sub_process', 'Подпроцесс - развернутый', ' type="Подпроцессы"'),
        ('sub_process_collapsed', 'Подпроцесс - свернутый', ' type="Подпроцессы" canHaveChildren="true"'),
        ('callActivity', 'Вызов', ' type="Подпроцессы"'),
        ('dataObject', 'Объект данных', ' type="Артефакты"'),
        ('inputData', 'Входные данные', ' type="Артефакты"'),
        ('outputData', 'Выходные данные', ' type="Артефакты"'),
        ('group_none', 'Группа', ' type="Артефакты"'),
    ]
    lines.append('  <notation name="BPMN">')
    for name, display, extra in bpmn:
        lines.append(_el(name, display, extra))
    lines.append('  </notation>')

    lines.append('  <notation name="Workflow">')
    for name, display, extra in [
        ('horizontalRoad', 'Горизонтальная дорожка', ''),
        ('verticalRoad', 'Вертикальная дорожка', ''),
        ('input', 'Текст', ''),
        ('in_out', 'Вход/Выход', ''),
        ('informationSystems', 'Информационные системы', ''),
        ('superProcess', 'Процесс', ' canHaveChildren="true"'),
        ('ifElement', 'Условие', ''),
        ('workflow_group', 'Группа', ''),
    ]:
        lines.append(_el(name, display, extra))
    lines.append('  </notation>')

    for notation, elements in [
        ('EPC', [('event', 'Событие'), ('function', 'Функция'), ('xor', 'XOR'), ('and', 'AND'), ('or', 'OR')]),
        ('VAD', [('process', 'Процесс'), ('interface', 'Интерфейс'), ('input', 'Текст')]),
        ('DFD', [('process', 'Процесс'), ('dataStore', 'Хранилище'), ('externalEntity', 'Внешняя сущность')]),
        ('uml_component', [('component', 'Компонент'), ('interface', 'Интерфейс')]),
        ('uml_deployment', [('node', 'Узел'), ('artifact', 'Артефакт')]),
        ('c4', [('person', 'Person'), ('softwareSystem', 'Software System'), ('container', 'Container')]),
        ('ArchiMate', [('business_process', 'Business Process'), ('application_component', 'Application Component')]),
    ]:
        lines.append(f'  <notation name="{escape_xml(notation)}">')
        for name, display in elements:
            lines.append(_el(name, display))
        lines.append('  </notation>')

    lines.append('</configuration>')
    return '\n'.join(lines) + '\n'


def generate_main_xml(map_slug: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Types xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}">\n'
        '  <Override PartName="/pm/configuration.xml" ContentType="application/xml" />\n'
        f'  <Override PartName="/pm/maps/{escape_xml(map_slug)}.xml" ContentType="application/xml" />\n'
        '</Types>\n'
    )


def generate_pmm_zip(process: BusinessProcess) -> bytes:
    slug, map_xml = generate_map_xml(process)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('main.xml', generate_main_xml(slug).encode('utf-8'))
        zf.writestr('pm/configuration.xml', generate_configuration_xml().encode('utf-8'))
        zf.writestr(f'pm/maps/{slug}.xml', map_xml.encode('utf-8'))
    return buf.getvalue()
