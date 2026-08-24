import base64
import zlib
import urllib.parse
import xml.etree.ElementTree as ET
import re
import uuid
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional

from app.models.process import (
    BusinessProcess,
    ProcessNode,
    ProcessEdge,
    ProcessEdgePoint,
    ProcessPassport,
    PixRegistrySchema,
    PixRegistryRecord,
    ProcessField,
    ProcessValidation,
    Geometry,
    NodeType,
    StepCategory
)
from app.services.conformance_engine import analyze_process_conformance

def clean_label(raw: Optional[str]) -> str:
    if not raw:
        return ''
    # Strip HTML tags and entities
    text = re.sub(r'<br\s*/?>', ' ', raw, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = (
        text.replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&amp;', '&')
            .replace('&quot;', '"')
            .replace('&#39;', "'")
    )
    return ' '.join(text.split()).strip()

def inflate_diagram(data: str) -> str:
    clean_data = re.sub(r'\s+', '', data.strip())
    if not clean_data:
        raise ValueError('Пустое содержимое диаграммы')

    if clean_data.startswith('<') or '<mxGraphModel' in clean_data:
        return clean_data

    binary = base64.b64decode(clean_data)
    # Draw.io uses raw deflate (-15)
    try:
        decompressed = zlib.decompress(binary, -zlib.MAX_WBITS)
    except Exception:
        try:
            decompressed = zlib.decompress(binary)
        except Exception:
            return binary.decode('utf-8', errors='ignore')

    decoded_str = decompressed.decode('utf-8', errors='ignore')
    try:
        return urllib.parse.unquote(decoded_str)
    except Exception:
        return decoded_str

def extract_graph_xml(content: str) -> Tuple[str, bool]:
    trimmed = content.strip()

    # 1. BPMN 2.0 XML
    if any(k in trimmed for k in ('<definitions', '<bpmn:definitions', '<bpmn2:definitions', '<bpmn:process')):
        return trimmed, True

    # 2. Raw mxGraphModel
    if '<mxGraphModel' in trimmed:
        root = ET.fromstring(trimmed)
        if root.tag == 'mxGraphModel':
            return trimmed, False
        model = root.find('.//mxGraphModel')
        if model is not None:
            return ET.tostring(model, encoding='unicode'), False

    # 3. mxfile container
    if '<mxfile' in trimmed or '<diagram' in trimmed:
        root = ET.fromstring(trimmed)
        diagram = root.find('.//diagram')
        if diagram is None:
            raise ValueError('В файле draw.io не найдено ни одной диаграммы (<diagram>)')

        model = diagram.find('.//mxGraphModel')
        if model is not None:
            return ET.tostring(model, encoding='unicode'), False

        root_el = diagram.find('.//root')
        if root_el is not None:
            return f"<mxGraphModel>{ET.tostring(root_el, encoding='unicode')}</mxGraphModel>", False

        inner_text = (diagram.text or '').strip()
        if inner_text:
            if '<mxGraphModel' in inner_text:
                return inner_text, False
            decompressed = inflate_diagram(inner_text)
            return decompressed, False

    raise ValueError('Файл не распознан как диаграмма draw.io или BPMN 2.0 XML')

def classify_vertex(style: str, label: str, has_incoming: bool, has_outgoing: bool, node_id: str) -> NodeType:
    s = style.lower()
    l = label.lower()
    i = node_id.lower()

    if 'swimlane' in s or 'pool;' in s or 'shape=pool' in s or 'horizontal=0' in s:
        return 'lane'

    if 'rhombus' in s or 'gateway' in s or 'shape=rhombus' in s or i.startswith('gw') or '?' in l:
        if 'outline=plus' in s or 'parallel' in s or '+' in l:
            return 'parallelGateway'
        if 'inclusive' in s or 'circle' in s:
            return 'inclusiveGateway'
        return 'exclusiveGateway'

    if 'ellipse' in s or 'bpmn.shape' in s or 'shape=ellipse' in s or any(k in i for k in ('start', 'end', 'reject')):
        if any(k in i for k in ('start', 'begin')) or any(k in l for k in ('старт', 'поступлен', 'начал')) or any(c in s for c in ('#10b981', '#22c55e', '#059669')):
            return 'startEvent'
        if any(k in i for k in ('end', 'reject', 'finish')) or any(k in l for k in ('заверш', 'конец', 'выдан', 'отказ')) or any(c in s for c in ('#ef4444', '#e11d48', '#be123c')):
            return 'endEvent'
        if not has_incoming and has_outgoing:
            return 'startEvent'
        if has_incoming and not has_outgoing:
            return 'endEvent'
        return 'startEvent'

    if any(k in s for k in ('robot', 'rpa', 'service', '#dcfce7', '#d5e8d4')) or any(k in l for k in ('rpa', 'робот', 'авто-')):
        return 'serviceTask'

    return 'userTask'

def classify_category(node_type: NodeType, name: str, style: str) -> StepCategory:
    lower = f"{name} {style}".lower()
    if node_type in ('startEvent', 'endEvent'):
        return 'notification'
    if node_type == 'serviceTask' or any(k in lower for k in ('rpa', 'робот', 'авто-', 'генерация')):
        return 'rpa_bot'
    if any(k in lower for k in ('согласован', 'комитет', 'утвержд', 'подпис', 'голос')):
        return 'approval'
    if any(k in lower for k in ('проверк', 'валидац', 'скоринг', 'скор', 'андеррайт', 'риск')):
        return 'validation'
    if any(k in lower for k in ('api', 'абс', 'сервис', 'цфт', 'didox')):
        return 'api_service'
    return 'manual'

def detect_system(name: str, lane_name: str) -> str:
    lower = f"{name} {lane_name}".lower()
    if 'rpa' in lower or 'робот' in lower:
        return 'PIX RPA'
    if any(k in lower for k in ('абс', 'счет', 'проводк', 'цфт', 'транш')):
        return 'АБС ЦФТ-Банк'
    if any(k in lower for k in ('гнк', 'налог', 'soliq')):
        return 'API Soliq (ГНК)'
    if 'катм' in lower or 'katm' in lower or 'бюро' in lower:
        return 'API KATM'
    if 'епигу' in lower or 'egrpo' in lower or 'егрпо' in lower:
        return 'ЕПИГУ / ЕГРПО'
    if 'didox' in lower or 'эдо' in lower or 'эцп' in lower:
        return 'Didox (ЭДО)'
    if 'swift' in lower or 'свифт' in lower:
        return 'SWIFT Alliance'
    return 'SQB CRM / Core'

def estimate_sla(category: StepCategory, node_type: NodeType) -> int:
    if node_type in ('startEvent', 'endEvent'):
        return 5
    if category == 'rpa_bot':
        return 3
    if category == 'api_service':
        return 2
    if category == 'validation':
        return 45
    if category == 'approval':
        return 180
    return 60

def parse_drawio_xml(content: str, filename: str) -> BusinessProcess:
    xml_str, is_bpmn = extract_graph_xml(content)

    if is_bpmn:
        return parse_bpmn_xml(xml_str, filename)

    root = ET.fromstring(xml_str)
    cells = root.findall('.//mxCell')
    cell_map: Dict[str, ET.Element] = {c.get('id', ''): c for c in cells if c.get('id')}

    # Collect text labels attached to parent nodes
    label_map: Dict[str, str] = {}
    label_ids: Set[str] = set()

    for c in cells:
        c_id = c.get('id', '')
        style = (c.get('style') or '').lower()
        raw_val = c.get('value', '')
        cleaned = clean_label(raw_val)

        is_label = (
            c_id.endswith('_label') or
            ('text;' in style and 'swimlane' not in style and ('strokecolor=none' in style or 'fillcolor=none' in style or not raw_val or len(cleaned) < 35))
        )
        if is_label and (c_id.endswith('_label') or c.get('vertex') == '1'):
            label_ids.add(c_id)
            base_id = re.sub(r'_label$', '', c_id)
            if base_id and cleaned:
                label_map[base_id] = cleaned

    swimlane_cells = [
        c for c in cells
        if c.get('vertex') == '1' and any(k in (c.get('style') or '').lower() for k in ('swimlane', 'shape=pool'))
    ]

    pool_ids: Set[str] = set()
    for sw in swimlane_cells:
        sw_id = sw.get('id', '')
        has_children = any(other.get('parent') == sw_id for other in swimlane_cells)
        if has_children or 'stacklayout' in (sw.get('style') or '').lower():
            pool_ids.add(sw_id)

    raw_vertices = [
        c for c in cells
        if c.get('vertex') == '1' and c.get('id') not in label_ids and c.get('id') not in pool_ids
    ]
    raw_edges = [c for c in cells if c.get('edge') == '1']

    incoming: Set[str] = {e.get('target', '') for e in raw_edges if e.get('target')}
    outgoing: Set[str] = {e.get('source', '') for e in raw_edges if e.get('source')}

    nodes: List[ProcessNode] = []
    step_index = 1

    for cell in raw_vertices:
        node_id = cell.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        style = cell.get('style') or ''
        raw_val = cell.get('value')
        cleaned = clean_label(raw_val) or label_map.get(node_id, '')

        parent_id = cell.get('parent')

        geo = cell.find('mxGeometry')
        x = float(geo.get('x', '100')) if geo is not None else 100.0
        y = float(geo.get('y', '100')) if geo is not None else 100.0
        width = float(geo.get('width', '120')) if geo is not None else 120.0
        height = float(geo.get('height', '60')) if geo is not None else 60.0

        # Parent offset calculation
        cur_p = parent_id
        while cur_p and cur_p not in ('0', '1'):
            p_cell = cell_map.get(cur_p)
            if not p_cell:
                break
            p_geo = p_cell.find('mxGeometry')
            if p_geo is not None:
                x += float(p_geo.get('x', '0'))
                y += float(p_geo.get('y', '0'))
            cur_p = p_cell.get('parent')

        node_type = classify_vertex(style, cleaned, node_id in incoming, node_id in outgoing, node_id)
        category = classify_category(node_type, cleaned, style)
        is_task = node_type in ('task', 'userTask', 'serviceTask')

        code = None
        code_match = re.search(r'\b(STEP[-_ ]?\d+|START|END|GW[-_ ]?\w+)\b', f"{raw_val or ''} {cleaned}", re.I)
        if code_match:
            code = code_match.group(1).upper().replace('_', '-')
        elif node_type == 'startEvent':
            code = 'START'
        elif node_type == 'endEvent':
            code = 'END'
        elif is_task:
            code = f"STEP-{step_index:02d}"
            step_index += 1

        clean_name = re.sub(r'^\[.*?\]\s*', '', cleaned, flags=re.I)
        clean_name = re.sub(r'^STEP[-_ ]?\d+[:\s-]*', '', clean_name, flags=re.I)
        clean_name = re.sub(r'^[0-9]+[.)]\s*', '', clean_name).strip()

        if not clean_name:
            if node_type == 'startEvent':
                clean_name = 'Старт'
            elif node_type == 'endEvent':
                clean_name = 'Завершение'
            elif 'Gateway' in node_type:
                clean_name = 'Условие'
            else:
                clean_name = f"Операция {code or node_id}"

        if node_type in ('startEvent', 'endEvent'):
            width, height = 48, 48
        elif 'Gateway' in node_type:
            width, height = 46, 46
        elif node_type == 'lane':
            width = max(int(width), 1400)
            height = max(int(height), 160)
        else:
            width = max(int(width), 160)
            height = max(int(height), 70)

        nodes.append(ProcessNode(
            id=node_id,
            name=clean_name,
            type=node_type,
            category=category,
            code=code,
            geometry=Geometry(x=int(x), y=int(y), width=int(width), height=int(height)),
            style=style,
            laneId=parent_id,
            slaMinutes=estimate_sla(category, node_type),
            costPerExecution=800 if category == 'rpa_bot' else 25000,
            automationPotential=95 if category == 'rpa_bot' else (60 if category == 'manual' else 35)
        ))

    lanes = [n for n in nodes if n.type == 'lane']
    lane_ids = {l.id for l in lanes}
    flow_nodes = [n for n in nodes if n.type != 'lane']

    for n in flow_nodes:
        if n.laneId and n.laneId not in lane_ids:
            n.laneId = None

    # Geometry-based lane assignment fallback
    for n in flow_nodes:
        if not n.laneId:
            hit = next((
                l for l in lanes
                if (n.geometry.x >= l.geometry.x - 50 and
                    n.geometry.x <= l.geometry.x + l.geometry.width + 50 and
                    n.geometry.y >= l.geometry.y and
                    n.geometry.y < l.geometry.y + l.geometry.height)
            ), None)
            if hit:
                n.laneId = hit.id
                n.laneName = hit.name
                n.role = hit.name

        if n.laneId:
            p_lane = next((l for l in lanes if l.id == n.laneId), None)
            if p_lane:
                n.laneName = p_lane.name
                n.role = n.role or p_lane.name
        n.system = detect_system(n.name, n.laneName or '')

    edges: List[ProcessEdge] = []
    for cell in raw_edges:
        pts: List[ProcessEdgePoint] = []
        for p in cell.findall('.//mxPoint'):
            pts.append(ProcessEdgePoint(
                x=int(float(p.get('x', '0'))),
                y=int(float(p.get('y', '0')))
            ))
        edges.append(ProcessEdge(
            id=cell.get('id') or f"edge_{uuid.uuid4().hex[:8]}",
            name=clean_label(cell.get('value')),
            sourceId=cell.get('source'),
            targetId=cell.get('target'),
            points=pts
        ))

    title = filename.replace('.drawio', '').replace('.xml', '')
    passport = ProcessPassport(
        code=f"PRC-SQB-{uuid.uuid4().int % 900 + 100}",
        name=title,
        version='1.0',
        status='draft',
        owner='Департамент бизнес-процессов АКБ «Узпромстройбанк»',
        department=lanes[0].name if lanes else 'Операционный блок',
        category='Банковские процессы',
        targetSlaHours=round(sum(n.slaMinutes or 0 for n in flow_nodes) / 60, 1) or 8.0,
        description=f"Импортирован из файла drawio: {filename}",
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d')
    )

    registry = PixRegistrySchema(
        id=f"reg-{uuid.uuid4().hex[:8]}",
        name=f"Реестр: {title}",
        code=f"REG_{passport.code.replace('-', '_')}",
        description=f"Операционный реестр по процессу {title}",
        fields=[
            ProcessField(id='f1', code='case_number', name='Номер заявки', type='string', required=True),
            ProcessField(id='f2', code='client_inn', name='ИНН Клиента', type='string', required=True),
            ProcessField(id='f3', code='client_title', name='Наименование компании', type='string', required=True),
            ProcessField(id='f4', code='status', name='Статус', type='select', required=True, options=['В работе', 'Одобрено', 'Отклонено'])
        ],
        records=[
            PixRegistryRecord(
                id='rec-1',
                caseId='SQB-2026-IMP01',
                createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
                status='in_progress',
                currentStepId=flow_nodes[1].id if len(flow_nodes) > 1 else (flow_nodes[0].id if flow_nodes else 'step-1'),
                currentStepName=flow_nodes[1].name if len(flow_nodes) > 1 else 'Первичный шаг',
                assignedTo=flow_nodes[1].role or 'Сотрудник банка' if len(flow_nodes) > 1 else 'Сотрудник банка',
                elapsedMinutes=25,
                data={
                    'case_number': 'SQB-2026-IMP01',
                    'client_inn': '309819284',
                    'client_title': 'OOO "ORIENT TRADE"',
                    'status': 'В работе'
                }
            )
        ]
    )

    validations: List[ProcessValidation] = []
    starts = [n for n in flow_nodes if n.type == 'startEvent']
    ends = [n for n in flow_nodes if n.type == 'endEvent']
    if not starts:
        validations.append(ProcessValidation(level='error', message='Отсутствует стартовое событие процесса'))
    if not ends:
        validations.append(ProcessValidation(level='warning', message='Отсутствует событие успешного завершения'))

    metrics = analyze_process_conformance(flow_nodes, passport, len(registry.records))

    return BusinessProcess(
        id=f"proc_{uuid.uuid4().hex[:8]}",
        name=title,
        fileName=filename,
        passport=passport,
        nodes=flow_nodes,
        edges=edges,
        lanes=lanes,
        validation=validations,
        registry=registry,
        miningMetrics=metrics
    )

def parse_bpmn_xml(xml_str: str, filename: str) -> BusinessProcess:
    root = ET.fromstring(xml_str)
    # Handle namespaces
    namespaces = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL', 'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI', 'dc': 'http://www.omg.org/spec/DD/20100524/DC'}
    
    title = filename.replace('.bpmn', '').replace('.xml', '')
    passport = ProcessPassport(
        code=f"PRC-SQB-{uuid.uuid4().int % 900 + 100}",
        name=title,
        version='1.0',
        status='draft',
        owner='Департамент бизнес-процессов АКБ «Узпромстройбанк»',
        department='Операционный блок',
        category='Банковские процессы',
        targetSlaHours=24.0,
        description=f"Импортирован из файла BPMN: {filename}",
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d')
    )

    nodes: List[ProcessNode] = []
    edges: List[ProcessEdge] = []
    lanes: List[ProcessNode] = []

    # Parse flow elements
    step_index = 1
    for el in root.iter():
        tag = el.tag.split('}')[-1].lower() if '}' in el.tag else el.tag.lower()
        if tag in ('usertask', 'servicetask', 'task', 'startevent', 'endevent', 'exclusivegateway', 'parallelgateway'):
            node_type: NodeType = 'userTask'
            if tag == 'startevent': node_type = 'startEvent'
            elif tag == 'endevent': node_type = 'endEvent'
            elif tag == 'servicetask': node_type = 'serviceTask'
            elif tag == 'exclusivegateway': node_type = 'exclusiveGateway'
            elif tag == 'parallelgateway': node_type = 'parallelGateway'

            name = el.get('name') or (f"Шаг {step_index}" if 'task' in tag else tag)
            code = f"STEP-{step_index:02d}" if 'task' in tag else None
            if 'task' in tag: step_index += 1

            category = classify_category(node_type, name, '')
            nodes.append(ProcessNode(
                id=el.get('id') or f"node_{uuid.uuid4().hex[:8]}",
                name=name,
                type=node_type,
                category=category,
                code=code,
                geometry=Geometry(x=100 + step_index * 150, y=100, width=170 if 'task' in tag else 48, height=70 if 'task' in tag else 48),
                slaMinutes=estimate_sla(category, node_type),
                system=detect_system(name, '')
            ))
        elif tag == 'sequenceflow':
            edges.append(ProcessEdge(
                id=el.get('id') or f"edge_{uuid.uuid4().hex[:8]}",
                name=el.get('name') or '',
                sourceId=el.get('sourceRef'),
                targetId=el.get('targetRef')
            ))

    registry = PixRegistrySchema(
        id=f"reg-{uuid.uuid4().hex[:8]}",
        name=f"Реестр: {title}",
        code=f"REG_{passport.code.replace('-', '_')}",
        description=f"Операционный реестр по процессу {title}",
        fields=[],
        records=[]
    )

    metrics = analyze_process_conformance(nodes, passport, 10)

    return BusinessProcess(
        id=f"proc_{uuid.uuid4().hex[:8]}",
        name=title,
        fileName=filename,
        passport=passport,
        nodes=nodes,
        edges=edges,
        lanes=lanes,
        validation=[],
        registry=registry,
        miningMetrics=metrics
    )
