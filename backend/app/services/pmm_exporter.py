"""PIX Process Studio native package (.pmm = ZIP of three XML parts).

Структура пакета воспроизводит выгрузку самой PIX Процессной студии:

    main.xml               — манифест частей (<Types><Override .../></Types>)
    pm/configuration.xml   — каталог свойств и нотаций студии
    pm/maps/<slug>.xml     — сама карта (<Map> с <node> и <connector>)

Ключевые соглашения формата, сверенные с эталонной выгрузкой PIX:

* узлы внутри дорожки (`horizontalRoad`) позиционируются ОТНОСИТЕЛЬНО дорожки,
  сама дорожка — в абсолютных координатах карты;
* подпись связи хранится в атрибуте ``Text``, а не ``label``
  (``label`` студия игнорирует — подписи шлюзов теряются);
* список ``waypoint`` — это ПОЛНАЯ ломаная, включая точки на границе исходного
  и целевого узла, а не только промежуточные изломы; ломаную задаём для каждой
  связи — без неё студия трассирует сама и на плотной карте кладёт линии
  поверх соседних;
* ``sourcePoint``/``targetPoint`` — необязательные индексы якорей: не задаём их,
  чтобы студия сама выбрала точку примыкания к грани фигуры;
* стиль линии (``lineStyle``) и оформление подписи хранятся дочерними
  элементами рядом с ``color``/``fontSize``.

В отличие от BPMN-выгрузки, здесь НЕ применяется нормализация степеней
событий: ``.pmm`` — это рисунок карты, и узел должен выглядеть так, как его
нарисовал аналитик. Валидную модель даёт экспорт в ``.bpmn``.
"""
from __future__ import annotations

import io
import re
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.models.process import BusinessProcess, ProcessEdge, ProcessNode
from app.services.bpmn_exporter import split_external_lanes
from app.services.edge_routing import message_flow_endpoints, orthogonal_waypoints

_NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_NS_XSD = 'http://www.w3.org/2001/XMLSchema'
_PIX_NS = uuid.UUID('8b2e0c5a-4d71-4f3a-9c1e-6a7f0d2b9e11')

_CONFIGURATION_PATH = Path(__file__).resolve().parent.parent / 'resources' / 'pix_configuration.xml'

#: Ширина заголовочной плашки карты; в эталоне PIX — 1988 px.
_TITLE_POOL_WIDTH = 1988
_TITLE_POOL_HEIGHT = 90

_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h', 'і': 'i', 'ї': 'i', 'є': 'e',
}


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


def transliterate(text: str) -> str:
    """Кириллица -> латиница, чтобы имя карты оставалось читаемым в студии.

    Имена файлов из Telegram/macOS приходят в разложенном виде (NFD): «ў» —
    это «у» + комбинирующая бреве. Без нормализации диакритика превращалась бы
    в подчёркивание посреди слова.
    """
    text = unicodedata.normalize('NFC', text or '')
    out: List[str] = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        lower = ch.lower()
        if lower in _TRANSLIT:
            mapped = _TRANSLIT[lower]
            out.append(mapped.upper() if ch.isupper() and mapped else mapped)
        else:
            out.append(ch)
    return ''.join(out)


def map_slug(process: BusinessProcess) -> str:
    """Имя карты: человекочитаемое имя процесса, а не служебный код паспорта.

    Имя части в ZIP и атрибут ``Map name`` совпадают — как в выгрузке PIX.
    """
    for candidate in (process.passport.name, process.name, process.passport.code):
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', transliterate(candidate or '')).strip('_')
        slug = re.sub(r'_{2,}', '_', slug)[:60].strip('_')
        if len(slug) >= 3:
            return slug
    return 'map'


def pix_type(node: ProcessNode) -> str:
    """Тип узла в словаре нотации BPMN Процессной студии (pm/configuration.xml)."""
    style = (node.style or '').lower()
    kind = node.type

    if kind == 'startEvent':
        if 'symbol=timer' in style:
            return 'start_event_timer'
        if 'symbol=message' in style:
            return 'start_event_message'
        return 'start_event_none'
    if kind == 'endEvent':
        if 'terminate' in style:
            return 'end_event_terminate'
        if 'symbol=message' in style:
            return 'end_event_message'
        return 'end_event_none'
    if kind == 'intermediateTimerEvent':
        return 'intermediate_event_catch_timer'
    if kind == 'intermediateMessageEvent':
        return 'intermediate_event_catch_message'
    if kind == 'exclusiveGateway':
        return 'gateway_xor'
    if kind == 'parallelGateway':
        return 'gateway_parallel'
    if kind == 'inclusiveGateway':
        return 'gateway_or'
    if kind == 'subProcess':
        return 'sub_process'
    if kind == 'dataStore':
        return 'dataStorage'
    if kind == 'dataObject':
        return 'dataObject'
    if kind == 'textAnnotation':
        return 'input'
    if kind == 'serviceTask' or node.category == 'rpa_bot':
        return 'serviceTask'
    if kind == 'userTask':
        return 'userTask'
    return 'task'


def _node_extra(node: ProcessNode) -> str:
    if node.type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway'):
        return ' labelPlacement="Left" font_size="16"'
    return ''


def _node_xml(
    node_type: str,
    nid: str,
    label: str,
    x: int,
    y: int,
    w: int,
    h: int,
    extra: str = '',
    fill: str = 'var(--bg-accent-node)',
    indent: str = '  ',
) -> str:
    return (
        f'{indent}<node type="{escape_xml(node_type)}" id="{escape_xml(nid)}"'
        f' label="{escape_xml(label)}" number="0"'
        f' x="{int(x)}" y="{int(y)}" width="{int(max(w, 8))}" height="{int(max(h, 8))}"'
        f' fill_color="{escape_xml(fill)}"{extra} />'
    )


def clamp_into_lane(child: ProcessNode, lane: ProcessNode) -> Tuple[int, int]:
    """Координаты узла относительно дорожки, зажатые в её границы.

    Узел, отнесённый к дорожке по геометрии, может выступать за её край — в
    студии он отрисовался бы поверх соседней дорожки.
    """
    lane_w = max(lane.geometry.width, 80)
    lane_h = max(lane.geometry.height, 80)
    rel_x = child.geometry.x - lane.geometry.x
    rel_y = child.geometry.y - lane.geometry.y
    rel_x = max(0, min(rel_x, lane_w - child.geometry.width))
    rel_y = max(0, min(rel_y, lane_h - child.geometry.height))
    return int(rel_x), int(rel_y)


def polyline(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
) -> List[Tuple[float, float]]:
    """Полная ломаная связи в абсолютных координатах карты.

    Ломаную задаём ВСЕГДА, а не только когда аналитик двигал изломы руками.
    Без waypoint студия трассирует связь сама, и на плотной карте банка это
    даёт то, на что жалуются аналитики: линии идут поверх соседних связей и
    сквозь чужие фигуры. В BPMN-выгрузке ломаная передаётся целиком, и там
    схема читается — здесь должно быть так же.

    ``placed`` — фактические абсолютные координаты узлов после зажатия в
    границы дорожки; концы ломаной обязаны лежать на них, а не на исходной
    геометрии draw.io.

    Ломаная строится ортогонально (`edge_routing`): связи в PIX имеют тип
    ``step``, и диагональные изломы в них выглядели бы чужеродно.
    """
    route = orthogonal_waypoints(edge, src, tgt, placed)
    return route if len(route) >= 2 else []


def _coord(value: float) -> str:
    return str(int(round(value)))


def generate_map_xml(process: BusinessProcess) -> Tuple[str, str]:
    slug = map_slug(process)
    id_map: Dict[str, str] = {}
    flow = [n for n in process.nodes if n.type != 'lane']
    lanes = list(process.lanes or [])
    # Полоса без единого шага — внешний участник (клиент): она остаётся строкой
    # карты, но, в отличие от дорожки с шагами, пунктир к ней осмыслен и
    # выгружается связью, а не отбрасывается как оформление.
    _, external = split_external_lanes(lanes, [n for n in flow if n.laneId])
    for n in flow + lanes:
        id_map[n.id] = _pix_id(n.id)
    node_by_id = {n.id: n for n in flow}

    lines: List[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        (
            f'<Map xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}" '
            f'name="{escape_xml(slug)}" notation="bpmn" paperEnabled="false" paperType="0">'
        ),
    ]

    # ── Заголовочная плашка карты ───────────────────────────────────────────
    title = process.passport.name or process.name or slug
    bounds_nodes = lanes or flow
    if bounds_nodes:
        min_x = min(n.geometry.x for n in bounds_nodes)
        min_y = min(n.geometry.y for n in bounds_nodes)
        # Плашка шире эталонной, если карта шире: узкий заголовок над картой
        # в 4600 px выглядит обрывком, а не шапкой схемы.
        title_width = max(
            _TITLE_POOL_WIDTH,
            max(n.geometry.x + n.geometry.width for n in bounds_nodes) - min_x,
        )
    else:
        min_x, min_y, title_width = 0, 120, _TITLE_POOL_WIDTH
    lines.append(
        _node_xml(
            'emptyPool',
            _pix_id(f'title:{process.id}'),
            title,
            min_x,
            min_y - (_TITLE_POOL_HEIGHT + 40),
            title_width,
            _TITLE_POOL_HEIGHT,
            extra=' font_size="28"',
        )
    )

    # ── Дорожки и их содержимое ─────────────────────────────────────────────
    # Фактическое абсолютное положение узла после зажатия в границы дорожки:
    # по нему строятся концы ломаных связей.
    placed: Dict[str, Tuple[int, int]] = {}
    assigned = set()
    for lane in lanes:
        children = [n for n in flow if n.laneId == lane.id]
        for n in children:
            assigned.add(n.id)
        lines.append(
            f'  <node type="horizontalRoad" id="{escape_xml(id_map[lane.id])}"'
            f' label="{escape_xml(lane.name)}" number="0"'
            f' x="{lane.geometry.x}" y="{lane.geometry.y}"'
            f' width="{max(lane.geometry.width, 80)}" height="{max(lane.geometry.height, 80)}"'
            f' fill_color="var(--bg-accent-road-node)">'
        )
        for n in children:
            rel_x, rel_y = clamp_into_lane(n, lane)
            placed[n.id] = (lane.geometry.x + rel_x, lane.geometry.y + rel_y)
            lines.append(
                _node_xml(
                    pix_type(n), id_map[n.id], n.name, rel_x, rel_y,
                    n.geometry.width, n.geometry.height, _node_extra(n), indent='    ',
                )
            )
        lines.append('  </node>')

    for n in flow:
        if n.id in assigned:
            continue
        lines.append(
            _node_xml(
                pix_type(n), id_map[n.id], n.name, n.geometry.x, n.geometry.y,
                n.geometry.width, n.geometry.height, _node_extra(n),
            )
        )

    # ── Связи ───────────────────────────────────────────────────────────────
    lane_ids = {lane.id for lane in lanes}
    external_lanes = {lane.id: lane for lane in external}
    for edge in process.edges:
        # Оформительские линии draw.io (разделители этапов) в карту PIX не идут.
        if edge.kind == 'annotationLine':
            continue
        if not edge.sourceId or not edge.targetId:
            continue
        if edge.sourceId not in id_map or edge.targetId not in id_map:
            continue

        src_node = node_by_id.get(edge.sourceId)
        tgt_node = node_by_id.get(edge.targetId)
        touches_lane = edge.sourceId in lane_ids or edge.targetId in lane_ids
        if touches_lane:
            lane_is_source = edge.sourceId in lane_ids
            lane_id = edge.sourceId if lane_is_source else edge.targetId
            lane = external_lanes.get(lane_id)
            other = tgt_node if lane_is_source else src_node
            # Связь с полосой-участником (клиент) остаётся на карте: это точка
            # контакта. Линия, упирающаяся в дорожку с шагами, — оформление.
            if lane is None or other is None:
                continue
            src_node, tgt_node = message_flow_endpoints(edge, other, lane, lane_is_source)

        dotted = edge.kind in ('association', 'messageFlow') or bool(edge.dashed)
        line_style = 'dotted' if dotted else 'solid'
        # Маркер конца берём из словаря React Flow, на котором построен холст
        # студии (`arrowclosed` пришёл из эталонной выгрузки): нестандартное
        # значение студия молча отбрасывает вместе со связью.
        marker_end = 'arrow' if dotted else 'arrowclosed'
        label = (edge.name or edge.condition or '').strip()
        # Подпись связи в PIX — атрибут Text; label студия не читает.
        text_attr = f' Text="{escape_xml(label)}"' if label else ''

        lines.append(
            f'  <connector id="{escape_xml(_pix_id(edge.id))}" type="step"{text_attr}'
            f' lineStyle="{line_style}"'
            f' sourceNodeId="{escape_xml(id_map[edge.sourceId])}"'
            f' targetNodeId="{escape_xml(id_map[edge.targetId])}">'
        )
        lines.append('    <MarkerStart>line</MarkerStart>')
        lines.append('    <MarkerMiddle />')
        lines.append(f'    <MarkerEnd>{marker_end}</MarkerEnd>')
        for index, (px, py) in enumerate(polyline(edge, src_node, tgt_node, placed)):
            lines.append(f'    <waypoint x="{_coord(px)}" y="{_coord(py)}" index="{index}" />')
        lines.append('    <labelPosition>50</labelPosition>')
        # Стиль линии дублируется дочерним элементом: остальные свойства
        # оформления (color, fontSize) студия хранит именно так, и как атрибут
        # lineStyle до неё не доезжал — пунктир приходил сплошной линией.
        lines.append(f'    <lineStyle>{line_style}</lineStyle>')
        lines.append('    <color>var(--fg-gray-primary)</color>')
        lines.append('    <fontSize>12</fontSize>')
        lines.append('    <fontBold>false</fontBold>')
        lines.append('    <fontItalic>false</fontItalic>')
        lines.append('    <fontUnderline>false</fontUnderline>')
        lines.append('    <fontStrikethrough>false</fontStrikethrough>')
        lines.append('  </connector>')

    lines.append('</Map>')
    return slug, '\n'.join(lines) + '\n'


def generate_configuration_xml() -> str:
    """Каталог свойств и нотаций студии.

    Отдаётся эталонный файл PIX без изменений: имена элементов нотаций
    (``dfd_process``, ``c4_person``, ``app_component`` и т.д.) заданы студией,
    и собственная реконструкция каталога рискует не пройти её валидацию.
    """
    return _CONFIGURATION_PATH.read_text(encoding='utf-8')


def generate_main_xml(slug: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Types xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}">\n'
        '  <Override PartName="/pm/configuration.xml" ContentType="application/xml" />\n'
        f'  <Override PartName="/pm/maps/{escape_xml(slug)}.xml" ContentType="application/xml" />\n'
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
