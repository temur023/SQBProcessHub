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
        diagrams = root.findall('.//diagram')
        if not diagrams:
            raise ValueError('В файле draw.io не найдено ни одной диаграммы (<diagram>)')

        # Собираем все диаграммы — если их несколько, объединяем с вертикальным смещением
        models = []
        for diag in diagrams:
            m = diag.find('.//mxGraphModel')
            if m is not None:
                models.append(ET.tostring(m, encoding='unicode'))
                continue
            r = diag.find('.//root')
            if r is not None:
                models.append(f"<mxGraphModel>{ET.tostring(r, encoding='unicode')}</mxGraphModel>")
                continue
            inner = (diag.text or '').strip()
            if inner:
                if '<mxGraphModel' in inner:
                    models.append(inner)
                else:
                    try:
                        models.append(inflate_diagram(inner))
                    except Exception:
                        continue
        if not models:
            raise ValueError('Не удалось извлечь ни одной диаграммы из mxfile')
        if len(models) == 1:
            return models[0], False
        # Объединяем несколько диаграмм: собираем все mxCell в один root
        # Для избежания наложения добавляем вертикальный offset по высоте каждой диаграммы
        try:
            combined_root = ET.Element('root')
            # базовые ячейки 0 и 1
            ET.SubElement(combined_root, 'mxCell', {'id': '0'})
            ET.SubElement(combined_root, 'mxCell', {'id': '1', 'parent': '0'})
            y_offset = 0
            for mx in models:
                try:
                    m_root = ET.fromstring(mx)
                    r = m_root.find('.//root')
                    if r is None:
                        continue
                    # вычисляем высоту этой диаграммы для offset
                    max_y = 0
                    for c in r.findall('mxCell'):
                        geo = c.find('mxGeometry')
                        if geo is not None:
                            try:
                                y = float(geo.get('y', '0') or 0)
                                h = float(geo.get('height', '0') or 0)
                                max_y = max(max_y, y + h)
                            except ValueError:
                                pass
                    for c in r.findall('mxCell'):
                        cid = c.get('id')
                        if cid in ('0', '1'):
                            continue
                        # клонируем ячейку
                        new_c = ET.SubElement(combined_root, 'mxCell', dict(c.attrib))
                        # копируем геометрию с y_offset для pool-совместимости
                        geo = c.find('mxGeometry')
                        if geo is not None:
                            ng = ET.SubElement(new_c, 'mxGeometry', dict(geo.attrib))
                            if y_offset != 0 and geo.get('relative') != '1':
                                try:
                                    orig_y = float(geo.get('y', '0') or 0)
                                    ng.set('y', str(orig_y + y_offset))
                                except ValueError:
                                    pass
                            for child in geo:
                                ET.SubElement(ng, child.tag, dict(child.attrib))
                        for child in c:
                            if child.tag != 'mxGeometry':
                                ET.SubElement(new_c, child.tag, dict(child.attrib))
                    y_offset += max_y + 100
                except Exception:
                    continue
            return f"<mxGraphModel>{ET.tostring(combined_root, encoding='unicode')}</mxGraphModel>", False
        except Exception:
            # fallback — первая диаграмма
            return models[0], False

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
    "ha", "yo'q", "yo`q", "yo’q", "да", "нет", "yes", "no",
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

def is_decoration_style(style: str) -> bool:
    s = (style or '').lower()
    return (
        'timer' in s
        or 'clock' in s
        or 'mxgraph.bpmn.icon' in s
        or 'shape=mxgraph.bpmn.timer' in s
        or 'eventicon' in s
        or 'symbol=timer' in s
        or 'symbol=clock' in s
        or 'shape=datastore' in s
        or 'shape=mxgraph.bpmn.datastore' in s
        or 'kind=datastore' in s
        or 'shape=mxgraph.signs' in s  # транспортные иконки car/train в файле кредита
        or 'shape=mxgraph.bpmn.dataobject' in s
        or 'shape=note' in s
        or 'shape=mxgraph.bpmn.annotation' in s
    )


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

def _id_has_token(node_id: str, token: str) -> bool:
    i = (node_id or '').lower()
    token = token.lower()
    if (
        i == token
        or i.startswith(token + '_')
        or i.startswith(token + '-')
        or i.endswith('_' + token)
        or i.endswith('-' + token)
    ):
        return True
    return token in re.split(r'[-_]', i)


def _style_map(style: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (style or '').split(';'):
        if not part:
            continue
        if '=' in part:
            key, val = part.split('=', 1)
            out[key.strip().lower()] = val.strip()
        else:
            out[part.strip().lower()] = '1'
    return out


def _style_float(style_map: Dict[str, str], key: str) -> Optional[float]:
    raw = style_map.get(key)
    if raw is None or raw == '':
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parent_origin(cell_id: Optional[str], cell_map: Dict[str, ET.Element], cache: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
    """Absolute top-left of a cell's parent chain.

    mxGraph semantics: children of a swimlane are positioned relative to the
    swimlane's FULL origin (including the title/startSize area) — verified by
    pool/lane geometry fitting exactly (lane y+h == pool h). Therefore we add
    only geo.x/geo.y of ancestors, no startSize shift.
    """
    if not cell_id or cell_id in ('0', '1'):
        return 0.0, 0.0
    if cell_id in cache:
        return cache[cell_id]
    cell = cell_map.get(cell_id)
    if cell is None:
        cache[cell_id] = (0.0, 0.0)
        return 0.0, 0.0
    px, py = _parent_origin(cell.get('parent'), cell_map, cache)
    geo = cell.find('mxGeometry')
    # Relative geometries (edge labels, edge frames) do not shift the coordinate space.
    if geo is not None and geo.get('relative') != '1':
        px += float(geo.get('x', '0') or 0)
        py += float(geo.get('y', '0') or 0)
    cache[cell_id] = (px, py)
    return px, py


def _local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.rsplit('}', 1)[-1]
    if ':' in tag:
        return tag.split(':')[-1]
    return tag


def classify_vertex(style: str, label: str, has_incoming: bool, has_outgoing: bool, node_id: str) -> NodeType:
    s = style.lower()
    l = label.lower()
    i = node_id.lower()
    smap = _style_map(style)
    shape = smap.get('shape', '').lower()

    if 'swimlane' in s or 'pool;' in s or 'shape=pool' in s:
        return 'lane'

    if 'mxgraph.bpmn.gateway' in s or shape.endswith('gateway2') or 'gwtype' in smap:
        gw = (smap.get('gwtype') or smap.get('symbol') or '').lower()
        if gw in ('parallel', 'and', 'complex') or 'outline=plus' in s or 'parallel' in s:
            return 'parallelGateway'
        if gw in ('inclusive', 'or') or 'inclusive' in s or 'outline=circle' in s:
            return 'inclusiveGateway'
        return 'exclusiveGateway'

    if 'mxgraph.bpmn.event' in s or shape.endswith('.event'):
        outline = (smap.get('outline') or '').lower()
        if outline in ('end', 'terminate') or 'outline=end' in s or 'outline=double' in s:
            return 'endEvent'
        if not has_incoming and has_outgoing:
            return 'startEvent'
        if has_incoming and not has_outgoing:
            return 'endEvent'
        return 'startEvent'

    if 'mxgraph.bpmn.task' in s:
        marker = (smap.get('taskmarker') or smap.get('symbol') or '').lower()
        if marker in ('service', 'script', 'send', 'receive', 'businessrule') or any(
            k in l for k in ('rpa', 'робот', 'авто-', 'avtomat', 'sms')
        ):
            return 'serviceTask'
        return 'userTask'

    is_gateway_shape = (
        'rhombus' in s
        or 'shape=rhombus' in s
        or 'gateway' in s
        or i.startswith('gw')
        or i.startswith('gateway')
        or '-gw-' in i
        or '_gw_' in i
    )
    if is_gateway_shape:
        if 'outline=plus' in s or 'parallel' in s or l.strip() in ('+', 'and', 'и'):
            return 'parallelGateway'
        if 'inclusive' in s or 'outline=circle' in s:
            return 'inclusiveGateway'
        return 'exclusiveGateway'

    is_event_shape = (
        'ellipse' in s
        or 'bpmn.shape' in s
        or 'shape=ellipse' in s
        or _id_has_token(node_id, 'start')
        or _id_has_token(node_id, 'end')
        or _id_has_token(node_id, 'reject')
    )
    if is_event_shape:
        if (
            any(k in l for k in ('rad etildi', 'rad javob', 'otkaz', 'отказ', 'bekor', 'отклон'))
            or _id_has_token(node_id, 'reject')
            or any(c in s for c in ('#ef4444', '#e11d48', '#be123c', '#dc2626', '#b91c1c'))
        ):
            return 'endEvent'

        is_end = (
            _id_has_token(node_id, 'end')
            or _id_has_token(node_id, 'finish')
            or any(k in l for k in ('заверш', 'конец', 'выдан', 'ochildi', 'tugashi', 'bajarildi', 'активирован'))
            or 'outline=double' in s
            or 'outline=end' in s
        )
        is_start = (
            _id_has_token(node_id, 'start')
            or _id_has_token(node_id, 'begin')
            or any(k in l for k in ('старт', 'поступлен', 'tashrif', 'boshlanish'))
        )

        # End markers win over a generic green fill (success end events are often green)
        if is_end:
            return 'endEvent'
        if is_start:
            return 'startEvent'

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

def parse_bpmn_xml(xml_str: str, filename: str) -> BusinessProcess:
    """Parse OMG BPMN 2.0 XML (any namespace prefix) into a BusinessProcess."""
    root = ET.fromstring(xml_str)

    process_el = None
    for el in root.iter():
        if _local_tag(el.tag).lower() == 'process':
            process_el = el
            break

    process_name = (
        (process_el.get('name') if process_el is not None else None)
        or filename.rsplit('.', 1)[0]
    )
    process_id_attr = process_el.get('id') if process_el is not None else None

    bounds_map: Dict[str, Geometry] = {}
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'bpmnshape':
            continue
        bpmn_element = el.get('bpmnElement')
        if not bpmn_element:
            continue
        for child in el:
            if _local_tag(child.tag).lower() == 'bounds':
                bounds_map[bpmn_element] = Geometry(
                    x=int(float(child.get('x', '100'))),
                    y=int(float(child.get('y', '100'))),
                    width=int(float(child.get('width', '120'))),
                    height=int(float(child.get('height', '60'))),
                )
                break

    type_map = {
        'startevent': 'startEvent',
        'endevent': 'endEvent',
        'usertask': 'userTask',
        'task': 'userTask',
        'servicetask': 'serviceTask',
        'exclusivegateway': 'exclusiveGateway',
        'parallelgateway': 'parallelGateway',
        'inclusivegateway': 'inclusiveGateway',
    }

    nodes: List[ProcessNode] = []
    step_index = 1
    for el in root.iter():
        node_type = type_map.get(_local_tag(el.tag).lower())
        if not node_type:
            continue
        node_id = el.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        raw_name = el.get('name') or ''
        category = classify_category(node_type, raw_name, '')
        is_task = node_type in ('task', 'userTask', 'serviceTask')
        if node_type == 'startEvent':
            code = 'START'
        elif node_type == 'endEvent':
            code = 'END'
        elif is_task:
            code = f"STEP-{step_index:02d}"
            step_index += 1
        else:
            code = None
        sla = extract_sla_minutes(raw_name, category, node_type)
        geo = bounds_map.get(node_id) or Geometry(
            x=100 + (step_index * 150), y=100, width=140, height=70
        )
        name = raw_name or (
            'Старт' if node_type == 'startEvent'
            else 'Завершение' if node_type == 'endEvent'
            else f'Шаг {code or node_id}'
        )
        nodes.append(ProcessNode(
            id=node_id,
            name=name,
            type=node_type,
            category=category,
            code=code,
            geometry=geo,
            style='',
            slaMinutes=sla,
            costPerExecution=800 if category == 'rpa_bot' else sla * 1932,
            automationPotential=95 if category == 'rpa_bot' else 60,
            system=detect_system(name, ''),
        ))

    if not nodes:
        raise ValueError('В BPMN-файле не найдено ни одного элемента процесса')

    node_ids = {n.id for n in nodes}
    lane_refs: Dict[str, List[str]] = {}
    lanes: List[ProcessNode] = []
    lane_idx = 0
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'lane':
            continue
        lane_id = el.get('id') or f"lane_{lane_idx}"
        lane_name = el.get('name') or f"Подразделение {lane_idx + 1}"
        refs = []
        for child in el.iter():
            if _local_tag(child.tag).lower() == 'flownoderef' and (child.text or '').strip():
                refs.append(child.text.strip())
        lane_refs[lane_id] = refs
        bounds = bounds_map.get(lane_id)
        lanes.append(ProcessNode(
            id=lane_id,
            name=lane_name,
            type='lane',
            role=lane_name,
            geometry=bounds or Geometry(x=50, y=50 + lane_idx * 180, width=1400, height=180),
            style='swimlane;',
        ))
        lane_idx += 1

    for lane in lanes:
        for ref in lane_refs.get(lane.id, []):
            node = next((n for n in nodes if n.id == ref), None)
            if node:
                node.laneId = lane.id
                node.laneName = lane.name
                node.role = node.role or lane.name
                node.system = detect_system(node.name, lane.name)

    edges: List[ProcessEdge] = []
    for el in root.iter():
        if _local_tag(el.tag).lower() != 'sequenceflow':
            continue
        source_id = el.get('sourceRef')
        target_id = el.get('targetRef')
        if not source_id or not target_id:
            continue
        if source_id not in node_ids or target_id not in node_ids:
            continue
        cond_text = ''
        for child in el:
            if _local_tag(child.tag).lower() == 'conditionexpression':
                cond_text = (child.text or '').strip()
                break
        edge_name = el.get('name') or cond_text or ''
        edges.append(ProcessEdge(
            id=el.get('id') or f"edge_{uuid.uuid4().hex[:8]}",
            name=edge_name,
            sourceId=source_id,
            targetId=target_id,
            condition=cond_text or edge_name or None,
            points=[],
        ))

    title = process_name
    total_hours = round(sum(n.slaMinutes or 0 for n in nodes) / 60, 1) or 8.0
    passport_code = (
        process_id_attr if process_id_attr and process_id_attr.startswith('PRC-')
        else f"PRC-SQB-{uuid.uuid4().hex[:6].upper()}"
    )
    passport = ProcessPassport(
        code=passport_code,
        name=title,
        version='1.0',
        status='draft',
        owner='Департамент бизнес-процессов АКБ «Узпромстройбанк»',
        department=lanes[0].name if lanes else 'Операционный блок',
        category='Банковские процессы',
        targetSlaHours=total_hours,
        description=f"Импортирован из файла BPMN: {filename}.",
        createdDate=datetime.now().strftime('%Y-%m-%d'),
        updatedDate=datetime.now().strftime('%Y-%m-%d')
    )

    first_task = next((n for n in nodes if n.type in ('userTask', 'serviceTask', 'task')), None)
    seed = first_task or (nodes[0] if nodes else None)
    registry = PixRegistrySchema(
        id=f"reg-{uuid.uuid4().hex[:8]}",
        name=f"Реестр: {title}",
        code=f"REG_{passport.code.replace('-', '_')}",
        description=f"Реестр заявок по процессу {title}",
        fields=[
            ProcessField(id='f1', code='case_number', name='Номер заявки', type='string', required=True),
            ProcessField(id='f2', code='client_inn', name='ИНН Клиента', type='string', required=True),
            ProcessField(id='f3', code='client_title', name='Компания', type='string', required=True),
            ProcessField(id='f4', code='status', name='Статус', type='select', required=True, options=['В работе', 'Одобрено', 'Отклонено'])
        ],
        records=[
            PixRegistryRecord(
                id='rec-1',
                caseId='SQB-2026-BPM01',
                createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
                status='in_progress',
                currentStepId=seed.id if seed else 'step-1',
                currentStepName=seed.name if seed else 'Первичный шаг',
                assignedTo=(seed.role if seed and seed.role else 'Сотрудник банка'),
                elapsedMinutes=15,
                data={
                    'case_number': 'SQB-2026-BPM01',
                    'client_inn': '309819284',
                    'client_title': 'OOO "GLOBAL AGRO"',
                    'status': 'В работе'
                }
            )
        ]
    )

    validations = _collect_validations(nodes, edges)
    metrics = analyze_process_conformance(nodes, passport, len(registry.records))

    return BusinessProcess(
        id=f"proc_{uuid.uuid4().hex[:8]}",
        name=title,
        fileName=filename,
        passport=passport,
        nodes=nodes,
        edges=edges,
        lanes=lanes,
        validation=validations,
        registry=registry,
        miningMetrics=metrics
    )


def _collect_validations(flow_nodes: List[ProcessNode], edges: List[ProcessEdge]) -> List[ProcessValidation]:
    validations: List[ProcessValidation] = []
    starts = [n for n in flow_nodes if n.type == 'startEvent']
    ends = [n for n in flow_nodes if n.type == 'endEvent']
    if not starts:
        validations.append(ProcessValidation(level='error', message='Отсутствует стартовое событие процесса'))
    if len(starts) > 1:
        validations.append(ProcessValidation(level='warning', message=f'Найдено {len(starts)} стартовых событий'))
    if not ends:
        validations.append(ProcessValidation(level='warning', message='Отсутствует событие успешного завершения'))
    for n in flow_nodes:
        in_e = [e for e in edges if e.targetId == n.id]
        out_e = [e for e in edges if e.sourceId == n.id]
        if n.type not in ('startEvent', 'lane') and not in_e:
            validations.append(ProcessValidation(
                level='error',
                message=f'Шаг «{n.name or n.id}» не имеет входящих переходов (тупик)',
                nodeId=n.id,
            ))
        if n.type not in ('endEvent', 'lane') and not out_e:
            validations.append(ProcessValidation(
                level='warning',
                message=f'Шаг «{n.name or n.id}» не имеет исходящих переходов',
                nodeId=n.id,
            ))
    return validations


def parse_drawio_xml(content: str, filename: str) -> BusinessProcess:
    xml_str, is_bpmn = extract_graph_xml(content)
    if is_bpmn:
        return parse_bpmn_xml(xml_str, filename)

    root = ET.fromstring(xml_str)
    cells = root.findall('.//mxCell')
    cell_map: Dict[str, ET.Element] = {c.get('id', ''): c for c in cells if c.get('id')}

    raw_edges = [c for c in cells if c.get('edge') == '1']
    edge_id_set = {e.get('id', '') for e in raw_edges if e.get('id')}

    incoming: Set[str] = {e.get('target', '') for e in raw_edges if e.get('target')}
    outgoing: Set[str] = {e.get('source', '') for e in raw_edges if e.get('source')}

    label_map: Dict[str, str] = {}
    edge_label_geo: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    ignore_cell_ids: Set[str] = set()
    origin_cache: Dict[str, Tuple[float, float]] = {}
    orphan_condition_labels: List[Tuple[str, float, float]] = []  # text, x, y

    def _remember_label_geo(edge_id: str, geo_el: Optional[ET.Element]) -> None:
        if geo_el is None or edge_id in edge_label_geo:
            return
        lx = geo_el.get('x')
        ly = geo_el.get('y')
        off = None
        for p in geo_el.findall('mxPoint'):
            if (p.get('as') or '') == 'offset':
                off = p
                break
        x_val = float(lx) if lx not in (None, '') else None
        y_val = float(ly) if ly not in (None, '') else None
        if off is not None:
            ox = float(off.get('x', '0') or 0)
            oy = float(off.get('y', '0') or 0)
            # offset.y всегда прибавляется к перпендикулярному смещению
            if y_val is None:
                y_val = oy
            else:
                y_val += oy
            # offset.x добавляем к доле вдоль ребра (маленький шаг, т.к. x - доля -1..1)
            if x_val is not None and ox != 0:
                # 100px ~ 0.1 доли, эвристика
                x_val += ox * 0.005
            elif x_val is None and ox != 0:
                x_val = ox * 0.005
        if x_val is not None or y_val is not None:
            edge_label_geo[edge_id] = (x_val, y_val)

    for c in cells:
        c_id = c.get('id', '')
        parent_id = c.get('parent', '')
        style = (c.get('style') or '').lower()
        raw_val = c.get('value', '')
        cleaned = clean_label(raw_val)
        geo = c.find('mxGeometry')
        is_relative = geo.get('relative') == '1' if geo is not None else False
        is_connectable0 = c.get('connectable') == '0'

        # 1. Child of an edge (relative label)
        if parent_id in edge_id_set:
            ignore_cell_ids.add(c_id)
            if cleaned:
                label_map[parent_id] = cleaned
            _remember_label_geo(parent_id, geo)
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
            if cleaned and cleaned.lower() in CONDITION_TAGS:
                g2 = c.find('mxGeometry')
                if g2 is not None:
                    # absolute centre
                    ox, oy = _parent_origin(c.get('parent'), {k: v for k, v in cell_map.items()}, origin_cache)  # approximate, will be recomputed later but ok
                    # fallback to direct parent origin
                    try:
                        lx = float(g2.get('x', '0') or 0) + ox + float(g2.get('width', '40') or 40) / 2
                        ly = float(g2.get('y', '0') or 0) + oy + float(g2.get('height', '20') or 20) / 2
                        orphan_condition_labels.append((cleaned, lx, ly))
                    except ValueError:
                        pass
            continue

        # 4. Diagram title banner
        if 'text;' in style and is_non_task_label(cleaned):
            ignore_cell_ids.add(c_id)
            if cleaned and cleaned.lower() in CONDITION_TAGS:
                g2 = c.find('mxGeometry')
                if g2 is not None:
                    try:
                        # need parent origin
                        px, py = _parent_origin(c.get('parent'), cell_map, origin_cache)
                        lx = float(g2.get('x', '0') or 0) + px + float(g2.get('width', '40') or 40) / 2
                        ly = float(g2.get('y', '0') or 0) + py + float(g2.get('height', '20') or 20) / 2
                        orphan_condition_labels.append((cleaned, lx, ly))
                    except ValueError:
                        pass
            continue

        # 5. Non-task system tags, artifacts, conditions without connections
        if is_non_task_label(cleaned) and c_id not in incoming and c_id not in outgoing:
            ignore_cell_ids.add(c_id)
            if cleaned and cleaned.lower() in CONDITION_TAGS:
                g2 = c.find('mxGeometry')
                if g2 is not None:
                    try:
                        px, py = _parent_origin(c.get('parent'), cell_map, origin_cache)
                        lx = float(g2.get('x', '0') or 0) + px + float(g2.get('width', '40') or 40) / 2
                        ly = float(g2.get('y', '0') or 0) + py + float(g2.get('height', '20') or 20) / 2
                        orphan_condition_labels.append((cleaned, lx, ly))
                    except ValueError:
                        pass
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

    swimlane_ids = {c.get('id', '') for c in swimlane_cells if c.get('id')}
    container_ids = set(pool_ids) | swimlane_ids | {'0', '1'}

    def _is_artifact_shape(s: str) -> bool:
        return any(k in s for k in ('datastore', 'dataobject', 'shape=note', 'shape=mxgraph.signs', 'shape=mxgraph.bpmn.annotation'))

    raw_vertices = []
    for c in cells:
        if c.get('vertex') != '1':
            continue
        c_id = c.get('id', '')
        if not c_id or c_id in ignore_cell_ids or c_id in pool_ids:
            continue
        style = (c.get('style') or '').lower()
        # артефакты (datastore IABS/EHA, note) — игнор только если без связей (легенда)
        if _is_artifact_shape(style) and c_id not in incoming and c_id not in outgoing:
            continue
        parent_id = c.get('parent') or ''
        parent_el = cell_map.get(parent_id)
        if parent_el is not None and parent_el.get('vertex') == '1' and parent_id not in container_ids:
            continue
        geo = c.find('mxGeometry')
        w = float(geo.get('width', '0')) if geo is not None else 0.0
        h = float(geo.get('height', '0')) if geo is not None else 0.0
        unlabeled = not clean_label(c.get('value')) and c_id not in label_map
        tiny = 0 < w <= 32 and 0 < h <= 32
        if is_decoration_style(style) and (unlabeled or tiny) and c_id not in incoming and c_id not in outgoing:
            continue
        if unlabeled and c_id not in incoming and c_id not in outgoing and tiny:
            continue
        raw_vertices.append(c)

    nodes: List[ProcessNode] = []
    step_index = 1

    for cell in raw_vertices:
        node_id = cell.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        style = cell.get('style') or ''
        raw_val = cell.get('value')
        raw_cleaned = clean_label(raw_val) or label_map.get(node_id, '')

        parent_id = cell.get('parent')

        geo = cell.find('mxGeometry')
        # В draw.io отсутствующий x/y означает 0 (не 100!)
        local_x = float(geo.get('x', '0')) if geo is not None else 0.0
        local_y = float(geo.get('y', '0')) if geo is not None else 0.0
        width = float(geo.get('width', '120')) if geo is not None else 120.0
        height = float(geo.get('height', '60')) if geo is not None else 60.0

        ox, oy = _parent_origin(parent_id, cell_map, origin_cache)
        x = local_x + ox
        y = local_y + oy

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

        # Guard against zero/negative and invisible boxes (8px не читаемо)
        if node_type == 'lane':
            width = max(int(round(width)), 40)
            height = max(int(round(height)), 40)
        elif node_type in ('startEvent', 'endEvent'):
            # Круг/эллипс: минимум 32px для читаемости
            width = max(int(round(width or 44)), 32)
            height = max(int(round(height or 44)), 32)
        elif 'Gateway' in node_type:
            width = max(int(round(width or 48)), 32)
            height = max(int(round(height or 48)), 32)
        else:
            # task / serviceTask: минимум 80x40 для текста
            width = max(int(round(width or 120)), 80)
            height = max(int(round(height or 60)), 40)

        fot_cost = (sla_min * 1932) if category != 'rpa_bot' else 800

        nodes.append(ProcessNode(
            id=node_id,
            name=clean_name,
            type=node_type,
            category=category,
            code=code,
            geometry=Geometry(x=int(round(x)), y=int(round(y)), width=int(width), height=int(height)),
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

    LANE_HEAD_WIDTH = 44
    for n in flow_nodes:
        if not n.laneId:
            hit = next((
                l for l in lanes
                if (n.geometry.x >= l.geometry.x + LANE_HEAD_WIDTH - 10 and
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

    valid_node_ids = {n.id for n in flow_nodes}
    valid_lane_ids = {l.id for l in lanes}

    edges: List[ProcessEdge] = []
    for cell in raw_edges:
        s_id = cell.get('source')
        t_id = cell.get('target')
        if not s_id or not t_id:
            continue
        if not ((s_id in valid_node_ids or s_id in valid_lane_ids) and (t_id in valid_node_ids or t_id in valid_lane_ids)):
            continue

        edge_id = cell.get('id') or f"edge_{uuid.uuid4().hex[:8]}"
        raw_val = cell.get('value')
        edge_name = clean_label(raw_val) or label_map.get(edge_id, '')
        raw_style = cell.get('style') or ''
        smap = _style_map(raw_style)

        eox, eoy = _parent_origin(cell.get('parent'), cell_map, origin_cache)
        pts: List[ProcessEdgePoint] = []
        for arr in cell.findall('.//Array'):
            if (arr.get('as') or '') != 'points':
                continue
            for p in arr.findall('mxPoint'):
                pts.append(ProcessEdgePoint(
                    x=int(round(float(p.get('x', '0') or 0) + eox)),
                    y=int(round(float(p.get('y', '0') or 0) + eoy)),
                ))

        lx, ly = edge_label_geo.get(edge_id, (None, None))
        geo = cell.find('mxGeometry')
        if lx is None and geo is not None and geo.get('x') not in (None, ''):
            try:
                lx = float(geo.get('x'))
            except ValueError:
                pass
        if ly is None and geo is not None and geo.get('y') not in (None, ''):
            try:
                ly = float(geo.get('y'))
            except ValueError:
                pass

        lower_style = raw_style.lower()
        is_dashed = 'dashed=1' in lower_style
        dash_pat = smap.get('dashpattern')
        edge_style = smap.get('edgestyle')
        stroke_col = smap.get('strokecolor')
        sw = _style_float(smap, 'strokewidth')

        edges.append(ProcessEdge(
            id=edge_id,
            name=edge_name,
            sourceId=s_id,
            targetId=t_id,
            condition=edge_name or None,
            points=pts,
            exitX=_style_float(smap, 'exitx'),
            exitY=_style_float(smap, 'exity'),
            entryX=_style_float(smap, 'entryx'),
            entryY=_style_float(smap, 'entryy'),
            labelX=lx,
            labelY=ly,
            style=raw_style,
            dashed=is_dashed if is_dashed else None,
            dashPattern=dash_pat,
            edgeStyle=edge_style,
            strokeColor=stroke_col,
            strokeWidth=sw,
        ))

    # Привязываем висячие метки Yo'q/Ha/To'liq к ближайшему безымянному ребру (как в draw.io отдельные text)
    if orphan_condition_labels:
        node_by_id = {n.id: n for n in flow_nodes + lanes}
        for text, lx, ly in orphan_condition_labels:
            best = None
            best_dist = float('inf')
            for e in edges:
                if e.name:
                    continue
                s = node_by_id.get(e.sourceId or '')
                t = node_by_id.get(e.targetId or '')
                if not s or not t:
                    continue
                is_gw = s.type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway')
                if e.points:
                    sx = s.geometry.x + s.geometry.width / 2
                    sy = s.geometry.y + s.geometry.height / 2
                    ex = t.geometry.x + t.geometry.width / 2
                    ey = t.geometry.y + t.geometry.height / 2
                    pts = [(sx, sy)] + [(p.x, p.y) for p in e.points] + [(ex, ey)]
                    cx = sum(p[0] for p in pts) / len(pts)
                    cy = sum(p[1] for p in pts) / len(pts)
                else:
                    cx = (s.geometry.x + s.geometry.width / 2 + t.geometry.x + t.geometry.width / 2) / 2
                    cy = (s.geometry.y + s.geometry.height / 2 + t.geometry.y + t.geometry.height / 2) / 2
                d = ((lx - cx) ** 2 + (ly - cy) ** 2) ** 0.5
                penalty = 0 if is_gw else 35
                if d + penalty < best_dist and d < 140:
                    best_dist = d + penalty
                    best = e
            if best:
                best.name = text
                best.condition = text

    title = filename.replace('.drawio', '').replace('.xml', '')
    total_hours = round(sum(n.slaMinutes or 0 for n in flow_nodes) / 60, 1) or 8.0

    passport = ProcessPassport(
        code=f"PRC-SQB-{uuid.uuid4().hex[:6].upper()}",
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
                assignedTo=(flow_nodes[1].role or 'Сотрудник банка') if len(flow_nodes) > 1 else 'Сотрудник банка',
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

    validations = _collect_validations(flow_nodes, edges)

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
