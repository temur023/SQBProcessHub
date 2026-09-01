"""XPDL 2.2 (WfMC) — запасной формат обмена картой процесса.

Зачем он нужен. Основной путь в PIX Процессную студию — ``.bpmn`` и нативный
``.pmm``. Когда студия отказывается открыть пакет, у сотрудника сейчас нет
второго варианта: он возвращается в draw.io и гадает. XPDL закрывает эту дыру —
это опубликованный стандарт обмена (WfMC XPDL 2.2, тот же набор понятий, что и
BPMN 2.0: пул, дорожки, активности, переходы, координаты), который читают
Bizagi, Together Workflow, ADONIS и другие средства моделирования. Даже если
студия его не возьмёт, карта останется переносимой: её можно открыть в стороннем
редакторе, сохранить в BPMN оттуда и внести уже проверенным путём.

ЧЕСТНАЯ ОГОВОРКА. Что PIX Studio импортирует XPDL — НЕ проверено: в репозитории
нет ни эталонного XPDL-файла студии, ни её документации, а интерфейс платформы
утверждает лишь, что «официальный обмен по-прежнему .bpmn / .vsdx». Поэтому
формат сделан строго по спецификации WfMC, а не подогнан под догадки о студии,
и до первой удачной пробной загрузки его следует считать запасным средством
переноса, а не вторым официальным каналом.

Соответствие понятий::

    дорожка draw.io      -> xpdl:Lane внутри единственного xpdl:Pool
    шаг                  -> xpdl:Activity + xpdl:Implementation
    событие              -> xpdl:Activity + xpdl:Event (Start/End/Intermediate)
    шлюз                 -> xpdl:Activity + xpdl:Route
    связь                -> xpdl:Transition (From/To)
    время шага           -> xpdl:SimulationInformation/xpdl:TimeEstimation

Артефакты (хранилища, документы, примечания) в XPDL 2.2 переносятся
``xpdl:Artifact`` и ``xpdl:Association``; ассоциации к ним не смешиваются с
``xpdl:Transition``, как и в BPMN.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    BusinessProcess,
    ProcessEdge,
    ProcessNode,
)
from app.services.bpmn_exporter import (
    duration_label,
    escape_xml,
    split_external_lanes,
)

#: Пространство имён XPDL 2.2 (WfMC).
XPDL_NS = 'http://www.wfmc.org/2009/XPDL2.2'
_XSI_NS = 'http://www.w3.org/2001/XMLSchema-instance'
_SCHEMA_LOCATION = f'{XPDL_NS} http://www.wfmc.org/standards/docs/bpmnxpdl_32.xsd'

#: Id в XPDL — xsd:NMTOKEN: буквы, цифры и . - _ : без пробелов.
_NMTOKEN_BAD = re.compile(r'[^A-Za-z0-9._:-]')

#: Шлюз draw.io -> xpdl:Route/@GatewayType.
_GATEWAY_KIND: Dict[str, str] = {
    'exclusiveGateway': 'Exclusive',
    'parallelGateway': 'Parallel',
    'inclusiveGateway': 'Inclusive',
    'complexGateway': 'Complex',
}

#: Промежуточное событие -> триггер XPDL.
_INTERMEDIATE_TRIGGER: Dict[str, str] = {
    'intermediateTimerEvent': 'Timer',
    'intermediateMessageEvent': 'Message',
}

#: Артефакт draw.io -> xpdl:Artifact/@ArtifactType.
_ARTIFACT_KIND: Dict[str, str] = {
    'dataStore': 'DataObject',
    'dataObject': 'DataObject',
    'textAnnotation': 'Annotation',
}


def _xpdl_id(raw: str, taken: Optional[set] = None) -> str:
    """Идентификатор, пригодный для xsd:NMTOKEN, и обязательно уникальный."""
    cleaned = _NMTOKEN_BAD.sub('_', raw or '')
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f'id_{cleaned}' if cleaned else 'id'
    if taken is None:
        return cleaned
    candidate, index = cleaned, 2
    while candidate in taken:
        candidate = f'{cleaned}_{index}'
        index += 1
    taken.add(candidate)
    return candidate


def _graphics(x: int, y: int, width: int, height: int,
              lane_id: Optional[str] = None, indent: str = '        ') -> List[str]:
    """Блок координат XPDL: он же переносит привязку фигуры к дорожке."""
    lane_attr = f' LaneId="{escape_xml(lane_id)}"' if lane_id else ''
    return [
        f'{indent}<xpdl:NodeGraphicsInfos>',
        f'{indent}  <xpdl:NodeGraphicsInfo ToolId="SQBProcessHub" '
        f'Height="{int(height)}" Width="{int(width)}"{lane_attr} '
        f'BorderColor="-16777216" FillColor="-1">',
        f'{indent}    <xpdl:Coordinates XCoordinate="{int(x)}" YCoordinate="{int(y)}" />',
        f'{indent}  </xpdl:NodeGraphicsInfo>',
        f'{indent}</xpdl:NodeGraphicsInfos>',
    ]


def _activity_body(node: ProcessNode, indent: str) -> List[str]:
    """Что за активность: событие, шлюз или задача.

    Порядок элементов внутри ``xpdl:Activity`` задан схемой XPDL: сначала род
    активности (Implementation | Route | Event | BlockActivity), затем описание,
    затем графика. Схема последовательная, и перестановка ломает разбор так же,
    как в BPMN.
    """
    kind = node.type
    if kind == 'startEvent':
        return [
            f'{indent}<xpdl:Event>',
            f'{indent}  <xpdl:StartEvent Trigger="None" />',
            f'{indent}</xpdl:Event>',
        ]
    if kind == 'endEvent':
        return [
            f'{indent}<xpdl:Event>',
            f'{indent}  <xpdl:EndEvent Result="None" />',
            f'{indent}</xpdl:Event>',
        ]
    if kind in _INTERMEDIATE_TRIGGER:
        trigger = _INTERMEDIATE_TRIGGER[kind]
        return [
            f'{indent}<xpdl:Event>',
            f'{indent}  <xpdl:IntermediateEvent Trigger="{trigger}" />',
            f'{indent}</xpdl:Event>',
        ]
    if kind in _GATEWAY_KIND:
        return [f'{indent}<xpdl:Route GatewayType="{_GATEWAY_KIND[kind]}" />']
    if kind == 'subProcess':
        return [
            f'{indent}<xpdl:Implementation>',
            f'{indent}  <xpdl:SubFlow Id="{escape_xml(node.id)}" Execution="SYNCHR" />',
            f'{indent}</xpdl:Implementation>',
        ]
    # Обычный шаг. <xpdl:No/> — «исполнителя не задано»: карта описывает
    # регламент, а не исполняемый движок, и подставлять сюда приложение нельзя.
    return [
        f'{indent}<xpdl:Implementation>',
        f'{indent}  <xpdl:No />',
        f'{indent}</xpdl:Implementation>',
    ]


def _performer(node: ProcessNode, indent: str) -> List[str]:
    """Роль исполнителя шага — то, ради чего в регламенте нужны дорожки."""
    role = (node.role or node.laneName or '').strip()
    if not role:
        return []
    return [f'{indent}<xpdl:Performers>',
            f'{indent}  <xpdl:Performer>{escape_xml(role)}</xpdl:Performer>',
            f'{indent}</xpdl:Performers>']


def _simulation(node: ProcessNode, indent: str) -> List[str]:
    """Норматив времени шага в штатном месте XPDL, а не в примечании."""
    minutes = int(node.slaMinutes or 0)
    if minutes <= 0:
        return []
    return [
        f'{indent}<xpdl:SimulationInformation>',
        f'{indent}  <xpdl:TimeEstimation>',
        f'{indent}    <xpdl:WorkingTime>{minutes * 60}</xpdl:WorkingTime>',
        f'{indent}  </xpdl:TimeEstimation>',
        f'{indent}</xpdl:SimulationInformation>',
    ]


def _documentation(node: ProcessNode) -> str:
    """Паспорт шага одной строкой: система, роль, время, потенциал RPA."""
    parts: List[str] = []
    if node.description:
        parts.append(node.description.strip())
    if node.system:
        parts.append(f'ИТ-система: {node.system}')
    if node.role:
        parts.append(f'Роль: {node.role}')
    label = duration_label(node.slaMinutes)
    if label:
        parts.append(f'Норматив: {label}')
    wait = duration_label(node.waitMinutes)
    if wait:
        parts.append(f'Ожидание: {wait}')
    if node.automationPotential:
        parts.append(f'Потенциал RPA: {node.automationPotential}%')
    return ' | '.join(parts)


def generate_xpdl(process: BusinessProcess) -> str:
    """Собирает пакет XPDL 2.2 из модели процесса."""
    taken: set = set()
    package_id = _xpdl_id(f'Package_{process.passport.code or process.id}', taken)
    workflow_id = _xpdl_id(f'Process_{process.passport.code or process.id}', taken)
    pool_id = _xpdl_id(f'Pool_{workflow_id}', taken)

    # Дорожка без единого шага — это внешняя сторона, а не зона ответственности
    # банка. В BPMN-выгрузке она становится отдельным участником; здесь по той
    # же причине она не попадает в Lanes основного пула.
    flow_nodes = [n for n in process.nodes
                  if n.type != 'lane' and n.type not in ARTIFACT_NODE_TYPES]
    artifacts = [n for n in process.nodes if n.type in ARTIFACT_NODE_TYPES]
    inner_lanes, external_lanes = split_external_lanes(process.lanes, flow_nodes)

    id_of: Dict[str, str] = {}
    for lane in inner_lanes + external_lanes:
        id_of[lane.id] = _xpdl_id(f'Lane_{lane.id}', taken)
    for node in flow_nodes:
        id_of[node.id] = _xpdl_id(f'Act_{node.id}', taken)
    for node in artifacts:
        id_of[node.id] = _xpdl_id(f'Art_{node.id}', taken)
    for edge in process.edges:
        id_of[edge.id] = _xpdl_id(f'Tr_{edge.id}', taken)

    known = {n.id for n in flow_nodes}
    artifact_ids = {n.id for n in artifacts}

    # Границы пула: пул обязан накрывать всё, что в нём лежит.
    boxes = [n.geometry for n in process.nodes if n.type != 'lane'] + \
            [lane.geometry for lane in inner_lanes]
    if boxes:
        min_x = min(b.x for b in boxes)
        min_y = min(b.y for b in boxes)
        max_x = max(b.x + b.width for b in boxes)
        max_y = max(b.y + b.height for b in boxes)
    else:
        min_x = min_y = 0
        max_x, max_y = 800, 400

    out: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<xpdl:Package xmlns:xpdl="{XPDL_NS}" xmlns:xsi="{_XSI_NS}"',
        f'  xsi:schemaLocation="{_SCHEMA_LOCATION}"',
        f'  Id="{package_id}" Name="{escape_xml(process.name)}">',
        '',
        '  <xpdl:PackageHeader>',
        '    <xpdl:XPDLVersion>2.2</xpdl:XPDLVersion>',
        '    <xpdl:Vendor>SQB Process Hub</xpdl:Vendor>',
        f'    <xpdl:Created>{datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}</xpdl:Created>',
        f'    <xpdl:Description>{escape_xml(process.passport.description or "")}'
        '</xpdl:Description>',
        '  </xpdl:PackageHeader>',
        '',
        '  <xpdl:Pools>',
        f'    <xpdl:Pool Id="{pool_id}" Name="{escape_xml(process.name)}" '
        f'Process="{workflow_id}" BoundaryVisible="true" Orientation="HORIZONTAL">',
    ]
    out += _graphics(min_x, min_y, max_x - min_x, max_y - min_y, indent='      ')
    out.append('      <xpdl:Lanes>')
    for lane in inner_lanes:
        geo = lane.geometry
        out.append(f'        <xpdl:Lane Id="{id_of[lane.id]}" '
                   f'Name="{escape_xml(lane.name)}">')
        out += _graphics(geo.x, geo.y, geo.width, geo.height, indent='          ')
        out.append('        </xpdl:Lane>')
    out += ['      </xpdl:Lanes>',
            '    </xpdl:Pool>']

    # Внешние стороны — отдельные пулы без содержимого («чёрный ящик»).
    for lane in external_lanes:
        geo = lane.geometry
        out.append(f'    <xpdl:Pool Id="{id_of[lane.id]}" '
                   f'Name="{escape_xml(lane.name)}" BoundaryVisible="true" '
                   f'Orientation="HORIZONTAL">')
        out += _graphics(geo.x, geo.y, geo.width, geo.height, indent='      ')
        out.append('    </xpdl:Pool>')
    out += ['  </xpdl:Pools>', '']

    out += [
        '  <xpdl:WorkflowProcesses>',
        f'    <xpdl:WorkflowProcess Id="{workflow_id}" '
        f'Name="{escape_xml(process.name)}">',
        '      <xpdl:ProcessHeader>',
        f'        <xpdl:Created>{datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}</xpdl:Created>',
        f'        <xpdl:Description>{escape_xml(process.passport.name or "")}'
        '</xpdl:Description>',
        '      </xpdl:ProcessHeader>',
        '      <xpdl:Activities>',
    ]
    for node in flow_nodes:
        geo = node.geometry
        lane_ref = id_of.get(node.laneId or '') if node.laneId else None
        out.append(f'        <xpdl:Activity Id="{id_of[node.id]}" '
                   f'Name="{escape_xml(node.name)}">')
        out += _activity_body(node, '          ')
        out += _performer(node, '          ')
        doc = _documentation(node)
        if doc:
            out.append(f'          <xpdl:Description>{escape_xml(doc)}'
                       '</xpdl:Description>')
        out += _simulation(node, '          ')
        out += _graphics(geo.x, geo.y, geo.width, geo.height, lane_ref, '          ')
        out.append('        </xpdl:Activity>')
    out += ['      </xpdl:Activities>', '', '      <xpdl:Transitions>']

    for edge in process.edges:
        # Поток управления связывает только шаги. Ассоциация к артефакту и
        # оформительская линия переходом не являются — как и в BPMN-выгрузке.
        if edge.kind == 'annotationLine':
            continue
        if edge.sourceId in artifact_ids or edge.targetId in artifact_ids:
            continue
        if edge.sourceId not in known or edge.targetId not in known:
            continue
        name = f' Name="{escape_xml(edge.name)}"' if edge.name else ''
        out.append(f'        <xpdl:Transition Id="{id_of[edge.id]}" '
                   f'From="{id_of[edge.sourceId]}" To="{id_of[edge.targetId]}"{name}>')
        if edge.name:
            out += [
                '          <xpdl:Condition Type="CONDITION">',
                f'            <xpdl:Expression>{escape_xml(edge.name)}</xpdl:Expression>',
                '          </xpdl:Condition>',
            ]
        out.append('        </xpdl:Transition>')
    out += ['      </xpdl:Transitions>']

    if artifacts:
        out += ['', '      <xpdl:Artifacts>']
        for node in artifacts:
            geo = node.geometry
            kind = _ARTIFACT_KIND.get(node.type, 'DataObject')
            text = (f' TextAnnotation="{escape_xml(node.name)}"'
                    if node.type == 'textAnnotation' else '')
            out.append(f'        <xpdl:Artifact Id="{id_of[node.id]}" '
                       f'Name="{escape_xml(node.name)}" '
                       f'ArtifactType="{kind}"{text}>')
            out += _graphics(geo.x, geo.y, geo.width, geo.height, indent='          ')
            out.append('        </xpdl:Artifact>')
        out += ['      </xpdl:Artifacts>']

        associations = [
            e for e in process.edges
            if (e.sourceId in artifact_ids or e.targetId in artifact_ids)
            and e.sourceId in id_of and e.targetId in id_of
        ]
        if associations:
            out += ['', '      <xpdl:Associations>']
            for edge in associations:
                out.append(f'        <xpdl:Association Id="{id_of[edge.id]}" '
                           f'Source="{id_of[edge.sourceId]}" '
                           f'Target="{id_of[edge.targetId]}" '
                           f'AssociationDirection="None" />')
            out += ['      </xpdl:Associations>']

    out += ['    </xpdl:WorkflowProcess>',
            '  </xpdl:WorkflowProcesses>',
            '</xpdl:Package>',
            '']
    return '\n'.join(out)
