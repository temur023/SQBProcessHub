import re

from app.models.process import BusinessProcess


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


def _sanitize_bpmn_id(raw: str, prefix: str = "id") -> str:
    """Делает строку валидным BPMN NCName: [A-Za-z_][A-Za-z0-9_.-]*"""
    s = re.sub(r'[^A-Za-z0-9_.-]+', '_', (raw or '').strip())
    if not s:
        s = f"{prefix}_1"
    if not re.match(r'^[A-Za-z_]', s):
        s = f"{prefix}_{s}"
    # BPMN ID не должен содержать двойной дефис в комментариях, но в ID допустим
    return s[:120]


def generate_bpmn_xml(process: BusinessProcess) -> str:
    """
    Generates standard OMG BPMN 2.0 XML with BPMNDiagram elements
    for direct import into Infomaximum Processet.
    """
    proc_id = _sanitize_bpmn_id(f"Process_{process.passport.code}", "Process")
    diag_id = _sanitize_bpmn_id(f"Diagram_{proc_id}", "Diagram")
    plane_id = _sanitize_bpmn_id(f"Plane_{proc_id}", "Plane")
    definitions_id = _sanitize_bpmn_id(f"Definitions_{process.id}", "Definitions")

    flow_nodes = [n for n in process.nodes if n.type != 'lane']
    lanes = process.lanes
    safe_name = escape_xml(process.name)
    safe_comment = escape_xml(process.name).replace('--', '—')

    incoming_by_node: dict[str, list[str]] = {n.id: [] for n in flow_nodes}
    outgoing_by_node: dict[str, list[str]] = {n.id: [] for n in flow_nodes}
    for edge in process.edges:
        if edge.targetId and edge.targetId in incoming_by_node:
            incoming_by_node[edge.targetId].append(edge.id)
        if edge.sourceId and edge.sourceId in outgoing_by_node:
            outgoing_by_node[edge.sourceId].append(edge.id)

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        '  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
        '  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
        '  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
        '  xmlns:sqb="http://sqb.uz/schema/bpmn"',
        f'  id="{escape_xml(definitions_id)}"',
        '  targetNamespace="http://bpmn.io/schema/bpmn">',
        '',
        f'  <!-- Process Definition: {safe_comment} -->',
        f'  <bpmn:process id="{escape_xml(proc_id)}" name="{safe_name}" isExecutable="true">',
    ]

    if lanes:
        xml_lines.append('    <bpmn:laneSet id="LaneSet_1">')
        for lane in lanes:
            xml_lines.append(
                f'      <bpmn:lane id="{escape_xml(lane.id)}" name="{escape_xml(lane.name)}">'
            )
            lane_child_nodes = [n for n in flow_nodes if n.laneId == lane.id]
            for child in lane_child_nodes:
                xml_lines.append(
                    f'        <bpmn:flowNodeRef>{escape_xml(child.id)}</bpmn:flowNodeRef>'
                )
            xml_lines.append('      </bpmn:lane>')
        xml_lines.append('    </bpmn:laneSet>')

    def _io_children(node_id: str) -> str:
        parts = []
        for edge_id in incoming_by_node.get(node_id, []):
            parts.append(f'      <bpmn:incoming>{escape_xml(edge_id)}</bpmn:incoming>')
        for edge_id in outgoing_by_node.get(node_id, []):
            parts.append(f'      <bpmn:outgoing>{escape_xml(edge_id)}</bpmn:outgoing>')
        return '\n'.join(parts)

    for node in flow_nodes:
        nid = escape_xml(node.id)
        name_attr = f'name="{escape_xml(node.name)}"'
        ext_props = (
            f' sqb:role="{escape_xml(node.role or "")}"'
            f' sqb:system="{escape_xml(node.system or "")}"'
            f' sqb:slaMinutes="{node.slaMinutes or 0}"'
            f' sqb:automationPotential="{node.automationPotential or 0}"'
        )
        children = _io_children(node.id)

        if node.type == 'startEvent':
            tag, extra = 'bpmn:startEvent', ext_props
        elif node.type == 'endEvent':
            tag, extra = 'bpmn:endEvent', ext_props
        elif node.type == 'serviceTask' or node.category == 'rpa_bot':
            tag, extra = 'bpmn:serviceTask', ext_props + ' implementation="PIX_RPA"'
        elif node.type == 'exclusiveGateway':
            tag, extra = 'bpmn:exclusiveGateway', ''
        elif node.type == 'parallelGateway':
            tag, extra = 'bpmn:parallelGateway', ''
        elif node.type == 'inclusiveGateway':
            tag, extra = 'bpmn:inclusiveGateway', ''
        else:
            tag, extra = 'bpmn:userTask', ext_props

        if children:
            xml_lines.append(f'    <{tag} id="{nid}" {name_attr}{extra}>')
            xml_lines.append(children)
            xml_lines.append(f'    </{tag}>')
        else:
            xml_lines.append(f'    <{tag} id="{nid}" {name_attr}{extra} />')

    # Фильтруем невалидные sequenceFlow (без source/target) — иначе BPMN невалиден
    valid_edges = [e for e in process.edges if e.sourceId and e.targetId]
    for edge in valid_edges:
        # Собираем атрибуты без лишних пробелов
        attrs = [f'id="{escape_xml(edge.id)}"']
        if edge.name:
            attrs.append(f'name="{escape_xml(edge.name)}"')
        attrs.append(f'sourceRef="{escape_xml(edge.sourceId)}"')
        attrs.append(f'targetRef="{escape_xml(edge.targetId)}"')
        xml_lines.append(
            f'    <bpmn:sequenceFlow {" ".join(attrs)} />'
        )

    xml_lines.append('  </bpmn:process>')
    xml_lines.append('')
    xml_lines.append('  <!-- BPMN 2.0 Diagram Layout for Processet -->')
    xml_lines.append(f'  <bpmndi:BPMNDiagram id="{escape_xml(diag_id)}">')
    xml_lines.append(f'    <bpmndi:BPMNPlane id="{escape_xml(plane_id)}" bpmnElement="{escape_xml(proc_id)}">')

    for lane in lanes:
        xml_lines.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(lane.id)}_di" bpmnElement="{escape_xml(lane.id)}" isHorizontal="true">'
        )
        xml_lines.append(
            f'        <dc:Bounds x="{lane.geometry.x}" y="{lane.geometry.y}" width="{lane.geometry.width}" height="{lane.geometry.height}" />'
        )
        xml_lines.append('      </bpmndi:BPMNShape>')

    for node in flow_nodes:
        xml_lines.append(
            f'      <bpmndi:BPMNShape id="{escape_xml(node.id)}_di" bpmnElement="{escape_xml(node.id)}">'
        )
        xml_lines.append(
            f'        <dc:Bounds x="{node.geometry.x}" y="{node.geometry.y}" width="{node.geometry.width}" height="{node.geometry.height}" />'
        )
        xml_lines.append('      </bpmndi:BPMNShape>')

    for edge in valid_edges:
        xml_lines.append(
            f'      <bpmndi:BPMNEdge id="{escape_xml(edge.id)}_di" bpmnElement="{escape_xml(edge.id)}">'
        )
        src = next((n for n in flow_nodes if n.id == edge.sourceId), None)
        tgt = next((n for n in flow_nodes if n.id == edge.targetId), None)
        if src and tgt:
            # Учитываем exitX/entryY и промежуточные точки edge.points
            def _constraint_point(node, fx, fy):
                if fx is not None and fy is not None:
                    return (node.geometry.x + fx * node.geometry.width,
                            node.geometry.y + fy * node.geometry.height)
                return None
            start = _constraint_point(src, edge.exitX, edge.exitY)
            end = _constraint_point(tgt, edge.entryX, edge.entryY)
            if start is None:
                start = (src.geometry.x + src.geometry.width, src.geometry.y + src.geometry.height // 2)
            if end is None:
                end = (tgt.geometry.x, tgt.geometry.y + tgt.geometry.height // 2)
            waypoints = [start]
            # промежуточные точки из draw.io
            for pt in (edge.points or []):
                waypoints.append((pt.x, pt.y))
            waypoints.append(end)
            for (x, y) in waypoints:
                xml_lines.append(f'        <di:waypoint x="{int(round(x))}" y="{int(round(y))}" />')
        xml_lines.append('      </bpmndi:BPMNEdge>')

    xml_lines.append('    </bpmndi:BPMNPlane>')
    xml_lines.append('  </bpmndi:BPMNDiagram>')
    xml_lines.append('</bpmn:definitions>')

    return '\n'.join(xml_lines)
