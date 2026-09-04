"""Проверка выгрузок теми же правилами, по которым их читает PIX.

Процессная студия отказывается открыть пакет целиком из-за одного дефекта и
сообщает об этом коротко и без адреса: «Notation element not found (Parameter
'type')», «Connector source and target node cannot be the same». Найти по такой
строке нужную фигуру среди шестисот невозможно, поэтому те же проверки платформа
делает у себя — до того, как файл уедет в студию, и с указанием конкретного
узла.

Проверки повторяют поведение импортёра студии и спецификации BPMN 2.0:

* фигура ссылается на элемент нотации, объявленный в ``pm/configuration.xml``;
* связь опирается на две РАЗНЫЕ существующие фигуры;
* манифест ``main.xml`` и содержимое ZIP описывают одни и те же части;
* идентификаторы уникальны, ссылки разрешимы, у каждой фигуры есть геометрия.

Уровни намеренно разделены: ``error`` — студия или импортёр BPMN файл отвергнут,
``warning`` — откроют, но что-то на карте будет выглядеть не так, как задумано.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from app.models.process import BusinessProcess
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.pmm_exporter import bpmn_notation, generate_pmm_zip, notation_categories

#: Категория элементов PIX, которым по природе положено содержать фигуры.
_CONTAINER_CATEGORY = 'Участники'

#: Артефакты BPMN: поток управления к ним подводить нельзя.
_BPMN_ARTIFACTS = {'datastorereference', 'dataobjectreference', 'textannotation', 'group'}

#: Элементы BPMN, между которыми допустим sequenceFlow.
_BPMN_FLOW_NODES = {
    'startevent', 'endevent', 'intermediatecatchevent', 'intermediatethrowevent',
    'boundaryevent', 'task', 'usertask', 'servicetask', 'manualtask', 'scripttask',
    'sendtask', 'receivetask', 'businessruletask', 'subprocess', 'callactivity',
    'transaction', 'exclusivegateway', 'parallelgateway', 'inclusivegateway',
    'complexgateway', 'eventbasedgateway',
}

#: xsd:ID: имя XML, а не произвольная строка. bpmn.io и PIX на этом спотыкаются.
_NCNAME_RE = re.compile(r'^[A-Za-z_][\w.\-]*$')

#: Символы, которых нет в XML 1.0: файл с ними не разберёт ни один импортёр.
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

#: Пространства имён, без которых импортёр не поймёт ни модель, ни диаграмму.
_NS_MODEL = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
_NS_DI = 'http://www.omg.org/spec/BPMN/20100524/DI'
_NS_DC = 'http://www.omg.org/spec/DD/20100524/DC'


@dataclass(frozen=True)
class Problem:
    """Одно замечание к файлу выгрузки."""

    level: str  # 'error' | 'warning'
    code: str
    message: str
    #: Идентификатор фигуры или связи в самом файле — чтобы найти её глазами.
    where: Optional[str] = None


@dataclass
class ExportCheck:
    """Итог проверки одного файла выгрузки."""

    format: str
    problems: List[Problem] = field(default_factory=list)

    @property
    def errors(self) -> List[Problem]:
        return [p for p in self.problems if p.level == 'error']

    @property
    def warnings(self) -> List[Problem]:
        return [p for p in self.problems if p.level == 'warning']

    @property
    def ok(self) -> bool:
        return not self.errors


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower()


def _number_ok(value: Optional[str]) -> bool:
    if value is None:
        return False
    try:
        float(value)
    except ValueError:
        return False
    return True


# ────────────────────────────── PIX .pmm ──────────────────────────────────

def validate_pmm_package(payload: bytes) -> ExportCheck:
    """Проверяет пакет .pmm так, как его читает Процессная студия.

    Проверка стоит на пути каждой выгрузки, поэтому сама она не падает никогда:
    неожиданная поломка файла превращается в замечание, а не в 500-ю ошибку.
    """
    try:
        return _validate_pmm_package(payload)
    except Exception as exc:  # noqa: BLE001 — см. docstring
        broken = ExportCheck(format='pmm')
        broken.problems.append(
            Problem('error', 'pmm_unreadable', f'Пакет не удалось разобрать: {exc}.')
        )
        return broken


def _validate_pmm_package(payload: bytes) -> ExportCheck:
    check = ExportCheck(format='pmm')
    add = lambda level, code, message, where=None: check.problems.append(  # noqa: E731
        Problem(level, code, message, where)
    )

    try:
        package = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        add('error', 'pmm_not_a_zip', f'Пакет не читается как ZIP: {exc}.')
        return check

    names = set(package.namelist())
    map_parts = sorted(n for n in names if n.startswith('pm/maps/') and n.endswith('.xml'))
    missing = [p for p in ('main.xml', 'pm/configuration.xml') if p not in names]
    for required in missing:
        add('error', 'pmm_part_missing', f'В пакете нет обязательной части «{required}».')
    if len(map_parts) != 1:
        add('error', 'pmm_map_count',
            f'В пакете должна быть ровно одна карта, найдено: {len(map_parts)}.')
    # Дальше разбирать нечего: без каталога и манифеста остальные проверки
    # опираться не на что, а падать проверка не имеет права — она стоит на
    # пути каждой выгрузки.
    if missing or not map_parts:
        return check

    # ── Манифест описывает ровно то, что лежит в пакете ─────────────────────
    try:
        manifest = ET.fromstring(package.read('main.xml').decode('utf-8'))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        add('error', 'pmm_manifest_broken', f'main.xml не разбирается: {exc}.')
        return check
    declared = {
        (o.get('PartName') or '').lstrip('/')
        for o in manifest.iter() if _local(o.tag) == 'override'
    }
    for part in declared - names:
        add('error', 'pmm_part_declared_missing',
            f'main.xml объявляет часть «{part}», но её нет в пакете.', part)
    for part in (names - declared) - {'main.xml'}:
        add('error', 'pmm_part_undeclared',
            f'Часть «{part}» лежит в пакете, но не объявлена в main.xml.', part)

    map_part = map_parts[0]
    try:
        map_root = ET.fromstring(package.read(map_part).decode('utf-8'))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        add('error', 'pmm_map_broken', f'Карта {map_part} не разбирается: {exc}.', map_part)
        return check

    if _local(map_root.tag) != 'map':
        add('error', 'pmm_map_root', f'Корень карты — <{map_root.tag}>, ожидался <Map>.', map_part)
    expected_name = map_part[len('pm/maps/'):-len('.xml')]
    if (map_root.get('name') or '') != expected_name:
        add('warning', 'pmm_map_name',
            f'Имя карты «{map_root.get("name")}» не совпадает с именем части «{expected_name}».',
            map_part)

    # ── Нотация и её элементы ───────────────────────────────────────────────
    try:
        config = ET.fromstring(package.read('pm/configuration.xml').decode('utf-8'))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        add('error', 'pmm_config_broken', f'pm/configuration.xml не разбирается: {exc}.')
        return check

    notations = {
        n.get('name'): {e.get('name') for e in n.findall('element') if e.get('name')}
        for n in config.iter('notation') if n.get('name')
    }
    # Регистр имени нотации не значит ничего: каталог объявляет её как ``BPMN``,
    # сама студия пишет в карту ``bpmn`` (tests/fixtures/sap.pmm). Сверяем без
    # учёта регистра, а по каталогу дальше ходим каноническим именем — иначе не
    # найдутся ни категории, ни ``canHaveChildren``, и дорожка с шагами внутри
    # выглядела бы недопустимым вложением.
    notation = map_root.get('notation') or ''
    canonical = {name.lower(): name for name in notations}
    notation_name = canonical.get(notation.lower())
    elements = notations.get(notation_name) if notation_name else None
    if elements is None:
        add('error', 'pmm_notation_unknown',
            f'Нотация «{notation}» не объявлена в каталоге студии. Известные: '
            f'{", ".join(sorted(notations)) or "нет"}.',
            map_part)
        elements = set()

    lookup = notation_name or notation
    categories = notation_categories(config, lookup)
    containers = {name for name, kind in categories.items() if kind == _CONTAINER_CATEGORY}
    with_children = {
        e.get('name')
        for n in config.iter('notation') if n.get('name') == lookup
        for e in n.findall('element') if e.get('canHaveChildren') == 'true'
    }

    # ── Фигуры ──────────────────────────────────────────────────────────────
    node_ids: Set[str] = set()
    parent_of: Dict[str, ET.Element] = {}
    for parent in map_root.iter():
        for child in parent:
            if _local(child.tag) == 'node':
                parent_of[id(child)] = parent

    nodes = [el for el in map_root.iter() if _local(el.tag) == 'node']
    for node in nodes:
        nid = node.get('id') or ''
        label = node.get('label') or nid or '(без подписи)'
        kind = node.get('type')
        if not nid:
            add('error', 'pmm_node_no_id', f'У фигуры «{label}» нет идентификатора.')
        elif nid in node_ids:
            add('error', 'pmm_node_duplicate_id',
                f'Идентификатор фигуры «{label}» повторяется: {nid}.', nid)
        else:
            node_ids.add(nid)

        # Та самая проверка, на которой студия говорит
        # «Notation element not found (Parameter 'type')».
        if not kind:
            add('error', 'pmm_node_no_type', f'У фигуры «{label}» не указан тип.', nid)
        elif elements and kind not in elements:
            add('error', 'pmm_node_type_unknown',
                f'Фигура «{label}»: тип «{kind}» не объявлен в нотации «{notation}».', nid)
        elif categories and not categories.get(kind):
            # Имени в каталоге мало: студия идёт за категорией элемента
            # («Задачи», «Шлюзы», «События»…) и на её отсутствии падает с
            # «Notation element not found (Parameter 'type')». В нотации BPMN
            # так устроен ровно один элемент из 91 — ``input`` («Текст»).
            add('error', 'pmm_node_type_no_category',
                f'Фигура «{label}»: у типа «{kind}» в каталоге студии не '
                f'проставлена категория — студия откажется открыть пакет.', nid)

        for attr in ('x', 'y', 'width', 'height'):
            if not _number_ok(node.get(attr)):
                add('error', 'pmm_node_geometry',
                    f'У фигуры «{label}» не задана координата «{attr}».', nid)
                break
        else:
            if float(node.get('width', '0')) <= 0 or float(node.get('height', '0')) <= 0:
                add('error', 'pmm_node_size',
                    f'У фигуры «{label}» нулевой размер.', nid)

        holder = parent_of.get(id(node))
        if holder is not None and _local(holder.tag) == 'node':
            holder_type = holder.get('type') or ''
            if holder_type not in containers and holder_type not in with_children:
                add('warning', 'pmm_node_nesting',
                    f'Фигура «{label}» вложена в «{holder_type}», который вложений не держит.',
                    nid)

    # ── Связи ───────────────────────────────────────────────────────────────
    connector_ids: Set[str] = set()
    for connector in map_root.iter():
        if _local(connector.tag) != 'connector':
            continue
        cid = connector.get('id') or ''
        src = connector.get('sourceNodeId') or ''
        tgt = connector.get('targetNodeId') or ''
        if not cid:
            add('error', 'pmm_connector_no_id', 'У связи нет идентификатора.')
        elif cid in connector_ids or cid in node_ids:
            add('error', 'pmm_connector_duplicate_id',
                f'Идентификатор связи повторяется: {cid}.', cid)
        else:
            connector_ids.add(cid)

        # Та самая проверка, на которой студия говорит
        # «Connector source and target node cannot be the same».
        if src and src == tgt:
            add('error', 'pmm_connector_self_loop',
                'Связь начинается и заканчивается на одной и той же фигуре.', cid)
        for role, ref in (('источник', src), ('приёмник', tgt)):
            if not ref:
                add('error', 'pmm_connector_dangling',
                    f'У связи не задан {role}.', cid)
            elif ref not in node_ids:
                add('error', 'pmm_connector_dangling',
                    f'{role.capitalize()} связи ссылается на несуществующую фигуру {ref}.', cid)

        waypoints = [w for w in connector if _local(w.tag) == 'waypoint']
        if len(waypoints) < 2:
            add('warning', 'pmm_connector_route',
                f'У связи {len(waypoints)} точек маршрута — студия проложит линию сама.', cid)
        for point in waypoints:
            if not (_number_ok(point.get('x')) and _number_ok(point.get('y'))):
                add('error', 'pmm_connector_waypoint',
                    'У точки маршрута связи нет координат.', cid)
                break

    if not nodes:
        add('error', 'pmm_map_empty', 'В карте нет ни одной фигуры.', map_part)
    return check


# ────────────────────────────── BPMN 2.0 ──────────────────────────────────

def validate_bpmn_xml(xml: str) -> ExportCheck:
    """Проверяет BPMN 2.0 правилами спецификации и импортёров (bpmn.io, PIX)."""
    try:
        return _validate_bpmn_xml(xml)
    except Exception as exc:  # noqa: BLE001 — проверка не имеет права падать
        broken = ExportCheck(format='bpmn')
        broken.problems.append(
            Problem('error', 'bpmn_unreadable', f'Файл не удалось разобрать: {exc}.')
        )
        return broken


def _validate_bpmn_xml(xml: str) -> ExportCheck:
    check = ExportCheck(format='bpmn')
    add = lambda level, code, message, where=None: check.problems.append(  # noqa: E731
        Problem(level, code, message, where)
    )

    # Управляющие символы ломают разбор ещё до всякой семантики. Ищем их до
    # ET.fromstring, чтобы сказать «в подписи мусор из Word», а не «not well-formed».
    control = _CONTROL_CHARS_RE.search(xml)
    if control:
        add('error', 'bpmn_control_chars',
            f'В файле есть символ, недопустимый в XML (код {ord(control.group())}, '
            f'позиция {control.start()}).')

    try:
        root = ET.fromstring(xml.encode('utf-8'))
    except ET.ParseError as exc:
        add('error', 'bpmn_broken', f'Файл не разбирается как XML: {exc}.')
        return check
    if _local(root.tag) != 'definitions':
        add('error', 'bpmn_root', f'Корень — <{root.tag}>, ожидался <definitions>.')
        return check

    # ── Пространства имён ───────────────────────────────────────────────────
    # Импортёр опознаёт элементы по namespace, а не по имени тега: <definitions>
    # без объявленного MODEL для него не BPMN, а безымянный XML.
    if not root.tag.startswith(f'{{{_NS_MODEL}}}'):
        add('error', 'bpmn_namespace',
            f'<definitions> объявлен не в пространстве имён BPMN MODEL '
            f'({_NS_MODEL}) — импортёр не опознает файл как BPMN 2.0.')
    if not (root.get('targetNamespace') or '').strip():
        add('error', 'bpmn_target_namespace',
            'У <definitions> не задан targetNamespace — он обязателен по схеме.')

    ids: Set[str] = set()
    by_id: Dict[str, ET.Element] = {}
    for el in root.iter():
        eid = el.get('id')
        if not eid:
            continue
        if eid in ids:
            add('error', 'bpmn_duplicate_id', f'Идентификатор повторяется: {eid}.', eid)
        ids.add(eid)
        by_id[eid] = el
        if not _NCNAME_RE.match(eid):
            add('error', 'bpmn_id_not_ncname',
                f'Идентификатор «{eid}» не является именем XML — импортёр его отвергнет.', eid)

    def _label(node_id: str) -> str:
        el = by_id.get(node_id)
        return (el.get('name') if el is not None else None) or node_id

    # ── Ссылки ──────────────────────────────────────────────────────────────
    for el in root.iter():
        tag = _local(el.tag)
        for attr in ('sourceRef', 'targetRef', 'bpmnElement', 'attachedToRef', 'processRef'):
            ref = el.get(attr)
            if ref and ref not in ids:
                add('error', 'bpmn_dangling_ref',
                    f'<{tag}> ссылается на несуществующий элемент {ref} ({attr}).',
                    el.get('id'))
        if tag in ('flownoderef', 'incoming', 'outgoing'):
            ref = (el.text or '').strip()
            if ref and ref not in ids:
                add('error', 'bpmn_dangling_ref',
                    f'<{tag}> ссылается на несуществующий элемент {ref}.', ref)

    # ── Потоки управления ───────────────────────────────────────────────────
    incoming: Dict[str, int] = {}
    outgoing: Dict[str, int] = {}
    for flow in root.iter():
        if _local(flow.tag) != 'sequenceflow':
            continue
        fid = flow.get('id')
        src, tgt = flow.get('sourceRef') or '', flow.get('targetRef') or ''
        if not src or not tgt:
            add('error', 'bpmn_flow_dangling', 'У перехода не задан один из концов.', fid)
            continue
        if src == tgt:
            add('error', 'bpmn_flow_self_loop',
                f'Переход у «{_label(src)}» замкнут на ту же фигуру.', fid)
        outgoing[src] = outgoing.get(src, 0) + 1
        incoming[tgt] = incoming.get(tgt, 0) + 1
        for role, ref in (('источник', src), ('приёмник', tgt)):
            el = by_id.get(ref)
            if el is None:
                continue
            kind = _local(el.tag)
            if kind in _BPMN_ARTIFACTS:
                add('error', 'bpmn_flow_to_artifact',
                    f'Поток управления подведён к артефакту «{_label(ref)}» ({role}) — '
                    'к хранилищам и документам ведёт только ассоциация.', fid)
            elif kind not in _BPMN_FLOW_NODES:
                add('warning', 'bpmn_flow_endpoint',
                    f'{role.capitalize()} перехода — <{kind}>, это не узел потока.', fid)

    for el in root.iter():
        tag = _local(el.tag)
        eid = el.get('id') or ''
        if tag == 'startevent' and incoming.get(eid):
            add('error', 'bpmn_start_has_incoming',
                f'В стартовое событие «{_label(eid)}» входит переход.', eid)
        if tag == 'endevent' and outgoing.get(eid):
            add('error', 'bpmn_end_has_outgoing',
                f'Из события завершения «{_label(eid)}» выходит переход.', eid)

    # ── Дорожки ─────────────────────────────────────────────────────────────
    lane_of: Dict[str, str] = {}
    for lane in root.iter():
        if _local(lane.tag) != 'lane':
            continue
        for ref_el in lane:
            if _local(ref_el.tag) != 'flownoderef':
                continue
            ref = (ref_el.text or '').strip()
            if ref in lane_of and lane_of[ref] != lane.get('id'):
                add('error', 'bpmn_node_in_two_lanes',
                    f'Фигура «{_label(ref)}» перечислена в двух дорожках.', ref)
            lane_of[ref] = lane.get('id') or ''

    # ── Диаграмма ───────────────────────────────────────────────────────────
    drawn = {
        el.get('bpmnElement')
        for el in root.iter()
        if _local(el.tag) in ('bpmnshape', 'bpmnedge') and el.get('bpmnElement')
    }
    for el in root.iter():
        tag = _local(el.tag)
        eid = el.get('id')
        if not eid or tag not in _BPMN_FLOW_NODES | _BPMN_ARTIFACTS:
            continue
        if eid not in drawn:
            add('warning', 'bpmn_shape_missing',
                f'У фигуры «{_label(eid)}» нет BPMNShape — импортёр может её не нарисовать.', eid)

    # ── Поток управления не пересекает границу пула ─────────────────────────
    # Спецификация допускает sequenceFlow только внутри одного процесса: между
    # пулами ходит messageFlow. Импортёр на таком переходе спотыкается, потому
    # что не может решить, в чей процесс класть связь.
    process_of: Dict[str, str] = {}
    processes = [el for el in root.iter() if _local(el.tag) == 'process']
    for proc in processes:
        for el in proc.iter():
            if el.get('id'):
                process_of[el.get('id')] = proc.get('id') or ''
    for flow in root.iter():
        if _local(flow.tag) != 'sequenceflow':
            continue
        src, tgt = flow.get('sourceRef') or '', flow.get('targetRef') or ''
        home, away = process_of.get(src), process_of.get(tgt)
        if home and away and home != away:
            add('error', 'bpmn_flow_crosses_pool',
                f'Переход соединяет «{_label(src)}» и «{_label(tgt)}» из разных пулов — '
                'между пулами допустим только messageFlow.', flow.get('id'))

    # ── Дорожка перечисляет узлы своего процесса ────────────────────────────
    for lane in root.iter():
        if _local(lane.tag) != 'lane':
            continue
        lane_home = process_of.get(lane.get('id') or '')
        for ref_el in lane:
            if _local(ref_el.tag) != 'flownoderef':
                continue
            ref = (ref_el.text or '').strip()
            if ref and process_of.get(ref) and process_of.get(ref) != lane_home:
                add('error', 'bpmn_lane_foreign_node',
                    f'Дорожка перечисляет «{_label(ref)}» из другого процесса.',
                    lane.get('id'))
            target = by_id.get(ref)
            if target is not None and _local(target.tag) in _BPMN_ARTIFACTS:
                add('error', 'bpmn_lane_artifact',
                    f'Дорожка перечисляет артефакт «{_label(ref)}»: '
                    'элементом потока он не является.', lane.get('id'))

    # ── Участник ссылается на существующий процесс ──────────────────────────
    for participant in root.iter():
        if _local(participant.tag) != 'participant':
            continue
        ref = participant.get('processRef')
        if ref and (ref not in by_id or _local(by_id[ref].tag) != 'process'):
            add('error', 'bpmn_participant_process',
                f'Участник «{_label(participant.get("id") or "")}» ссылается на {ref}, '
                'а такого <process> в файле нет.', participant.get('id'))

    # ── Граничное событие держится за активность ────────────────────────────
    for boundary in root.iter():
        if _local(boundary.tag) != 'boundaryevent':
            continue
        host = boundary.get('attachedToRef')
        bid = boundary.get('id')
        if not host:
            add('error', 'bpmn_boundary_detached',
                f'У граничного события «{_label(bid or "")}» не задан attachedToRef.', bid)
        elif process_of.get(host) != process_of.get(bid or ''):
            add('error', 'bpmn_boundary_foreign_host',
                f'Граничное событие «{_label(bid or "")}» прицеплено к активности '
                'из другого процесса.', bid)

    # ── Диаграмма ───────────────────────────────────────────────────────────
    # Требование задания «обязательное наличие корректного блока BPMNDI»: без
    # плоскости импортёр открывает файл пустым холстом, а фигура без Bounds или
    # связь с одной точкой роняют разбор диаграммы целиком.
    planes = [el for el in root.iter() if _local(el.tag) == 'bpmnplane']
    if not any(_local(el.tag) == 'bpmndiagram' for el in root.iter()):
        add('error', 'bpmn_no_diagram',
            'В файле нет <BPMNDiagram> — импортёр не получит ни одной координаты.')
    elif not planes:
        add('error', 'bpmn_no_plane', 'В <BPMNDiagram> нет <BPMNPlane>.')
    for plane in planes:
        anchor = plane.get('bpmnElement')
        if not anchor:
            add('error', 'bpmn_plane_anchor',
                'У <BPMNPlane> не задан bpmnElement.', plane.get('id'))
        elif anchor not in ids:
            add('error', 'bpmn_plane_anchor',
                f'<BPMNPlane> ссылается на несуществующий {anchor}.', plane.get('id'))

    drawn_twice: Dict[str, int] = {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in ('bpmnshape', 'bpmnedge'):
            continue
        anchor = el.get('bpmnElement') or ''
        drawn_twice[anchor] = drawn_twice.get(anchor, 0) + 1
        if tag == 'bpmnshape':
            bounds = [c for c in el if _local(c.tag) == 'bounds']
            if not bounds:
                add('error', 'bpmn_shape_no_bounds',
                    f'У фигуры «{_label(anchor)}» нет <dc:Bounds>.', anchor)
                continue
            box = bounds[0]
            if not all(_number_ok(box.get(a)) for a in ('x', 'y', 'width', 'height')):
                add('error', 'bpmn_shape_bounds_broken',
                    f'У фигуры «{_label(anchor)}» в <dc:Bounds> не все координаты числа.',
                    anchor)
            elif float(box.get('width')) <= 0 or float(box.get('height')) <= 0:
                add('error', 'bpmn_shape_zero_size',
                    f'У фигуры «{_label(anchor)}» нулевой размер.', anchor)
        else:
            points = [c for c in el if _local(c.tag) == 'waypoint']
            if len(points) < 2:
                add('error', 'bpmn_edge_waypoints',
                    f'У связи «{_label(anchor)}» {len(points)} точек маршрута, нужно минимум две.',
                    anchor)
            elif not all(_number_ok(w.get('x')) and _number_ok(w.get('y')) for w in points):
                add('error', 'bpmn_edge_waypoint_broken',
                    f'У связи «{_label(anchor)}» точка маршрута без координат.', anchor)
    for anchor, times in drawn_twice.items():
        if times > 1:
            add('error', 'bpmn_di_duplicate',
                f'Элемент «{_label(anchor)}» нарисован на диаграмме {times} раза.', anchor)

    if not any(_local(el.tag) == 'process' for el in root.iter()):
        add('error', 'bpmn_no_process', 'В файле нет ни одного <process>.')
    return check


def validate_process_exports(process: BusinessProcess) -> List[ExportCheck]:
    """Проверяет обе выгрузки процесса — то, что уедет в PIX."""
    return [
        validate_bpmn_xml(generate_bpmn_xml(process)),
        validate_pmm_package(generate_pmm_zip(process)),
    ]


def summary_line(checks: Iterable[ExportCheck]) -> str:
    """Однострочный итог для заголовка ответа и журнала."""
    errors = sum(len(c.errors) for c in checks)
    warnings = sum(len(c.warnings) for c in checks)
    if not errors and not warnings:
        return 'ok'
    return f'errors={errors}; warnings={warnings}'


def _cli(argv: List[str]) -> int:
    """Проверка файла из командной строки: ``python -m app.services.export_validation …``.

    Принимает и карту draw.io (соберёт обе выгрузки и проверит их), и уже
    готовый ``.pmm`` или ``.bpmn`` — тот самый файл, который несут в студию.
    """
    if not argv:
        print('Использование: python -m app.services.export_validation <файл…>')
        return 2

    from app.services.drawio_parser import parse_drawio_xml

    worst = 0
    for path in argv:
        with open(path, 'rb') as handle:
            payload = handle.read()
        if path.lower().endswith('.pmm'):
            checks = [validate_pmm_package(payload)]
        elif path.lower().endswith('.bpmn'):
            checks = [validate_bpmn_xml(payload.decode('utf-8', 'ignore'))]
        else:
            process = parse_drawio_xml(payload.decode('utf-8', 'ignore'), path)
            checks = validate_process_exports(process)

        print(f'{path}: {summary_line(checks)}')
        for check in checks:
            for problem in check.problems:
                where = f' [{problem.where}]' if problem.where else ''
                print(f'  {check.format} {problem.level}: {problem.message}{where}')
            if check.errors:
                worst = 1
    return worst


if __name__ == '__main__':
    import sys as _sys

    raise SystemExit(_cli(_sys.argv[1:]))
