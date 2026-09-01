"""PIX Process Studio native package (.pmm = ZIP of three XML parts).

Структура пакета воспроизводит выгрузку самой PIX Процессной студии:

    main.xml               — манифест частей (<Types><Override .../></Types>)
    pm/configuration.xml   — каталог свойств и нотаций студии
    pm/maps/<slug>.xml     — сама карта (<Map> с <node> и <connector>)

Ключевые соглашения формата, сверенные с выгрузкой самой студии
(``tests/fixtures/sap.pmm`` — карта, сделанная и сохранённая в PIX):

* узлы внутри дорожки (`horizontalRoad`) позиционируются ОТНОСИТЕЛЬНО дорожки,
  сама дорожка — в абсолютных координатах карты;
* подпись связи хранится в атрибуте ``Text``, а не ``label``
  (``label`` студия игнорирует — подписи шлюзов теряются);
* список ``waypoint`` — это ПОЛНАЯ ломаная, включая точки на границе исходного
  и целевого узла, а не только промежуточные изломы; ломаную задаём для каждой
  связи — без неё студия трассирует сама и на плотной карте кладёт линии
  поверх соседних;
* ``sourcePoint``/``targetPoint`` — необязательные индексы якорей: задаём их
  для тех граней, чей номер эталон называет однозначно, для остальных
  опускаем, и студия выбирает точку примыкания сама (в своей выгрузке она
  опускает ``sourcePoint`` у 30 связей из 50);
* стиль линии — атрибут ``lineStyle`` у самой связи, а не дочерний элемент:
  дочернего ``<lineStyle>`` в выгрузке студии нет ни разу;
* маркеры концов берутся парой к стилю линии (см. ``_line_decoration``):
  незнакомый маркер студия отбрасывает вместе со связью.

В отличие от BPMN-выгрузки, здесь НЕ применяется нормализация степеней
событий: ``.pmm`` — это рисунок карты, и узел должен выглядеть так, как его
нарисовал аналитик. Валидную модель даёт экспорт в ``.bpmn``.
"""
from __future__ import annotations

import io
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.models.process import (
    TASK_NODE_TYPES,
    BusinessProcess,
    ProcessEdge,
    ProcessNode,
)
from app.services.bpmn_exporter import split_external_lanes, step_duration_text
from app.services.edge_routing import message_flow_endpoints, orthogonal_waypoints

_NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_NS_XSD = 'http://www.w3.org/2001/XMLSchema'
_PIX_NS = uuid.UUID('8b2e0c5a-4d71-4f3a-9c1e-6a7f0d2b9e11')

_CONFIGURATION_PATH = Path(__file__).resolve().parent.parent / 'resources' / 'pix_configuration.xml'

#: Элемент нотации, по которому опознаётся набор BPMN в каталоге студии.
_BPMN_PROBE_ELEMENT = 'gateway_xor'
#: Куда падает тип, которого в каталоге студии не оказалось.
_FALLBACK_ELEMENT = 'task'
#: Как саму нотацию подписывает студия в атрибуте ``<Map notation="…">``.
#: В каталоге она объявлена как ``BPMN``, но в собственной выгрузке студии
#: (``tests/fixtures/sap.pmm``) стоит строчное ``bpmn`` — пишем ровно так же,
#: чтобы не расходиться с эталоном на первом же атрибуте карты.
_MAP_NOTATION = 'bpmn'


@lru_cache(maxsize=1)
def bpmn_notation() -> Tuple[str, frozenset]:
    """Каноническое имя BPMN-нотации в каталоге студии и её элементы.

    Имя берём из самого каталога, а не пишем константой: по нему ищутся
    типы фигур, и незнакомый тип валит импорт целиком («Notation element not
    found (Parameter 'type')»). В карту, однако, уезжает не оно, а
    ``_MAP_NOTATION``: регистр имени студия не различает, а пишет строчными.
    """
    root = ET.fromstring(_CONFIGURATION_PATH.read_text(encoding='utf-8'))
    for notation in root.iter('notation'):
        elements = {e.get('name') for e in notation.findall('element') if e.get('name')}
        if _BPMN_PROBE_ELEMENT in elements:
            return notation.get('name') or 'BPMN', frozenset(elements)
    raise ValueError('В каталоге PIX нет нотации BPMN')


def notation_categories(config: ET.Element, notation: str) -> Dict[str, str]:
    """Категория каждого элемента нотации («Задачи», «Шлюзы», «Участники»…).

    Каталог студии делит элементы на группы, и по группе видно, чему положено
    содержать вложенные фигуры: дорожка и пул — «Участники».
    """
    return {
        e.get('name'): e.get('type') or ''
        for n in config.iter('notation') if n.get('name') == notation
        for e in n.findall('element') if e.get('name')
    }


def pix_element(kind: str) -> str:
    """Тип фигуры, который студия точно знает.

    Незнакомый тип валит импорт всего пакета, поэтому лучше отдать обычную
    задачу: карта откроется, а о подмене аналитик уже предупреждён отчётом
    о качестве импорта.
    """
    return kind if kind in bpmn_notation()[1] else _FALLBACK_ELEMENT

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
    """Тип узла в словаре нотации BPMN Процессной студии (pm/configuration.xml).

    Результат обязательно сверяется с каталогом: студия не открывает пакет
    целиком, если встретит хоть один тип, которого в её нотации нет.
    """
    return pix_element(_pix_type_raw(node))


def _pix_type_raw(node: ProcessNode) -> str:
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
    if kind == 'complexGateway':
        return 'gateway_complex'
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
    if node.type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway'):
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


#: Диаметр значка длительности на карте PIX, px.
_DURATION_SIDE = 24

#: Отступ значка длительности от правого края шага, px.
_DURATION_INSET = 20


def duration_node_xml(
    node: ProcessNode,
    x: float,
    y: float,
    indent: str,
    lane: Optional[ProcessNode] = None,
) -> Optional[str]:
    """Часы со временем шага — отдельная фигура-таймер у его нижней грани.

    Граничных событий формат .pmm не знает: студия рисует ровно те узлы, что
    перечислены в карте. Поэтому длительность показывается здесь так же, как её
    рисует аналитик в draw.io, — мелким таймером в правом нижнем углу шага.
    Связей у него нет, поток карты он не меняет; в BPMN-выгрузке то же самое
    время едет некрывающим граничным таймером.

    ``x``/``y`` — координаты шага в той же системе, в которой пишется его
    собственный ``<node>``: относительные внутри дорожки и абсолютные вне её.
    Значок шага у самого низа дорожки зажимается в её границы: вылезший за
    край узел студия рисует поверх соседней дорожки.
    """
    if node.type not in TASK_NODE_TYPES:
        return None
    text = step_duration_text(node)
    if not text:
        return None
    half = _DURATION_SIDE // 2
    # Узкий шаг значком не разрезать пополам: у него часы встают по центру
    # нижней грани, у обычного — в правом нижнем углу.
    offset = max(node.geometry.width - _DURATION_INSET, node.geometry.width / 2)
    mx = int(round(x + offset - half))
    my = int(round(y + node.geometry.height - half))
    if lane is not None:
        mx = max(0, min(mx, max(lane.geometry.width, 80) - _DURATION_SIDE))
        my = max(0, min(my, max(lane.geometry.height, 80) - _DURATION_SIDE))
    return _node_xml(
        'intermediate_event_catch_timer',
        _pix_id(f'duration:{node.id}'),
        text,
        mx,
        my,
        _DURATION_SIDE,
        _DURATION_SIDE,
        indent=indent,
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
    if len(route) >= 2:
        return route
    # Маршрут не построился (у связи нет обеих опор или они совпали по центру):
    # отдаём хотя бы прямой отрезок между центрами. Связь без единой точки
    # студия рисует по-своему, и на плотной карте это лишний пересекающий луч.
    if src is None or tgt is None:
        return []
    def _centre(node: ProcessNode) -> Tuple[float, float]:
        x, y = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
        return x + node.geometry.width / 2, y + node.geometry.height / 2
    start, end = _centre(src), _centre(tgt)
    return [start, end] if start != end else []


def _coord(value: float) -> str:
    return str(int(round(value)))


#: Оформление связи по её роду: стиль линии и маркеры концов. Снято с выгрузки
#: самой студии (``tests/fixtures/sap.pmm``), где встречаются ровно три
#: сочетания, и каждое отвечает своему понятию BPMN:
#:
#:     solid  + line   + arrowclosed — поток управления      (28 связей)
#:     dotted + line   + arrowLine   — ассоциация с артефактом (21)
#:     dashed + circle + arrowEmpty  — поток сообщений         (1)
#:
#: Писавшийся раньше ``arrow`` в выгрузке студии не встречается ни разу, а
#: незнакомый маркер она молча отбрасывает вместе со связью — пунктирные линии
#: до карты не доезжали именно поэтому.
_SEQUENCE_DECORATION = ('solid', 'line', 'arrowclosed')
_ASSOCIATION_DECORATION = ('dotted', 'line', 'arrowLine')
_MESSAGE_DECORATION = ('dashed', 'circle', 'arrowEmpty')

#: Индекс точки привязки к грани фигуры (``sourcePoint``/``targetPoint``).
#: Номера — не сторона света, а место фигуры в её собственном списке якорей,
#: и полностью этот список по одному эталону не восстанавливается: у грани их
#: несколько (для верхней встречаются и 1, и 17). Поэтому пишем только те, что
#: эталон подтверждает однозначно, а для остальных граней атрибут опускаем —
#: студия сама выберет точку примыкания, как делает и в своей выгрузке
#: (``sourcePoint`` там стоит лишь у 20 связей из 50).
#:
#: Сколько случаев за каждым номером в ``tests/fixtures/sap.pmm``:
#: источник — низ 0 (4), левая 6 (2); цель — левая 6 (9), верх 1 (7),
#: низ 3 (5), правая 4 (2).
_SOURCE_ANCHOR = {'bottom': 0, 'left': 6}
_TARGET_ANCHOR = {'top': 1, 'right': 4, 'bottom': 3, 'left': 6}

#: Насколько точка ломаной может отойти от грани и всё ещё считаться лежащей
#: на ней: ломаная округляется к целым и выравнивается по осям сдвигом на
#: пиксель (``edge_routing._snap_to_pixel_grid``).
_ANCHOR_TOLERANCE = 2.0


def _line_decoration(edge: ProcessEdge) -> Tuple[str, str, str]:
    """Стиль линии и маркеры её концов — по роду связи."""
    if edge.kind == 'messageFlow':
        return _MESSAGE_DECORATION
    if edge.kind == 'association' or edge.dashed:
        return _ASSOCIATION_DECORATION
    return _SEQUENCE_DECORATION


def _anchor_side(
    node: ProcessNode,
    point: Tuple[float, float],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[str]:
    """Грань фигуры, на которой лежит конец ломаной, или ``None``.

    Сторону определяем по той же ломаной, которая уезжает в файл: якорь,
    разошедшийся с нарисованной линией, хуже отсутствующего — студия увела бы
    связь к другой грани.
    """
    ox, oy = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
    width, height = node.geometry.width, node.geometry.height
    if width <= 0 or height <= 0:
        return None
    px, py = point
    within_x = ox - _ANCHOR_TOLERANCE <= px <= ox + width + _ANCHOR_TOLERANCE
    within_y = oy - _ANCHOR_TOLERANCE <= py <= oy + height + _ANCHOR_TOLERANCE
    distance: Dict[str, float] = {}
    if within_y:
        distance['left'] = abs(px - ox)
        distance['right'] = abs(px - (ox + width))
    if within_x:
        distance['top'] = abs(py - oy)
        distance['bottom'] = abs(py - (oy + height))
    if not distance:
        return None
    side = min(distance, key=lambda k: distance[k])
    return side if distance[side] <= _ANCHOR_TOLERANCE else None


def _anchor_attrs(
    route: List[Tuple[float, float]],
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> str:
    """Атрибуты ``sourcePoint``/``targetPoint`` связи — те, что известны."""
    if len(route) < 2:
        return ''
    attrs = ''
    if src is not None:
        index = _SOURCE_ANCHOR.get(_anchor_side(src, route[0], placed))
        if index is not None:
            attrs += f' sourcePoint="{index}"'
    if tgt is not None:
        index = _TARGET_ANCHOR.get(_anchor_side(tgt, route[-1], placed))
        if index is not None:
            attrs += f' targetPoint="{index}"'
    return attrs


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
            f'name="{escape_xml(slug)}" notation="{_MAP_NOTATION}" '
            'paperEnabled="false" paperType="0">'
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
            marker = duration_node_xml(n, rel_x, rel_y, '    ', lane)
            if marker:
                lines.append(marker)
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
        marker = duration_node_xml(n, n.geometry.x, n.geometry.y, '  ')
        if marker:
            lines.append(marker)

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
        # Петля из фигуры в саму себя: студия из-за одной такой линии
        # отказывается открыть всю карту целиком.
        if edge.sourceId == edge.targetId:
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

        line_style, marker_start, marker_end = _line_decoration(edge)
        label = (edge.name or edge.condition or '').strip()
        # Подпись связи в PIX — атрибут Text; label студия не читает.
        text_attr = f' Text="{escape_xml(label)}"' if label else ''

        route = polyline(edge, src_node, tgt_node, placed)
        anchors = _anchor_attrs(route, src_node, tgt_node, placed)

        lines.append(
            f'  <connector id="{escape_xml(_pix_id(edge.id))}" type="step"{text_attr}'
            f' lineStyle="{line_style}"'
            f' sourceNodeId="{escape_xml(id_map[edge.sourceId])}"'
            f' targetNodeId="{escape_xml(id_map[edge.targetId])}"{anchors}>'
        )
        lines.append(f'    <MarkerStart>{marker_start}</MarkerStart>')
        lines.append('    <MarkerMiddle />')
        lines.append(f'    <MarkerEnd>{marker_end}</MarkerEnd>')
        for index, (px, py) in enumerate(route):
            lines.append(f'    <waypoint x="{_coord(px)}" y="{_coord(py)}" index="{index}" />')
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
