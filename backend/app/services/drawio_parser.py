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
    text = raw.replace('&nbsp;', ' ')
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
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

    if any(k in trimmed for k in ('<definitions', '<bpmn:definitions', '<bpmn2:definitions', '<bpmn:process')):
        return trimmed, True

    if '<mxGraphModel' in trimmed:
        root = ET.fromstring(trimmed)
        if root.tag == 'mxGraphModel':
            return trimmed, False
        model = root.find('.//mxGraphModel')
        if model is not None:
            return ET.tostring(model, encoding='unicode'), False

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

SYSTEM_TAGS = {
    'iabs', 'iabs / crm', 'iabs / eha', 'eha', 'edo', 'zoom', 'crobs', 'excell rmr',
    'dragle bi', 'nibbd', 'soliq', 'katm', 'didox', 'myorg.uz', 'ihamkor', 'orginfo',
    'registr.stat.uz', 'jira', 'e-baholash.uz', 'garov.uz', 'davreestr.uz', 'korporativ pochta',
    'tsoyat', 'intranet', 'emv service', 'sqb crm', 'internet saytlari', 'crobs, internet saytlari',
    'internet saytlari, tsoyat, crobs', 'internet saytlari va iabs', 'internet saytlari tsoyat',
    'iabs, regstr.uz', 'iabs, garov.uz, davreestr.uz', 'iabs, garov.uz, davrestr.uz', 'iabs, crobs'
}

ARTIFACT_TAGS = {
    'dalolatnoma', 'chek-list', "yig'ma jild", 'yig‘majild', 'yig‘ma jild', 'asoslantiruvchi xat',
    'fotosuratlar', 'fotosuratlar va hujjatlar', "hujjatlar ro'yxati", 'hujjatlar',
    'xulosa', 'loyiha hujjatlari', "ko'chirma", 'kuzatuv kengash bayonnomasi',
    'yirik bitimlar bayonnomasi', 'kredit/sug‘urta/kafillik', 'kredit/sug\'urta/kafillik',
    'qo‘shimcha kelishuv', "qo'shimcha kelishuv", 'shartnoma', 'baholash dalolatnomasi',
    'garov xulosasi', "yig'ilish bayonnomasi", 'yuriskonsult xulosasi', 'qaror loyihasi',
    'asoslantirilgan xat', 'moliyaviy hisobotlar', 'skaner', 'kredit/garov/kafillik shartnomasi',
    'kredit/kafillik/sug\'urta shartnomasi', 'hukumat qarori', 'tegishli qaror', "ma'lumotnoma",
    'mijoz murojaati, ta`sischilar qarori', 'ta`sischilar qarori'
}

CONDITION_TAGS = {
    "ha", "yo'q", "yo`q", "yo’q", "yo'q ", "ha ", "да", "нет", "yes", "no",
    "to'liq", "to'liq emas", "to`liq", "to`liq emas",
    "mos keldi", "mos kelmaydi", "mos kelmadi", "to'liq mos keladi",
    "manba aniqlandi", "qabul qilindi", "rad etildi", "rad javob berildi",
    "asoslantirilgan rad javob berildi", "mulkiy", "nomulkiy", "o'rganildi", "bajarildi",
    "nazorat uchun", "ijobiy", "salbiy", "kamchilik mavjudmi", "kamchiliklar mavjudmi",
    "kamchilik mavjudmi?", "kamchiliklar mavjudmi?", "to'g'ri rasmiylashtirilganmi?",
    "tog'ri rasmiylashtirilganmi?", "barcha ma'lumotlar to'g'ri kiritilganmi?",
    "barcha hujjatlar mavjudmi", "hujjatlar to'liqmi?", "hujjatlar to'plami to'liqmi?",
    "resurs mablag'lari mavjudmi?", "muzokara ijobiymi?", "muqobil resurs aniqlandimi ?",
    "vakolatli organ qarori ijobiymi?", "qo'mita qarori ijobiymi?", "qaror qabul qilish qo'mita vakolatidami?",
    "kredit maqsadli ishlatilganmi?", "mijoz talabi kredit mahsuloti shartlariga muvofiqmi?",
    "garov obyekti qiymati mustaqil baholovchining hisoboti bilan mosligini o'rganish"
}

def is_non_task_label(val: str) -> bool:
    v = val.lower().strip()
    if not v:
        return True
    if v in CONDITION_TAGS or v in SYSTEM_TAGS or v in ARTIFACT_TAGS:
        return True
    if v.startswith('kutish vaqti') or v.startswith("o'rtacha kutish vaqti"):
        return True
    if any(k in v for k in ('(as is)', '(to be)', '(as-is)', '(to-be)')):
        return True
    return False

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
        # Explicit Reject / Declined End Event (Red)
        if any(k in l for k in ('rad etildi', 'rad javob', 'otkaz', 'отказ', 'bekor')) or 'reject' in i or any(c in s for c in ('#ef4444', '#e11d48', '#be123c')):
            return 'endEvent'

        # Explicit Start Event (Green)
        if any(k in i for k in ('start', 'begin')) or any(k in l for k in ('старт', 'поступлен', 'tashrif', 'boshlanish')) or any(c in s for c in ('#10b981', '#22c55e', '#059669')):
            return 'startEvent'

        # Explicit Success End Event (Green double border)
        if any(k in i for k in ('end', 'finish')) or any(k in l for k in ('заверш', 'конец', 'выдан', 'ochildi', 'tugashi', 'bajarildi')) or 'outline=double' in s or 'outline=end' in s:
            return 'endEvent'

        if not has_incoming and has_outgoing:
            return 'startEvent'
        if has_incoming and not has_outgoing:
            return 'endEvent'
        return 'startEvent'

    if any(k in s for k in ('robot', 'rpa', 'service', '#dcfce7', '#d5e8d4')) or any(k in l for k in ('rpa', 'робот', 'авто-', 'avtomat', 'sms')):
        return 'serviceTask'

    return 'userTask'

def classify_category(node_type: NodeType, name: str, style: str) -> StepCategory:
    lower = f"{name} {style}".lower()
    if node_type in ('startEvent', 'endEvent'):
        return 'notification'
    if node_type == 'serviceTask' or any(k in lower for k in ('rpa', 'робот', 'авто-', 'avtomat', 'генерация', 'sms')):
        return 'rpa_bot'
    if any(k in lower for k in ('согласован', 'комитет', 'утвержд', 'подпис', 'голос', 'imzo', 'vizo', 'tasdiq', 'himoya')):
        return 'approval'
    if any(k in lower for k in ('проверк', 'валидац', 'скоринг', 'скор', 'андеррайт', 'риск', 'tekshirish', 'solishtirish', 'identifikatsiya', 'o\'rganish')):
        return 'validation'
    if any(k in lower for k in ('api', 'абс', 'сервис', 'цфт', 'didox', 'iabs', 'eha', 'edo', 'nibbd')):
        return 'api_service'
    return 'manual'

def detect_system(name: str, lane_name: str) -> str:
    lower = f"{name} {lane_name}".lower()
    if 'rpa' in lower or 'робот' in lower or 'avtomat sms' in lower:
        return 'PIX RPA'
    if 'nibbd' in lower:
        return 'NIBBD / ЦБ РУз'
    if 'eha' in lower or 'еха' in lower:
        return 'EHA Dasturi'
    if 'edo' in lower or 'эдо' in lower or 'didox' in lower or 'эцп' in lower:
        return 'EDO / Didox (ЭЦП)'
    if 'aml' in lower or 'komplayens' in lower:
        return 'AML/CFT Moduli'
    if 'iabs' in lower or 'абс' in lower or 'счет' in lower or 'проводк' in lower or 'цфт' in lower or 'клиенты и счета' in lower or 'комиссия' in lower:
        return 'iABS (ЦФТ-Банк)'
    if any(k in lower for k in ('гнк', 'налог', 'soliq')):
        return 'API Soliq (ГНК)'
    if 'катм' in lower or 'katm' in lower or 'бюро' in lower:
        return 'API KATM'
    if 'епигу' in lower or 'egrpo' in lower or 'егрпо' in lower:
        return 'ЕПИГУ / ЕГРПО'
    if 'dragle' in lower:
        return 'Dragle BI'
    if 'crobs' in lower:
        return 'CROBS Risk Engine'
    if 'zoom' in lower:
        return 'Zoom Video Conf'
    if 'swift' in lower or 'свифт' in lower:
        return 'SWIFT Alliance'
    return 'SQB CRM / Core'

def extract_sla_minutes(raw_text: str, category: StepCategory, node_type: NodeType) -> int:
    if node_type in ('startEvent', 'endEvent'):
        return 5

    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:min|daq|минут|мин|m\b)', raw_text, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        return max(1, int(round(val)))

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

    root = ET.fromstring(xml_str)
    cells = root.findall('.//mxCell')
    cell_map: Dict[str, ET.Element] = {c.get('id', ''): c for c in cells if c.get('id')}

    raw_edges = [c for c in cells if c.get('edge') == '1']
    edge_id_set = {e.get('id', '') for e in raw_edges if e.get('id')}

    incoming: Set[str] = {e.get('target', '') for e in raw_edges if e.get('target')}
    outgoing: Set[str] = {e.get('source', '') for e in raw_edges if e.get('source')}

    label_map: Dict[str, str] = {}
    ignore_cell_ids: Set[str] = set()

    for c in cells:
        c_id = c.get('id', '')
        parent_id = c.get('parent', '')
        style = (c.get('style') or '').lower()
        raw_val = c.get('value', '')
        cleaned = clean_label(raw_val)
        geo = c.find('mxGeometry')
        is_relative = geo.get('relative') == '1' if geo is not None else False
        is_connectable0 = c.get('connectable') == '0'

        # 1. Child of an edge
        if parent_id in edge_id_set:
            ignore_cell_ids.add(c_id)
            if cleaned:
                label_map[parent_id] = cleaned
            continue

        # 2. Explicit edgeLabel or relative=1
        if 'edgelabel' in style or is_connectable0 or is_relative:
            ignore_cell_ids.add(c_id)
            if cleaned and parent_id:
                label_map[parent_id] = cleaned
            continue

        # 3. Text label overlay
        if c_id.endswith('_label') or ('text;' in style and 'swimlane' not in style and ('strokecolor=none' in style or 'fillcolor=none' in style or is_non_task_label(cleaned) or len(cleaned) < 2)):
            ignore_cell_ids.add(c_id)
            base_id = re.sub(r'_label$', '', c_id)
            if base_id and cleaned:
                label_map[base_id] = cleaned
            continue

        # 4. Diagram title banner
        if 'text;' in style and is_non_task_label(cleaned):
            ignore_cell_ids.add(c_id)
            continue

        # 5. Non-task system tags, artifacts, conditions without connections
        if is_non_task_label(cleaned) and c_id not in incoming and c_id not in outgoing:
            ignore_cell_ids.add(c_id)
            continue

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
        if c.get('vertex') == '1' and c.get('id') not in ignore_cell_ids and c.get('id') not in pool_ids
    ]

    nodes: List[ProcessNode] = []
    step_index = 1

    for cell in raw_vertices:
        node_id = cell.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        style = cell.get('style') or ''
        raw_val = cell.get('value')
        raw_cleaned = clean_label(raw_val) or label_map.get(node_id, '')

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

        node_type = classify_vertex(style, raw_cleaned, node_id in incoming, node_id in outgoing, node_id)
        category = classify_category(node_type, raw_cleaned, style)
        is_task = node_type in ('task', 'userTask', 'serviceTask')

        code = None
        code_match = re.search(r'\b(STEP[-_ ]?\d+|START|END|GW[-_ ]?\w+)\b', f"{raw_val or ''} {raw_cleaned}", re.I)
        num_prefix = re.match(r'^(\d+)[.)]\s*', raw_cleaned)

        if code_match:
            code = code_match.group(1).upper().replace('_', '-')
        elif num_prefix and is_task:
            code = f"STEP-{int(num_prefix.group(1)):02d}"
        elif node_type == 'startEvent':
            code = 'START'
        elif node_type == 'endEvent':
            code = 'END'
        elif is_task:
            code = f"STEP-{step_index:02d}"
            step_index += 1

        sla_min = extract_sla_minutes(f"{raw_val or ''} {raw_cleaned}", category, node_type)

        clean_name = raw_cleaned
        clean_name = re.sub(r'^\[.*?\]\s*', '', clean_name, flags=re.I)
        clean_name = re.sub(r'^STEP[-_ ]?\d+[:\s-]*', '', clean_name, flags=re.I)
        clean_name = re.sub(r'^[0-9]+[.)]\s*', '', clean_name)
        clean_name = re.sub(r'\b\d+(?:\.\d+)?\s*(?:min|daq|минут|мин)\b.*$', '', clean_name, flags=re.I).strip()

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

        fot_cost = (sla_min * 1932) if category != 'rpa_bot' else 800

        nodes.append(ProcessNode(
            id=node_id,
            name=clean_name,
            type=node_type,
            category=category,
            code=code,
            geometry=Geometry(x=int(x), y=int(y), width=int(width), height=int(height)),
            style=style,
            laneId=parent_id,
            slaMinutes=sla_min,
            costPerExecution=fot_cost,
            automationPotential=95 if category == 'rpa_bot' else (65 if category == 'manual' else 40)
        ))

    lanes = [n for n in nodes if n.type == 'lane']
    lane_ids = {l.id for l in lanes}
    flow_nodes = [n for n in nodes if n.type != 'lane']

    for n in flow_nodes:
        if n.laneId and n.laneId not in lane_ids:
            n.laneId = None

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

    # Draw.io Grid Snap & Layout Spacing (10px grid unit)
    GRID_SIZE = 10
    def snap(v: float) -> int:
        return int(round(v / GRID_SIZE) * GRID_SIZE)

    # 1. Snap lanes
    for lane in lanes:
        lane.geometry.x = snap(lane.geometry.x)
        lane.geometry.y = snap(lane.geometry.y)
        lane.geometry.width = max(snap(lane.geometry.width), 1600)
        lane.geometry.height = max(snap(lane.geometry.height), 180)

    # 2. Snap and resolve node collisions within each lane
    for lane in lanes:
        lane_nodes = [n for n in flow_nodes if n.laneId == lane.id]
        if not lane_nodes:
            continue

        lane_center_y = lane.geometry.y + snap((lane.geometry.height - 70) / 2)
        lane_nodes.sort(key=lambda n: n.geometry.x)

        for idx, node in enumerate(lane_nodes):
            node.geometry.width = snap(node.geometry.width)
            node.geometry.height = snap(node.geometry.height)

            is_reject_branch = (
                'reject' in node.id.lower() or
                'отказ' in node.name.lower() or
                'rad etildi' in node.name.lower()
            )

            if is_reject_branch:
                prev_gw = next((
                    other for other in lane_nodes
                    if 'Gateway' in other.type and abs(other.geometry.x - node.geometry.x) < 120
                ), None)
                if prev_gw:
                    node.geometry.x = prev_gw.geometry.x
                    node.geometry.y = prev_gw.geometry.y + prev_gw.geometry.height + 30
                    continue

            if idx > 0:
                prev = lane_nodes[idx - 1]
                min_x = prev.geometry.x + prev.geometry.width + 40
                if node.geometry.x < min_x:
                    node.geometry.x = snap(min_x)
                else:
                    node.geometry.x = snap(node.geometry.x)
            else:
                node.geometry.x = snap(max(lane.geometry.x + 40, node.geometry.x))

            if node.type in ('startEvent', 'endEvent'):
                node.geometry.y = lane.geometry.y + snap((lane.geometry.height - 48) / 2)
            elif 'Gateway' in node.type:
                node.geometry.y = lane.geometry.y + snap((lane.geometry.height - 46) / 2)
            else:
                node.geometry.y = lane_center_y

    valid_node_ids = {n.id for n in flow_nodes}

    edges: List[ProcessEdge] = []
    for cell in raw_edges:
        s_id = cell.get('source')
        t_id = cell.get('target')
        if not s_id or not t_id or (s_id not in valid_node_ids and t_id not in valid_node_ids):
            continue

        edge_id = cell.get('id') or f"edge_{uuid.uuid4().hex[:8]}"
        raw_val = cell.get('value')
        edge_name = clean_label(raw_val) or label_map.get(edge_id, '')

        pts: List[ProcessEdgePoint] = []
        for p in cell.findall('.//mxPoint'):
            pts.append(ProcessEdgePoint(
                x=int(float(p.get('x', '0'))),
                y=int(float(p.get('y', '0')))
            ))
        edges.append(ProcessEdge(
            id=edge_id,
            name=edge_name,
            sourceId=s_id,
            targetId=t_id,
            points=pts
        ))

    title = filename.replace('.drawio', '').replace('.xml', '')
    total_hours = round(sum(n.slaMinutes or 0 for n in flow_nodes) / 60, 1) or 8.0

    passport = ProcessPassport(
        code=f"PRC-SQB-{uuid.uuid4().int % 900 + 100}",
        name=title,
        version='1.0',
        status='draft',
        owner='Департамент бизнес-процессов АКБ «Узпромстройбанк»',
        department=lanes[0].name if lanes else 'Операционный блок',
        category='Банковские процессы (Методика SQB)',
        targetSlaHours=total_hours,
        description=f"Импортирован из файла draw.io: {filename}. Сформирован регламент по Методологии АКБ «Узпромстройбанк» (1-ILOVA / 4-ILOVA).",
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d')
    )

    registry = PixRegistrySchema(
        id=f"reg-{uuid.uuid4().hex[:8]}",
        name=f"Реестр: {title}",
        code=f"REG_{passport.code.replace('-', '_')}",
        description=f"Операционный реестр по процессу {title} (PIX BPM)",
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
