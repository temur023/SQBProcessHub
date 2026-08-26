import re
from typing import Dict, Iterable, List, Optional, Tuple

from app.models.process import BusinessProcess, ProcessEdge, ProcessNode

_NCNAME = re.compile(r'^[A-Za-z_][A-Za-z0-9._-]*$')


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


def _anchor(node: ProcessNode, frac_x: float, frac_y: float) -> Tuple[int, int]:
    g = node.geometry
    return (
        int(round(g.x + g.width * frac_x)),
        int(round(g.y + g.height * frac_y)),
    )


def _edge_waypoints(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
) -> List[Tuple[int, int]]:
    if not src or not tgt:
        return [(100, 100), (250, 100)]
    exit_x = edge.exitX if edge.exitX is not None else 1.0
    exit_y = edge.exitY if edge.exitY is not None else 0.5
    entry_x = edge.entryX if edge.entryX is not None else 0.0
    entry_y = edge.entryY if edge.entryY is not None else 0.5
    pts: List[Tuple[int, int]] = [_anchor(src, exit_x, exit_y)]
    for p in edge.points or []:
        pts.append((int(p.x), int(p.y)))
    pts.append(_anchor(tgt, entry_x, entry_y))
    out: List[Tuple[int, int]] = []
    for pt in pts:
        if not out or out[-1] != pt:
            out.append(pt)
    if len(out) < 2:
        return [pts[0], pts[-1]]
    return out


def _node_tag(node: ProcessNode) -> str:
    if node.type == 'startEvent':
        return 'bpmn:startEvent'
    if node.type == 'endEvent':
        return 'bpmn:endEvent'
    if node.type == 'exclusiveGateway':
        return 'bpmn:exclusiveGateway'
    if node.type == 'parallelGateway':
        return 'bpmn:parallelGateway'
    if node.type == 'inclusiveGateway':
        return 'bpmn:inclusiveGateway'
    if node.type == 'serviceTask' or node.category == 'rpa_bot':
        return 'bpmn:serviceTask'
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
        bits.append(f'SLA: {node.slaMinutes} min')
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
    """OMG BPMN 2.0 + BPMNDI for PIX Process Studio / Processet / Camunda."""
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

    flow_nodes = [n for n in process.nodes if n.type != 'lane']
    lanes = list(process.lanes or [])
    node_by_id = {n.id: n for n in flow_nodes}

    id_of = {}
    for n in flow_nodes:
        id_of[n.id] = _safe_id(n.id, 'Node', used, taken)
    for lane in lanes:
        id_of[lane.id] = _safe_id(lane.id, 'Lane', used, taken)
    for edge in process.edges:
        id_of[edge.id] = _safe_id(edge.id, 'Flow', used, taken)

    incoming_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    outgoing_by_node: Dict[str, List[str]] = {n.id: [] for n in flow_nodes}
    for edge in process.edges:
        if edge.targetId and edge.targetId in incoming_by_node:
            incoming_by_node[edge.targetId].append(edge.id)
        if edge.sourceId and edge.sourceId in outgoing_by_node:
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
        '  exporterVersion="1.1">',
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

    for node in flow_nodes:
        nid = escape_xml(id_of[node.id])
        tag = _node_tag(node)
        name_attr = f' name="{escape_xml(node.name)}"'
        extras: List[str] = []
        out_count = len(outgoing_by_node.get(node.id, []))
        if 'Gateway' in node.type and out_count > 1:
            extras.append(' gatewayDirection="Diverging"')
        elif 'Gateway' in node.type and len(incoming_by_node.get(node.id, [])) > 1:
            extras.append(' gatewayDirection="Converging"')

        children: List[str] = []
        doc = _node_documentation(node)
        if doc:
            children.append(f'      <bpmn:documentation>{escape_xml(doc)}</bpmn:documentation>')
        for edge_id in incoming_by_node.get(node.id, []):
            children.append(
                f'      <bpmn:incoming>{escape_xml(id_of[edge_id])}</bpmn:incoming>'
            )
        for edge_id in outgoing_by_node.get(node.id, []):
            children.append(
                f'      <bpmn:outgoing>{escape_xml(id_of[edge_id])}</bpmn:outgoing>'
            )

        extra = ''.join(extras)
        if children:
            xml.append(f'    <{tag} id="{nid}"{name_attr}{extra}>')
            xml.extend(children)
            xml.append(f'    </{tag}>')
        else:
            xml.append(f'    <{tag} id="{nid}"{name_attr}{extra} />')

    for edge in process.edges:
        eid = escape_xml(id_of[edge.id])
        name = edge.name or edge.condition or ''
        name_attr = f' name="{escape_xml(name)}"' if name else ''
        src = escape_xml(id_of[edge.sourceId]) if edge.sourceId and edge.sourceId in id_of else ''
        tgt = escape_xml(id_of[edge.targetId]) if edge.targetId and edge.targetId in id_of else ''
        src_attr = f' sourceRef="{src}"' if src else ''
        tgt_attr = f' targetRef="{tgt}"' if tgt else ''
        expr = (edge.condition or edge.name or '').strip()
        if expr:
            xml.append(
                f'    <bpmn:sequenceFlow id="{eid}"{name_attr}{src_attr}{tgt_attr}>'
            )
            xml.append(
                f'      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
                f'{escape_xml(expr)}</bpmn:conditionExpression>'
            )
            xml.append('    </bpmn:sequenceFlow>')
        else:
            xml.append(
                f'    <bpmn:sequenceFlow id="{eid}"{name_attr}{src_attr}{tgt_attr} />'
            )

    xml.append('  </bpmn:process>')
    xml.append('')
    xml.append(f'  <bpmndi:BPMNDiagram id="{escape_xml(diag_id)}">')
    xml.append(
        f'    <bpmndi:BPMNPlane id="{escape_xml(plane_id)}" bpmnElement="{escape_xml(plane_ref)}">'
    )

    if use_collab:
        px, py, pw, ph = _union_bounds(lanes)
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

    for node in flow_nodes:
        xml.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(id_of[node.id])}_di" '
            f'bpmnElement="{escape_xml(id_of[node.id])}">'
        )
        xml.append(
            f'        <dc:Bounds x="{node.geometry.x}" y="{node.geometry.y}" '
            f'width="{node.geometry.width}" height="{node.geometry.height}" />'
        )
        xml.append('      </bpmndi:BPMNShape>')

    for edge in process.edges:
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
