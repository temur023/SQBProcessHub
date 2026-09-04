"""PIX Process Studio native package (.pmm = ZIP of three XML parts).

Структура пакета воспроизводит выгрузку самой PIX Процессной студии:

    main.xml               — манифест частей (<Types><Override .../></Types>)
    pm/configuration.xml   — каталог свойств и нотаций студии
    pm/maps/<slug>.xml     — сама карта (<Map> с <node> и <connector>)

Ключевые соглашения формата, сверенные с выгрузкой самой студии
(``tests/fixtures/sap.pmm`` — карта, сделанная и сохранённая в PIX):

* узлы внутри дорожки (`horizontalRoad`) позиционируются ОТНОСИТЕЛЬНО дорожки,
  сама дорожка — в абсолютных координатах карты;
* подпись связи хранится в атрибуте ``Text``, а не ``label``
  (``label`` студия игнорирует — подписи шлюзов теряются);
* список ``waypoint`` — это ПОЛНАЯ ломаная, включая точки на границе исходного
  и целевого узла, а не только промежуточные изломы; ломаную задаём для каждой
  связи — без неё студия трассирует сама и на плотной карте кладёт линии
  поверх соседних;
* ``sourcePoint``/``targetPoint`` — необязательные индексы якорей: задаём их
  для тех граней, чей номер эталон называет однозначно, для остальных
  опускаем, и студия выбирает точку примыкания сама (в своей выгрузке она
  опускает ``sourcePoint`` у 30 связей из 50);
* стиль линии — атрибут ``lineStyle`` у самой связи, а не дочерний элемент:
  дочернего ``<lineStyle>`` в выгрузке студии нет ни разу;
* маркеры концов берутся парой к стилю линии (см. ``_line_decoration``):
  незнакомый маркер студия отбрасывает вместе со связью.

В отличие от BPMN-выгрузки, здесь НЕ применяется нормализация степеней
событий: ``.pmm`` — это рисунок карты, и узел должен выглядеть так, как его
нарисовал аналитик. Валидную модель даёт экспорт в ``.bpmn``.
"""
from __future__ import annotations

import io
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.process import (
    ARTIFACT_NODE_TYPES,
    GATEWAY_NODE_TYPES,
    TASK_NODE_TYPES,
    BusinessProcess,
    Geometry,
    ProcessEdge,
    ProcessNode,
)
from app.services.bpmn_exporter import (
    map_label,
    split_external_lanes,
    step_duration_text,
)
from app.services.layout import _free_space, wrapped_line_count
from app.services.edge_routing import (
    Corridors,
    Obstacles,
    build_obstacles,
    message_flow_endpoints,
    orthogonal_waypoints,
)

_NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_NS_XSD = 'http://www.w3.org/2001/XMLSchema'
_PIX_NS = uuid.UUID('8b2e0c5a-4d71-4f3a-9c1e-6a7f0d2b9e11')

_CONFIGURATION_PATH = Path(__file__).resolve().parent.parent / 'resources' / 'pix_configuration.xml'

#: Элемент нотации, по которому опознаётся набор BPMN в каталоге студии.
_BPMN_PROBE_ELEMENT = 'gateway_xor'
#: Куда падает тип, которого в каталоге студии не оказалось.
_FALLBACK_ELEMENT = 'task'
#: Как саму нотацию подписывает студия в атрибуте ``<Map notation="…">``.
#: В каталоге она объявлена как ``BPMN``, но в собственной выгрузке студии
#: (``tests/fixtures/sap.pmm``) стоит строчное ``bpmn`` — пишем ровно так же,
#: чтобы не расходиться с эталоном на первом же атрибуте карты.
_MAP_NOTATION = 'bpmn'


@lru_cache(maxsize=1)
def bpmn_notation() -> Tuple[str, frozenset]:
    """Каноническое имя BPMN-нотации в каталоге студии и её пригодные элементы.

    Имя берём из самого каталога, а не пишем константой: по нему ищутся
    типы фигур, и незнакомый тип валит импорт целиком («Notation element not
    found (Parameter 'type')»). В карту, однако, уезжает не оно, а
    ``_MAP_NOTATION``: регистр имени студия не различает, а пишет строчными.

    ПОЧЕМУ НЕ ВСЕ ЭЛЕМЕНТЫ ПОДРЯД. Пригодным считается только тот, у которого в
    каталоге проставлена категория (``type``): «Задачи», «Шлюзы», «События»…
    В нотации BPMN её нет ровно у одного элемента из 91 — ``input``
    («Текст»). Студия, встретив такую фигуру, идёт за её категорией, не
    находит и падает с сообщением, где параметр так и назван — ``'type'``.
    Отсюда и правило: имени в каталоге мало, нужна категория.
    """
    root = ET.fromstring(_CONFIGURATION_PATH.read_text(encoding='utf-8'))
    for notation in root.iter('notation'):
        names = {e.get('name') for e in notation.findall('element') if e.get('name')}
        if _BPMN_PROBE_ELEMENT not in names:
            continue
        usable = {
            e.get('name') for e in notation.findall('element')
            if e.get('name') and e.get('type')
        }
        return notation.get('name') or 'BPMN', frozenset(usable)
    raise ValueError('В каталоге PIX нет нотации BPMN')


def notation_categories(config: ET.Element, notation: str) -> Dict[str, str]:
    """Категория каждого элемента нотации («Задачи», «Шлюзы», «Участники»…).

    Каталог студии делит элементы на группы, и по группе видно, чему положено
    содержать вложенные фигуры: дорожка и пул — «Участники».
    """
    return {
        e.get('name'): e.get('type') or ''
        for n in config.iter('notation') if n.get('name') == notation
        for e in n.findall('element') if e.get('name')
    }


def pix_element(kind: str) -> str:
    """Тип фигуры, который студия точно знает и умеет разложить по категориям.

    Незнакомый тип валит импорт всего пакета, поэтому лучше отдать обычную
    задачу: карта откроется, а о подмене аналитик уже предупреждён отчётом
    о качестве импорта. Тип без категории в каталоге (см. ``bpmn_notation``)
    незнакомому равносилен — студия спотыкается на нём так же.
    """
    return kind if kind in bpmn_notation()[1] else _FALLBACK_ELEMENT

#: Ширина заголовочной плашки карты; в эталоне PIX — 1988 px.
_TITLE_POOL_WIDTH = 1988
_TITLE_POOL_HEIGHT = 90

_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h', 'і': 'i', 'ї': 'i', 'є': 'e',
}


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


def _pix_id(raw: str) -> str:
    return str(uuid.uuid5(_PIX_NS, raw or 'node'))


def transliterate(text: str) -> str:
    """Кириллица -> латиница, чтобы имя карты оставалось читаемым в студии.

    Имена файлов из Telegram/macOS приходят в разложенном виде (NFD): «ў» —
    это «у» + комбинирующая бреве. Без нормализации диакритика превращалась бы
    в подчёркивание посреди слова.
    """
    text = unicodedata.normalize('NFC', text or '')
    out: List[str] = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        lower = ch.lower()
        if lower in _TRANSLIT:
            mapped = _TRANSLIT[lower]
            out.append(mapped.upper() if ch.isupper() and mapped else mapped)
        else:
            out.append(ch)
    return ''.join(out)


def map_slug(process: BusinessProcess) -> str:
    """Имя карты: человекочитаемое имя процесса, а не служебный код паспорта.

    Имя части в ZIP и атрибут ``Map name`` совпадают — как в выгрузке PIX.
    """
    for candidate in (process.passport.name, process.name, process.passport.code):
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', transliterate(candidate or '')).strip('_')
        slug = re.sub(r'_{2,}', '_', slug)[:60].strip('_')
        if len(slug) >= 3:
            return slug
    return 'map'


def pix_type(node: ProcessNode) -> str:
    """Тип узла в словаре нотации BPMN Процессной студии (pm/configuration.xml).

    Результат обязательно сверяется с каталогом: студия не открывает пакет
    целиком, если встретит хоть один тип, которого в её нотации нет.
    """
    return pix_element(_pix_type_raw(node))


def _pix_type_raw(node: ProcessNode) -> str:
    style = (node.style or '').lower()
    kind = node.type

    if kind == 'startEvent':
        if 'symbol=timer' in style:
            return 'start_event_timer'
        if 'symbol=message' in style:
            return 'start_event_message'
        return 'start_event_none'
    if kind == 'endEvent':
        if 'terminate' in style:
            return 'end_event_terminate'
        if 'symbol=message' in style:
            return 'end_event_message'
        return 'end_event_none'
    if kind == 'intermediateTimerEvent':
        return 'intermediate_event_catch_timer'
    if kind == 'intermediateMessageEvent':
        return 'intermediate_event_catch_message'
    if kind == 'exclusiveGateway':
        return 'gateway_xor'
    if kind == 'parallelGateway':
        return 'gateway_parallel'
    if kind == 'inclusiveGateway':
        return 'gateway_or'
    if kind == 'complexGateway':
        return 'gateway_complex'
    if kind == 'subProcess':
        return 'sub_process'
    if kind == 'dataStore':
        return 'dataStorage'
    if kind == 'dataObject':
        return 'dataObject'
    if kind == 'textAnnotation':
        # Точного двойника примечанию в нотации студии нет: элемента-выноски
        # там не объявлено вовсе. Элемент «Текст» (``input``) подошёл бы по
        # смыслу, но он единственный в каталоге BPMN без категории и валит
        # импорт всего пакета.
        #
        # Из того, что есть, ближе всего к коробочке из draw.io «Объект
        # данных»: рамка с загнутым углом и текстом внутри. «Группа»
        # (``group_none``) рисуется квадратной скобкой — вид примечания по
        # стандарту BPMN, но на исходную карту не похож.
        return 'dataObject'
    # Все действия — плоская «Задача». Студия рисует у пользовательской задачи
    # человечка, у сервисной — шестерёнку, и рисует их В ЛЕВОМ ВЕРХНЕМ УГЛУ,
    # прямо поверх первых букв подписи: «1.Eksport akkreditivi…» превращается
    # в «👤Eksport akkreditivi…». В draw.io, который для нас эталон вида карты,
    # у шагов иконок нет вовсе — только рамка с текстом.
    #
    # Различие «ручная / роботизированная» при этом не теряется: оно едет в
    # регламент и в выгрузку BPMN, где тип шага задан элементом, а не значком.
    return 'task'


# ── Время шага в свойствах фигуры ───────────────────────────────────────────
#
# Студия хранит время не подписью на карте, а свойством фигуры: дочерний узел
# ``<Properties>`` внутри ``<node>``, значения — в формате .NET ``TimeSpan``.
# Пока платформа его не писала, панель свойств шага показывала «000д 00ч 00м
# 00с» даже там, где на карте стояли часы с цифрой.

#: Множитель к минутам для каждой единицы, какой её пишут аналитики банка.
#: Языка три (русский, узбекский, английский), и сокращают все по-своему.
_TIME_UNITS: Tuple[Tuple[Tuple[str, ...], float], ...] = (
    (('d', 'day', 'days', 'дн', 'день', 'дня', 'дней', 'сут', 'сутки', 'kun'), 1440.0),
    (('h', 'hr', 'hrs', 'hour', 'hours', 'ч', 'час', 'часа', 'часов', 'soat'), 60.0),
    (('m', 'min', 'mins', 'minute', 'minutes',
      'м', 'мин', 'минут', 'минута', 'минуты', 'daq', 'daqiqa'), 1.0),
    (('s', 'sec', 'secs', 'second', 'seconds', 'с', 'сек', 'секунд', 'soniya'), 1.0 / 60.0),
)

#: Число с необязательной дробной частью и разделителем тысяч, следом —
#: необязательная единица. «15», «15m», «1.5h», «1 440 мин», «2 ч».
_TIME_TOKEN_RE = re.compile(
    r'(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)?)\s*'
    r'([A-Za-zА-Яа-яЁё\u2018\u2019\'`]*)',
    re.IGNORECASE,
)


def _unit_factor(unit: str) -> Optional[float]:
    """Множитель единицы к минутам. Пустая единица — это минуты."""
    cleaned = unit.strip().strip('.').lower()
    if not cleaned:
        return 1.0
    for names, factor in _TIME_UNITS:
        if cleaned in names:
            return factor
    return None


def parse_duration_minutes(raw: Optional[str]) -> Optional[float]:
    """Длительность из произвольной строки в минутах, или ``None``.

    Понимает то, что аналитики действительно пишут в draw.io: голое число
    (это минуты), число с единицей на любом из трёх языков, дробное значение
    («1.5h», «1,5 ч») и разделитель тысяч («1 440 мин»). Составные записи
    вида «1 ч 30 мин» складываются.

    ``None`` возвращается на всём, что разобрать нельзя, — тег со значением
    «мусор» в файл уходить не должен.
    """
    if raw is None:
        return None
    text = str(raw).replace('\u00a0', ' ').replace('\u202f', ' ').strip()
    if not text:
        return None
    # Отрицательная длительность — это не «минус пять минут», а опечатка.
    # Знак стоит вне числа, и без этой проверки он просто отбрасывался.
    if '-' in text or '\u2212' in text:
        return None

    total = 0.0
    matched = False
    for match in _TIME_TOKEN_RE.finditer(text):
        number, unit = match.group(1), match.group(2)
        factor = _unit_factor(unit)
        if factor is None:
            # Единица есть, но незнакомая: это не длительность, а что-то
            # другое («3 shtuk»). Молча считать её минутами нельзя.
            return None
        value = float(number.replace(' ', '').replace(',', '.'))
        total += value * factor
        matched = True
    if not matched or total <= 0:
        return None
    return total


def minutes_to_timespan(minutes: Optional[float]) -> Optional[str]:
    """Минуты в .NET ``TimeSpan``, или ``None``, если времени нет.

    Формат «hh:mm:ss», а от суток и больше — «d.hh:mm:ss». Это не украшение:
    ``TimeSpan.Parse("24:00:00")`` в .NET падает, часы обязаны лежать в 0..23,
    а сутки выносятся отдельным полем через точку. Сейчас в картах банка
    максимум 960 минут, но правило должно быть верным и для более длинных.
    """
    if minutes is None:
        return None
    total_seconds = int(round(float(minutes) * 60))
    if total_seconds <= 0:
        return None
    days, rest = divmod(total_seconds, 86400)
    hours, rest = divmod(rest, 3600)
    mins, secs = divmod(rest, 60)
    stamp = f'{hours:02d}:{mins:02d}:{secs:02d}'
    return f'{days}.{stamp}' if days else stamp


def parse_time_to_timespan(raw: Optional[str]) -> Optional[str]:
    """Сырая строка из draw.io -> .NET ``TimeSpan``, или ``None``."""
    return minutes_to_timespan(parse_duration_minutes(raw))


#: Как свойства времени называются в файле карты.
#:
#: Имена — не наши, а из каталога студии ``pix_configuration.xml``: панель
#: свойств шага строится по нему, и ключ, которого в каталоге нет, приезжает
#: «теневым атрибутом» — файл открывается, значение хранится, а в интерфейсе
#: его не видно. Имён ``ProcessingTime`` / ``WaitingTime`` в каталоге нет ни
#: одного, поэтому написанное так время до панели не доходило.
#:
#: Шаблонов времени в каталоге три, и «Время процесса» из них ДВА — с одной
#: подписью, но разной ролью:
#:
#:     vremya_protsessa     Время процесса    обычный шаблон -> «Основные»
#:     system_process_time  Время процесса    defaultProperty -> «Системные»
#:     vremya_ozhidaniya    Время ожидания    обычный шаблон -> «Основные»
#:
#: Пишем оба «Время процесса» с одним значением: разделы панели разные, и
#: заполнить надо каждый. Дубли по имени каталог допускает и сам —
#: ``attached_files`` и ``document`` объявлены в нём по два раза точно так же.
#: Времени ожидания системного двойника не существует, поэтому оно одно.
_PROCESSING_TIME_PROPERTIES = ('vremya_protsessa', 'system_process_time')
_WAITING_TIME_PROPERTIES = ('vremya_ozhidaniya',)


def node_properties_xml(node: ProcessNode, indent: str) -> List[str]:
    """Блок ``<Properties>`` фигуры: время выполнения и время ожидания.

    Свойства получают только те фигуры, у которых время что-то значит.

    У шага это время операции (ST) и ожидания перед ней (WT) — ровно то, что
    аналитик замерял. У события-ожидания время одно, и оно по смыслу и есть
    ожидание: «Kutish vaqti 15 min» — это не работа, а пауза в потоке.
    Остальным фигурам свойство не пишется вовсе: у стартового события и шлюза
    ``slaMinutes`` — значение по умолчанию из модели, а не замер, и выгружать
    его значило бы выдать умолчание за факт.

    Пустой блок не пишется: фигура без времени остаётся самозакрывающейся, как
    в выгрузке самой студии.
    """
    if node.type in TASK_NODE_TYPES:
        if not node.slaMeasured:
            # Времени у шага не было и в draw.io: подставленное импортом в
            # панель свойств не идёт — иначе догадка выглядит замером.
            return []
        durations = (
            (_PROCESSING_TIME_PROPERTIES, minutes_to_timespan(node.slaMinutes)),
            (_WAITING_TIME_PROPERTIES, minutes_to_timespan(node.waitMinutes)),
        )
    elif node.type == 'intermediateTimerEvent':
        durations = (
            (_WAITING_TIME_PROPERTIES,
             minutes_to_timespan(node.waitMinutes or node.slaMinutes)),
        )
    else:
        return []
    present = [
        (name, value)
        for names, value in durations if value
        for name in names
    ]
    if not present:
        return []
    lines = [f'{indent}  <Properties>']
    for name, value in present:
        lines.append(
            f'{indent}    <Property name="{escape_xml(name)}" '
            f'value="{escape_xml(value)}" />'
        )
    lines.append(f'{indent}  </Properties>')
    return lines


#: Куда студия кладёт подпись шлюза.
#:
#: В эталонной выгрузке студии стоит ``Left``, и платформа повторяла её. На
#: плотной карте банка это не работает: слева от ромба стоит предыдущий шаг, и
#: вопрос («Banknotalar muomalaga yaroqlimi») ложится прямо на его текст. В
#: draw.io аналитик ставит вопрос НАД ромбом — там свободно всегда, потому что
#: поток идёт по горизонтали.
#:
#: Оговорка честная: значение ``Top`` эталоном не подтверждено, в выгрузках
#: студии встречается только ``Left``. Проверяется пробой
#: ``test_08_pmm_label_placement_top.pmm``; если студия его не примет, здесь
#: достаточно вернуть ``Left``.
_GATEWAY_LABEL_PLACEMENT = 'Top'


def _node_extra(node: ProcessNode) -> str:
    if node.type in GATEWAY_NODE_TYPES:
        return f' labelPlacement="{_GATEWAY_LABEL_PLACEMENT}" font_size="16"'
    # Имя системы аналитик пишет НАД цилиндром — «iABS», «EHA». Сбоку оно
    # налезает на соседний шаг, и на карте не понять, к чему относится.
    if node.type in ('dataStore', 'dataObject'):
        return ' labelPlacement="Top"'
    return ''


def _node_xml(
    node_type: str,
    nid: str,
    label: str,
    x: int,
    y: int,
    w: int,
    h: int,
    extra: str = '',
    fill: str = 'var(--bg-accent-node)',
    indent: str = '  ',
    children: Sequence[str] = (),
) -> str:
    head = (
        f'{indent}<node type="{escape_xml(node_type)}" id="{escape_xml(nid)}"'
        f' label="{escape_xml(label)}" number="0"'
        f' x="{int(x)}" y="{int(y)}" width="{int(max(w, 8))}" height="{int(max(h, 8))}"'
        f' fill_color="{escape_xml(fill)}"{extra}'
    )
    if not children:
        return head + ' />'
    # Фигура со свойствами перестаёт быть самозакрывающейся. Без детей тег
    # закрываем сразу — так пишет и сама студия.
    return '\n'.join([head + '>', *children, f'{indent}</node>'])


# ── Канонические размеры фигур Процессной студии ────────────────────────────
#
# Сняты с выгрузки самой PIX (``tests/fixtures/sap.pmm``), где 66 фигур и на
# каждый тип ровно один размер: события 48 × 48, шлюзы 60 × 60, хранилище
# данных 62 × 56. Подробнее о том, зачем приводить к ним свои, — в
# ``normalize_geometry``.
_EVENT_SIDE = 48
_GATEWAY_SIDE = 60
_DATA_STORAGE_SIZE = (62, 56)

#: Префикс имени элемента -> его канонический размер.
_CANONICAL_BY_PREFIX: Tuple[Tuple[str, Tuple[int, int]], ...] = (
    ('start_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('end_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('intermediate_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('boundary_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('boundary_non_interrupting_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('start_interrupting_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('start_non_interrupting_event', (_EVENT_SIDE, _EVENT_SIDE)),
    ('gateway_', (_GATEWAY_SIDE, _GATEWAY_SIDE)),
)


#: Диаметр значка длительности на карте PIX, px.
#:
#: Тот же, что у остальных событий (``_EVENT_SIDE``), и это не косметика:
#: студия рисует событие своим шаблоном в 48 px независимо от того, какую
#: рамку мы объявили. Прежние 24 px значили, что она дорисовывает кружок вдвое
#: больше объявленного — и он наезжал на подпись шага.
_DURATION_SIDE = _EVENT_SIDE

#: Насколько центр значка отступает от правого края шага, px. Подобран так,
#: чтобы кружок в 48 px не вылезал за правую грань: 28 − 24 = 4 px запаса.
_DURATION_INSET = 28

#: Элемент значка длительности в каталоге студии.
#:
#: Раньше здесь стоял ``intermediate_event_catch_timer`` — «промежуточное
#: событие-таймер». Формально такой узел обязан стоять В потоке, между двумя
#: шагами, поэтому студия на каждом значке писала «У элемента отсутствует
#: входящий поток управления» и столько же раз — про исходящий. На карте из
#: 279 шагов это давало под шестьсот замечаний, за которыми не было видно
#: настоящих: список «Проблемы» упирался в «99+».
#:
#: Значок длительности потоком и не является: он помечает время шага, а не
#: ожидание между шагами. Ровно для этого в BPMN есть непрерывающее граничное
#: событие — оно живёт на границе активности и связей не требует. В выгрузке
#: BPMN платформа так его и пишет (``cancelActivity="false"``), а здесь
#: расходилась с собой. Теперь тип общий для обоих форматов.
_DURATION_ELEMENT = 'boundary_non_interrupting_event_timer'


def duration_badge_origin(
    node: ProcessNode,
    x: float,
    y: float,
    lane: Optional[ProcessNode] = None,
) -> Tuple[int, int]:
    """Левый верхний угол значка длительности.

    Вынесено отдельно, потому что считать это место надо дважды и обязательно
    одинаково: один раз — чтобы нарисовать значок, второй — чтобы трассировщик
    знал о нём как о препятствии. Пока знал только рисовальщик, линии шли прямо
    сквозь часы: из 90 пересечений с чужими фигурами 68 приходилось на них.
    """
    half = _DURATION_SIDE // 2
    # Узкий шаг значком не разрезать пополам: у него часы встают по центру
    # нижней грани, у обычного — в правом нижнем углу.
    offset = max(node.geometry.width - _DURATION_INSET, node.geometry.width / 2)
    mx = int(round(x + offset - half))
    # Значок ставим ЦЕЛИКОМ под шагом, а не по его нижней грани. Раньше кружок
    # сидел на границе, и верхняя его половина уходила под заливку шага: в
    # студии от значка была видна только нижняя дуга. Замер по двенадцати
    # картам банка: пересечений с линиями и фигурами было 1016, стало 283.
    my = int(round(y + node.geometry.height))
    if lane is not None:
        mx = max(0, min(mx, max(lane.geometry.width, 80) - _DURATION_SIDE))
        my = max(0, min(my, max(lane.geometry.height, 80) - _DURATION_SIDE))
    return mx, my


def duration_badge_boxes(
    flow: Sequence[ProcessNode],
    placed: Dict[str, Tuple[int, int]],
) -> List[Tuple[str, str, Tuple[float, float, float, float]]]:
    """Рамки значков длительности: ключ, шаг-хозяин, абсолютная рамка."""
    boxes: List[Tuple[str, str, Tuple[float, float, float, float]]] = []
    for node in flow:
        if node.type not in TASK_NODE_TYPES or not step_duration_text(node):
            continue
        ox, oy = placed.get(node.id, (node.geometry.x, node.geometry.y))
        # Значок кладём по абсолютным координатам: зажатие в границы дорожки
        # уже учтено в ``placed``.
        mx, my = duration_badge_origin(node, ox, oy)
        boxes.append((f'duration:{node.id}', node.id,
                      (float(mx), float(my), float(_DURATION_SIDE), float(_DURATION_SIDE))))
    return boxes


def duration_node_xml(
    node: ProcessNode,
    x: float,
    y: float,
    indent: str,
    lane: Optional[ProcessNode] = None,
) -> Optional[str]:
    """Часы со временем шага — непрерывающее граничное событие под ним.

    Длительность показывается так же, как её рисует аналитик в draw.io: часы у
    правого нижнего угла шага, время подписью под ними. Связей у значка нет и
    быть не должно: граничное событие помечает время самой активности, а не
    переход между активностями. В BPMN-выгрузке это же время едет тем же самым
    непрерывающим граничным таймером — форматы не должны расходиться.

    ``x``/``y`` — координаты шага в той же системе, в которой пишется его
    собственный ``<node>``: относительные внутри дорожки и абсолютные вне её.
    Значок шага у самого низа дорожки зажимается в её границы: вылезший за
    край узел студия рисует поверх соседней дорожки.
    """
    if node.type not in TASK_NODE_TYPES:
        return None
    text = step_duration_text(node)
    if not text:
        return None
    mx, my = duration_badge_origin(node, x, y, lane)
    return _node_xml(
        pix_element(_DURATION_ELEMENT),
        _pix_id(f'duration:{node.id}'),
        text,
        mx,
        my,
        _DURATION_SIDE,
        _DURATION_SIDE,
        indent=indent,
    )


# ── Канонические размеры фигур Процессной студии ────────────────────────────
#
# Сняты с выгрузки самой PIX (``tests/fixtures/sap.pmm``), где 66 фигур и на
# каждый тип ровно один размер:
#
#     события (start/end/intermediate)   48 × 48
#     шлюзы                              60 × 60
#     хранилище данных                   62 × 56
#     задача                            156 × 108 и 180 × 108
#
# ЗАЧЕМ ПРИВОДИТЬ К НИМ СВОИ. В draw.io аналитик рисует фигуры на глаз: в
# двенадцати картах банка событие встречается семи размеров (40, 44, 46, 50,
# 55, 56), шлюз — 50 × 50 против канонических 60 × 60, цилиндр базы — 80 × 40
# вместо 62 × 56. Студия рисует фигуру своим шаблоном, а линию тянет к рамке,
# которую мы объявили. Пока рамка и шаблон расходятся на десять пикселей,
# стрелка не доходит до фигуры или, наоборот, въезжает внутрь неё — ровно то,
# на что жалуется аналитик, глядя на импортированную карту.
#
# Приводим только те фигуры, у которых размер задан шаблоном и менять его
# бессмысленно: события, шлюзы, хранилище. Задачу и дорожку оставляем как
# нарисовано — их размер несёт смысл (длина подписи, набор шагов внутри).
def canonical_size(element: str) -> Optional[Tuple[int, int]]:
    """Размер, которым студия рисует фигуру этого типа, или ``None``.

    ``None`` значит «размер задаёт аналитик»: задача, подпроцесс, дорожка и
    артефакты.

    Хранилище данных приводилось к 62 × 56 — размеру из выгрузки студии. На
    картах банка цилиндр нарисован вдвое мельче (30 × 25, 40 × 40, 30 × 40), и
    канон делал его заметнее шага, к которому он относится: пунктир от него
    упирался шагу прямо в рамку. Размер фигур с фиксированным ШАБЛОНОМ —
    событий и шлюзов — приводить по-прежнему нужно: там аналитик рисует на
    глаз и одно событие встречается семи размеров.
    """
    for prefix, size in _CANONICAL_BY_PREFIX:
        if element.startswith(prefix):
            return size
    return None


def normalize_geometry(process: BusinessProcess) -> BusinessProcess:
    """Копия процесса, где фигуры с фиксированным шаблоном приведены к канону.

    Размер меняется вокруг ЦЕНТРА фигуры, а не левого верхнего угла: иначе
    схема поедет — шлюз, выросший с 50 до 60, сдвинул бы линию, которая к нему
    подходит, и раскладка аналитика перестала бы совпадать с исходной картой.

    Правка делается один раз и до всего остального, поэтому и сами фигуры, и
    концы ломаных, и значки длительности считаются от одной геометрии. Раньше
    ломаная строилась по рамке из draw.io, а студия рисовала фигуру своим
    шаблоном — линия и фигура расходились.
    """
    changed: List[ProcessNode] = []
    for node in process.nodes:
        size = canonical_size(pix_type(node)) if node.type != 'lane' else None
        geo = node.geometry
        if size is None or (geo.width, geo.height) == size:
            changed.append(node)
            continue
        width, height = size
        copy = node.model_copy(deep=True)
        copy.geometry = Geometry(
            x=int(round(geo.x + geo.width / 2 - width / 2)),
            y=int(round(geo.y + geo.height / 2 - height / 2)),
            width=width,
            height=height,
        )
        changed.append(copy)
    if all(a is b for a, b in zip(changed, process.nodes)):
        return process
    normalized = process.model_copy()
    normalized.nodes = changed
    return normalized


#: Зазор, который оставляем между артефактом и шагом, px.
_ARTIFACT_CLEARANCE = 8


def _overlap(a: Geometry, b: Geometry) -> Tuple[float, float]:
    """Глубина взаимного проникновения двух рамок по каждой оси."""
    return (
        min(a.x + a.width, b.x + b.width) - max(a.x, b.x),
        min(a.y + a.height, b.y + b.height) - max(a.y, b.y),
    )


#: Кегль, которым Процессная студия печатает подпись шага.
#:
#: Снят с самой студии: в панели форматирования при выделенном шаге стоит 14.
#: На холсте платформы и в draw.io — 12, и разница ровно в этом: рамку рисовал
#: аналитик под мелкий шрифт, а студия печатает крупнее. На двенадцати картах
#: банка при кегле 14 подпись не помещается у 106 шагов из 810 — текст вылезает
#: за рамку и ложится на соседние фигуры и линии.
_PIX_FONT_SIZE = 14.0

#: Насколько шаг разрешено раздвинуть, чтобы вместить подпись. Дальше растить
#: бессмысленно: карта аналитика поедет сильнее, чем выиграет читаемость.
_MAX_FIT_WIDTH = 300
_MAX_FIT_HEIGHT = 260


def _label_box_height(text: str, width: float) -> float:
    """Высота, которой хватит подписи в рамке заданной ширины."""
    lines = wrapped_line_count(text, width - 2 * _LABEL_PADDING, _PIX_FONT_SIZE)
    return lines * _PIX_FONT_SIZE * 1.25 + 2 * _LABEL_PADDING


#: Поля подписи внутри фигуры, px.
_LABEL_PADDING = 10.0


def _share(extra: float, before: float, after: float) -> Tuple[float, float]:
    """Сколько прибавить фигуре и насколько сдвинуть её начало.

    Рост делится между сторонами поровну, но ровно настолько, насколько с
    каждой стороны действительно свободно. Симметричный рост «на глаз»
    подставлял фигуру соседу: свободных ста пикселей справа не хватает, чтобы
    вырасти на пятьдесят влево, где соседний шаг стоит вплотную.
    """
    extra = max(0.0, extra)
    if extra <= 0:
        return 0.0, 0.0
    take_before = min(extra / 2, max(before, 0.0))
    take_after = min(extra - take_before, max(after, 0.0))
    take_before = min(extra - take_after, max(before, 0.0))
    return take_before + take_after, take_before


def fit_task_labels(process: BusinessProcess) -> BusinessProcess:
    """Расширяет шаги, у которых подпись не помещается в рамку студии.

    Растим сначала в ширину — строка становится длиннее, строк нужно меньше, — и
    только потом в высоту. И то и другое вокруг центра фигуры и лишь настолько,
    насколько рядом свободно: раздвинуть шаг, наехав им на соседний, значит
    поменять одну проблему на худшую.
    """
    tasks = [n for n in process.nodes if n.type in TASK_NODE_TYPES]
    if not tasks:
        return process
    others = [n for n in process.nodes if n.type != 'lane']
    lane_of = {lane.id: lane for lane in (process.lanes or [])}

    changed: Dict[str, Geometry] = {}
    for node in tasks:
        text = map_label(node)
        geo = node.geometry
        if not text or _label_box_height(text, geo.width) <= geo.height:
            continue

        lane = lane_of.get(node.laneId or '')
        before_x, after_x = _free_space(node, others, lane, 'x')
        grow_x, shift_x = _share(min(before_x + after_x, _MAX_FIT_WIDTH - geo.width),
                                 before_x, after_x)
        width = geo.width + grow_x
        need = _label_box_height(text, width)

        before_y, after_y = _free_space(node, others, lane, 'y')
        grow_y, shift_y = _share(
            min(max(need - geo.height, 0.0), before_y + after_y, _MAX_FIT_HEIGHT - geo.height),
            before_y, after_y)
        height = geo.height + grow_y
        if grow_x <= 0 and grow_y <= 0:
            continue
        changed[node.id] = Geometry(
            x=int(round(geo.x - shift_x)),
            y=int(round(geo.y - shift_y)),
            width=int(round(width)),
            height=int(round(height)),
        )

    if not changed:
        return process
    updated = process.model_copy()
    updated.nodes = [
        n.model_copy(update={'geometry': changed[n.id]}) if n.id in changed else n
        for n in process.nodes
    ]
    return updated


def separate_artifacts(process: BusinessProcess) -> BusinessProcess:
    """Отодвигает артефакты, налезшие на шаги, по кратчайшему пути.

    Хранилище данных студия рисует шаблоном 62 × 56, а в draw.io его рисуют
    плоским прямоугольником 80 × 40. После приведения к канону цилиндр стал на
    16 px выше и у полусотни фигур въехал в соседний шаг. Двигаем именно
    артефакт: он привязан к шагу пунктиром, и пара пикселей в сторону ничего
    не значит, тогда как шаг стоит в потоке — его положение аналитик выбирал
    осознанно, и трогать его нельзя.

    Сдвиг всегда по той оси, где проникновение меньше: так фигура отходит на
    минимальное расстояние и остаётся там, где её ищут глазами.
    """
    flow = [
        n for n in process.nodes
        if n.type != 'lane' and n.type not in ARTIFACT_NODE_TYPES
    ]
    artifacts = [n for n in process.nodes if n.type in ARTIFACT_NODE_TYPES]
    if not flow or not artifacts:
        return process

    moved: Dict[str, Geometry] = {}
    for art in artifacts:
        geo = art.geometry.model_copy()
        # Двух проходов хватает: артефакт отодвигается от ближайшего шага, и
        # только если попал в другой — ещё раз. Больше — уже перекладывание
        # карты, а не устранение нахлёста.
        for _ in range(2):
            worst = None
            for node in flow:
                ox, oy = _overlap(geo, node.geometry)
                if ox <= 0 or oy <= 0:
                    continue
                depth = min(ox, oy)
                if worst is None or depth > worst[0]:
                    worst = (depth, ox, oy, node)
            if worst is None:
                break
            _, ox, oy, node = worst
            if ox <= oy:
                centre_left = geo.x + geo.width / 2 < node.geometry.x + node.geometry.width / 2
                geo.x += int(round(-(ox + _ARTIFACT_CLEARANCE) if centre_left
                                   else ox + _ARTIFACT_CLEARANCE))
            else:
                above = geo.y + geo.height / 2 < node.geometry.y + node.geometry.height / 2
                geo.y += int(round(-(oy + _ARTIFACT_CLEARANCE) if above
                                   else oy + _ARTIFACT_CLEARANCE))
        if (geo.x, geo.y) != (art.geometry.x, art.geometry.y):
            moved[art.id] = geo

    if not moved:
        return process
    updated = process.model_copy()
    updated.nodes = [
        n.model_copy(update={'geometry': moved[n.id]}) if n.id in moved else n
        for n in process.nodes
    ]
    return updated


def clamp_into_lane(child: ProcessNode, lane: ProcessNode) -> Tuple[int, int]:
    """Координаты узла относительно дорожки, зажатые в её границы.

    Узел, отнесённый к дорожке по геометрии, может выступать за её край — в
    студии он отрисовался бы поверх соседней дорожки.
    """
    lane_w = max(lane.geometry.width, 80)
    lane_h = max(lane.geometry.height, 80)
    rel_x = child.geometry.x - lane.geometry.x
    rel_y = child.geometry.y - lane.geometry.y
    rel_x = max(0, min(rel_x, lane_w - child.geometry.width))
    rel_y = max(0, min(rel_y, lane_h - child.geometry.height))
    return int(rel_x), int(rel_y)


def polyline(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    obstacles: Optional[Obstacles] = None,
    corridors: Optional[Corridors] = None,
) -> List[Tuple[float, float]]:
    """Полная ломаная связи в абсолютных координатах карты.

    Ломаную задаём ВСЕГДА, а не только когда аналитик двигал изломы руками.
    Без waypoint студия трассирует связь сама, и на плотной карте банка это
    даёт то, на что жалуются аналитики: линии идут поверх соседних связей и
    сквозь чужие фигуры. В BPMN-выгрузке ломаная передаётся целиком, и там
    схема читается — здесь должно быть так же.

    ``placed`` — фактические абсолютные координаты узлов после зажатия в
    границы дорожки; концы ломаной обязаны лежать на них, а не на исходной
    геометрии draw.io.

    Ломаная строится ортогонально (`edge_routing`): связи в PIX имеют тип
    ``step``, и диагональные изломы в них выглядели бы чужеродно.
    """
    route = orthogonal_waypoints(edge, src, tgt, placed, obstacles, corridors)
    if len(route) >= 2:
        return route
    # Маршрут не построился (у связи нет обеих опор или они совпали по центру):
    # отдаём хотя бы прямой отрезок между центрами. Связь без единой точки
    # студия рисует по-своему, и на плотной карте это лишний пересекающий луч.
    if src is None or tgt is None:
        return []
    def _centre(node: ProcessNode) -> Tuple[float, float]:
        x, y = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
        return x + node.geometry.width / 2, y + node.geometry.height / 2
    start, end = _centre(src), _centre(tgt)
    return [start, end] if start != end else []


def _coord(value: float) -> str:
    return str(int(round(value)))


#: Оформление связи по её роду: стиль линии и маркеры концов. Снято с выгрузки
#: самой студии (``tests/fixtures/sap.pmm``), где встречаются ровно три
#: сочетания, и каждое отвечает своему понятию BPMN:
#:
#:     solid  + line   + arrowclosed — поток управления      (28 связей)
#:     dotted + line   + arrowLine   — ассоциация с артефактом (21)
#:     dashed + circle + arrowEmpty  — поток сообщений         (1)
#:
#: Писавшийся раньше ``arrow`` в выгрузке студии не встречается ни разу, а
#: незнакомый маркер она молча отбрасывает вместе со связью — пунктирные линии
#: до карты не доезжали именно поэтому.
_SEQUENCE_DECORATION = ('solid', 'line', 'arrowclosed')
_ASSOCIATION_DECORATION = ('dotted', 'line', 'arrowLine')
_MESSAGE_DECORATION = ('dashed', 'circle', 'arrowEmpty')

#: Индекс точки привязки к грани фигуры (``sourcePoint``/``targetPoint``).
#: Номера — не сторона света, а место фигуры в её собственном списке якорей,
#: и полностью этот список по одному эталону не восстанавливается: у грани их
#: несколько (для верхней встречаются и 1, и 17). Поэтому пишем только те, что
#: эталон подтверждает однозначно, а для остальных граней атрибут опускаем —
#: студия сама выберет точку примыкания, как делает и в своей выгрузке
#: (``sourcePoint`` там стоит лишь у 20 связей из 50).
#:
#: Сколько случаев за каждым номером в ``tests/fixtures/sap.pmm``:
#: источник — низ 0 (4), левая 6 (2); цель — левая 6 (9), верх 1 (7),
#: низ 3 (5), правая 4 (2).
_SOURCE_ANCHOR = {'bottom': 0, 'left': 6}
_TARGET_ANCHOR = {'top': 1, 'right': 4, 'bottom': 3, 'left': 6}

#: Насколько точка ломаной может отойти от грани и всё ещё считаться лежащей
#: на ней: ломаная округляется к целым и выравнивается по осям сдвигом на
#: пиксель (``edge_routing._snap_to_pixel_grid``).
_ANCHOR_TOLERANCE = 2.0


def _line_decoration(edge: ProcessEdge) -> Tuple[str, str, str]:
    """Стиль линии и маркеры её концов — по роду связи."""
    if edge.kind == 'messageFlow':
        return _MESSAGE_DECORATION
    if edge.kind == 'association' or edge.dashed:
        return _ASSOCIATION_DECORATION
    return _SEQUENCE_DECORATION


def _anchor_side(
    node: ProcessNode,
    point: Tuple[float, float],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[str]:
    """Грань фигуры, на которой лежит конец ломаной, или ``None``.

    Сторону определяем по той же ломаной, которая уезжает в файл: якорь,
    разошедшийся с нарисованной линией, хуже отсутствующего — студия увела бы
    связь к другой грани.
    """
    ox, oy = (placed or {}).get(node.id, (node.geometry.x, node.geometry.y))
    width, height = node.geometry.width, node.geometry.height
    if width <= 0 or height <= 0:
        return None
    px, py = point
    within_x = ox - _ANCHOR_TOLERANCE <= px <= ox + width + _ANCHOR_TOLERANCE
    within_y = oy - _ANCHOR_TOLERANCE <= py <= oy + height + _ANCHOR_TOLERANCE
    distance: Dict[str, float] = {}
    if within_y:
        distance['left'] = abs(px - ox)
        distance['right'] = abs(px - (ox + width))
    if within_x:
        distance['top'] = abs(py - oy)
        distance['bottom'] = abs(py - (oy + height))
    if not distance:
        return None
    side = min(distance, key=lambda k: distance[k])
    return side if distance[side] <= _ANCHOR_TOLERANCE else None


def _anchor_attrs(
    route: List[Tuple[float, float]],
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> str:
    """Атрибуты ``sourcePoint``/``targetPoint`` связи — те, что известны."""
    if len(route) < 2:
        return ''
    attrs = ''
    if src is not None:
        index = _SOURCE_ANCHOR.get(_anchor_side(src, route[0], placed))
        if index is not None:
            attrs += f' sourcePoint="{index}"'
    if tgt is not None:
        index = _TARGET_ANCHOR.get(_anchor_side(tgt, route[-1], placed))
        if index is not None:
            attrs += f' targetPoint="{index}"'
    return attrs


#: Где по умолчанию встаёт подпись ветки шлюза, % длины связи.
#:
#: Не по центру: у шлюза от одной точки расходится несколько линий, их середины
#: попадают в гущу схемы, и «Ha» с «Yo‘q» ложатся поверх соседних фигур.
#: У самого шлюза место свободно всегда — там подпись и читается, и по ней
#: сразу видно, к какой ветке она относится.
_GATEWAY_LABEL_POSITION = 20

#: Где встаёт подпись обычной связи, % длины. Столько же в выгрузке студии у
#: 42 связей из 50.
_DEFAULT_LABEL_POSITION = 50

#: Ближе к концам подпись не ставим: она налезла бы на саму фигуру.
_LABEL_POSITION_LIMITS = (5, 95)


def label_position(edge: ProcessEdge, source: Optional[ProcessNode]) -> int:
    """Доля длины связи, на которой стоит её подпись, в процентах.

    Место подписи аналитик выбирает сам, растаскивая надписи так, чтобы схема
    читалась; draw.io хранит выбор в ``labelX`` — доле от -1 (начало связи) до
    +1 (конец). Раньше выгрузка это выбрасывала и ставила всем подряд 50 %, то
    есть ровно в середину линии — там, где на плотной карте гуще всего фигур.
    Студия своё расположение подписей хранит так же и в собственной выгрузке
    пишет самые разные значения (3, 7, 10, 25, 32, 56, 80).
    """
    raw = edge.labelX
    if raw is not None and -1.0 <= raw <= 1.0:
        percent = int(round((raw + 1.0) * 50.0))
    elif source is not None and source.type in GATEWAY_NODE_TYPES:
        percent = _GATEWAY_LABEL_POSITION
    else:
        percent = _DEFAULT_LABEL_POSITION
    low, high = _LABEL_POSITION_LIMITS
    return max(low, min(high, percent))


#: Размер маркера сообщения на границе полосы внешнего участника.
_CONTACT_SIDE = _EVENT_SIDE


def contact_markers(
    edges: Sequence[ProcessEdge],
    lane_ids: Dict[str, ProcessNode],
    node_by_id: Dict[str, ProcessNode],
) -> Dict[str, Tuple[ProcessNode, ProcessNode]]:
    """Точки контакта с внешним участником: маркер сообщения на его полосе.

    ПОЧЕМУ НЕ ВЕСТИ ЛИНИЮ ПРЯМО В ПОЛОСУ. В собственных выгрузках студии нет
    НИ ОДНОЙ связи, упирающейся в дорожку или пул, — и не случайно: студия
    цепляет такую линию за центр фигуры, а полоса клиента тянется на всю ширину
    карты. Семнадцать пунктиров к «Mijoz» сходились в одну точку посреди схемы
    и сливались в жирную линию, из которой не разобрать ни одной связи.

    В draw.io линия оканчивается прямо на границе полосы, и там нарисован
    кружок-маркер сообщения. Повторяем это: на месте, где линия встречает
    полосу, ставим маркер и ведём связь к нему. Каждая линия получает свою
    точку — пучок распадается, а смысл («здесь процесс общается с клиентом»)
    остаётся ровно тем же.
    """
    out: Dict[str, Tuple[ProcessNode, ProcessNode]] = {}
    for edge in edges:
        lane_is_source = edge.sourceId in lane_ids
        if not lane_is_source and edge.targetId not in lane_ids:
            continue
        lane = lane_ids[edge.sourceId if lane_is_source else edge.targetId]
        peer = node_by_id.get(edge.targetId if lane_is_source else edge.sourceId)
        if peer is None:
            continue
        stub, _ = message_flow_endpoints(edge, peer, lane, True)
        cx = stub.geometry.x + stub.geometry.width / 2
        # Маркер прижимаем к той грани полосы, которая смотрит на шаг.
        peer_below = peer.geometry.y >= lane.geometry.y + lane.geometry.height / 2
        cy = (lane.geometry.y + lane.geometry.height) if peer_below else lane.geometry.y
        half = _CONTACT_SIDE / 2
        marker = ProcessNode(
            id=f'contact:{edge.id}',
            name='',
            type='intermediateMessageEvent',
            laneId=lane.id,
            geometry=Geometry(
                x=int(round(cx - half)),
                y=int(round(cy - half)),
                width=_CONTACT_SIDE,
                height=_CONTACT_SIDE,
            ),
        )
        out[edge.id] = (marker, lane)
    return out


def generate_map_xml(process: BusinessProcess) -> Tuple[str, str]:
    # Геометрия приводится к канону студии ДО всего остального: и фигуры, и
    # концы ломаных, и значки длительности обязаны считаться от одних рамок.
    # Порядок важен: сначала канон размеров, потом подгонка рамок под кегль
    # студии, и только затем разведение артефактов — оно должно считать уже
    # окончательные габариты шагов.
    process = separate_artifacts(fit_task_labels(normalize_geometry(process)))
    slug = map_slug(process)
    id_map: Dict[str, str] = {}
    flow = [n for n in process.nodes if n.type != 'lane']
    lanes = list(process.lanes or [])
    # Полоса без единого шага — внешний участник (клиент): она остаётся строкой
    # карты, но, в отличие от дорожки с шагами, пунктир к ней осмыслен и
    # выгружается связью, а не отбрасывается как оформление.
    _, external = split_external_lanes(lanes, [n for n in flow if n.laneId])
    node_by_id = {n.id: n for n in flow}

    # Точки контакта с внешним участником становятся полноценными фигурами
    # карты: они получают идентификатор, рисуются внутри своей полосы и
    # участвуют в трассировке как препятствия. Связь ведётся к ним, а не в
    # полосу — см. ``contact_markers``.
    contacts = contact_markers(
        [e for e in process.edges if e.kind != 'annotationLine'],
        {lane.id: lane for lane in external},
        node_by_id,
    )
    contact_of = {edge_id: marker for edge_id, (marker, _lane) in contacts.items()}
    flow = flow + list(contact_of.values())
    node_by_id.update({m.id: m for m in contact_of.values()})

    for n in flow + lanes:
        id_map[n.id] = _pix_id(n.id)

    lines: List[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        (
            f'<Map xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}" '
            f'name="{escape_xml(slug)}" notation="{_MAP_NOTATION}" '
            'paperEnabled="false" paperType="0">'
        ),
    ]

    # ── Заголовочная плашка карты ───────────────────────────────────────────
    title = process.passport.name or process.name or slug
    bounds_nodes = lanes or flow
    if bounds_nodes:
        min_x = min(n.geometry.x for n in bounds_nodes)
        min_y = min(n.geometry.y for n in bounds_nodes)
        # Плашка шире эталонной, если карта шире: узкий заголовок над картой
        # в 4600 px выглядит обрывком, а не шапкой схемы.
        title_width = max(
            _TITLE_POOL_WIDTH,
            max(n.geometry.x + n.geometry.width for n in bounds_nodes) - min_x,
        )
    else:
        min_x, min_y, title_width = 0, 120, _TITLE_POOL_WIDTH
    lines.append(
        _node_xml(
            'emptyPool',
            _pix_id(f'title:{process.id}'),
            title,
            min_x,
            min_y - (_TITLE_POOL_HEIGHT + 40),
            title_width,
            _TITLE_POOL_HEIGHT,
            extra=' font_size="28"',
        )
    )

    # ── Дорожки и их содержимое ─────────────────────────────────────────────
    # Фактическое абсолютное положение узла после зажатия в границы дорожки:
    # по нему строятся концы ломаных связей.
    placed: Dict[str, Tuple[int, int]] = {}
    assigned = set()
    for lane in lanes:
        children = [n for n in flow if n.laneId == lane.id]
        for n in children:
            assigned.add(n.id)
        lines.append(
            f'  <node type="horizontalRoad" id="{escape_xml(id_map[lane.id])}"'
            f' label="{escape_xml(lane.name)}" number="0"'
            f' x="{lane.geometry.x}" y="{lane.geometry.y}"'
            f' width="{max(lane.geometry.width, 80)}" height="{max(lane.geometry.height, 80)}"'
            f' fill_color="var(--bg-accent-road-node)">'
        )
        for n in children:
            rel_x, rel_y = clamp_into_lane(n, lane)
            placed[n.id] = (lane.geometry.x + rel_x, lane.geometry.y + rel_y)
            lines.append(
                _node_xml(
                    pix_type(n), id_map[n.id], map_label(n), rel_x, rel_y,
                    n.geometry.width, n.geometry.height, _node_extra(n), indent='    ',
                    children=node_properties_xml(n, '    '),
                )
            )
            marker = duration_node_xml(n, rel_x, rel_y, '    ', lane)
            if marker:
                lines.append(marker)
        lines.append('  </node>')

    for n in flow:
        if n.id in assigned:
            continue
        lines.append(
            _node_xml(
                pix_type(n), id_map[n.id], map_label(n), n.geometry.x, n.geometry.y,
                n.geometry.width, n.geometry.height, _node_extra(n),
                children=node_properties_xml(n, '  '),
            )
        )
        marker = duration_node_xml(n, n.geometry.x, n.geometry.y, '  ')
        if marker:
            lines.append(marker)

    # ── Связи ───────────────────────────────────────────────────────────────
    # Препятствия считаются один раз на карту и по УЖЕ размещённым координатам:
    # узлы внутри дорожки зажаты в её границы, и трассировать надо по тому, где
    # фигура окажется в студии, а не где она лежала в draw.io.
    obstacles = build_obstacles(flow, placed, extra=duration_badge_boxes(flow, placed))
    # Накопитель проложенных связей: каждая следующая знает, где прошли
    # предыдущие, и не ложится поверх них.
    corridors = Corridors()
    lane_ids = {lane.id for lane in lanes}
    external_lanes = {lane.id: lane for lane in external}
    for edge in process.edges:
        # Оформительские линии draw.io (разделители этапов) в карту PIX не идут.
        if edge.kind == 'annotationLine':
            continue
        if not edge.sourceId or not edge.targetId:
            continue
        if edge.sourceId not in id_map or edge.targetId not in id_map:
            continue
        # Петля из фигуры в саму себя: студия из-за одной такой линии
        # отказывается открыть всю карту целиком.
        if edge.sourceId == edge.targetId:
            continue

        src_node = node_by_id.get(edge.sourceId)
        tgt_node = node_by_id.get(edge.targetId)
        touches_lane = edge.sourceId in lane_ids or edge.targetId in lane_ids
        if touches_lane:
            # Связь с полосой-участником (клиент) остаётся на карте: это точка
            # контакта. Линия, упирающаяся в дорожку с шагами, — оформление.
            marker = contact_of.get(edge.id)
            if marker is None:
                continue
            lane_is_source = edge.sourceId in lane_ids
            if lane_is_source:
                src_node = marker
                source_key, target_key = marker.id, edge.targetId
            else:
                tgt_node = marker
                source_key, target_key = edge.sourceId, marker.id
            if src_node is None or tgt_node is None:
                continue
        else:
            source_key, target_key = edge.sourceId, edge.targetId

        line_style, marker_start, marker_end = _line_decoration(edge)
        label = (edge.name or edge.condition or '').strip()
        # Подпись связи в PIX — атрибут Text; label студия не читает.
        text_attr = f' Text="{escape_xml(label)}"' if label else ''

        route = polyline(edge, src_node, tgt_node, placed, obstacles, corridors)
        corridors.occupy(route)
        anchors = _anchor_attrs(route, src_node, tgt_node, placed)

        lines.append(
            f'  <connector id="{escape_xml(_pix_id(edge.id))}" type="step"{text_attr}'
            f' lineStyle="{line_style}"'
            f' sourceNodeId="{escape_xml(id_map[source_key])}"'
            f' targetNodeId="{escape_xml(id_map[target_key])}"{anchors}>'
        )
        lines.append(f'    <MarkerStart>{marker_start}</MarkerStart>')
        lines.append('    <MarkerMiddle />')
        lines.append(f'    <MarkerEnd>{marker_end}</MarkerEnd>')
        for index, (px, py) in enumerate(route):
            lines.append(f'    <waypoint x="{_coord(px)}" y="{_coord(py)}" index="{index}" />')
        lines.append(
            f'    <labelPosition>{label_position(edge, src_node)}</labelPosition>')
        lines.append('    <color>var(--fg-gray-primary)</color>')
        lines.append('    <fontSize>12</fontSize>')
        lines.append('    <fontBold>false</fontBold>')
        lines.append('    <fontItalic>false</fontItalic>')
        lines.append('    <fontUnderline>false</fontUnderline>')
        lines.append('    <fontStrikethrough>false</fontStrikethrough>')
        lines.append('  </connector>')

    lines.append('</Map>')
    return slug, '\n'.join(lines) + '\n'


def generate_configuration_xml() -> str:
    """Каталог свойств и нотаций студии.

    Отдаётся эталонный файл PIX без изменений: имена элементов нотаций
    (``dfd_process``, ``c4_person``, ``app_component`` и т.д.) заданы студией,
    и собственная реконструкция каталога рискует не пройти её валидацию.
    """
    return _CONFIGURATION_PATH.read_text(encoding='utf-8')


def generate_main_xml(slug: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Types xmlns:xsi="{_NS_XSI}" xmlns:xsd="{_NS_XSD}">\n'
        '  <Override PartName="/pm/configuration.xml" ContentType="application/xml" />\n'
        f'  <Override PartName="/pm/maps/{escape_xml(slug)}.xml" ContentType="application/xml" />\n'
        '</Types>\n'
    )


def generate_pmm_zip(process: BusinessProcess) -> bytes:
    slug, map_xml = generate_map_xml(process)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('main.xml', generate_main_xml(slug).encode('utf-8'))
        zf.writestr('pm/configuration.xml', generate_configuration_xml().encode('utf-8'))
        zf.writestr(f'pm/maps/{slug}.xml', map_xml.encode('utf-8'))
    return buf.getvalue()
