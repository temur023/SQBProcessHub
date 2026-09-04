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
    StepCategory,
    EdgeKind,
    ARTIFACT_NODE_TYPES,
    TASK_NODE_TYPES,
)
from app.services.conformance_engine import analyze_process_conformance
from app.services.diagnostics import collect_import_diagnostics
from app.services.layout import normalize_layout

#: Хвост тега: атрибуты вплоть до закрывающей скобки. Значение в кавычках
#: пропускается целиком, потому что внутри него ``>`` — обычный символ, а не
#: конец тега. Наивное ``[^>]*>`` на таком значении обрывается посреди
#: атрибута, и весь остаток разметки вываливается в подпись как текст: подписи
#: с классами Tailwind (``class="[&>*]:pointer-events-auto …"``) приезжали в
#: Процессную студию вместе с версткой вместо названия шага.
_TAG_TAIL = r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*>'

#: Буфер обмена draw.io, попавший в подпись.
#:
#: Копируя фигуры, редактор кладёт в буфер их модель — ``<mxGraphModel>`` со
#: всеми ячейками, — и при вставке в текстовое поле она приезжает туда как
#: есть, обычно в процентной кодировке. Настоящая подпись при этом остаётся
#: в начале строки, а дальше тянется полотно разметки: на карте студии оно
#: разворачивается колонкой мусора через полсхемы.
_CLIPBOARD_PAYLOAD_RE = re.compile(
    r'(?:%3C|<)\s*mxGraphModel\b.*', re.IGNORECASE | re.DOTALL)

#: Фигуры-цилиндры draw.io: все варианты базы данных, какие есть в библиотеке.
_CYLINDER_SHAPE_RE = re.compile(r'^(?:cylinder\d*|datastore|db)$', re.IGNORECASE)

#: Теги, которые в draw.io означают перенос строки, а не оформление внутри неё.
_BLOCK_TAG_RE = re.compile(
    rf'<\s*/?\s*(?:br|div|p|li|ul|ol|tr|td|h[1-6])\b{_TAG_TAIL}', re.IGNORECASE)

#: Любой тег — снимается после блочных, уже без замены на пробел.
_ANY_TAG_RE = re.compile(rf'<\s*/?\s*[a-zA-Z][^\s/>]*{_TAG_TAIL}')

#: Комментарии и служебные объявления: внутри них ``>`` тоже допустим.
_COMMENT_RE = re.compile(r'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<![^>]*>', re.DOTALL)


def clean_label(raw: Optional[str]) -> str:
    """Текст подписи фигуры без разметки draw.io.

    Блочные теги заменяются пробелом (это перенос строки), а строчные снимаются
    без пробела. Разница существенная: редактор режет подпись тегом ``<span>``
    посреди слова, как только к части текста применили оформление, и пробел на
    этом месте разрывал число — «1 440 min» превращалось в «1 44» и «0 min»,
    а время шага уезжало с 1440 минут на 0.

    Теги снимаются с учётом кавычек: в подпись, вставленную копированием с
    веб-страницы, приезжает разметка, где значение атрибута само содержит
    ``>``. Разбор по первому попавшемуся ``>`` обрывался внутри атрибута, и
    остаток верстки уходил в название шага — в Процессной студии вместо
    «Avizlovchi bank …» стояло полстраницы классов и data-атрибутов.
    """
    if not raw:
        return ''
    text = raw.replace('&nbsp;', ' ')
    # Буфер обмена редактора обрезаем ДО снятия тегов: внутри него разметки
    # больше, чем текста, и снимать её по одному тегу незачем.
    text = _CLIPBOARD_PAYLOAD_RE.sub('', text)
    text = _COMMENT_RE.sub('', text)
    text = _BLOCK_TAG_RE.sub(' ', text)
    text = _ANY_TAG_RE.sub('', text)
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

def _model_vertex_count(model_xml: str) -> int:
    """Сколько фигур на странице draw.io: пустые страницы пропускаем."""
    try:
        root = ET.fromstring(model_xml)
    except ET.ParseError:
        return 0
    return sum(
        1 for c in root.iter('mxCell')
        if c.get('vertex') == '1' and c.get('id') not in ('0', '1')
    )


def _diagram_models(root: ET.Element) -> List[str]:
    """XML каждой страницы <diagram> в порядке следования в файле."""
    models: List[str] = []
    for diag in root.findall('.//diagram'):
        m = diag.find('.//mxGraphModel')
        if m is not None:
            models.append(ET.tostring(m, encoding='unicode'))
            continue
        r = diag.find('.//root')
        if r is not None:
            models.append(f"<mxGraphModel>{ET.tostring(r, encoding='unicode')}</mxGraphModel>")
            continue
        inner = (diag.text or '').strip()
        if not inner:
            continue
        if '<mxGraphModel' in inner:
            models.append(inner)
            continue
        try:
            models.append(inflate_diagram(inner))
        except Exception:
            continue
    return models


def page_report(content: str) -> Tuple[str, List[str]]:
    """Имя импортированной страницы и имена пропущенных.

    Нужно, чтобы сотрудник увидел: файл многостраничный, а в работу взята одна
    страница. Молча брать первую и не сказать об этом нельзя — на второй
    странице обычно лежит TO-BE, и её отсутствие выглядит как потеря данных.
    """
    trimmed = (content or '').strip()
    if '<mxfile' not in trimmed and '<diagram' not in trimmed:
        return '', []
    try:
        root = ET.fromstring(trimmed)
    except ET.ParseError:
        return '', []
    diagrams = root.findall('.//diagram')
    if len(diagrams) < 2:
        return (diagrams[0].get('name') or 'Страница 1') if diagrams else '', []

    names = [d.get('name') or f'Страница {i + 1}' for i, d in enumerate(diagrams)]
    models = _diagram_models(root)
    used = 0
    for index, model in enumerate(models):
        if _model_vertex_count(model) > 0:
            used = index
            break
    return names[used], [n for i, n in enumerate(names) if i != used]


def extract_graph_xml(content: str) -> Tuple[str, bool]:
    """XML одной карты процесса + признак «это BPMN 2.0, а не draw.io».

    Порядок проверок важен: файл ``.drawio`` — это ``<mxfile>`` со страницами,
    и внутри него тоже встречается подстрока ``<mxGraphModel``. Если сначала
    искать модель, многостраничный файл разбирается как его первая страница
    случайно, а сжатый — попадает в другую ветку и разбирается иначе.

    Страницы НЕ объединяются: в картах банка это варианты одного процесса
    (AS-IS, AS-IS с изменениями, TO-BE). Склейка накладывала их друг на друга —
    получалась одна нечитаемая схема с дублями шагов и пересечениями связей.
    Берём первую непустую страницу — ту же, что draw.io открывает по умолчанию.
    """
    trimmed = content.strip()

    if any(k in trimmed for k in ('<definitions', '<bpmn:definitions', '<bpmn2:definitions', '<bpmn:process')):
        return trimmed, True

    if '<mxfile' in trimmed or '<diagram' in trimmed:
        root = ET.fromstring(trimmed)
        if root.findall('.//diagram'):
            models = _diagram_models(root)
            if not models:
                raise ValueError('Не удалось извлечь ни одной диаграммы из mxfile')
            for model in models:
                if _model_vertex_count(model) > 0:
                    return model, False
            return models[0], False

    if '<mxGraphModel' in trimmed:
        root = ET.fromstring(trimmed)
        if root.tag == 'mxGraphModel':
            return trimmed, False
        model = root.find('.//mxGraphModel')
        if model is not None:
            return ET.tostring(model, encoding='unicode'), False

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

def is_clipart_style(style: str) -> bool:
    """Картинка из библиотеки draw.io — украшение схемы, а не шаг процесса.

    На картах SQB встречаются иконки телефона, поезда, здания: аналитик ставит
    их рядом с шагом «для наглядности». Фигурой BPMN такая картинка не является
    никогда, но раньше безымянная иконка со случайной линией превращалась в
    полноценную «Операцию STEP-NN» и попадала в регламент.
    """
    s = (style or '').lower()
    return (
        s.startswith('image;')
        or ';image=' in s
        or 'shape=image' in s
        or 'shape=mxgraph.signs' in s
        # Библиотека Office — тот же клипарт: телефон, монитор, здание. Иконка
        # телефона с подписью-времени превращалась в «Операцию STEP-NN» и
        # ложилась поверх ряда шагов.
        #
        # Кроме документов: ``office.concepts.documents`` — это лист бумаги,
        # который аналитик ставит рядом с шагом и ведёт к нему пунктир. Это
        # артефакт процесса, а не украшение, и он разбирается ниже как документ.
        or ('shape=mxgraph.office.' in s and 'office.concepts.documents' not in s)
    )


def is_text_overlay(style: str) -> bool:
    """Текстовая накладка draw.io: подпись поверх холста, а не фигура процесса.

    В draw.io стиль ``text;`` — это просто текст без рамки и заливки. На картах
    им подписывают безымянные шлюзы и документы, а в повёрнутом виде — саму
    дорожку. Обрамлённая врезка (``strokeColor`` задан) — исключение: это
    примечание к процессу, его отбирает :func:`is_text_note`.
    """
    s = (style or '').lower()
    return 'text;' in s and 'swimlane' not in s and 'edgelabel' not in s


#: Фигуры draw.io, которые импортёр разбирает по имени.
_KNOWN_SHAPE_RE = re.compile(
    r'mxgraph\.bpmn\.'
    r'|mxgraph\.flowchart\.annotation'
    r'|^(?:datastore|dataobject|note|rhombus|ellipse|process|pool|swimlane|image)$'
)
#: Виды шлюзов, у которых на карте банка есть свой смысл.
_KNOWN_GATEWAY_KINDS = frozenset({
    '', 'none', 'exclusive', 'xor', 'parallel', 'and',
    'inclusive', 'or', 'complex', 'multiple',
})
#: Значки событий, которые модель различает.
_KNOWN_EVENT_SYMBOLS = frozenset({
    '', 'none', 'general', 'timer', 'message', 'terminate', 'terminate2',
})


def unsupported_shape(style: str) -> Optional[str]:
    """Чего импортёр в фигуре не понял — короткой строкой для отчёта.

    Молча подставлять «ручную операцию» вместо незнакомой фигуры нельзя:
    аналитик рисует схему в draw.io и вправе считать, что видит на карте банка
    то же самое. Если фигура или её значок платформе неизвестны, она обязана
    сказать об этом, а не тихо заменить смысл ближайшим похожим.
    """
    lowered = (style or '').lower()
    if 'swimlane' in lowered or 'shape=pool' in lowered:
        return None
    smap = _style_map(style)
    shape = (smap.get('shape') or '').lower()
    if shape and not _KNOWN_SHAPE_RE.search(shape):
        return f'фигуру «{shape}»'
    if 'gateway' in shape:
        kind = (smap.get('gwtype') or '').lower()
        if kind not in _KNOWN_GATEWAY_KINDS:
            return f'шлюз «{kind}»'
    if shape.endswith('.event'):
        symbol = (smap.get('symbol') or '').lower()
        if symbol not in _KNOWN_EVENT_SYMBOLS:
            return f'событие со значком «{symbol}»'
    return None


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


#: Число длительности: «5», «0.5», «1 440» (пробел — разделитель тысяч).
_DURATION_NUMBER = r'\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)?'
#: «5 min», «0.5 daq», «120 мин», «1 440 min» — длительность в подписи фигуры.
_DURATION_RE = re.compile(
    rf'({_DURATION_NUMBER})\s*(?:min|daq|мин)[a-zа-я]*',
    re.IGNORECASE,
)
#: Хвост подписи, начинающийся с длительности: в имя шага он не идёт.
_DURATION_TAIL_RE = re.compile(
    rf'\b(?:{_DURATION_NUMBER})\s*(?:min|daq|минут|мин)\b.*$',
    re.IGNORECASE,
)
#: Подпись, помечающая время ОЖИДАНИЯ (WT), а не выполнения (ST).
_WAIT_RE = re.compile(r"kutish\s+vaqti|время\s+ожидания|wait", re.IGNORECASE)


def duration_minutes(text: Optional[str]) -> Optional[float]:
    """Минуты из подписи-бейджа длительности; None — если числа нет."""
    if not text:
        return None
    m = _DURATION_RE.search(text)
    if not m:
        return None
    digits = re.sub(r'[ \u00a0\u202f]', '', m.group(1)).replace(',', '.')
    try:
        return float(digits)
    except ValueError:
        return None


def is_wait_label(text: Optional[str]) -> bool:
    return bool(text) and bool(_WAIT_RE.search(text))


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

#: Короткая подпись рядом с фигурой — это бейдж, а не примечание к процессу.
TEXT_NOTE_MIN_LEN = 12


def is_text_note(style: str, label: str) -> bool:
    """Обрамлённая текстовая врезка draw.io — примечание к процессу.

    На картах SQB перечень требуемых документов оформлен как ``text``-фигура с
    рамкой (``strokeColor`` задан). Такие врезки раньше отбрасывались вместе с
    подписями-накладками, и содержательный текст пропадал из выгрузки. Заголовок
    схемы и подписи связей рамки не имеют (``strokeColor=none``) — они по-прежнему
    остаются оформлением.
    """
    s = (style or '').lower()
    if 'text;' not in s or 'swimlane' in s or 'edgelabel' in s:
        return False
    text = (label or '').strip()
    if len(text) < TEXT_NOTE_MIN_LEN or is_non_task_label(text):
        return False
    if duration_minutes(text) is not None:
        return False
    stroke = _style_map(s).get('strokecolor', 'none')
    return stroke not in ('none', '')


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


def _parent_origin(
    cell_id: Optional[str],
    cell_map: Dict[str, ET.Element],
    cache: Dict[str, Tuple[float, float]],
    _chain: Optional[Set[str]] = None,
) -> Tuple[float, float]:
    """Absolute top-left of a cell's parent chain.

    mxGraph semantics: children of a swimlane are positioned relative to the
    swimlane's FULL origin (including the title/startSize area) — verified by
    pool/lane geometry fitting exactly (lane y+h == pool h). Therefore we add
    only geo.x/geo.y of ancestors, no startSize shift.

    ``_chain`` — идентификаторы, уже пройденные на этом спуске. Файл с
    повторяющимися id (обычная история при склейке двух схем) даёт ячейку,
    которая оказывается сама себе предком, и обход уходил в бесконечную
    рекурсию: импорт падал с RecursionError, то есть пятисотой ошибкой без
    единого понятного слова для сотрудника. Цикл разрываем на первом же
    повторе; сам дефект ловит и объясняет предпроверка (``drawio_precheck``).
    """
    if not cell_id or cell_id in ('0', '1'):
        return 0.0, 0.0
    if cell_id in cache:
        return cache[cell_id]
    if _chain and cell_id in _chain:
        cache[cell_id] = (0.0, 0.0)
        return 0.0, 0.0
    cell = cell_map.get(cell_id)
    if cell is None:
        cache[cell_id] = (0.0, 0.0)
        return 0.0, 0.0
    px, py = _parent_origin(
        cell.get('parent'), cell_map, cache, (_chain or set()) | {cell_id}
    )
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

    # ── Артефакты (2-ILOVA: Artefaktlar) ────────────────────────────────────
    # Хранилище данных: IABS, EHA, EDO, Korporativ pochta.
    #
    # Цилиндр рисуют не только фигурой BPMN: в библиотеке draw.io их несколько
    # (``cylinder``, ``cylinder2``, ``cylinder3``, база из блок-схем), и
    # аналитики берут ту, что попалась под руку. Нераспознанный цилиндр
    # становился шагом — лишней строкой в регламенте и прямоугольником вместо
    # базы на карте студии.
    if (
        'shape=datastore' in s
        or 'mxgraph.bpmn.datastore' in s
        or 'kind=datastore' in s
        or _CYLINDER_SHAPE_RE.match(shape)
        or 'mxgraph.flowchart.database' in s
    ):
        return 'dataStore'
    # Объект данных: Dalolatnoma, Yig'ma jild, Hujjatlar ro'yxati.
    if ('mxgraph.bpmn.data2' in s or shape.endswith('bpmn.data') or 'shape=dataobject' in s
            or 'office.concepts.documents' in s):
        return 'dataObject'
    # Текстовое примечание: фигура-заметка или обрамлённая текстовая врезка.
    # Выноска (``callout``) — это записка к шагу, как её и рисует аналитик:
    # облачко с текстом над фигурой. Нераспознанная, она становилась
    # полноразмерной задачей и ложилась поверх соседнего ряда.
    if ('shape=note' in s or 'mxgraph.bpmn.annotation' in s
            or 'shape=mxgraph.flowchart.annotation' in s or 'shape=callout' in s):
        return 'textAnnotation'
    if is_text_note(style, label):
        return 'textAnnotation'

    if 'mxgraph.bpmn.gateway' in s or shape.endswith('gateway2') or 'gwtype' in smap:
        gw = (smap.get('gwtype') or smap.get('symbol') or '').lower()
        # Сложный шлюз (звёздочка) — не то же самое, что параллельный (плюс):
        # у параллельного срабатывают все ветки, у сложного условие задаётся
        # отдельно. Раньше они сливались, и на карте звёздочка превращалась
        # в плюс — аналитик видел не ту схему, что нарисовал.
        if gw in ('complex', 'multiple') or 'outline=star' in s:
            return 'complexGateway'
        if gw in ('parallel', 'and') or 'outline=plus' in s or 'parallel' in s:
            return 'parallelGateway'
        if gw in ('inclusive', 'or') or 'inclusive' in s or 'outline=circle' in s:
            return 'inclusiveGateway'
        return 'exclusiveGateway'

    if 'mxgraph.bpmn.event' in s or shape.endswith('.event'):
        outline = (smap.get('outline') or '').lower()
        symbol = (smap.get('symbol') or '').lower()
        # Таймер внутри потока — «Kutish vaqti»: промежуточное событие-обработчик.
        # Одиночные таймеры-бейджи длительности сюда не доходят: их снимает
        # _collect_duration_badges() и переносит в ST/WT ближайшего шага.
        if symbol == 'timer' and has_incoming and has_outgoing:
            return 'intermediateTimerEvent'
        if symbol == 'message' and has_incoming and has_outgoing:
            return 'intermediateMessageEvent'
        if outline in ('end', 'terminate') or 'outline=end' in s or 'outline=double' in s:
            return 'endEvent'
        if outline == 'catching' and has_incoming and has_outgoing:
            return 'intermediateTimerEvent' if symbol == 'timer' else 'intermediateMessageEvent'
        if not has_incoming and has_outgoing:
            return 'startEvent'
        if has_incoming and not has_outgoing:
            return 'endEvent'
        return 'startEvent'

    if 'mxgraph.bpmn.task' in s:
        marker = (smap.get('taskmarker') or smap.get('symbol') or '').lower()
        if marker in ('sub', 'subprocess') or 'issubprocess=1' in s or 'mxgraph.bpmn.transaction' in s:
            return 'subProcess'
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
        if 'complex' in s or l.strip() in ('*', '✳', '✱'):
            return 'complexGateway'
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
    if node_type in ARTIFACT_NODE_TYPES:
        return 'api_service' if node_type == 'dataStore' else 'manual'
    if node_type in ('startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent'):
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

def sla_is_measured(raw_text: str, node_type: NodeType) -> bool:
    """Стояло ли время в самой подписи фигуры.

    Отличать измеренное от подставленного нужно ровно для карты: время,
    которого в draw.io не было, не должно появляться на ней ни значком часов,
    ни свойством шага.
    """
    if node_type in ARTIFACT_NODE_TYPES:
        return False
    if duration_minutes(raw_text) is not None:
        return True
    return bool(re.search(r'(\d+(?:[.,]\d+)?)\s*m\b', raw_text or '', re.IGNORECASE))


def extract_sla_minutes(raw_text: str, category: StepCategory, node_type: NodeType) -> int:
    if node_type in ARTIFACT_NODE_TYPES:
        return 0
    if node_type in ('startEvent', 'endEvent'):
        return 5

    if node_type in ('intermediateTimerEvent', 'intermediateMessageEvent'):
        # Событие ожидания: длительность — это и есть его подпись.
        minutes = duration_minutes(raw_text)
        return max(1, int(round(minutes))) if minutes else 30

    minutes = duration_minutes(raw_text)
    if minutes is None:
        # «15 m» без единицы целиком: отдельный случай, в бейджах не встречается.
        short = re.search(r'(\d+(?:[.,]\d+)?)\s*m\b', raw_text, re.IGNORECASE)
        minutes = float(short.group(1).replace(',', '.')) if short else None
    if minutes is not None:
        return max(1, int(round(minutes)))

    if category == 'rpa_bot':
        return 3
    if category == 'api_service':
        return 2
    if category == 'validation':
        return 45
    if category == 'approval':
        return 180
    return 60

def _format_minutes(minutes: float) -> str:
    return str(int(minutes)) if float(minutes).is_integer() else f'{minutes:g}'


def fallback_node_name(node_type: NodeType, code: Optional[str], raw_text: str = '') -> str:
    """Осмысленное имя фигуры без подписи — никогда не идентификатор ячейки.

    Раньше на карту и в выгрузку попадали заголовки вида «Операция
    G9DXMv3N_W9X6-3aXuzq-1»: у промежуточного таймера вся подпись — это
    длительность («10 min»), а её снимает нормализация имени шага. Возвращаем
    длительность в имя события и подписываем остальные фигуры по их роли.
    """
    if node_type == 'intermediateTimerEvent':
        minutes = duration_minutes(raw_text)
        return f'Ожидание {_format_minutes(minutes)} мин' if minutes is not None else 'Ожидание'
    if node_type == 'intermediateMessageEvent':
        return 'Событие-сообщение'
    if node_type == 'startEvent':
        return 'Старт'
    if node_type == 'endEvent':
        return 'Завершение'
    if node_type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway'):
        return 'Условие'
    if node_type == 'dataStore':
        return 'Информационная система'
    if node_type == 'dataObject':
        return 'Документ'
    if node_type == 'textAnnotation':
        return 'Примечание'
    if node_type == 'subProcess':
        return f'Подпроцесс {code}' if code else 'Подпроцесс'
    return f'Операция {code}' if code else 'Операция'


#: Элемент BPMN 2.0 -> тип узла карты. Список закрытый: всё, что сюда не
#: попало (``laneSet``, ``participant``, ``documentation``), фигурой не является.
BPMN_TYPE_MAP: Dict[str, NodeType] = {
    'startevent': 'startEvent',
    'endevent': 'endEvent',
    'intermediatecatchevent': 'intermediateMessageEvent',  # уточняется по definition
    'intermediatethrowevent': 'intermediateMessageEvent',
    'boundaryevent': 'intermediateMessageEvent',
    'task': 'userTask',
    'usertask': 'userTask',
    'manualtask': 'userTask',
    'receivetask': 'userTask',
    'sendtask': 'serviceTask',
    'servicetask': 'serviceTask',
    'scripttask': 'serviceTask',
    'businessruletask': 'serviceTask',
    'subprocess': 'subProcess',
    'callactivity': 'subProcess',
    'exclusivegateway': 'exclusiveGateway',
    'eventbasedgateway': 'exclusiveGateway',
    'parallelgateway': 'parallelGateway',
    'inclusivegateway': 'inclusiveGateway',
    'complexgateway': 'complexGateway',
    'datastorereference': 'dataStore',
    'dataobjectreference': 'dataObject',
    'textannotation': 'textAnnotation',
}


#: Длительность в подписи значка — в тех единицах, в которых её пишет выгрузка.
_BADGE_UNIT_RE = re.compile(
    r'\d+(?:[.,]\d+)?\s*(?:мин|ч|дн|min|daq)\b', re.IGNORECASE,
)
#: Что может остаться от подписи значка длительности, кроме самого числа.
_BADGE_RESIDUE_RE = re.compile(
    r"[\s·,;:/–—-]+|ожидание|kutish\s*vaqti|wait", re.IGNORECASE,
)


def is_duration_badge_name(name: str) -> bool:
    """Подпись «15 мин · ожидание 2 ч» — значок времени, а не имя события.

    Выгрузка в BPMN вешает время шага граничным таймером: иначе Процессная
    студия открывает карту без единой цифры. При обратном чтении такой таймер
    обязан исчезнуть — время уже восстановлено из документации шага, а иначе
    у каждого шага появлялось бы фантомное событие-двойник.
    """
    text = (name or '').strip()
    if not text or not _BADGE_UNIT_RE.search(text):
        return False
    return not _BADGE_RESIDUE_RE.sub('', _BADGE_UNIT_RE.sub(' ', text)).strip()


def _bpmn_documentation(el: ET.Element) -> Dict[str, str]:
    """Паспорт шага из ``<documentation>``, который пишет наш же экспортёр.

    Стандартный BPMN не хранит ни исполнителя, ни время операции, ни систему —
    выгрузка складывает их в документацию строкой ``Role: …; ST: 15 min; …``.
    Без обратного разбора карта, выгруженная и открытая заново, теряла и роли,
    и SLA, и весь расчёт экономии.
    """
    meta: Dict[str, str] = {}
    for child in el:
        if _local_tag(child.tag).lower() != 'documentation':
            continue
        for chunk in (child.text or '').split(';'):
            key, sep, value = chunk.partition(':')
            if sep and value.strip():
                meta[key.strip().lower()] = value.strip()
        break
    return meta


def _apply_bpmn_documentation(node: ProcessNode, meta: Dict[str, str]) -> None:
    if not meta:
        return
    if meta.get('code'):
        node.code = meta['code']
    if meta.get('role'):
        node.role = meta['role']
    if meta.get('lane'):
        node.laneName = meta['lane']
    if meta.get('system'):
        node.system = meta['system']
    for key, field in (('st', 'slaMinutes'), ('wt', 'waitMinutes')):
        minutes = duration_minutes(meta.get(key))
        if minutes is not None:
            setattr(node, field, int(round(minutes)))
    for key, field in (('in', 'inputArtifacts'), ('out', 'outputArtifacts')):
        if meta.get(key):
            setattr(node, field, [p.strip() for p in meta[key].split(',') if p.strip()])
    category = meta.get('category')
    if category in ('manual', 'rpa_bot', 'api_service', 'approval', 'validation', 'notification'):
        node.category = category
    potential = re.match(r'(\d+)', meta.get('rpa potential', ''))
    if potential:
        node.automationPotential = int(potential.group(1))
    node.costPerExecution = 800 if node.category == 'rpa_bot' else (node.slaMinutes or 0) * 1932


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

    nodes: List[ProcessNode] = []
    step_index = 1
    for el in root.iter():
        tag = _local_tag(el.tag).lower()
        node_type = BPMN_TYPE_MAP.get(tag)
        if not node_type:
            continue
        node_id = el.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        raw_name = el.get('name') or ''
        if tag == 'boundaryevent' and is_duration_badge_name(raw_name):
            continue
        if node_type == 'textAnnotation' and not raw_name:
            for child in el:
                if _local_tag(child.tag).lower() == 'text':
                    raw_name = (child.text or '').strip()
                    break
        if tag == 'intermediatecatchevent':
            has_timer = any(
                _local_tag(child.tag).lower() == 'timereventdefinition' for child in el
            )
            node_type = 'intermediateTimerEvent' if has_timer else 'intermediateMessageEvent'
        meta = _bpmn_documentation(el)
        category = classify_category(node_type, raw_name, '')
        is_task = node_type in TASK_NODE_TYPES
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
        sla_measured = sla_is_measured(raw_name, node_type)
        geo = bounds_map.get(node_id) or Geometry(
            x=100 + (step_index * 150), y=100, width=140, height=70
        )
        name = raw_name or fallback_node_name(node_type, code, raw_name)
        node = ProcessNode(
            id=node_id,
            name=name,
            type=node_type,
            category=category,
            code=code,
            geometry=geo,
            style='',
            slaMinutes=sla,
            slaMeasured=sla_measured,
            costPerExecution=800 if category == 'rpa_bot' else sla * 1932,
            automationPotential=95 if category == 'rpa_bot' else 60,
            system=detect_system(name, ''),
        )
        _apply_bpmn_documentation(node, meta)
        nodes.append(node)

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

    # Ассоциации и сообщения — такие же связи карты, как переходы: без них с
    # карты пропадают все системы и документы, привязанные к шагам.
    edge_kinds: Dict[str, EdgeKind] = {
        'sequenceflow': 'sequenceFlow',
        'association': 'association',
        'messageflow': 'messageFlow',
    }
    edges: List[ProcessEdge] = []

    # ── Связи с данными живут ВНУТРИ активности ────────────────────────────
    #
    # По спецификации хранилище и документ подключаются к шагу не ассоциацией,
    # а ``dataInputAssociation`` / ``dataOutputAssociation`` — вложенными в сам
    # шаг элементами, у которых концы записаны не атрибутами, а дочерними
    # тегами ``sourceRef`` / ``targetRef``. Обычный обход по атрибутам их не
    # видит, и при повторном чтении собственной выгрузки все системы и
    # документы отваливались от шагов.
    #
    # Направление читается из имени тега: вход — из данных в шаг, выход — из
    # шага в данные. У выходной связи ``sourceRef`` может отсутствовать вовсе:
    # источник выводится из места объявления.
    for holder in root.iter():
        holder_id = holder.get('id')
        if not holder_id:
            continue
        for child in holder:
            tag = _local_tag(child.tag).lower()
            if tag not in ('datainputassociation', 'dataoutputassociation'):
                continue
            refs = {
                _local_tag(g.tag).lower(): (g.text or '').strip()
                for g in child
                if _local_tag(g.tag).lower() in ('sourceref', 'targetref')
            }
            if tag == 'datainputassociation':
                data_id, step_id = refs.get('sourceref', ''), holder_id
            else:
                data_id, step_id = refs.get('targetref', ''), holder_id
            if data_id not in node_ids or step_id not in node_ids:
                continue
            source_id, target_id = (
                (data_id, step_id) if tag == 'datainputassociation' else (step_id, data_id)
            )
            edges.append(ProcessEdge(
                id=child.get('id') or f'edge_{uuid.uuid4().hex[:8]}',
                name='',
                kind='association',
                sourceId=source_id,
                targetId=target_id,
                dashed=True,
            ))

    for el in root.iter():
        kind = edge_kinds.get(_local_tag(el.tag).lower())
        if not kind:
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
            kind=kind,
            condition=cond_text or edge_name or None,
            points=[],
        ))

    title = process_name
    # Паспорт считаем по тем же правилам, что и при импорте draw.io: только
    # шаги регламента и вместе с ожиданием. Иначе карта, выгруженная и открытая
    # заново, показывала SLA в разы больше — время событий и артефактов.
    total_hours = round(
        sum(
            (n.slaMinutes or 0) + (n.waitMinutes or 0)
            for n in nodes if n.type in TASK_NODE_TYPES
        ) / 60,
        1,
    ) or 8.0
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

    _resolve_artifact_links(nodes, edges)
    validations = collect_import_diagnostics(nodes, lanes, edges)
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


#: Максимальное расстояние от бейджа длительности до центра шага, px.
#: На реальных картах SQB бейдж лежит в 56-90 px от центра своего шага,
#: а до соседнего шага — не ближе ~190 px, поэтому порог однозначен.
BADGE_ATTACH_RADIUS = 130.0


def _name_and_prune_lanes(
    lanes: List[ProcessNode],
    flow_nodes: List[ProcessNode],
    lane_titles: Optional[Dict[str, str]] = None,
) -> None:
    """Убирает безымянные дорожки-баннеры и даёт имена остальным.

    На картах SQB встречаются swimlane-рамки оформления (шапка схемы) — без
    подписи и без шагов внутри. Настоящая безымянная дорожка содержит шаги,
    поэтому её сохраняем: сперва берём заголовок, набранный отдельным
    повёрнутым текстом внутри дорожки, и только потом — позиционное имя.

    Порядок важен: чистку делаем по собственной подписи дорожки. Заголовок
    схемы тоже лежит внутри пустой swimlane-рамки, и если сначала раздать
    заголовки, шапка схемы останется на карте как ещё одно подразделение.
    """
    populated = {n.laneId for n in flow_nodes if n.laneId}
    doomed = {
        lane.id for lane in lanes
        if not (lane.name or '').strip() and lane.id not in populated
    }
    if doomed:
        lanes[:] = [lane for lane in lanes if lane.id not in doomed]
        for node in flow_nodes:
            if node.laneId in doomed:
                node.laneId = None
                node.laneName = None

    for index, lane in enumerate(sorted(lanes, key=lambda l: (l.geometry.y, l.geometry.x)), 1):
        if (lane.name or '').strip():
            continue
        lane.name = (lane_titles or {}).get(lane.id) or f'Дорожка {index}'
        lane.role = lane.name


#: Насколько близко свободный конец линии должен подойти к фигуре, px.
FREE_ENDPOINT_SNAP = 30.0

#: Насколько близко текстовая накладка должна лежать к безымянной фигуре, px.
#: На картах SQB подпись касается своего шлюза или документа (зазор 0-34 px),
#: а до следующей безымянной фигуры не ближе ~100 px, поэтому порог однозначен.
TEXT_LABEL_SNAP = 60.0


def _box_gap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Зазор между двумя прямоугольниками (0 — если пересекаются)."""
    dx = max(b[0] - (a[0] + a[2]), 0.0, a[0] - (b[0] + b[2]))
    dy = max(b[1] - (a[1] + a[3]), 0.0, a[1] - (b[1] + b[3]))
    return (dx * dx + dy * dy) ** 0.5


#: Подпись дорожки из одних символов («+», «—») смыслом не является.
_WORDY_RE = re.compile(r'[0-9A-Za-z\u0400-\u04FF]')


def is_junk_label(text: str) -> bool:
    """Подпись без единой буквы и цифры — оформление, а не имя."""
    return not _WORDY_RE.search(text or '')


def attach_text_overlays(
    overlays: List[Tuple[str, str, Tuple[float, float, float, float], str]],
    targets: List[Tuple[str, Tuple[float, float, float, float]]],
    lane_of: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Раздаёт текстовые накладки безымянным фигурам и дорожкам.

    В draw.io подпись фигуры можно набрать отдельным текстовым блоком рядом —
    редактор рисует то же самое, но в модели у фигуры остаётся пустое ``value``.
    Без этой привязки шлюз попадал в регламент как «Условие», документ — как
    «Документ», а сам текст либо терялся, либо становился отдельным шагом.

    ``overlays`` — ``(id накладки, текст, рамка, id дорожки-родителя)``;
    ``targets`` — рамки фигур, у которых своей подписи нет;
    ``lane_of`` — дорожки без осмысленного имени: ``id -> id``.

    Возвращает ``(подписи фигур, названия дорожек)``.
    """
    pairs: List[Tuple[float, str, str]] = []
    for overlay_id, _text, box, _lane in overlays:
        for target_id, target_box in targets:
            gap = _box_gap(box, target_box)
            if gap <= TEXT_LABEL_SNAP:
                pairs.append((gap, overlay_id, target_id))
    pairs.sort(key=lambda t: (t[0], t[1], t[2]))

    text_by_overlay = {o[0]: o[1] for o in overlays}
    shape_labels: Dict[str, str] = {}
    used_overlays: Set[str] = set()
    for _gap, overlay_id, target_id in pairs:
        if overlay_id in used_overlays or target_id in shape_labels:
            continue
        shape_labels[target_id] = text_by_overlay[overlay_id]
        used_overlays.add(overlay_id)

    # Накладка, не нашедшая фигуры, но лежащая в безымянной дорожке, —
    # её заголовок: так подписаны вертикальные дорожки на картах SQB.
    lane_titles: Dict[str, str] = {}
    for overlay_id, text, _box, lane_id in overlays:
        if overlay_id in used_overlays or not lane_id or lane_id not in lane_of:
            continue
        current = lane_titles.get(lane_id, '')
        if len(text) > len(current):
            lane_titles[lane_id] = text
    return shape_labels, lane_titles


#: Условия-антонимы: подпись одной ветки развилки задаёт подпись второй.
_CONDITION_OPPOSITES: Dict[str, str] = {
    'ha': "Yo'q", "yo'q": 'Ha', 'yo`q': 'Ha', 'yo’q': 'Ha',
    'да': 'Нет', 'нет': 'Да',
    'yes': 'No', 'no': 'Yes',
    "to'liq": "To'liq emas", "to'liq emas": "To'liq",
    'mos keldi': 'Mos kelmadi', 'mos kelmadi': 'Mos keldi',
    'ijobiy': 'Salbiy', 'salbiy': 'Ijobiy',
    'qabul qilindi': 'Rad etildi', 'rad etildi': 'Qabul qilindi',
}


def complete_binary_gateway_conditions(
    flow_nodes: List[ProcessNode],
    edges: List[ProcessEdge],
) -> List[Tuple[ProcessNode, ProcessEdge, str]]:
    """Достраивает подпись второй ветки развилки по подписи первой.

    У развилки «да/нет» аналитик часто подписывает только отрицательную ветку:
    на рисунке и так понятно, что вторая — «Ha». PIX BPM так не умеет: ветка без
    условия не автоматизируется, и шаг регламента упирается в неё.

    Достраиваем только там, где догадка однозначна: исключающий шлюз ровно с
    двумя исходящими ветками, подписана ровно одна, и её подпись входит в пару
    антонимов. Всё остальное остаётся ошибкой — угадывать смысл развилки на
    три ветки платформа не вправе.

    Возвращает список ``(шлюз, ветка, подставленное условие)`` — о каждой
    подстановке отчёт обязан сказать сотруднику.
    """
    outgoing: Dict[str, List[ProcessEdge]] = {}
    for edge in edges:
        if (edge.kind or 'sequenceFlow') == 'sequenceFlow' and edge.sourceId:
            outgoing.setdefault(edge.sourceId, []).append(edge)

    filled: List[Tuple[ProcessNode, ProcessEdge, str]] = []
    for node in flow_nodes:
        if node.type != 'exclusiveGateway':
            continue
        branches = outgoing.get(node.id, [])
        if len(branches) != 2:
            continue
        named = [e for e in branches if (e.name or e.condition or '').strip()]
        blank = [e for e in branches if not (e.name or e.condition or '').strip()]
        if len(named) != 1 or len(blank) != 1:
            continue
        known = (named[0].name or named[0].condition or '').strip().lower()
        opposite = _CONDITION_OPPOSITES.get(known)
        if not opposite:
            continue
        blank[0].name = opposite
        blank[0].condition = opposite
        filled.append((node, blank[0], opposite))
    return filled


def reclassify_events(flow_nodes: List[ProcessNode], edges: List[ProcessEdge]) -> None:
    """Приводит тип события к его реальной степени на карте.

    Тип фигуры определяется до того, как разобраны связи: у линии draw.io конец
    может висеть в пустоте, и её притягивает к фигуре уже отдельный проход. До
    этого прохода промежуточное событие выглядит как висячее и получает тип
    ``startEvent`` — на карте вместо одного старта рисуется пять, а в отчёте
    появляется ложное «Стартовых событий: 5».
    """
    incoming: Set[str] = set()
    outgoing: Set[str] = set()
    for e in edges:
        if (e.kind or 'sequenceFlow') != 'sequenceFlow':
            continue
        if e.targetId:
            incoming.add(e.targetId)
        if e.sourceId:
            outgoing.add(e.sourceId)

    for node in flow_nodes:
        if node.type not in ('startEvent', 'endEvent'):
            continue
        has_in = node.id in incoming
        has_out = node.id in outgoing
        if has_in and has_out:
            style_l = (node.style or '').lower()
            is_timer = 'symbol=timer' in style_l or is_wait_label(node.name)
            new_type = 'intermediateTimerEvent' if is_timer else 'intermediateMessageEvent'
        elif has_in and not has_out and node.type == 'startEvent':
            new_type = 'endEvent'
        elif has_out and not has_in and node.type == 'endEvent':
            new_type = 'startEvent'
        else:
            continue
        was_generated = node.name in (
            fallback_node_name(node.type, node.code),
            fallback_node_name(node.type, None),
        )
        node.type = new_type
        node.category = classify_category(new_type, node.name, node.style or '')
        node.code = 'START' if new_type == 'startEvent' else 'END' if new_type == 'endEvent' else None
        if was_generated:
            node.name = fallback_node_name(new_type, node.code)


def _distance_to_box(px: float, py: float, node: ProcessNode) -> float:
    g = node.geometry
    dx = max(g.x - px, 0.0, px - (g.x + g.width))
    dy = max(g.y - py, 0.0, py - (g.y + g.height))
    return (dx * dx + dy * dy) ** 0.5


def _resolve_free_endpoint(
    point: Optional[Tuple[float, float]],
    candidates: List[ProcessNode],
    exclude_id: Optional[str] = None,
) -> Optional[str]:
    """Фигура под свободным концом линии draw.io.

    В draw.io конец связи может быть не привязан к фигуре, а задан точкой
    (``mxPoint as="sourcePoint"``). Редактор всё равно рисует линию, а мы
    раньше выбрасывали её целиком — на карте пропадали и потоки, и пунктирные
    ассоциации к хранилищам данных.
    """
    if point is None or not candidates:
        return None
    best_id: Optional[str] = None
    best_key = (FREE_ENDPOINT_SNAP, float('inf'))
    for node in candidates:
        # Второй конец той же линии уже занял эту фигуру: связь фигуры с самой
        # собой не бывает ни в BPMN, ни в PIX — студия отказывается открывать
        # карту целиком («Connector source and target node cannot be the same»).
        if exclude_id is not None and node.id == exclude_id:
            continue
        dist = _distance_to_box(point[0], point[1], node)
        # При равном расстоянии выигрывает меньшая фигура: точка внутри шага
        # лежит и внутри его дорожки, но связать её надо с шагом.
        key = (dist, float(node.geometry.width) * float(node.geometry.height))
        if dist <= FREE_ENDPOINT_SNAP and key < best_key:
            best_key, best_id = key, node.id
    return best_id


def _apply_duration_badges(
    flow_nodes: List[ProcessNode],
    badges: List[Tuple[float, float, float, bool]],
) -> Set[str]:
    """Переносит ST/WT из фигур-таймеров в ближайший шаг процесса (4-ILOVA).

    Возвращает шаги, которым время проставил реальный бейдж с карты: остальным
    оно досталось от эвристики по категории, и об этом надо сказать аналитику.
    """
    tasks = [n for n in flow_nodes if n.type in TASK_NODE_TYPES]
    if not tasks or not badges:
        return set()
    st_seen: Set[str] = set()
    for cx, cy, minutes, is_wait in badges:
        best: Optional[ProcessNode] = None
        best_dist = BADGE_ATTACH_RADIUS
        for t in tasks:
            tx = t.geometry.x + t.geometry.width / 2
            ty = t.geometry.y + t.geometry.height / 2
            dist = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
            if dist < best_dist:
                best_dist, best = dist, t
        if best is None:
            continue
        value = max(1, int(round(minutes)))
        # Время пришло с карты — отдельной фигурой-часами рядом с шагом. Именно
        # так его и рисует аналитик: в подписи шага цифры нет.
        best.slaMeasured = True
        if is_wait:
            best.waitMinutes = (best.waitMinutes or 0) + value
        elif best.id in st_seen:
            best.slaMinutes = (best.slaMinutes or 0) + value
        else:
            best.slaMinutes = value
            st_seen.add(best.id)
        if best.category != 'rpa_bot':
            best.costPerExecution = (best.slaMinutes or 0) * 1932
    return st_seen


def _resolve_artifact_links(flow_nodes: List[ProcessNode], edges: List[ProcessEdge]) -> None:
    """Системы и документы шага — из реальных ассоциаций карты, а не из эвристик."""
    by_id = {n.id: n for n in flow_nodes}
    systems: Dict[str, List[str]] = {}
    inputs: Dict[str, List[str]] = {}
    outputs: Dict[str, List[str]] = {}

    def _add(bucket: Dict[str, List[str]], key: str, value: str) -> None:
        if not value:
            return
        items = bucket.setdefault(key, [])
        if value not in items:
            items.append(value)

    for e in edges:
        if e.kind != 'association':
            continue
        src = by_id.get(e.sourceId or '')
        tgt = by_id.get(e.targetId or '')
        if not src or not tgt:
            continue
        for artifact, step, incoming_dir in ((src, tgt, True), (tgt, src, False)):
            if step.type not in TASK_NODE_TYPES:
                continue
            if artifact.type == 'dataStore':
                _add(systems, step.id, artifact.name)
            elif artifact.type == 'dataObject':
                _add(inputs if incoming_dir else outputs, step.id, artifact.name)

    for node in flow_nodes:
        linked = systems.get(node.id)
        if linked:
            node.system = ', '.join(linked)
        if inputs.get(node.id):
            node.inputArtifacts = inputs[node.id]
        if outputs.get(node.id):
            node.outputArtifacts = outputs[node.id]


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
    #: Текстовые накладки: (id, текст, рамка в абсолютных координатах, id родителя).
    text_overlays: List[Tuple[str, str, Tuple[float, float, float, float], str]] = []
    # Бейджи длительности: (центр X, центр Y, минуты, это ожидание WT?)
    duration_badges: List[Tuple[float, float, float, bool]] = []

    # ── Бейджи ST/WT ────────────────────────────────────────────────────────
    # По Методике (4-ILOVA) время операции проставляется отдельной мелкой
    # фигурой-таймером рядом с шагом, а не в самой подписи шага. Такие фигуры
    # не соединены рёбрами: снимаем их с карты и переносим в ST/WT шага.
    for c in cells:
        if c.get('vertex') != '1':
            continue
        c_id = c.get('id', '')
        if not c_id or c_id in incoming or c_id in outgoing:
            continue
        style_l = (c.get('style') or '').lower()
        if 'swimlane' in style_l:
            continue
        text = clean_label(c.get('value'))
        minutes = duration_minutes(text)
        if minutes is None:
            continue
        residual = _DURATION_RE.sub('', text).strip(' .,:;-')
        is_timer_shape = 'symbol=timer' in style_l or 'shape=mxgraph.bpmn.timer' in style_l
        if not is_timer_shape:
            # Текстовая пометка «O'rtacha kutish vaqti 1440 min»: только текст,
            # никогда не фигура BPMN, иначе можно съесть настоящий шаг.
            if 'mxgraph.bpmn' in style_l or len(residual) > 32:
                continue
        geo_b = c.find('mxGeometry')
        if geo_b is None:
            continue
        try:
            bx, by = _parent_origin(c.get('parent'), cell_map, origin_cache)
            cx = float(geo_b.get('x', '0') or 0) + bx + float(geo_b.get('width', '20') or 20) / 2
            cy = float(geo_b.get('y', '0') or 0) + by + float(geo_b.get('height', '20') or 20) / 2
        except ValueError:
            continue
        duration_badges.append((cx, cy, minutes, is_wait_label(text)))
        ignore_cell_ids.add(c_id)

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

        # 2.5. Обрамлённая текстовая врезка — примечание, а не оформление.
        if is_text_note(style, cleaned):
            continue

        # 3. Text label overlay
        if c_id.endswith('_label') or is_text_overlay(style):
            ignore_cell_ids.add(c_id)
            base_id = re.sub(r'_label$', '', c_id)
            if base_id != c_id and cleaned:
                label_map[base_id] = cleaned
            elif cleaned:
                # Подпись отдельным блоком: чью фигуру она называет, решаем
                # по геометрии — соседство на холсте здесь и есть связь.
                g3 = c.find('mxGeometry')
                if g3 is not None:
                    try:
                        ox3, oy3 = _parent_origin(c.get('parent'), cell_map, origin_cache)
                        text_overlays.append((
                            c_id,
                            cleaned,
                            (
                                float(g3.get('x', '0') or 0) + ox3,
                                float(g3.get('y', '0') or 0) + oy3,
                                float(g3.get('width', '0') or 0),
                                float(g3.get('height', '0') or 0),
                            ),
                            parent_id,
                        ))
                    except ValueError:
                        pass
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
        # Дорожки исключены: у безымянной дорожки подпись пустая, но это не мусор.
        if 'swimlane' in style or 'shape=pool' in style:
            continue
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

    # ── Подписи, набранные отдельным текстовым блоком ────────────────────────
    # Их адресат определяется соседством на холсте, поэтому раздаём подписи
    # после того, как собраны все фигуры и известно, у каких из них имени нет.
    unlabeled_boxes: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for c in cells:
        c_id = c.get('id', '')
        if c.get('vertex') != '1' or not c_id or c_id in ignore_cell_ids:
            continue
        if c_id in swimlane_ids or c_id in pool_ids or c_id in label_map:
            continue
        if clean_label(c.get('value')):
            continue
        g4 = c.find('mxGeometry')
        if g4 is None:
            continue
        try:
            ox4, oy4 = _parent_origin(c.get('parent'), cell_map, origin_cache)
            unlabeled_boxes.append((c_id, (
                float(g4.get('x', '0') or 0) + ox4,
                float(g4.get('y', '0') or 0) + oy4,
                float(g4.get('width', '0') or 0),
                float(g4.get('height', '0') or 0),
            )))
        except ValueError:
            continue

    nameless_lane_ids = {
        c.get('id', ''): c.get('id', '')
        for c in swimlane_cells
        if c.get('id') and is_junk_label(clean_label(c.get('value')))
    }
    overlay_labels, lane_titles = attach_text_overlays(
        text_overlays, unlabeled_boxes, nameless_lane_ids,
    )
    label_map.update(overlay_labels)

    def _is_artifact_shape(s: str) -> bool:
        return any(k in s for k in ('datastore', 'dataobject', 'shape=note', 'shape=mxgraph.signs', 'shape=mxgraph.bpmn.annotation'))

    #: Иконки-украшения, снятые с карты: о них сотруднику тоже надо сказать.
    skipped_clipart: List[str] = []
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
        # Безымянная картинка из библиотеки — украшение, даже если к ней
        # подведена линия: шагом регламента иконка телефона не бывает.
        if is_clipart_style(style) and unlabeled:
            skipped_clipart.append(_style_map(style).get('shape') or 'image')
            continue
        if is_decoration_style(style) and (unlabeled or tiny) and c_id not in incoming and c_id not in outgoing:
            continue
        if unlabeled and c_id not in incoming and c_id not in outgoing and tiny:
            continue
        raw_vertices.append(c)

    nodes: List[ProcessNode] = []
    step_index = 1
    #: Шаги, чьё время взято с карты, а не из эвристики по категории.
    timed_step_ids: Set[str] = set()
    #: Фигуры, смысл которых импортёр не понял: описание -> подставленные узлы.
    unsupported: Dict[str, List[ProcessNode]] = {}

    for cell in raw_vertices:
        node_id = cell.get('id') or f"node_{uuid.uuid4().hex[:8]}"
        style = cell.get('style') or ''
        raw_val = cell.get('value')
        raw_cleaned = clean_label(raw_val)
        if not raw_cleaned and 'swimlane' not in (style or '').lower():
            raw_cleaned = label_map.get(node_id, '')

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
        # Номер шага бывает составным («84.1.», «37.1.1») и не всегда кончается
        # точкой. Берём его целиком: иначе шаг 84.1 получает код своего родителя
        # STEP-84 и в регламенте эти строки не отличить. Одиночное число без
        # разделителя номером не считаем — «2026 yil» не шаг №2026.
        num_prefix = re.match(r'^(\d+(?:\.\d+)*)([.)]?)(?=\s)', raw_cleaned)
        if num_prefix and not (num_prefix.group(2) or '.' in num_prefix.group(1)):
            num_prefix = None

        if code_match:
            code = code_match.group(1).upper().replace('_', '-')
        elif num_prefix and is_task:
            head, _, rest = num_prefix.group(1).partition('.')
            code = f"STEP-{int(head):02d}" + (f'.{rest}' if rest else '')
        elif node_type == 'startEvent':
            code = 'START'
        elif node_type == 'endEvent':
            code = 'END'
        elif is_task:
            code = f"STEP-{step_index:02d}"
            step_index += 1

        raw_text = f"{raw_val or ''} {raw_cleaned}"
        # Время ищем только в очищенной подписи: в сырой разметке draw.io лежат
        # цвета вида «#000», и «0 min» из атрибута стиля забирал шагу его SLA.
        sla_min = extract_sla_minutes(raw_cleaned, category, node_type)
        sla_measured = sla_is_measured(raw_cleaned, node_type)
        if duration_minutes(raw_cleaned) is not None:
            timed_step_ids.add(node_id)

        # Номер шага остаётся в названии: на карте draw.io он написан перед
        # текстом, по нему аналитик находит шаг в регламенте и в самой Методике.
        # Снимаем только служебные пометки — тег вида «[PIX RPA]», машинный код
        # «STEP-01:» и приписанное к подписи время.
        clean_name = raw_cleaned
        clean_name = re.sub(r'^\[.*?\]\s*', '', clean_name, flags=re.I)
        clean_name = re.sub(r'^STEP[-_ ]?\d+[:\s-]*', '', clean_name, flags=re.I)
        clean_name = _DURATION_TAIL_RE.sub('', clean_name).strip()

        if node_type == 'lane' and is_junk_label(clean_name):
            clean_name = ''  # «+», «—»: имя присвоим позиционно после разбора
        elif not clean_name:
            clean_name = fallback_node_name(node_type, code, raw_cleaned)

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
        elif node_type in ARTIFACT_NODE_TYPES:
            # Артефакт подписан СНАРУЖИ — и в bpmn.io, и в студии, — поэтому
            # места под текст внутри ему не нужно. Минимум 80×40, рассчитанный
            # на шаг, раздувал цилиндр базы вдвое против нарисованного (в
            # картах банка он 30×25 … 50×36) и делал его заметнее самого шага.
            width = max(int(round(width or 40)), 24)
            height = max(int(round(height or 30)), 20)
        else:
            # task / serviceTask: минимум 80x40 для текста
            width = max(int(round(width or 120)), 80)
            height = max(int(round(height or 60)), 40)

        fot_cost = (sla_min * 1932) if category != 'rpa_bot' else 800

        node = ProcessNode(
            id=node_id,
            name=clean_name,
            type=node_type,
            category=category,
            code=code,
            geometry=Geometry(x=int(round(x)), y=int(round(y)), width=int(width), height=int(height)),
            style=style,
            laneId=parent_id,
            slaMinutes=sla_min,
            slaMeasured=sla_measured,
            costPerExecution=fot_cost,
            automationPotential=95 if category == 'rpa_bot' else (65 if category == 'manual' else 40)
        )
        unknown = unsupported_shape(style)
        if unknown:
            unsupported.setdefault(unknown, []).append(node)
        nodes.append(node)

    # ── Идентификаторы фигур уникальны ──────────────────────────────────────
    # Инвариант модели, а не удобство экспортёра: и BPMN (xsd:ID), и .pmm
    # требуют уникальности, а карта, склеенная из двух файлов, приносит по два
    # `id="node1"`. Раньше такие фигуры доезжали до выгрузки как есть, и студия
    # отвергала весь пакет. Первое вхождение сохраняет идентификатор — на него
    # уже ссылаются связи, — последующие получают суффикс.
    seen_ids: Set[str] = set()
    for n in nodes:
        if n.id not in seen_ids:
            seen_ids.add(n.id)
            continue
        suffix = 2
        while f'{n.id}__dup{suffix}' in seen_ids:
            suffix += 1
        n.id = f'{n.id}__dup{suffix}'
        seen_ids.add(n.id)

    lanes = [n for n in nodes if n.type == 'lane']
    lane_ids = {l.id for l in lanes}
    flow_nodes = [n for n in nodes if n.type != 'lane']

    for n in flow_nodes:
        if n.laneId and n.laneId not in lane_ids:
            n.laneId = None

    _name_and_prune_lanes(lanes, flow_nodes, lane_titles)

    lane_by_id = {l.id: l for l in lanes}

    def _lane_under(node: ProcessNode) -> Optional[ProcessNode]:
        """Дорожка, внутри которой лежит центр фигуры."""
        cx = node.geometry.x + node.geometry.width / 2
        cy = node.geometry.y + node.geometry.height / 2
        for lane in lanes:
            g = lane.geometry
            if g.x <= cx <= g.x + g.width and g.y <= cy < g.y + g.height:
                return lane
        return None

    for n in flow_nodes:
        # Родитель ячейки в draw.io — не то же самое, что дорожка на рисунке:
        # фигуру можно утащить из дорожки, и редактор сохранит прежнего родителя.
        # Исполнителя шага определяем по тому, где фигура лежит на самом деле,
        # иначе роль в регламенте берётся от чужого подразделения.
        declared = lane_by_id.get(n.laneId or '')
        actual = _lane_under(n)
        if actual is not None and actual.id != (declared.id if declared else None):
            n.laneId = actual.id
            n.role = None
        elif declared is not None and actual is None:
            n.laneId = None
            n.laneName = None
            n.role = None

        p_lane = lane_by_id.get(n.laneId or '')
        if p_lane:
            n.laneName = p_lane.name
            n.role = n.role or p_lane.name
        n.system = detect_system(n.name, n.laneName or '')

    valid_node_ids = {n.id for n in flow_nodes}
    valid_lane_ids = {l.id for l in lanes}
    type_by_id: Dict[str, str] = {n.id: n.type for n in flow_nodes}
    # Дорожка без единого шага — это внешний участник (клиент, госорган):
    # аналитик отводит ему полосу и тянет к ней пунктир от шагов банка.
    external_lane_ids = valid_lane_ids - {n.laneId for n in flow_nodes if n.laneId}
    #: Типы, которым BPMN разрешает быть концом messageFlow (InteractionNode).
    _INTERACTION_TYPES = (
        'task', 'userTask', 'serviceTask', 'subProcess',
        'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
    )

    def _edge_kind(src_id: str, tgt_id: str, dashed: bool) -> EdgeKind:
        if src_id in valid_lane_ids or tgt_id in valid_lane_ids:
            lane_id, other_id = (
                (src_id, tgt_id) if src_id in valid_lane_ids else (tgt_id, src_id)
            )
            # Пунктир «шаг банка ↔ полоса клиента» — это обмен сообщениями с
            # внешним участником, а не оформление: в выгрузке он обязан
            # остаться, иначе с карты пропадают точки контакта с клиентом.
            if lane_id in external_lane_ids and type_by_id.get(other_id, '') in _INTERACTION_TYPES:
                return 'messageFlow'
            # Линия, упирающаяся в дорожку с шагами, — разделитель этапов.
            return 'annotationLine'
        st, tt = type_by_id.get(src_id, ''), type_by_id.get(tgt_id, '')
        # Связь с артефактом — всегда ассоциация (по BPMN sequenceFlow к
        # хранилищу/документу недопустим).
        if st in ARTIFACT_NODE_TYPES or tt in ARTIFACT_NODE_TYPES:
            return 'association'
        if dashed and 'intermediateMessageEvent' in (st, tt):
            return 'messageFlow'
        return 'sequenceFlow'

    edges: List[ProcessEdge] = []
    snap_targets = flow_nodes + lanes
    #: Связи, у которых конец висел в пустоте и был притянут к фигуре.
    snapped_edge_ids: List[str] = []

    for cell in raw_edges:
        s_id = cell.get('source')
        t_id = cell.get('target')

        edge_geo = cell.find('mxGeometry')
        edge_ox, edge_oy = _parent_origin(cell.get('parent'), cell_map, origin_cache)
        free_ends: Dict[str, Tuple[float, float]] = {}
        if edge_geo is not None:
            for mx in edge_geo.findall('mxPoint'):
                role = mx.get('as') or ''
                if role not in ('sourcePoint', 'targetPoint'):
                    continue
                try:
                    free_ends[role] = (
                        float(mx.get('x', '0') or 0) + edge_ox,
                        float(mx.get('y', '0') or 0) + edge_oy,
                    )
                except ValueError:
                    continue

        # Конец без привязки — притягиваем к ближайшей фигуре под ним, но не к
        # той, на которой уже стоит второй конец: у линии, оба конца которой
        # висят в пустоте рядом, иначе получалась петля из фигуры в себя.
        if not s_id:
            s_id = _resolve_free_endpoint(free_ends.get('sourcePoint'), snap_targets, t_id)
            if s_id:
                snapped_edge_ids.append(cell.get('id') or '')
        if not t_id:
            t_id = _resolve_free_endpoint(free_ends.get('targetPoint'), snap_targets, s_id)
            if t_id and (cell.get('id') or '') not in snapped_edge_ids:
                snapped_edge_ids.append(cell.get('id') or '')

        def _known(node_id: Optional[str]) -> bool:
            return bool(node_id) and (node_id in valid_node_ids or node_id in valid_lane_ids)

        s_known, t_known = _known(s_id), _known(t_id)
        if not s_known:
            s_id = None
        if not t_known:
            t_id = None
        # Линия без единой опоры на фигуру — не связь и не оформление, а мусор.
        if not s_known and not t_known:
            continue
        # Оформительская линия: один конец висит в пустоте. Рисуем, но в
        # выгрузку BPMN/PIX не отдаём — такой конструкции там нет.
        is_annotation_line = not (s_known and t_known)
        if is_annotation_line and not free_ends:
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
            kind='annotationLine' if is_annotation_line else _edge_kind(s_id, t_id, is_dashed),
            sourceId=s_id,
            targetId=t_id,
            condition=edge_name or None,
            points=pts,
            sourcePoint=(
                ProcessEdgePoint(x=int(round(free_ends['sourcePoint'][0])), y=int(round(free_ends['sourcePoint'][1])))
                if 'sourcePoint' in free_ends else None
            ),
            targetPoint=(
                ProcessEdgePoint(x=int(round(free_ends['targetPoint'][0])), y=int(round(free_ends['targetPoint'][1])))
                if 'targetPoint' in free_ends else None
            ),
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
                is_gw = s.type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')
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

    # Связи разобраны и притянуты к фигурам — только теперь у события известна
    # его настоящая степень, а значит и тип.
    reclassify_events(flow_nodes, edges)
    completed_branches = complete_binary_gateway_conditions(flow_nodes, edges)
    badge_step_ids = _apply_duration_badges(flow_nodes, duration_badges)
    _resolve_artifact_links(flow_nodes, edges)
    # Геометрию правим до сбора замечаний: часть из них про размеры фигур.
    layout_report = normalize_layout(flow_nodes, lanes)
    timed_step_ids |= badge_step_ids

    title = filename.replace('.drawio', '').replace('.xml', '')
    task_nodes = [n for n in flow_nodes if n.type in TASK_NODE_TYPES]
    total_hours = round(
        sum((n.slaMinutes or 0) + (n.waitMinutes or 0) for n in task_nodes) / 60, 1
    ) or 8.0

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

    # Идентификаторы связей живут в том же пространстве, что и фигуры: в .pmm
    # `id` связи и `id` фигуры лежат в одной карте, а в BPMN оба попадают в
    # xsd:ID. Совпадение здесь так же ломает импорт, как два одинаковых шага.
    taken_ids: Set[str] = {n.id for n in nodes}
    for e in edges:
        if e.id not in taken_ids:
            taken_ids.add(e.id)
            continue
        suffix = 2
        while f'{e.id}__dup{suffix}' in taken_ids:
            suffix += 1
        e.id = f'{e.id}__dup{suffix}'
        taken_ids.add(e.id)

    page_used, pages_skipped = page_report(content)
    validations = collect_import_diagnostics(
        flow_nodes, lanes, edges,
        pages_skipped=pages_skipped,
        page_used=page_used,
        snapped_edges=snapped_edge_ids,
        layout_report=layout_report,
        timed_step_ids=timed_step_ids,
        unsupported_shapes=unsupported,
        skipped_clipart=skipped_clipart,
        completed_branches=completed_branches,
    )

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
