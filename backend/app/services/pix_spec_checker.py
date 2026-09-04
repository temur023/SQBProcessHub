"""Строгая проверка выгрузок под импортёр PIX Процессной студии.

Зачем отдельный модуль рядом с ``export_validation``. Тот проверяет файл по
спецификации BPMN 2.0 и по правилам, общим для импортёров (bpmn.io, Camunda):
разрешимые ссылки, уникальные ID, наличие BPMNDI. Этого достаточно, чтобы файл
открылся в браузерном редакторе, и НЕ достаточно, чтобы его принял PIX: студия
разбирает файл своим парсером и спотыкается там, где bpmn.io пожимает плечами —
на префиксе неймспейса, на дробной координате, на типе фигуры, которого нет в
её каталоге нотаций.

Отсюда разделение: ``export_validation`` отвечает на вопрос «файл соответствует
стандарту?», этот модуль — на вопрос «файл откроется именно в PIX?». Второй
строже первого и запускается после него.

ОТКУДА ВЗЯТО КАЖДОЕ ПРАВИЛО. Это важно: доступа к исходникам парсера студии у
платформы нет, и выдавать догадки за спецификацию нельзя. Поэтому у каждой
проверки в коде проставлен источник, а всего их четыре:

``catalog``
    Проверено по эталонному каталогу студии ``app/resources/pix_configuration.xml``
    — это файл самой PIX, он же уезжает внутри каждого ``.pmm``. Всё, что
    сверяется с ним (имена нотаций, типы элементов, вложенность), — факт, а не
    предположение.

``observed``
    Воспроизведено по сообщениям, которые студия выдавала на реальных отказах:
    «Notation element not found (Parameter 'type')», «Connector source and
    target node cannot be the same», — и по её собственной выгрузке
    ``tests/fixtures/sap.pmm``. Эталон стоит выше догадок: правило, которое
    его бракует, ошибочно по определению, и так снялись требование точного
    регистра имени нотации и требование целых координат ``waypoint``.

``reference``
    Соглашения формата ``.pmm``, снятые с эталонной выгрузки студии и описанные
    в ``pmm_exporter``: относительные координаты внутри дорожки, подпись связи в
    атрибуте ``Text``, полная ломаная в ``waypoint``.

``required``
    Требования, заданные командой платформы к выгрузке для PIX: жёсткие
    префиксы неймспейсов, явные ``processType``/``isExecutable``, только целые
    числа в ``dc:Bounds`` и ``di:waypoint``, строгое соответствие
    ``bpmnElement`` идентификатору узла. Формально стандарт допускает и другое
    (BPMN разрешает любой префикс и дробные координаты), но выгрузка обязана
    держать этот более узкий профиль — он и проверяется здесь.

Правила с источником ``required`` — единственные, которые нельзя подтвердить
файлом из репозитория; если студия окажется к чему-то из них терпима, правило
надо ослабить осознанно, а не тихо.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Optional, Set, Tuple

from app.models.process import BusinessProcess
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.export_validation import ExportCheck, Problem, _local, _number_ok
from app.services.pmm_exporter import (
    bpmn_notation,
    generate_pmm_zip,
    notation_categories,
)

# ── Профиль неймспейсов, который обязана держать выгрузка (required) ─────────
#: Префикс -> URI. Проверяется и то, и другое: URI задаёт смысл, префикс —
#: то, как элементы выглядят в тексте файла.
_REQUIRED_NS: Dict[str, str] = {
    'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
    'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
    'dc': 'http://www.omg.org/spec/DD/20100524/DC',
    'di': 'http://www.omg.org/spec/DD/20100524/DI',
}

#: processType по спецификации — None | Private | Public.
_PROCESS_TYPES = {'None', 'Private', 'Public'}

#: Целое число без дробной части и экспоненты.
_INTEGER_RE = re.compile(r'^-?\d+$')

#: xsd:ID — имя XML, а не произвольная строка.
_NCNAME_RE = re.compile(r'^[A-Za-z_][\w.\-]*$')

#: Канонический вид UUID: студия хранит идентификаторы карты именно так.
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)

#: Узлы потока BPMN — у каждого на диаграмме обязана быть своя фигура.
_FLOW_NODES = {
    'startevent', 'endevent', 'intermediatecatchevent', 'intermediatethrowevent',
    'boundaryevent', 'task', 'usertask', 'servicetask', 'manualtask', 'scripttask',
    'sendtask', 'receivetask', 'businessruletask', 'subprocess', 'callactivity',
    'transaction', 'exclusivegateway', 'parallelgateway', 'inclusivegateway',
    'complexgateway', 'eventbasedgateway',
}

#: Связи BPMN — у каждой обязан быть свой BPMNEdge.
_FLOW_EDGES = {'sequenceflow', 'messageflow', 'association', 'dataassociation'}

#: Категория каталога, которой единственной положено содержать вложенные фигуры.
_CONTAINER_CATEGORY = 'Участники'

#: Свойства фигуры, значение которых обязано быть .NET TimeSpan (catalog).
#: Имена взяты из каталога студии: там у всех троих ``typeId="7"`` —
#: длительность. Ключ не из каталога панель свойств не показывает, поэтому
#: неизвестное имя проверяется отдельным правилом ниже.
_TIMESPAN_PROPERTIES = frozenset({
    'vremya_protsessa', 'system_process_time', 'vremya_ozhidaniya',
})

#: .NET TimeSpan: «hh:mm:ss», перед ним необязательные сутки через точку.
#: Часы строго 0..23 — ``TimeSpan.Parse("24:00:00")`` падает, сутки выносятся
#: отдельным полем. Проверка нужна именно поэтому: файл со значением «24:00:00»
#: открывается любым XML-разбором и валится уже в студии.
_TIMESPAN_RE = re.compile(r'^(?:\d+\.)?(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,7})?$')


def _add(check: ExportCheck, level: str, code: str, message: str,
         where: Optional[str] = None) -> None:
    check.problems.append(Problem(level, code, message, where))


def _declared_namespaces(xml: str) -> Dict[str, str]:
    """Объявленные в файле префиксы неймспейсов.

    ElementTree разворачивает префиксы в ``{uri}tag`` и сам префикс теряет,
    а проверять надо именно его: файл с ``xmlns:b=…MODEL`` формально
    эквивалентен нашему, но выглядит иначе, и профиль выгрузки этого не
    допускает. Поэтому читаем события ``start-ns`` — единственный способ
    увидеть префиксы штатными средствами.
    """
    found: Dict[str, str] = {}
    try:
        for event, payload in ET.iterparse(io.StringIO(xml), events=('start-ns',)):
            prefix, uri = payload
            found.setdefault(prefix, uri)
    except ET.ParseError:
        pass
    return found


# ────────────────────────────── BPMN 2.0 ──────────────────────────────────

def validate_bpmn_for_pix(xml: str) -> ExportCheck:
    """Проверяет BPMN профилем PIX. Никогда не падает: стоит на пути выгрузки."""
    check = ExportCheck(format='bpmn/pix')
    try:
        _validate_bpmn_for_pix(xml, check)
    except Exception as exc:  # noqa: BLE001 — см. docstring
        _add(check, 'error', 'pix_bpmn_unreadable',
             f'Проверка профиля PIX не смогла разобрать файл: {exc}.')
    return check


def _validate_bpmn_for_pix(xml: str, check: ExportCheck) -> None:
    # ── Неймспейсы и префиксы (required) ────────────────────────────────────
    declared = _declared_namespaces(xml)
    for prefix, uri in _REQUIRED_NS.items():
        actual = declared.get(prefix)
        if actual is None:
            owner = next((p for p, u in declared.items() if u == uri), None)
            hint = f' (тот же URI объявлен под префиксом «{owner}»)' if owner else ''
            _add(check, 'error', 'pix_ns_prefix_missing',
                 f'Не объявлен префикс «{prefix}» для {uri}{hint}. '
                 'Профиль выгрузки для PIX требует именно эти префиксы.')
        elif actual != uri:
            _add(check, 'error', 'pix_ns_prefix_wrong',
                 f'Префикс «{prefix}» привязан к {actual}, ожидался {uri}.')

    try:
        root = ET.fromstring(xml.encode('utf-8'))
    except ET.ParseError as exc:
        _add(check, 'error', 'pix_bpmn_broken', f'Файл не разбирается как XML: {exc}.')
        return
    if _local(root.tag) != 'definitions':
        _add(check, 'error', 'pix_bpmn_root',
             f'Корень — <{root.tag}>, ожидался <bpmn:definitions>.')
        return

    if not (root.get('targetNamespace') or '').strip():
        _add(check, 'error', 'pix_target_namespace',
             'У <bpmn:definitions> пуст targetNamespace.')
    if not (root.get('id') or '').strip():
        _add(check, 'error', 'pix_definitions_id',
             'У <bpmn:definitions> нет id — студия адресует по нему всю модель.')

    by_id: Dict[str, ET.Element] = {}
    for el in root.iter():
        eid = el.get('id')
        if eid:
            by_id.setdefault(eid, el)

    def label(eid: str) -> str:
        el = by_id.get(eid)
        return (el.get('name') if el is not None else None) or eid

    # ── Процессы: явные атрибуты (required) ─────────────────────────────────
    processes = [el for el in root.iter() if _local(el.tag) == 'process']
    if not processes:
        _add(check, 'error', 'pix_no_process', 'В файле нет ни одного <bpmn:process>.')
    for proc in processes:
        pid = proc.get('id') or ''
        if not pid:
            _add(check, 'error', 'pix_process_no_id', 'У <bpmn:process> нет id.')
        elif not _NCNAME_RE.match(pid):
            _add(check, 'error', 'pix_process_id_ncname',
                 f'id процесса «{pid}» не является именем XML.', pid)
        if proc.get('isExecutable') is None:
            _add(check, 'error', 'pix_process_is_executable',
                 f'У процесса «{label(pid)}» не задан isExecutable — '
                 'профиль PIX требует его явно.', pid)
        elif proc.get('isExecutable') not in ('true', 'false'):
            _add(check, 'error', 'pix_process_is_executable',
                 f'isExecutable у процесса «{label(pid)}» — '
                 f'«{proc.get("isExecutable")}», ожидается true или false.', pid)
        kind = proc.get('processType')
        if kind is None:
            _add(check, 'error', 'pix_process_type',
                 f'У процесса «{label(pid)}» не задан processType — '
                 'профиль PIX требует его явно.', pid)
        elif kind not in _PROCESS_TYPES:
            _add(check, 'error', 'pix_process_type',
                 f'processType «{kind}» у процесса «{label(pid)}» вне '
                 f'допустимых значений ({", ".join(sorted(_PROCESS_TYPES))}).', pid)

    # ── Диаграмма: идентификаторы и привязка (required) ─────────────────────
    diagrams = [el for el in root.iter() if _local(el.tag) == 'bpmndiagram']
    planes = [el for el in root.iter() if _local(el.tag) == 'bpmnplane']
    if not diagrams:
        _add(check, 'error', 'pix_no_diagram',
             'Нет <bpmndi:BPMNDiagram> — студии нечего рисовать.')
    for diagram in diagrams:
        if not (diagram.get('id') or '').strip():
            _add(check, 'error', 'pix_diagram_id', 'У <bpmndi:BPMNDiagram> нет id.')
    for plane in planes:
        if not (plane.get('id') or '').strip():
            _add(check, 'error', 'pix_plane_id', 'У <bpmndi:BPMNPlane> нет id.')
        anchor = plane.get('bpmnElement')
        if not anchor:
            _add(check, 'error', 'pix_plane_anchor',
                 'У <bpmndi:BPMNPlane> не задан bpmnElement.', plane.get('id'))
        elif anchor not in by_id:
            _add(check, 'error', 'pix_plane_anchor',
                 f'<bpmndi:BPMNPlane> ссылается на несуществующий {anchor}.',
                 plane.get('id'))

    # ── Геометрия: только целые числа (required) ───────────────────────────
    # Дробная координата — обычный результат импорта из draw.io, где фигуру
    # подвинули мышью: x="500.0000000000002". Стандарт такое допускает, профиль
    # выгрузки — нет, и округлять надо на нашей стороне, а не надеяться на чужой
    # разбор.
    drawn: Dict[str, int] = {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in ('bpmnshape', 'bpmnedge'):
            continue
        anchor = el.get('bpmnElement') or ''
        drawn[anchor] = drawn.get(anchor, 0) + 1
        if not anchor:
            _add(check, 'error', 'pix_di_no_anchor',
                 f'У <{tag}> не задан bpmnElement.', el.get('id'))
        elif anchor not in by_id:
            _add(check, 'error', 'pix_di_dangling',
                 f'<{tag}> нарисован для несуществующего элемента {anchor}.', anchor)

        if tag == 'bpmnshape':
            bounds = [c for c in el if _local(c.tag) == 'bounds']
            if not bounds:
                _add(check, 'error', 'pix_shape_no_bounds',
                     f'У фигуры «{label(anchor)}» нет <dc:Bounds>.', anchor)
                continue
            box = bounds[0]
            for attr in ('x', 'y', 'width', 'height'):
                raw = box.get(attr)
                if raw is None:
                    _add(check, 'error', 'pix_bounds_missing',
                         f'В <dc:Bounds> фигуры «{label(anchor)}» нет «{attr}».', anchor)
                elif not _INTEGER_RE.match(raw.strip()):
                    _add(check, 'error', 'pix_bounds_not_integer',
                         f'В <dc:Bounds> фигуры «{label(anchor)}» «{attr}»="{raw}" — '
                         'профиль PIX допускает только целые числа.', anchor)
            if _number_ok(box.get('width')) and float(box.get('width')) <= 0:
                _add(check, 'error', 'pix_shape_zero_size',
                     f'У фигуры «{label(anchor)}» нулевая ширина.', anchor)
            if _number_ok(box.get('height')) and float(box.get('height')) <= 0:
                _add(check, 'error', 'pix_shape_zero_size',
                     f'У фигуры «{label(anchor)}» нулевая высота.', anchor)
        else:
            points = [c for c in el if _local(c.tag) == 'waypoint']
            if len(points) < 2:
                _add(check, 'error', 'pix_edge_waypoints',
                     f'У связи «{label(anchor)}» {len(points)} точек маршрута, '
                     'нужно минимум две.', anchor)
            for point in points:
                for attr in ('x', 'y'):
                    raw = point.get(attr)
                    if raw is None or not _INTEGER_RE.match(raw.strip()):
                        _add(check, 'error', 'pix_waypoint_not_integer',
                             f'У связи «{label(anchor)}» точка маршрута '
                             f'«{attr}»="{raw}" — нужны целые числа.', anchor)
                        break

    for anchor, times in drawn.items():
        if times > 1:
            _add(check, 'error', 'pix_di_duplicate',
                 f'Элемент «{label(anchor)}» нарисован {times} раза.', anchor)

    # ── Каждому узлу — своя фигура, каждой связи — своя линия (required) ────
    for el in root.iter():
        tag = _local(el.tag)
        eid = el.get('id')
        if not eid:
            continue
        if tag in _FLOW_NODES and eid not in drawn:
            _add(check, 'error', 'pix_node_not_drawn',
                 f'У узла «{label(eid)}» нет <bpmndi:BPMNShape> — '
                 'студия не примет узел без координат.', eid)
        elif tag in _FLOW_EDGES and eid not in drawn:
            _add(check, 'error', 'pix_edge_not_drawn',
                 f'У связи «{label(eid)}» нет <bpmndi:BPMNEdge>.', eid)


# ──────────────────────────────── PIX .pmm ─────────────────────────────────

def validate_pmm_for_pix(payload: bytes) -> ExportCheck:
    """Проверяет пакет .pmm профилем студии. Никогда не падает."""
    check = ExportCheck(format='pmm/pix')
    try:
        _validate_pmm_for_pix(payload, check)
    except Exception as exc:  # noqa: BLE001 — см. docstring
        _add(check, 'error', 'pix_pmm_unreadable',
             f'Проверка профиля PIX не смогла разобрать пакет: {exc}.')
    return check


def _validate_pmm_for_pix(payload: bytes, check: ExportCheck) -> None:
    try:
        package = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        _add(check, 'error', 'pix_pmm_not_zip', f'Пакет не читается как ZIP: {exc}.')
        return

    names = set(package.namelist())
    maps = sorted(n for n in names if n.startswith('pm/maps/') and n.endswith('.xml'))
    if not maps:
        _add(check, 'error', 'pix_pmm_no_map', 'В пакете нет карты pm/maps/*.xml.')
        return
    if 'pm/configuration.xml' not in names:
        _add(check, 'error', 'pix_pmm_no_config',
             'В пакете нет pm/configuration.xml — студии нечем опознать типы фигур.')
        return

    map_part = maps[0]
    root = ET.fromstring(package.read(map_part).decode('utf-8'))
    config = ET.fromstring(package.read('pm/configuration.xml').decode('utf-8'))

    # ── Нотация карты объявлена в каталоге (observed) ───────────────────────
    # Регистр имени не значит ничего: каталог объявляет нотацию как ``BPMN``,
    # а сама студия в своей выгрузке пишет ``notation="bpmn"``
    # (tests/fixtures/sap.pmm). Сверка точным равенством забраковала бы файл,
    # сделанный самой PIX, поэтому имя приводим к нижнему регистру, а дальше
    # работаем с каноническим написанием из каталога.
    notation = root.get('notation') or ''
    catalogue: Dict[str, Set[str]] = {
        n.get('name'): {e.get('name') for e in n.findall('element') if e.get('name')}
        for n in config.iter('notation') if n.get('name')
    }
    # Категория элемента («Задачи», «Шлюзы», «События»…). Имени в каталоге
    # мало: студия идёт за категорией и на её отсутствии падает с сообщением,
    # где параметр так и назван — ``'type'``. В нотации BPMN категории нет
    # ровно у одного элемента из 91 — ``input`` («Текст»). Источник: catalog.
    element_category: Dict[str, Dict[str, str]] = {
        n.get('name'): {
            e.get('name'): e.get('type') or ''
            for e in n.findall('element') if e.get('name')
        }
        for n in config.iter('notation') if n.get('name')
    }
    canonical = {name.lower(): name for name in catalogue}
    notation_name = canonical.get(notation.lower())
    allowed = catalogue.get(notation_name) if notation_name else None
    categories = element_category.get(notation_name or '', {})
    if allowed is None:
        _add(check, 'error', 'pix_pmm_notation',
             f'Нотация «{notation}» не объявлена в каталоге студии. '
             f'Есть: {", ".join(sorted(catalogue)) or "ни одной"}.', map_part)
        allowed = set()

    # Имя карты и имя части совпадают (reference).
    expected = map_part[len('pm/maps/'):-len('.xml')]
    if (root.get('name') or '') != expected:
        _add(check, 'error', 'pix_pmm_map_name',
             f'Имя карты «{root.get("name")}» не совпадает с именем части «{expected}».',
             map_part)

    # Дальше по каталогу ходим под каноническим именем: с ``bpmn`` из файла
    # ни категории, ни ``canHaveChildren`` не нашлись бы, и дорожка с шагами
    # внутри выглядела бы недопустимым вложением.
    lookup = notation_name or notation
    #: Имена шаблонов свойств из каталога: панель свойств строится по ним.
    property_names = {
        t.get('name') for t in config.findall('propertyTemplate') if t.get('name')
    }
    categories = notation_categories(config, lookup)
    containers = {name for name, kind in categories.items() if kind == _CONTAINER_CATEGORY}
    with_children = {
        e.get('name')
        for n in config.iter('notation') if n.get('name') == lookup
        for e in n.findall('element') if e.get('canHaveChildren') == 'true'
    }

    # ── Фигуры ──────────────────────────────────────────────────────────────
    seen: Set[str] = set()

    def walk(parent: ET.Element, box: Optional[Tuple[float, float]], depth: int) -> None:
        for node in parent:
            if node.tag != 'node':
                continue
            nid = node.get('id') or ''
            name = node.get('label') or nid or '(без подписи)'
            kind = node.get('type') or ''

            # Идентификатор — канонический UUID (reference).
            if not _UUID_RE.match(nid):
                _add(check, 'error', 'pix_pmm_id_not_uuid',
                     f'Идентификатор фигуры «{name}» — «{nid}», ожидается UUID.', nid)
            if nid in seen:
                _add(check, 'error', 'pix_pmm_duplicate_id',
                     f'Идентификатор фигуры «{name}» повторяется.', nid)
            seen.add(nid)

            # Тип есть в нотации карты (catalog + observed: студия отвечает
            # «Notation element not found (Parameter 'type')»).
            if not kind:
                _add(check, 'error', 'pix_pmm_no_type',
                     f'У фигуры «{name}» не указан type.', nid)
            elif allowed and kind not in allowed:
                _add(check, 'error', 'pix_pmm_type_unknown',
                     f'Тип «{kind}» фигуры «{name}» не объявлен в нотации '
                     f'«{notation}».', nid)
            elif not categories.get(kind, ''):
                _add(check, 'error', 'pix_pmm_type_no_category',
                     f'У типа «{kind}» фигуры «{name}» в каталоге студии не '
                     f'проставлена категория — студия откажется открыть пакет.',
                     nid)

            # Геометрия — целые числа (required).
            geometry: Dict[str, float] = {}
            for attr in ('x', 'y', 'width', 'height'):
                raw = node.get(attr)
                if raw is None:
                    _add(check, 'error', 'pix_pmm_geometry',
                         f'У фигуры «{name}» нет координаты «{attr}».', nid)
                elif not _INTEGER_RE.match(raw.strip()):
                    _add(check, 'error', 'pix_pmm_geometry_not_integer',
                         f'У фигуры «{name}» «{attr}»="{raw}" — нужны целые числа.', nid)
                else:
                    geometry[attr] = float(raw)
            if geometry.get('width', 1) <= 0 or geometry.get('height', 1) <= 0:
                _add(check, 'error', 'pix_pmm_zero_size',
                     f'У фигуры «{name}» нулевой размер.', nid)

            # Свойства фигуры: значения времени — .NET TimeSpan (reference).
            for props in node.findall('Properties'):
                for prop in props.findall('Property'):
                    pname = prop.get('name') or ''
                    value = prop.get('value')
                    if not pname:
                        _add(check, 'error', 'pix_pmm_property_no_name',
                             f'У свойства фигуры «{name}» не указано имя.', nid)
                    elif pname not in property_names:
                        # Ключ не из каталога студия принимает молча: значение
                        # оседает теневым атрибутом, файл открывается без
                        # ошибок, а в панели свойств поля просто нет. Дефект
                        # без этого правила обнаруживается только глазами.
                        _add(check, 'error', 'pix_pmm_property_unknown',
                             f'Свойство «{pname}» фигуры «{name}» не объявлено '
                             'в каталоге студии: значение сохранится, но в '
                             'панели свойств не появится.', nid)
                    if pname in _TIMESPAN_PROPERTIES and not _TIMESPAN_RE.match(value or ''):
                        _add(check, 'error', 'pix_pmm_property_timespan',
                             f'Свойство «{pname}» фигуры «{name}» = «{value}»: '
                             'ожидается .NET TimeSpan вида «hh:mm:ss» или '
                             '«d.hh:mm:ss» с часами в диапазоне 0..23.', nid)

            # Вложение: держать детей вправе только участники и элементы,
            # помеченные canHaveChildren (catalog).
            children = [c for c in node if c.tag == 'node']
            if children and kind not in containers and kind not in with_children:
                _add(check, 'error', 'pix_pmm_nesting',
                     f'Фигура «{name}» типа «{kind}» содержит вложенные фигуры, '
                     'хотя каталог студии этого не допускает.', nid)

            # Координаты ребёнка отсчитываются от родителя и обязаны лежать
            # внутри него (reference): дорожка рисуется по своим границам, и
            # шаг, выехавший за них, в студии оказывается в чужой дорожке.
            if depth > 0 and box and 'x' in geometry and 'width' in geometry:
                pw, ph = box
                if (geometry['x'] < 0 or geometry['y'] < 0
                        or geometry['x'] + geometry['width'] > pw
                        or geometry['y'] + geometry['height'] > ph):
                    _add(check, 'error', 'pix_pmm_child_outside',
                         f'Фигура «{name}» выходит за границы своей дорожки '
                         f'({geometry["x"]:.0f},{geometry["y"]:.0f} '
                         f'{geometry["width"]:.0f}×{geometry["height"]:.0f} '
                         f'при дорожке {pw:.0f}×{ph:.0f}).', nid)

            walk(node, (geometry.get('width', 0), geometry.get('height', 0)), depth + 1)

    walk(root, None, 0)
    if not seen:
        _add(check, 'error', 'pix_pmm_empty', 'В карте нет ни одной фигуры.', map_part)

    # ── Связи ───────────────────────────────────────────────────────────────
    for connector in root.iter('connector'):
        cid = connector.get('id') or ''
        src = connector.get('sourceNodeId') or ''
        tgt = connector.get('targetNodeId') or ''
        if not _UUID_RE.match(cid):
            _add(check, 'error', 'pix_pmm_id_not_uuid',
                 f'Идентификатор связи «{cid}» не является UUID.', cid)
        if cid in seen:
            _add(check, 'error', 'pix_pmm_duplicate_id',
                 f'Идентификатор связи {cid} уже занят фигурой.', cid)
        seen.add(cid)

        # observed: «Connector source and target node cannot be the same».
        if src and src == tgt:
            _add(check, 'error', 'pix_pmm_self_loop',
                 'Связь начинается и заканчивается на одной фигуре.', cid)
        for role, ref in (('источник', src), ('приёмник', tgt)):
            if not ref:
                _add(check, 'error', 'pix_pmm_connector_end',
                     f'У связи не задан {role}.', cid)
            elif ref not in seen and ref not in {n.get('id') for n in root.iter('node')}:
                _add(check, 'error', 'pix_pmm_connector_end',
                     f'{role.capitalize()} связи ссылается на несуществующую фигуру.', cid)

        # reference: подпись связи студия читает из Text, атрибут label
        # игнорирует — подписи веток шлюзов иначе теряются.
        if connector.get('label') and not connector.get('Text'):
            _add(check, 'error', 'pix_pmm_connector_label',
                 'Подпись связи задана атрибутом label; студия читает Text.', cid)

        points = [w for w in connector if w.tag == 'waypoint']
        if points and len(points) < 2:
            _add(check, 'error', 'pix_pmm_waypoints',
                 f'У связи одна точка маршрута — нужны либо две и больше, '
                 'либо ни одной.', cid)
        # observed: требовать здесь целые числа было ошибкой. Студия сама
        # пишет точки маршрута дробными и с накопленной погрешностью
        # (`x="1704.0000385955093"` в tests/fixtures/sap.pmm), так что правило
        # забраковало бы её собственную выгрузку. Проверяем только то, что
        # координата вообще число: разбор нечислового значения студия не
        # переживёт.
        for point in points:
            if not all(_number_ok((point.get(a) or '').strip()) for a in ('x', 'y')):
                _add(check, 'error', 'pix_pmm_waypoint_not_number',
                     'У точки маршрута связи координаты не числовые.', cid)
                break

    # ── Роли: дорожка без подписи оставляет шаг без ответственного ──────────
    for node in root.iter('node'):
        if node.get('type') in containers and not (node.get('label') or '').strip():
            _add(check, 'warning', 'pix_pmm_lane_unnamed',
                 'У дорожки нет подписи — в студии шаги внутри неё останутся '
                 'без ответственного подразделения.', node.get('id'))


# ──────────────────────────────── XPDL 2.2 ────────────────────────────────

def validate_xpdl(xml: str) -> ExportCheck:
    """Проверяет запасную выгрузку XPDL по спецификации WfMC.

    Правил PIX здесь нет и быть не может: что студия делает с XPDL, неизвестно.
    Проверяется соответствие опубликованному стандарту — того, что файл вообще
    переносим, достаточно, чтобы отдать его сотруднику как запасной вариант.
    """
    check = ExportCheck(format='xpdl')
    try:
        _validate_xpdl(xml, check)
    except Exception as exc:  # noqa: BLE001 — проверка не имеет права падать
        _add(check, 'error', 'xpdl_unreadable', f'Файл не удалось разобрать: {exc}.')
    return check


def _validate_xpdl(xml: str, check: ExportCheck) -> None:
    from app.services.xpdl_exporter import XPDL_NS

    declared = _declared_namespaces(xml)
    if declared.get('xpdl') != XPDL_NS:
        _add(check, 'error', 'xpdl_namespace',
             f'Префикс «xpdl» должен быть привязан к {XPDL_NS}, '
             f'а привязан к {declared.get("xpdl")}.')

    try:
        root = ET.fromstring(xml.encode('utf-8'))
    except ET.ParseError as exc:
        _add(check, 'error', 'xpdl_broken', f'Файл не разбирается как XML: {exc}.')
        return
    if _local(root.tag) != 'package':
        _add(check, 'error', 'xpdl_root',
             f'Корень — <{root.tag}>, ожидался <xpdl:Package>.')
        return
    for attr in ('Id', 'Name'):
        if not (root.get(attr) or '').strip():
            _add(check, 'error', 'xpdl_package_attrs',
                 f'У <xpdl:Package> не задан атрибут {attr}.')

    version = next((e.text for e in root.iter() if _local(e.tag) == 'xpdlversion'), None)
    if (version or '').strip() != '2.2':
        _add(check, 'error', 'xpdl_version',
             f'<xpdl:XPDLVersion> = «{version}», ожидалось 2.2.')

    activities = {a.get('Id'): a for a in root.iter() if _local(a.tag) == 'activity'}
    artifacts = {a.get('Id') for a in root.iter() if _local(a.tag) == 'artifact'}
    workflows = {w.get('Id') for w in root.iter() if _local(w.tag) == 'workflowprocess'}
    lanes = {l.get('Id') for l in root.iter() if _local(l.tag) == 'lane'}

    if not workflows:
        _add(check, 'error', 'xpdl_no_process',
             'В пакете нет ни одного <xpdl:WorkflowProcess>.')
    if not activities:
        _add(check, 'error', 'xpdl_no_activities',
             'В процессе нет ни одной <xpdl:Activity>.')

    seen: Set[str] = set()
    for aid in list(activities) + list(artifacts):
        if not aid:
            _add(check, 'error', 'xpdl_no_id', 'У активности или артефакта нет Id.')
        elif aid in seen:
            _add(check, 'error', 'xpdl_duplicate_id',
                 f'Идентификатор «{aid}» повторяется.', aid)
        seen.add(aid)

    # Пул ссылается на существующий процесс.
    for pool in root.iter():
        if _local(pool.tag) != 'pool':
            continue
        ref = pool.get('Process')
        if ref and ref not in workflows:
            _add(check, 'error', 'xpdl_pool_process',
                 f'Пул ссылается на процесс «{ref}», которого нет в пакете.',
                 pool.get('Id'))

    # Переход соединяет две существующие активности и не ведёт к артефакту.
    for transition in root.iter():
        if _local(transition.tag) != 'transition':
            continue
        tid = transition.get('Id')
        for role, ref in (('From', transition.get('From')), ('To', transition.get('To'))):
            if not ref:
                _add(check, 'error', 'xpdl_transition_end',
                     f'У перехода не задан {role}.', tid)
            elif ref in artifacts:
                _add(check, 'error', 'xpdl_transition_to_artifact',
                     f'{role} перехода указывает на артефакт «{ref}» — '
                     'к артефактам ведёт только <xpdl:Association>.', tid)
            elif ref not in activities:
                _add(check, 'error', 'xpdl_transition_end',
                     f'{role} перехода ссылается на несуществующую активность «{ref}».',
                     tid)
        if transition.get('From') and transition.get('From') == transition.get('To'):
            _add(check, 'error', 'xpdl_transition_self_loop',
                 'Переход замкнут на одну активность.', tid)

    # Координаты — целые, привязка к дорожке разрешима.
    for info in root.iter():
        if _local(info.tag) != 'nodegraphicsinfo':
            continue
        lane_ref = info.get('LaneId')
        if lane_ref and lane_ref not in lanes:
            _add(check, 'error', 'xpdl_lane_ref',
                 f'Фигура привязана к дорожке «{lane_ref}», которой нет в пакете.')
        for attr in ('Height', 'Width'):
            raw = info.get(attr)
            if raw is not None and not _INTEGER_RE.match(raw.strip()):
                _add(check, 'error', 'xpdl_size_not_integer',
                     f'{attr}="{raw}" — ожидается целое число.')
        for point in info:
            if _local(point.tag) != 'coordinates':
                continue
            for attr in ('XCoordinate', 'YCoordinate'):
                raw = point.get(attr)
                if raw is None or not _INTEGER_RE.match(raw.strip()):
                    _add(check, 'error', 'xpdl_coord_not_integer',
                         f'{attr}="{raw}" — ожидается целое число.')


def validate_for_pix(process: BusinessProcess) -> List[ExportCheck]:
    """Все выгрузки процесса, проверенные профилем PIX и стандартом XPDL."""
    from app.services.xpdl_exporter import generate_xpdl

    return [
        validate_bpmn_for_pix(generate_bpmn_xml(process)),
        validate_pmm_for_pix(generate_pmm_zip(process)),
        validate_xpdl(generate_xpdl(process)),
    ]
