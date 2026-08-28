"""Приведение геометрии карты к читаемому виду после импорта draw.io.

draw.io прощает то, чего не прощают BPMN-редакторы и PIX Процессная студия:

* фигура события объявлена ``aspect=fixed`` — редактор рисует круг по меньшей
  стороне, а рамка может быть прямоугольной (80×50). Импортёр рисует эллипс, и
  значок таймера («часы») внутри него растягивается и вылезает за круг;
* подпись шага в draw.io свободно выходит за рамку, а bpmn.io и студия обрезают
  её по фигуре и кладут поверх маркера задачи в левом верхнем углу — читать
  текст становится невозможно;
* хранилище данных может лежать поверх шага: в draw.io оно уходит на задний
  план, в выгрузке — перекрывает подпись.

Модуль правит именно геометрию — состав узлов и связей не меняется, чтобы
модель процесса осталась ровно такой, какой её нарисовал аналитик.

Клиентский двойник — ``app/src/lib/layout.ts``.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.process import ARTIFACT_NODE_TYPES, TASK_NODE_TYPES, ProcessNode

#: Кегль подписи фигуры в bpmn.io и в Процессной студии.
FONT_SIZE = 12.0
#: Межстрочный интервал bpmn.io (1.2 кегля).
LINE_HEIGHT = FONT_SIZE * 1.2
#: Горизонтальные и вертикальные поля подписи внутри фигуры.
PAD_X = 12.0
PAD_Y = 10.0
#: Маркер задачи (человечек/шестерёнка) bpmn.io рисует в полосе 12–26 px от
#: верхней грани. Подпись центрируется по высоте и по ширине, поэтому увести
#: первую строку из-под маркера можно только запасом высоты — по 22 px сверху
#: и снизу от текстового блока.
MARKER_BAND = 44.0

#: Минимальный зазор до соседней фигуры, который сохраняем при расширении.
GAP_X = 24.0
GAP_Y = 16.0

#: Насколько шаг вообще разрешено расширять — иначе карта «поплывёт».
MAX_TASK_WIDTH = 260
MAX_TASK_HEIGHT = 220
#: Примечание — это текст, ему нужна ширина, а не высота.
MAX_NOTE_WIDTH = 340

#: Диаметр события: стандарт BPMN — 36 px, крупнее 56 выглядит непропорционально.
MIN_EVENT_SIDE = 36
MAX_EVENT_SIDE = 56
MIN_GATEWAY_SIDE = 40
MAX_GATEWAY_SIDE = 60

_EVENT_TYPES = (
    'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
)
_GATEWAY_TYPES = ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway')

#: Ширина символа в долях кегля (Arial/Helvetica, латиница и кириллица).
_NARROW = set(" iljtfrI.,:;|!'`()[]{}/\\-")
_WIDE = set('mwMWШЩЮЫФ@%')


def text_width(text: str, font_size: float = FONT_SIZE) -> float:
    """Ширина строки в пикселях — оценка без обращения к движку шрифтов."""
    total = 0.0
    for ch in text or '':
        if ch == ' ':
            total += 0.28
        elif ch in _NARROW:
            total += 0.32
        elif ch in _WIDE:
            total += 0.86
        elif ch.isdigit():
            total += 0.56
        elif ch.isupper():
            total += 0.68
        else:
            total += 0.55
    return total * font_size


def wrapped_line_count(text: str, available_px: float, font_size: float = FONT_SIZE) -> int:
    """Сколько строк займёт подпись при переносе по словам."""
    words = (text or '').split()
    if not words:
        return 1
    if available_px <= 0:
        return len(words)
    space = text_width(' ', font_size)
    lines = 1
    cursor = 0.0
    for word in words:
        w = text_width(word, font_size)
        if cursor and cursor + space + w > available_px:
            lines += 1
            cursor = w
        else:
            cursor += (space if cursor else 0.0) + w
        # Слово длиннее строки переносится посимвольно.
        while cursor > available_px and w > available_px:
            lines += 1
            cursor -= available_px
            w -= available_px
    return lines


def label_height(text: str, box_width: float, marker: bool) -> float:
    """Высота, которой хватит подписи внутри фигуры заданной ширины."""
    lines = wrapped_line_count(text, box_width - PAD_X)
    return lines * LINE_HEIGHT + PAD_Y + (MARKER_BAND if marker else 0.0)


def _boxes_overlap(a: ProcessNode, b: ProcessNode, gap_x: float, gap_y: float) -> bool:
    ag, bg = a.geometry, b.geometry
    return (
        ag.x - gap_x < bg.x + bg.width
        and bg.x - gap_x < ag.x + ag.width
        and ag.y - gap_y < bg.y + bg.height
        and bg.y - gap_y < ag.y + ag.height
    )


def _free_space(
    node: ProcessNode,
    others: Iterable[ProcessNode],
    lane: Optional[ProcessNode],
    axis: str,
) -> Tuple[float, float]:
    """Свободное место слева/справа (``axis='x'``) или сверху/снизу от фигуры.

    Соседями считаются только фигуры, перекрывающиеся с узлом по другой оси:
    шаг из соседнего ряда расширению не мешает.
    """
    g = node.geometry
    if axis == 'x':
        low, high = float(g.x), float(g.x + g.width)
        cross_low, cross_high = float(g.y), float(g.y + g.height)
        gap = GAP_X
    else:
        low, high = float(g.y), float(g.y + g.height)
        cross_low, cross_high = float(g.x), float(g.x + g.width)
        gap = GAP_Y

    before, after = 1e9, 1e9
    for other in others:
        if other.id == node.id:
            continue
        og = other.geometry
        if axis == 'x':
            o_low, o_high = float(og.x), float(og.x + og.width)
            o_cross_low, o_cross_high = float(og.y), float(og.y + og.height)
        else:
            o_low, o_high = float(og.y), float(og.y + og.height)
            o_cross_low, o_cross_high = float(og.x), float(og.x + og.width)
        # Мешает росту только тот, кто перекрывается по ДРУГОЙ оси. Соседа,
        # разведённого по перпендикуляру, рост вдоль этой оси не задевает —
        # с зазором в проверке шаг переставал расти из-за фигуры сбоку.
        if o_cross_high <= cross_low or o_cross_low >= cross_high:
            continue
        if o_high <= low:
            before = min(before, low - o_high - gap)
        elif o_low >= high:
            after = min(after, o_low - high - gap)
        else:
            # Фигуры уже пересекаются — в эту сторону не растём.
            before = min(before, 0.0)
            after = min(after, 0.0)

    if lane is not None:
        lg = lane.geometry
        if axis == 'x':
            before = min(before, low - lg.x)
            after = min(after, lg.x + lg.width - high)
        else:
            before = min(before, low - lg.y)
            after = min(after, lg.y + lg.height - high)

    return (max(before, 0.0), max(after, 0.0))


def _grow(node: ProcessNode, extra: float, axis: str, before: float, after: float) -> None:
    """Расширяет фигуру симметрично, перекладывая нехватку на свободную сторону."""
    take_before = min(extra / 2, before)
    take_after = min(extra - take_before, after)
    take_before = min(extra - take_after, before)
    g = node.geometry
    if axis == 'x':
        g.x = int(round(g.x - take_before))
        g.width = int(round(g.width + take_before + take_after))
    else:
        g.y = int(round(g.y - take_before))
        g.height = int(round(g.height + take_before + take_after))


def square_up_events(nodes: List[ProcessNode]) -> List[str]:
    """Событиям и шлюзам возвращает квадратную рамку.

    В draw.io такие фигуры объявлены ``aspect=fixed`` и рисуются по меньшей
    стороне; прямоугольная рамка (80×50) — это просто место под подпись сбоку.
    Импортёр про ``aspect`` не знает и растягивает круг в эллипс, а значок
    внутри (часы таймера, конверт сообщения) вылезает за контур.
    """
    touched: List[str] = []
    for node in nodes:
        if node.type in _EVENT_TYPES:
            lo, hi = MIN_EVENT_SIDE, MAX_EVENT_SIDE
        elif node.type in _GATEWAY_TYPES:
            lo, hi = MIN_GATEWAY_SIDE, MAX_GATEWAY_SIDE
        else:
            continue
        g = node.geometry
        side = int(round(min(max(min(g.width, g.height), lo), hi)))
        if side == g.width and side == g.height:
            continue
        g.x = int(round(g.x + (g.width - side) / 2))
        g.y = int(round(g.y + (g.height - side) / 2))
        g.width = side
        g.height = side
        touched.append(node.id)
    return touched


def fit_labels(nodes: List[ProcessNode], lanes: List[ProcessNode]) -> List[str]:
    """Расширяет шаги и примечания так, чтобы подпись помещалась в рамку.

    Сначала пробуем добрать ширину — строк становится меньше, и карта остаётся
    такой же высоты. Если места по горизонтали нет, добираем высоту. Рост
    ограничен свободным местом до соседней фигуры и границами дорожки, поэтому
    шаги не наезжают друг на друга.
    """
    lane_by_id = {lane.id: lane for lane in lanes}
    fitted: List[str] = []

    targets = [
        n for n in nodes
        if n.type in TASK_NODE_TYPES or n.type == 'textAnnotation'
    ]
    # Крупные фигуры расширяем первыми: им сложнее найти место.
    targets.sort(key=lambda n: -len(n.name or ''))

    for node in targets:
        is_note = node.type == 'textAnnotation'
        marker = not is_note
        lane = lane_by_id.get(node.laneId or '')
        g = node.geometry
        if label_height(node.name, g.width, marker) <= g.height:
            continue

        max_width = MAX_NOTE_WIDTH if is_note else MAX_TASK_WIDTH
        before_x, after_x = _free_space(node, nodes, lane, 'x')
        room_x = min(before_x + after_x, max(max_width - g.width, 0))

        # Расширяем, только пока это убирает строку: подпись в одну строку от
        # лишней ширины не выигрывает, а фигура зря съедает место соседей.
        lines = wrapped_line_count(node.name, g.width - PAD_X)
        add_x = 0.0
        probe = 0.0
        step = 10.0
        while probe < room_x and lines > 1:
            probe = min(probe + step, room_x)
            probe_lines = wrapped_line_count(node.name, g.width + probe - PAD_X)
            if probe_lines >= lines:
                continue
            add_x, lines = probe, probe_lines
            if label_height(node.name, g.width + add_x, marker) <= g.height:
                break
        if add_x > 0:
            _grow(node, add_x, 'x', before_x, after_x)

        need_h = label_height(node.name, g.width, marker)
        if need_h > g.height:
            max_height = MAX_TASK_HEIGHT if not is_note else MAX_TASK_HEIGHT
            before_y, after_y = _free_space(node, nodes, lane, 'y')
            room_y = min(before_y + after_y, max(max_height - g.height, 0))
            _grow(node, min(need_h - g.height, room_y), 'y', before_y, after_y)

        fitted.append(node.id)
    return fitted


def _fits(box: Tuple[int, int, int, int], lane: Optional[ProcessNode]) -> bool:
    if lane is None:
        return True
    x, y, w, h = box
    lg = lane.geometry
    return lg.x <= x and lg.y <= y and x + w <= lg.x + lg.width and y + h <= lg.y + lg.height


def separate_artifacts(nodes: List[ProcessNode], lanes: List[ProcessNode]) -> List[str]:
    """Убирает наложение артефакта на шаг.

    В draw.io хранилище данных уходит на задний план, и наложение аналитику не
    мешает. В выгрузке порядок отрисовки другой — цилиндр ложится поверх
    подписи шага. Сдвигаем артефакт, шаг не трогаем, и только туда, где он
    останется внутри своей дорожки и никого не заденет.
    """
    lane_by_id = {lane.id: lane for lane in lanes}
    flow = [n for n in nodes if n.type not in ARTIFACT_NODE_TYPES and n.type != 'lane']
    moved: List[str] = []

    for artifact in nodes:
        if artifact.type not in ARTIFACT_NODE_TYPES or artifact.type == 'textAnnotation':
            continue
        step = next((s for s in flow if _boxes_overlap(artifact, s, 0, 0)), None)
        if step is None:
            continue

        ag, sg = artifact.geometry, step.geometry
        lane = lane_by_id.get(artifact.laneId or '')

        # Смещение считаем до полного выхода за грань шага, а не по величине
        # пересечения: цилиндр может лежать целиком внутри шага, и тогда сдвиг
        # «на ширину пересечения» его не освобождает.
        gap = 8
        up = (ag.y + ag.height) - sg.y + gap
        down = (sg.y + sg.height) - ag.y + gap
        left = (ag.x + ag.width) - sg.x + gap
        right = (sg.x + sg.width) - ag.x + gap
        candidates = [
            (ag.x, ag.y - up, ag.width, ag.height),
            (ag.x, ag.y + down, ag.width, ag.height),
            (ag.x - left, ag.y, ag.width, ag.height),
            (ag.x + right, ag.y, ag.width, ag.height),
        ]
        # Сначала — куда двигать ближе.
        candidates.sort(key=lambda c: abs(c[0] - ag.x) + abs(c[1] - ag.y))

        others = [n for n in nodes if n.id != artifact.id and n.type != 'lane']
        for x, y, w, h in candidates:
            if not _fits((x, y, w, h), lane):
                continue
            clash = any(
                min(x + w, o.geometry.x + o.geometry.width) - max(x, o.geometry.x) > 0
                and min(y + h, o.geometry.y + o.geometry.height) - max(y, o.geometry.y) > 0
                for o in others
            )
            if clash:
                continue
            ag.x, ag.y = int(x), int(y)
            moved.append(artifact.id)
            break
    return moved


def normalize_layout(nodes: List[ProcessNode], lanes: List[ProcessNode]) -> Dict[str, List[str]]:
    """Полный проход нормализации. Возвращает, что именно пришлось поправить.

    Артефакты разводим дважды: до подгонки подписей — чтобы наложение не
    считалось «нет свободного места» и шаг мог расшириться, и после — потому
    что подросший шаг может задеть соседний цилиндр.
    """
    squared = square_up_events(nodes)
    moved = separate_artifacts(nodes, lanes)
    fitted = fit_labels(nodes, lanes)
    moved += [nid for nid in separate_artifacts(nodes, lanes) if nid not in moved]
    return {'squared': squared, 'fitted': fitted, 'moved': moved}


#: Подписи событий, шлюзов и артефактов bpmn.io рисует ВНЕ фигуры и переносит
#: по узкой рамке в 90 px. Длинное имя шлюза превращалось в столбец из семи
#: строк, который ложился на соседние шаги и на подписи связей.
MIN_EXTERNAL_LABEL_WIDTH = 90
MAX_EXTERNAL_LABEL_WIDTH = 220
#: Сколько строк подписи считаем приемлемыми, прежде чем расширять рамку.
EXTERNAL_LABEL_TARGET_LINES = 2
EXTERNAL_LABEL_GAP = 6.0

#: Типы, подпись которых импортёр выносит за пределы фигуры.
EXTERNAL_LABEL_TYPES = _EVENT_TYPES + _GATEWAY_TYPES + ('dataStore', 'dataObject')

Box = Tuple[int, int, int, int]


def style_map(style: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (style or '').split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            out[key.strip().lower()] = value.strip().lower()
    return out


def _overlap_area(box: Box, other: Box) -> float:
    ax, ay, aw, ah = box
    bx, by, bw, bh = other
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    return dx * dy if dx > 0 and dy > 0 else 0.0


def choose_label_box(candidates: List[Box], obstacles: Iterable[Box]) -> Box:
    """Первая позиция подписи, которая ни на что не наезжает.

    Если свободной нет, берём наименее конфликтную: подпись всё равно должна
    где-то стоять, но пусть перекрывает как можно меньше.
    """
    obstacles = list(obstacles)
    best: Optional[Box] = None
    best_area = None
    for box in candidates:
        area = sum(_overlap_area(box, o) for o in obstacles)
        if area == 0:
            return box
        if best_area is None or area < best_area:
            best, best_area = box, area
    return best if best is not None else candidates[0]


def label_size(text: str) -> Tuple[int, int]:
    """Рамка подписи: по тексту, а не по фиксированной ширине.

    Короткому «To'liq» рамка в 90 px не нужна — на плотной карте лишние 55 px
    ровно и приводят к тому, что подпись ветки ложится на соседний шаг.
    Длинную подпись расширяем, пока она не уляжется в пару строк.
    """
    snug = int(round(text_width(text) + 10))
    if snug <= MAX_EXTERNAL_LABEL_WIDTH:
        return max(snug, 32), int(round(LINE_HEIGHT + 4))
    width = MIN_EXTERNAL_LABEL_WIDTH
    while (
        width < MAX_EXTERNAL_LABEL_WIDTH
        and wrapped_line_count(text, width) > EXTERNAL_LABEL_TARGET_LINES
    ):
        width += 10
    lines = wrapped_line_count(text, width)
    return width, int(round(lines * LINE_HEIGHT + 4))


def label_size_variants(text: str) -> List[Tuple[int, int]]:
    """Варианты рамки подписи — от широкой к узкой.

    Если на карте не нашлось места под привычную двухстрочную рамку, подпись
    лучше сложить в три-четыре строки, чем положить поверх соседней фигуры.
    """
    variants = [label_size(text)]
    for target in (3, 4):
        width = MIN_EXTERNAL_LABEL_WIDTH
        while width < MAX_EXTERNAL_LABEL_WIDTH and wrapped_line_count(text, width) > target:
            width += 10
        lines = wrapped_line_count(text, width)
        candidate = (width, int(round(lines * LINE_HEIGHT + 4)))
        if candidate[0] < variants[-1][0] and candidate not in variants:
            variants.append(candidate)
    return variants


def external_label_candidates(node: ProcessNode) -> List[Box]:
    """Позиции выносной подписи — в порядке предпочтения.

    Первой идёт та, которую выбрал аналитик в draw.io: стиль фигуры хранит
    ``labelPosition`` и ``verticalLabelPosition``, и на карте банка они
    расставлены не случайно — подпись таймера уведена влево, чтобы не лечь на
    вертикальную связь, а подпись хранилища данных поднята над цилиндром.
    Импортёр про эти стили не знает и кладёт всё под фигуру.
    """
    text = node.name or ''
    g = node.geometry
    gap = EXTERNAL_LABEL_GAP
    smap = style_map(node.style)
    horizontal = smap.get('labelposition', 'center')
    vertical = smap.get('verticallabelposition', 'bottom')

    order: List[Box] = []
    for width, height in label_size_variants(text):
        center_x = int(round(g.x + g.width / 2 - width / 2))
        middle_y = int(round(g.y + g.height / 2 - height / 2))
        above_y = int(round(g.y - gap - height))
        below_y = int(round(g.y + g.height + gap))

        below: Box = (center_x, below_y, width, height)
        above: Box = (center_x, above_y, width, height)
        left: Box = (int(round(g.x - gap - width)), middle_y, width, height)
        right: Box = (int(round(g.x + g.width + gap)), middle_y, width, height)

        if horizontal == 'left':
            preferred = left
        elif horizontal == 'right':
            preferred = right
        elif vertical == 'top':
            preferred = above
        else:
            preferred = below

        # Запасные позиции по диагоналям и на второй «полке»: на плотной карте
        # четырёх сторон не хватает, и подпись садилась на соседнюю фигуру.
        far = int(round(gap + height + 4))
        side = int(round(gap + width / 2))
        order.extend([
            preferred, below, above, right, left,
            (center_x - side, below_y, width, height),
            (center_x + side, below_y, width, height),
            (center_x - side, above_y, width, height),
            (center_x + side, above_y, width, height),
            (center_x, below_y + far, width, height),
            (center_x, above_y - far, width, height),
        ])

    seen: List[Box] = []
    for box in order:
        if box not in seen:
            seen.append(box)
    return seen


def node_obstacles(nodes: Iterable[ProcessNode], skip_id: str = '') -> List[Box]:
    return [
        (n.geometry.x, n.geometry.y, n.geometry.width, n.geometry.height)
        for n in nodes
        if n.id != skip_id and n.type != 'lane'
    ]


def external_label_box(
    node: ProcessNode,
    nodes: Iterable[ProcessNode],
    extra_obstacles: Iterable[Box] = (),
) -> Box:
    """Рамка выносной подписи фигуры, разведённая с соседями."""
    obstacles = node_obstacles(nodes, node.id) + list(extra_obstacles)
    return choose_label_box(external_label_candidates(node), obstacles)


#: Доли длины ломаной, около которых можно поставить подпись связи. Середина
#: предпочтительна, но на плотной карте там бывает занято — тогда подпись
#: сдвигается вдоль своей же линии, а не садится на чужую фигуру.
EDGE_LABEL_FRACTIONS = (0.5, 0.4, 0.6, 0.28, 0.72, 0.15, 0.85)


def _point_at(route: Sequence[Tuple[int, int]], fraction: float) -> Tuple[float, float, bool]:
    """Точка на ломаной по доле её длины и ориентация отрезка в этом месте."""
    lengths = [
        abs(b[0] - a[0]) + abs(b[1] - a[1])
        for a, b in zip(route, route[1:])
    ]
    total = sum(lengths)
    if total <= 0:
        return float(route[0][0]), float(route[0][1]), False
    target = total * fraction
    for (a, b), seg in zip(zip(route, route[1:]), lengths):
        if seg <= 0:
            continue
        if target <= seg:
            t = target / seg
            return (
                a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                abs(b[1] - a[1]) > abs(b[0] - a[0]),
            )
        target -= seg
    a, b = route[-2], route[-1]
    return float(b[0]), float(b[1]), abs(b[1] - a[1]) > abs(b[0] - a[0])


def edge_label_candidates(route: Sequence[Tuple[int, int]], text: str) -> List[Box]:
    """Позиции подписи связи вдоль ломаной.

    Порядок зависит от того, как идёт линия в этом месте: подпись у
    вертикального отрезка уводим вбок, у горизонтального — вверх. Иначе линия
    проходит ровно через текст, и подпись ветки шлюза не прочитать.
    """
    sizes = label_size_variants(text)
    points = list(route)
    if not points:
        w, h = sizes[0]
        return [(0, 0, w, h)]
    if len(points) == 1:
        points = points * 2

    out: List[Box] = []
    for width, height in sizes:
        for fraction in EDGE_LABEL_FRACTIONS:
            cx, cy, vertical = _point_at(points, fraction)
            x = int(round(cx - width / 2))
            y = int(round(cy - height / 2))
            above: Box = (x, int(round(cy - height - 4)), width, height)
            below: Box = (x, int(round(cy + 4)), width, height)
            right: Box = (int(round(cx + 8)), y, width, height)
            left: Box = (int(round(cx - 8 - width)), y, width, height)
            out.extend([right, left, above, below] if vertical else [above, below, right, left])

    seen: List[Box] = []
    for box in out:
        if box not in seen:
            seen.append(box)
    return seen


#: Толщина «коридора» линии связи при разведении подписей. Меньше — подпись
#: липнет к линии, больше — её некуда поставить на плотной карте.
SEGMENT_THICKNESS = 3


def segment_boxes(route: Iterable[Tuple[int, int]]) -> List[Box]:
    """Отрезки ломаной как узкие прямоугольники — препятствия для подписей.

    Без них подпись ветки шлюза ложится ровно на вертикальную связь и читается
    как перечёркнутая.
    """
    points = list(route)
    boxes: List[Box] = []
    half = SEGMENT_THICKNESS
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        boxes.append((
            int(left - half), int(top - half),
            int(right - left + 2 * half), int(bottom - top + 2 * half),
        ))
    return boxes
