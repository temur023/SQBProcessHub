from app.models.process import BusinessProcess

def generate_bpmn_xml(process: BusinessProcess) -> str:
    """
    Generates standard OMG BPMN 2.0 XML with BPMNDiagram elements
    for direct import into Infomaximum Processet.
    """
    proc_id = f"Process_{process.passport.code.replace('-', '_')}"
    diag_id = f"Diagram_{proc_id}"
    plane_id = f"Plane_{proc_id}"

    flow_nodes = [n for n in process.nodes if n.type != 'lane']
    lanes = process.lanes

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        '  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
        '  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
        '  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
        '  xmlns:sqb="http://sqb.uz/schema/bpmn"',
        f'  id="Definitions_{process.id}"',
        '  targetNamespace="http://bpmn.io/schema/bpmn">',
        '',
        f'  <!-- Process Definition: {process.name} -->',
        f'  <bpmn:process id="{proc_id}" name="{process.name}" isExecutable="true">',
    ]

    # Lanes
    if lanes:
        xml_lines.append('    <bpmn:laneSet id="LaneSet_1">')
        for lane in lanes:
            xml_lines.append(f'      <bpmn:lane id="{lane.id}" name="{lane.name}">')
            lane_child_nodes = [n for n in flow_nodes if n.laneId == lane.id]
            for child in lane_child_nodes:
                xml_lines.append(f'        <bpmn:flowNodeRef>{child.id}</bpmn:flowNodeRef>')
            xml_lines.append('      </bpmn:lane>')
        xml_lines.append('    </bpmn:laneSet>')

    # Nodes
    for node in flow_nodes:
        name_attr = f'name="{node.name}"'
        ext_props = (
            f' sqb:role="{node.role or ""}"'
            f' sqb:system="{node.system or ""}"'
            f' sqb:slaMinutes="{node.slaMinutes or 0}"'
            f' sqb:automationPotential="{node.automationPotential or 0}"'
        )

        if node.type == 'startEvent':
            xml_lines.append(f'    <bpmn:startEvent id="{node.id}" {name_attr}{ext_props} />')
        elif node.type == 'endEvent':
            xml_lines.append(f'    <bpmn:endEvent id="{node.id}" {name_attr}{ext_props} />')
        elif node.type == 'serviceTask' or node.category == 'rpa_bot':
            xml_lines.append(f'    <bpmn:serviceTask id="{node.id}" {name_attr}{ext_props} implementation="PIX_RPA" />')
        elif node.type == 'exclusiveGateway':
            xml_lines.append(f'    <bpmn:exclusiveGateway id="{node.id}" {name_attr} />')
        elif node.type == 'parallelGateway':
            xml_lines.append(f'    <bpmn:parallelGateway id="{node.id}" {name_attr} />')
        elif node.type == 'inclusiveGateway':
            xml_lines.append(f'    <bpmn:inclusiveGateway id="{node.id}" {name_attr} />')
        else:
            xml_lines.append(f'    <bpmn:userTask id="{node.id}" {name_attr}{ext_props} />')

    # Sequence Flows
    for edge in process.edges:
        name_attr = f'name="{edge.name}"' if edge.name else ''
        src_attr = f'sourceRef="{edge.sourceId}"' if edge.sourceId else ''
        tgt_attr = f'targetRef="{edge.targetId}"' if edge.targetId else ''
        xml_lines.append(f'    <bpmn:sequenceFlow id="{edge.id}" {name_attr} {src_attr} {tgt_attr} />')

    xml_lines.append('  </bpmn:process>')
    xml_lines.append('')
    xml_lines.append('  <!-- BPMN 2.0 Diagram Layout for Processet -->')
    xml_lines.append(f'  <bpmndi:BPMNDiagram id="{diag_id}">')
    xml_lines.append(f'    <bpmndi:BPMNPlane id="{plane_id}" bpmnElement="{proc_id}">')

    # DI Shapes for Lanes
    for lane in lanes:
        xml_lines.append(f'      <bpmndi:BPMNShape id="{lane.id}_di" bpmnElement="{lane.id}" isHorizontal="true">')
        xml_lines.append(f'        <dc:Bounds x="{lane.geometry.x}" y="{lane.geometry.y}" width="{lane.geometry.width}" height="{lane.geometry.height}" />')
        xml_lines.append('      </bpmndi:BPMNShape>')

    # DI Shapes for Nodes
    for node in flow_nodes:
        xml_lines.append(f'      <bpmndi:BPMNShape id="{node.id}_di" bpmnElement="{node.id}">')
        xml_lines.append(f'        <dc:Bounds x="{node.geometry.x}" y="{node.geometry.y}" width="{node.geometry.width}" height="{node.geometry.height}" />')
        xml_lines.append('      </bpmndi:BPMNShape>')

    # DI Edges for Transitions
    for edge in process.edges:
        xml_lines.append(f'      <bpmndi:BPMNEdge id="{edge.id}_di" bpmnElement="{edge.id}">')
        src = next((n for n in flow_nodes if n.id == edge.sourceId), None)
        tgt = next((n for n in flow_nodes if n.id == edge.targetId), None)
        if src and tgt:
            x1 = src.geometry.x + src.geometry.width
            y1 = src.geometry.y + src.geometry.height // 2
            x2 = tgt.geometry.x
            y2 = tgt.geometry.y + tgt.geometry.height // 2
            xml_lines.append(f'        <di:waypoint x="{x1}" y="{y1}" />')
            xml_lines.append(f'        <di:waypoint x="{x2}" y="{y2}" />')
        xml_lines.append('      </bpmndi:BPMNEdge>')

    xml_lines.append('    </bpmndi:BPMNPlane>')
    xml_lines.append('  </bpmndi:BPMNDiagram>')
    xml_lines.append('</bpmn:definitions>')

    return '\n'.join(xml_lines)
