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
  элементами потока не являются.
"""
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    BusinessProcess,
    ProcessEdge,
    ProcessNode,
)
from app.services.edge_routing import orthogonal_waypoints

_NCNAME = re.compile(r'^[A-Za-z_][A-Za-z0-9._-]*$')

_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway')


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
    original = raw or ''
    if original in used:
        return used[original]
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


def _edge_waypoints(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
) -> List[Tuple[int, int]]:
    """Ортогональная ломаная — та же, что рисует draw.io.

    Раньше сюда шла только пара «точка выхода — точка входа», и в bpmn.io
    схема выглядела диагональной паутиной.
    """
    route = orthogonal_waypoints(edge, src, tgt)
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


def _union_bounds(nodes: Iterable[ProcessNode], header: int = 30) -> Tuple[int, int, int, int]:
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
    lanes = list(process.lanes or [])
    node_by_id = {n.id: n for n in all_nodes}
    valid_ids = set(node_by_id)

    # Висячие связи и оформительские линии draw.io в схему не идут: у
    # annotationLine хотя бы один конец не опирается на шаг процесса.
    edges = [
        e for e in process.edges
        if e.kind != 'annotationLine' and e.sourceId in valid_ids and e.targetId in valid_ids
    ]
    # messageFlow допустим только между пулами; карта SQB — один пул, поэтому
    # такие связи выгружаются как обычный поток управления.
    sequence_edges = [e for e in edges if e.kind != 'association']
    association_edges = [e for e in edges if e.kind == 'association']

    effective_type = normalize_event_types(flow_nodes, sequence_edges)

    id_of: Dict[str, str] = {}
    for n in flow_nodes:
        id_of[n.id] = _safe_id(n.id, 'Node', used, taken)
    for n in artifact_nodes:
        id_of[n.id] = _safe_id(n.id, 'Artifact', used, taken)
    for lane in lanes:
        id_of[lane.id] = _safe_id(lane.id, 'Lane', used, taken)
    for edge in edges:
        id_of[edge.id] = _safe_id(edge.id, 'Flow', used, taken)

    incoming_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    outgoing_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    for edge in sequence_edges:
        if edge.targetId in incoming_by_node:
            incoming_by_node[edge.targetId].append(edge.id)
        if edge.sourceId in outgoing_by_node:
            outgoing_by_node[edge.sourceId].append(edge.id)

    use_collab = bool(lanes)
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
        xml.append('  </bpmn:collaboration>')
        xml.append('')

    xml.append(
        f'  <bpmn:process id="{escape_xml(proc_id)}" name="{process_name}" isExecutable="true">'
    )

    # ── 1. laneSet (только узлы потока) ─────────────────────────────────────
    if lanes:
        xml.append(f'    <bpmn:laneSet id="{escape_xml(lane_set_id)}">')
        for lane in lanes:
            xml.append(
                f'      <bpmn:lane id="{escape_xml(id_of[lane.id])}" name="{escape_xml(lane.name)}">'
            )
            for child in flow_nodes:
                if child.laneId == lane.id:
                    xml.append(
                        f'        <bpmn:flowNodeRef>{escape_xml(id_of[child.id])}</bpmn:flowNodeRef>'
                    )
            xml.append('      </bpmn:lane>')
        xml.append('    </bpmn:laneSet>')

    # ── 2. Узлы потока ──────────────────────────────────────────────────────
    for node in flow_nodes:
        nid = escape_xml(id_of[node.id])
        kind = effective_type[node.id]
        tag = _node_tag(node, kind)
        name_attr = f' name="{escape_xml(node.name)}"'
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

    for edge in association_edges:
        xml.append(
            f'    <bpmn:association id="{escape_xml(id_of[edge.id])}"'
            f' sourceRef="{escape_xml(id_of[edge.sourceId])}"'
            f' targetRef="{escape_xml(id_of[edge.targetId])}"'
            ' associationDirection="One" />'
        )

    xml.append('  </bpmn:process>')
    xml.append('')

    # ── Диаграмма ───────────────────────────────────────────────────────────
    xml.append(f'  <bpmndi:BPMNDiagram id="{escape_xml(diag_id)}">')
    xml.append(
        f'    <bpmndi:BPMNPlane id="{escape_xml(plane_id)}" bpmnElement="{escape_xml(plane_ref)}">'
    )

    if use_collab:
        # Пул охватывает и дорожки, и узлы: иначе часть карты окажется вне пула.
        px, py, pw, ph = _union_bounds(lanes + all_nodes)
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(participant_id)}_di" '
            f'bpmnElement="{escape_xml(participant_id)}" isHorizontal="true">'
        )
        xml.append(f'        <dc:Bounds x="{px}" y="{py}" width="{pw}" height="{ph}" />')
        xml.append('      </bpmndi:BPMNShape>')

    for lane in lanes:
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[lane.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[lane.id])}" isHorizontal="true">'
        )
        xml.append(
            f'        <dc:Bounds x="{lane.geometry.x}" y="{lane.geometry.y}" '
            f'width="{lane.geometry.width}" height="{lane.geometry.height}" />'
        )
        xml.append('      </bpmndi:BPMNShape>')

    for node in all_nodes:
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[node.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[node.id])}">'
        )
        xml.append(
            f'        <dc:Bounds x="{node.geometry.x}" y="{node.geometry.y}" '
            f'width="{node.geometry.width}" height="{node.geometry.height}" />'
        )
        xml.append('      </bpmndi:BPMNShape>')

    for edge in edges:
        src = node_by_id.get(edge.sourceId or '')
        tgt = node_by_id.get(edge.targetId or '')
        xml.append(
            f'      <bpmndi:BPMNEdge id="{escape_xml(id_of[edge.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[edge.id])}">'
        )
        for x, y in _edge_waypoints(edge, src, tgt):
            xml.append(f'        <di:waypoint x="{x}" y="{y}" />')
        xml.append('      </bpmndi:BPMNEdge>')

    xml.append('    </bpmndi:BPMNPlane>')
    xml.append('  </bpmndi:BPMNDiagram>')
    xml.append('</bpmn:definitions>')
    return '\n'.join(xml)
