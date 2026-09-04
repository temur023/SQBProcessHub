"""Ортогональная трассировка связей — как рисует draw.io.

draw.io по умолчанию ведёт связи стилем ``edgeStyle=orthogonalEdgeStyle``:
линия выходит из фигуры перпендикулярно её грани и идёт только по осям.
В самом файле при этом хранятся лишь те изломы, которые аналитик подвинул
руками, — всё остальное редактор достраивает при отрисовке.

Если выгружать в BPMN только «точка выхода → точка входа», схема в bpmn.io
превращается в паутину диагоналей. Этот модуль восстанавливает ту ломаную,
которую показывает draw.io, и используется обоими экспортёрами.
"""
from __future__ import annotations

import heapq
import math
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.process import Geometry, ProcessEdge, ProcessNode

Point = Tuple[float, float]
Direction = Tuple[int, int]

#: Длина перпендикулярного «уса» от грани фигуры, px. draw.io использует 20.
STUB = 20.0
_EPS = 0.5


def _origin(node: ProcessNode, placed: Optional[Dict[str, Tuple[int, int]]]) -> Tuple[float, float]:
    if placed and node.id in placed:
        x, y = placed[node.id]
        return float(x), float(y)
    return float(node.geometry.x), float(node.geometry.y)


def anchor_point(
    node: ProcessNode,
    frac_x: float,
    frac_y: float,
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
) -> Point:
    ox, oy = _origin(node, placed)
    return (ox + node.geometry.width * frac_x, oy + node.geometry.height * frac_y)


def _center(node: ProcessNode, placed: Optional[Dict[str, Tuple[int, int]]]) -> Point:
    return anchor_point(node, 0.5, 0.5, placed)


def _dominant_axis(src: ProcessNode, tgt: ProcessNode, placed) -> Direction:
    """Направление от источника к цели по преобладающей оси."""
    sx, sy = _center(src, placed)
    tx, ty = _center(tgt, placed)
    if abs(tx - sx) >= abs(ty - sy):
        return (1, 0) if tx >= sx else (-1, 0)
    return (0, 1) if ty >= sy else (0, -1)


def _side_direction(frac_x: Optional[float], frac_y: Optional[float]) -> Optional[Direction]:
    """Внешняя нормаль грани, на которой сидит якорь draw.io."""
    if frac_x is not None:
        if frac_x <= 0.0:
            return (-1, 0)
        if frac_x >= 1.0:
            return (1, 0)
    if frac_y is not None:
        if frac_y <= 0.0:
            return (0, -1)
        if frac_y >= 1.0:
            return (0, 1)
    return None


def exit_direction(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode, placed=None) -> Direction:
    side = _side_direction(edge.exitX, edge.exitY)
    if side:
        return side
    if edge.points:
        # Первый заданный аналитиком излом задаёт ось выхода.
        sx, sy = _center(src, placed)
        px, py = float(edge.points[0].x), float(edge.points[0].y)
        if abs(px - sx) >= abs(py - sy):
            return (1, 0) if px >= sx else (-1, 0)
        return (0, 1) if py >= sy else (0, -1)
    return _dominant_axis(src, tgt, placed)


def entry_direction(edge: ProcessEdge, src: ProcessNode, tgt: ProcessNode, placed=None) -> Direction:
    """Направление ДВИЖЕНИЯ линии в момент входа в целевую фигуру."""
    side = _side_direction(edge.entryX, edge.entryY)
    if side:
        return (-side[0], -side[1])
    if edge.points:
        tx, ty = _center(tgt, placed)
        px, py = float(edge.points[-1].x), float(edge.points[-1].y)
        if abs(tx - px) >= abs(ty - py):
            return (1, 0) if tx >= px else (-1, 0)
        return (0, 1) if ty >= py else (0, -1)
    return _dominant_axis(src, tgt, placed)


def _simplify(points: Sequence[Point]) -> List[Point]:
    """Убирает дубли и точки, лежащие внутри прямого отрезка."""
    out: List[Point] = []
    for pt in points:
        if out and abs(out[-1][0] - pt[0]) < _EPS and abs(out[-1][1] - pt[1]) < _EPS:
            continue
        out.append(pt)
    cleaned: List[Point] = []
    for i, pt in enumerate(out):
        if 0 < i < len(out) - 1:
            prev, nxt = out[i - 1], out[i + 1]
            # Точка лишняя, только если лежит ВНУТРИ прямого отрезка. Разворот
            # (сосед по одну сторону и по другую совпадают) внутренней точкой
            # не является: это вершина обхода, и убрать её значит стереть
            # излом, который аналитик нарисовал специально. На картах банка так
            # терялась почти треть его изломов.
            same_x = abs(prev[0] - pt[0]) < _EPS and abs(pt[0] - nxt[0]) < _EPS
            same_y = abs(prev[1] - pt[1]) < _EPS and abs(pt[1] - nxt[1]) < _EPS
            between_y = min(prev[1], nxt[1]) - _EPS <= pt[1] <= max(prev[1], nxt[1]) + _EPS
            between_x = min(prev[0], nxt[0]) - _EPS <= pt[0] <= max(prev[0], nxt[0]) + _EPS
            if (same_x and between_y) or (same_y and between_x):
                continue
        cleaned.append(pt)
    return cleaned


#: Короче этого отрезок в ломаной не оставляем: студия рисует связи типа
#: ``step``, и отрезок в два-три пикселя виден не как поворот, а как дефект
#: отрисовки.
#:
#: Порог выбран по выгрузке самой студии (``tests/fixtures/sap.pmm``): там
#: самый короткий настоящий отрезок — 6 px, а преобладают 16–23 px. Берём 8 —
#: на ступень строже эталона, но того же порядка: правило должно убирать
#: зазубрины, а не перекладывать ломаную, которую аналитик нарисовал сам.
MIN_SEGMENT = 8.0


def _faces_overlap(
    src: ProcessNode,
    tgt: ProcessNode,
    horizontal: bool,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[float]:
    """Середина общего участка встречных граней или ``None``, если его нет.

    Две фигуры, стоящие друг напротив друга, соединяются одной прямой, если
    их грани перекрываются: точку входа и выхода достаточно поставить на
    середину перекрытия. Ровно это и делает аналитик, когда тянет линию от
    шага к шагу, — и ровно это ломалось после приведения фигур к канону
    студии: шлюз стал на 10 px больше, его центр разошёлся с центром соседней
    задачи, и на прямой линии появилась лесенка в пять пикселей.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    if horizontal:
        lo = max(sy, ty)
        hi = min(sy + src.geometry.height, ty + tgt.geometry.height)
    else:
        lo = max(sx, tx)
        hi = min(sx + src.geometry.width, tx + tgt.geometry.width)
    # Впритык грани не считаем: линия прошла бы по самому краю фигуры.
    return (lo + hi) / 2 if hi - lo >= MIN_SEGMENT else None


def _straighten(
    start: Point,
    end: Point,
    d0: Direction,
    d1: Direction,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Tuple[Point, Point]:
    """Ставит оба конца на одну ось, если фигуры стоят друг напротив друга."""
    if d0 != d1:
        return start, end
    horizontal = d0[0] != 0
    shared = _faces_overlap(src, tgt, horizontal, placed)
    if shared is None:
        return start, end
    if horizontal:
        return (start[0], shared), (end[0], shared)
    return (shared, start[1]), (shared, end[1])


def _face_span(
    node: Optional[ProcessNode],
    axis: int,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[Tuple[float, float]]:
    """Отрезок, в пределах которого конец связи может скользить по грани."""
    if node is None:
        return None
    ox, oy = _origin(node, placed)
    if axis == 0:
        return (ox, ox + node.geometry.width)
    return (oy, oy + node.geometry.height)


def _is_orthogonal(points: Sequence[Point]) -> bool:
    """Каждый отрезок ломаной идёт строго по оси."""
    return all(
        abs(b[0] - a[0]) < _EPS or abs(b[1] - a[1]) < _EPS
        for a, b in zip(points, points[1:])
    )


def _shift(pts: List[Point], indices: Tuple[int, int], axis: int, value: float) -> bool:
    """Двигает пару точек на общую линию, если это не сделает ломаную косой.

    Проверка обязательна: связи в студии имеют тип ``step``, и косой отрезок в
    них выглядит поломкой. Пока сдвиг применялся без проверки, снятие ступенек
    у ломаной с разворотом рождало два десятка диагоналей на двенадцати картах.
    """
    before = [pts[k] for k in indices]
    for k in indices:
        point = list(pts[k])
        point[axis] = value
        pts[k] = (point[0], point[1])
    if _is_orthogonal(pts):
        return True
    for k, point in zip(indices, before):
        pts[k] = point
    return False


def _drop_micro_jogs(
    points: Sequence[Point],
    src: Optional[ProcessNode] = None,
    tgt: Optional[ProcessNode] = None,
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
) -> List[Point]:
    """Убирает ступеньки короче ``MIN_SEGMENT``, не ломая ортогональность.

    Ступенька — это короткий отрезок между двумя параллельными, разошедшимися
    на пару пикселей. Убрать её, подвинув саму ступеньку, нельзя: соседний
    отрезок станет косым. Сдвигать надо соседний отрезок целиком — на линию
    второго.

    Двигаем сначала тот из соседей, что не упирается в фигуру. Если упираются
    оба (короткая ломаная в четыре точки), конец сдвигаем ВДОЛЬ грани, на
    которой он стоит, и только в её пределах: точка примыкания при этом
    остаётся на фигуре, а линия выпрямляется. Уйти с грани нельзя — связь
    отошла бы от фигуры, а это худший из дефектов отрисовки.
    """
    pts: List[Point] = [(float(x), float(y)) for x, y in points]
    for _ in range(len(pts)):
        jog = None
        for i in range(1, len(pts) - 2):
            a, b = pts[i], pts[i + 1]
            length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            if _EPS <= length < MIN_SEGMENT:
                jog = i
                break
        if jog is None:
            break

        i = jog
        a, b = pts[i], pts[i + 1]
        horizontal = abs(b[0] - a[0]) > abs(b[1] - a[1])
        axis = 0 if horizontal else 1

        if i - 1 > 0 and _shift(pts, (i - 1, i), axis, b[axis]):
            pass                           # сдвинули левый отрезок на линию b
        elif i + 2 < len(pts) - 1 and _shift(pts, (i + 1, i + 2), axis, a[axis]):
            pass                           # или правый — на линию a
        else:
            # Оба соседа упираются в фигуры: скользим концом вдоль грани.
            moved = False
            for indices, node, value in (
                ((0, 1), src, b[axis]),
                ((len(pts) - 2, len(pts) - 1), tgt, a[axis]),
            ):
                span = _face_span(node, axis, placed)
                if span is None or not (span[0] - _EPS <= value <= span[1] + _EPS):
                    continue
                if indices[0] > i or indices[1] < i + 1:
                    for k in indices:
                        pt = list(pts[k])
                        pt[axis] = value
                        pts[k] = (pt[0], pt[1])
                    moved = True
                    break
            if not moved:
                break

        pts = _simplify(pts)
    return pts


# ── Обход фигур ─────────────────────────────────────────────────────────────

#: Насколько линия ОБХОДИТ чужую фигуру: на столько отступают линии сетки от
#: её краёв. Это запас на аккуратность, а не допуск.
_CLEARANCE = 10.0

#: Насколько глубоко линия может зайти в фигуру, прежде чем это считается
#: пересечением, px.
#:
#: Разделять这 два числа обязательно, и раньше они были одним. Проверка сжимала
#: рамку на весь ``_CLEARANCE``, то есть закрывала глаза на линию, врезавшуюся
#: в фигуру на девять пикселей: у значка длительности в 48 px «неприкосновенной»
#: оставалась середина 28×28, у шлюза в 60 px — 40×40. Трассировщик считал такие
#: маршруты чистыми и не искал обход, а на карте линия шла прямо по фигуре.
#: Здесь нужен допуск на округление, и только он.
_PIERCE_TOLERANCE = 1.0

#: Сторона ячейки сетки, по которой ищутся препятствия рядом с отрезком.
_GRID = 256.0


class Obstacles:
    """Фигуры, которые линия обязана обойти, с поиском по сетке.

    Проверять каждый отрезок против всех фигур карты нельзя: на карте банка их
    девятьсот, связей семьсот, и перебор становится квадратичным. Фигуры
    раскладываются по сетке с крупной ячейкой, и отрезок сверяется только с
    теми, что попали в его ячейки.
    """

    __slots__ = ('_cells', '_boxes', '_owner')

    def __init__(self, boxes: Sequence[Tuple[str, Tuple[float, float, float, float]]],
                 owners: Optional[Dict[str, str]] = None):
        self._boxes: Dict[str, Tuple[float, float, float, float]] = dict(boxes)
        #: Ключ рамки -> фигура, которой она принадлежит. Значок длительности
        #: принадлежит своему шагу: связь, идущая В этот шаг, проходит рядом со
        #: значком по праву, и считать это пересечением нельзя.
        self._owner: Dict[str, str] = dict(owners or {})
        self._cells: Dict[Tuple[int, int], List[str]] = {}
        for key, (x, y, w, h) in self._boxes.items():
            for cx in range(int(x // _GRID), int((x + w) // _GRID) + 1):
                for cy in range(int(y // _GRID), int((y + h) // _GRID) + 1):
                    self._cells.setdefault((cx, cy), []).append(key)

    def _near(self, a: Point, b: Point) -> List[str]:
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        keys: List[str] = []
        seen = set()
        for cx in range(int(lo_x // _GRID), int(hi_x // _GRID) + 1):
            for cy in range(int(lo_y // _GRID), int(hi_y // _GRID) + 1):
                for key in self._cells.get((cx, cy), ()):
                    if key not in seen:
                        seen.add(key)
                        keys.append(key)
        return keys

    def hits(self, route: Sequence[Point], exclude: Sequence[str]) -> int:
        """Сколько раз ломаная входит внутрь чужой фигуры."""
        skip = set(exclude)
        count = 0
        for a, b in zip(route, route[1:]):
            for key in self._near(a, b):
                if key in skip or self._owner.get(key) in skip:
                    continue
                if _segment_in_box(a, b, self._boxes[key]):
                    count += 1
        return count

    def box(self, key: str) -> Optional[Tuple[float, float, float, float]]:
        return self._boxes.get(key)

    def within(
        self,
        area: Tuple[float, float, float, float],
        exclude: Sequence[str] = (),
    ) -> List[Tuple[float, float, float, float]]:
        """Рамки, попавшие в окрестность, кроме принадлежащих концам связи."""
        ax, ay, aw, ah = area
        skip = set(exclude)
        out: List[Tuple[float, float, float, float]] = []
        for key, (x, y, w, h) in self._boxes.items():
            if key in skip or self._owner.get(key) in skip:
                continue
            if x > ax + aw or x + w < ax or y > ay + ah or y + h < ay:
                continue
            out.append((x, y, w, h))
        return out


def build_obstacles(
    nodes: Sequence[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    skip_types: Sequence[str] = ('lane',),
    extra: Sequence[Tuple[str, str, Tuple[float, float, float, float]]] = (),
) -> Obstacles:
    """Препятствия для трассировки: все фигуры карты, кроме контейнеров.

    Дорожка и пул препятствиями не считаются: связь между дорожками обязана их
    пересекать, это нормальный ход потока. Обходить надо шаги, шлюзы, события
    и артефакты — то, поверх чего линия читается как ошибка.
    """
    skip = set(skip_types)
    boxes = []
    for node in nodes:
        if node.type in skip:
            continue
        x, y = _origin(node, placed)
        boxes.append((node.id, (x, y, float(node.geometry.width), float(node.geometry.height))))
    # ``extra`` — фигуры, которых нет в модели, но которые появятся на карте:
    # значки длительности рисует сам экспортёр. Трассировщик обязан знать о них
    # так же, как о шагах, иначе линия пройдёт сквозь часы. Каждая такая рамка
    # приходит с хозяином — узлом, которому она принадлежит.
    owners: Dict[str, str] = {}
    for key, owner, box in extra:
        boxes.append((key, box))
        owners[key] = owner
    return Obstacles(boxes, owners)


class Corridors:
    """Отрезки, уже занятые проложенными связями.

    Две линии, легшие на одну прямую, на карте выглядят одной: вторая просто
    исчезает под первой, и схема врёт — связи не видно, хотя она есть. Поэтому
    маршруты прокладываются по очереди, и каждый следующий знает, где прошли
    предыдущие: общий коридор ему дороже, и при равных прочих он отходит на
    соседнюю линию сетки.

    Это не запрет: пересечь чужую линию можно и нужно, речь только о ДЛИННОМ
    совпадении. Поэтому штраф считается по длине наложения.
    """

    __slots__ = ('_h', '_v')

    def __init__(self) -> None:
        #: y -> занятые интервалы по x (горизонтальные отрезки) и наоборот.
        self._h: Dict[int, List[Tuple[float, float]]] = {}
        self._v: Dict[int, List[Tuple[float, float]]] = {}

    def occupy(self, route: Sequence[Point]) -> None:
        for a, b in zip(route, route[1:]):
            if abs(a[1] - b[1]) < _EPS:
                self._h.setdefault(int(round(a[1])), []).append(
                    (min(a[0], b[0]), max(a[0], b[0])))
            elif abs(a[0] - b[0]) < _EPS:
                self._v.setdefault(int(round(a[0])), []).append(
                    (min(a[1], b[1]), max(a[1], b[1])))

    def overlap(self, route: Sequence[Point]) -> float:
        """Суммарная длина, на которой ломаная лежит поверх уже проложенных."""
        total = 0.0
        for a, b in zip(route, route[1:]):
            if abs(a[1] - b[1]) < _EPS:
                bucket, lo, hi = self._h.get(int(round(a[1]))), min(a[0], b[0]), max(a[0], b[0])
            elif abs(a[0] - b[0]) < _EPS:
                bucket, lo, hi = self._v.get(int(round(a[0]))), min(a[1], b[1]), max(a[1], b[1])
            else:
                continue
            for start, end in bucket or ():
                total += max(0.0, min(hi, end) - max(lo, start))
        return total


#: Во сколько раз пиксель общего коридора «дороже» пикселя своего пути.
#: Ломаная охотно удлиняется, лишь бы не слиться с соседней, но не любой ценой:
#: крюк вокруг половины карты хуже, чем сто пикселей рядом с чужой линией.
_CORRIDOR_WEIGHT = 3.0


def _segment_in_box(a: Point, b: Point, box: Tuple[float, float, float, float]) -> bool:
    """Отрезок заходит внутрь рамки глубже допуска на округление."""
    x, y, w, h = box
    x += _PIERCE_TOLERANCE
    y += _PIERCE_TOLERANCE
    w -= 2 * _PIERCE_TOLERANCE
    h -= 2 * _PIERCE_TOLERANCE
    if w <= 0 or h <= 0:
        return False
    lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
    lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
    return not (hi_x < x or lo_x > x + w or hi_y < y or lo_y > y + h)


def _candidate_routes(start: Point, d0: Direction, end: Point, d1: Direction) -> List[List[Point]]:
    """Несколько допустимых ортогональных маршрутов между теми же концами.

    Первый — классический «ус, колено, ус» из draw.io; остальные разводят
    колено в стороны, чтобы обойти то, что стоит на пути. Все начинаются и
    кончаются в тех же точках на гранях: конец связи двигать нельзя.
    """
    a = (start[0] + d0[0] * STUB, start[1] + d0[1] * STUB)
    b = (end[0] - d1[0] * STUB, end[1] - d1[1] * STUB)
    horizontal_exit = d0[0] != 0
    horizontal_entry = d1[0] != 0

    routes: List[List[Point]] = []

    def add(middle: List[Point]) -> None:
        routes.append(_simplify([start, a, *middle, b, end]))

    # Насколько далеко уводится обходной коридор. Несколько расстояний — чтобы
    # обойти и узкий шаг, и высокую дорожку с фигурами: одного отступа хватает
    # не всегда, а перебирать их дешевле, чем строить видимость по всей карте.
    detours = (40.0, 90.0, 160.0, 260.0)

    if horizontal_exit and horizontal_entry:
        for frac in (0.5, 0.25, 0.75, 0.12, 0.88):
            mid_x = a[0] + (b[0] - a[0]) * frac
            add([(mid_x, a[1]), (mid_x, b[1])])
        # Обход поверху и понизу: колено уводится за пределы обеих фигур.
        for gap in detours:
            add([(a[0], min(a[1], b[1]) - gap), (b[0], min(a[1], b[1]) - gap)])
            add([(a[0], max(a[1], b[1]) + gap), (b[0], max(a[1], b[1]) + gap)])
    elif not horizontal_exit and not horizontal_entry:
        for frac in (0.5, 0.25, 0.75, 0.12, 0.88):
            mid_y = a[1] + (b[1] - a[1]) * frac
            add([(a[0], mid_y), (b[0], mid_y)])
        for gap in detours:
            add([(min(a[0], b[0]) - gap, a[1]), (min(a[0], b[0]) - gap, b[1])])
            add([(max(a[0], b[0]) + gap, a[1]), (max(a[0], b[0]) + gap, b[1])])
    elif horizontal_exit:
        add([(b[0], a[1])])
        add([(a[0], b[1]), (b[0], b[1])])
        # Через промежуточный коридор: сначала вбок, потом по вертикали и снова
        # вбок. Нужен, когда прямое колено упирается в соседнюю фигуру.
        for gap in detours:
            for lane_y in (min(a[1], b[1]) - gap, max(a[1], b[1]) + gap):
                add([(a[0], lane_y), (b[0], lane_y)])
    else:
        add([(a[0], b[1])])
        add([(b[0], a[1]), (b[0], b[1])])
        for gap in detours:
            for lane_x in (min(a[0], b[0]) - gap, max(a[0], b[0]) + gap):
                add([(lane_x, a[1]), (lane_x, b[1])])
    return routes


#: Крюк короче этого считаем допустимым: обход препятствия честно ходит назад.
_UTURN_TOLERANCE = 40.0


def _doubles_back(route: Sequence[Point]) -> bool:
    """Ломаная уходит в одну сторону и заметно возвращается обратно.

    Так выглядит связь, у которой ус направлен прочь от цели: линия делает
    двадцать пикселей вправо, разворачивается и идёт влево через собственную
    фигуру. На карте это читается как поворот на 180°, и аналитик не понимает,
    куда ведёт связь.

    Считаем по каждой оси отдельно: если ход вперёд и ход назад сопоставимы,
    значит линия ходила и вернулась.

    Правило намеренно грубое: под него попадает и честный обход «вверх, вбок,
    вниз», у которого возврат по одной оси компенсирован продвижением по
    другой. Отделять одно от другого пробовали — развороты выросли с 19 до 27,
    а линий сквозь фигуры стало больше: обход, за который никто не штрафует,
    трассировщик начинает выбирать и там, где он не нужен. Цена ошибки здесь
    невелика: пересечение стоит дороже разворота, поэтому обход, которым
    действительно обходят фигуру, правило не отменяет.
    """
    for axis in (0, 1):
        steps = [b[axis] - a[axis] for a, b in zip(route, route[1:])
                 if abs(b[axis] - a[axis]) > _EPS]
        if not steps:
            continue
        forward = sum(s for s in steps if s > 0)
        backward = -sum(s for s in steps if s < 0)
        detour = min(forward, backward)
        if detour > _UTURN_TOLERANCE and detour > abs(forward - backward) * 0.5:
            return True
    return False


def _route_cost(route: Sequence[Point], obstacles: Optional[Obstacles],
                exclude: Sequence[str],
                corridors: Optional[Corridors] = None) -> Tuple[int, int, int, float]:
    """Чем маршрут хуже: пересечения, разворот, изломы, длина — в этом порядке.

    Разворот стоит сразу после пересечений: линия, ушедшая от цели и
    вернувшаяся, читается как ошибка чертежа. Уступает он только обходу — если
    вернуться назад можно лишь так, это лучше, чем пройти сквозь чужой шаг.

    Совпадение с уже проложенными связями входит в последний член: линия,
    легшая поверх соседней, не ошибка модели, но на карте её не видно.
    """
    pierced = obstacles.hits(route, exclude) if obstacles is not None else 0
    length = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route, route[1:]))
    if corridors is not None:
        length += _CORRIDOR_WEIGHT * corridors.overlap(route)
    return (pierced, int(_doubles_back(route)), max(len(route) - 2, 0), length)


# ── Трассировка в обход препятствий ─────────────────────────────────────────
#
# Перебор готовых форм («колено посередине», «обход поверху на 90 px») закрывает
# простые случаи и упирается в сложные: если между фигурами стоят три чужих
# шага, ни одна заготовка не подойдёт. Здесь строится настоящий маршрут.
#
# ПОЧЕМУ СЕТКА ХАНАНА, А НЕ ПИКСЕЛЬНАЯ. Ортогональный путь оптимальной длины
# всегда можно провести по линиям, проходящим через края препятствий: между
# двумя соседними краями поворачивать негде и незачем. Поэтому сетка строится
# из координат самих фигур — десятки линий вместо тысяч пикселей, и поиск по
# ней идёт за миллисекунды даже на карте в девятьсот фигур.
#
# Сетка локальная: берутся только препятствия рядом с самой связью. Строить её
# по всей карте бессмысленно — путь всё равно не уходит за пределы окрестности,
# а размер сетки растёт квадратично.

#: Насколько окрестность связи шире её собственной рамки, px. Меньше — и обход
#: вокруг крупного шага не поместится в сетку.
_NEIGHBOURHOOD = 320.0

#: Больше этого числа препятствий в окрестности — сетка становится слишком
#: большой, и поиск дороже пользы. Такие связи остаются на заготовках.
_MAX_LOCAL_OBSTACLES = 48

#: Штраф за поворот в стоимости пути. Ломаная из двух колен читается лучше, чем
#: из шести той же длины, поэтому поворот стоит как 60 px пути.
_BEND_PENALTY = 60.0


def _grid_lines(values: Sequence[float], limit: Tuple[float, float]) -> List[float]:
    """Уникальные координаты сетки внутри окрестности, по возрастанию."""
    low, high = limit
    seen = sorted({round(v, 1) for v in values if low - 1 <= v <= high + 1})
    return seen


def _clear(a: Point, b: Point, boxes: Sequence[Tuple[float, float, float, float]]) -> bool:
    return not any(_segment_in_box(a, b, box) for box in boxes)


def _astar_route(
    start: Point,
    d0: Direction,
    end: Point,
    d1: Direction,
    obstacles: Optional[Obstacles],
    exclude: Sequence[str],
) -> Optional[List[Point]]:
    """Кратчайший ортогональный путь, не задевающий чужие фигуры.

    Возвращает ``None``, если пути нет или окрестность слишком плотная: тогда
    вызывающий остаётся на заготовке. Пустой результат лучше кривого — связь
    без маршрута студия проведёт сама.
    """
    if obstacles is None:
        return None

    lo_x = min(start[0], end[0]) - _NEIGHBOURHOOD
    hi_x = max(start[0], end[0]) + _NEIGHBOURHOOD
    lo_y = min(start[1], end[1]) - _NEIGHBOURHOOD
    hi_y = max(start[1], end[1]) + _NEIGHBOURHOOD
    boxes = obstacles.within((lo_x, lo_y, hi_x - lo_x, hi_y - lo_y), exclude)
    if not boxes or len(boxes) > _MAX_LOCAL_OBSTACLES:
        return None

    # Ус: первый шаг обязан уйти по своей оси, иначе линия отойдёт от фигуры
    # вбок прямо на её границе.
    a = (start[0] + d0[0] * STUB, start[1] + d0[1] * STUB)
    b = (end[0] - d1[0] * STUB, end[1] - d1[1] * STUB)

    xs = [a[0], b[0]]
    ys = [a[1], b[1]]
    for x, y, w, h in boxes:
        xs.extend((x - _CLEARANCE - 1, x + w + _CLEARANCE + 1))
        ys.extend((y - _CLEARANCE - 1, y + h + _CLEARANCE + 1))
    xs = _grid_lines(xs, (lo_x, hi_x))
    ys = _grid_lines(ys, (lo_y, hi_y))
    if not xs or not ys:
        return None
    xi = {v: i for i, v in enumerate(xs)}
    yi = {v: i for i, v in enumerate(ys)}
    ax, ay = round(a[0], 1), round(a[1], 1)
    bx, by = round(b[0], 1), round(b[1], 1)
    if ax not in xi or ay not in yi or bx not in xi or by not in yi:
        return None

    start_cell = (xi[ax], yi[ay])
    goal_cell = (xi[bx], yi[by])

    def point(cell: Tuple[int, int]) -> Point:
        return (xs[cell[0]], ys[cell[1]])

    def heuristic(cell: Tuple[int, int]) -> float:
        px, py = point(cell)
        return abs(px - b[0]) + abs(py - b[1])

    # Состояние — клетка плюс ось, по которой в неё пришли: без этого поворот
    # не отличить от продолжения прямой, и штрафовать его нечем.
    open_heap: List[Tuple[float, float, Tuple[int, int], int]] = [
        (heuristic(start_cell), 0.0, start_cell, 0 if d0[0] else 1)
    ]
    best: Dict[Tuple[Tuple[int, int], int], float] = {(start_cell, 0 if d0[0] else 1): 0.0}
    came: Dict[Tuple[Tuple[int, int], int], Optional[Tuple[Tuple[int, int], int]]] = {
        (start_cell, 0 if d0[0] else 1): None
    }
    goal_axis = 0 if d1[0] else 1
    found: Optional[Tuple[Tuple[int, int], int]] = None

    while open_heap:
        _f, g, cell, axis = heapq.heappop(open_heap)
        if best.get((cell, axis), math.inf) < g - _EPS:
            continue
        if cell == goal_cell and axis == goal_axis:
            found = (cell, axis)
            break
        cx, cy = cell
        for naxis, ncell in (
            (0, (cx - 1, cy)), (0, (cx + 1, cy)),
            (1, (cx, cy - 1)), (1, (cx, cy + 1)),
        ):
            if not (0 <= ncell[0] < len(xs) and 0 <= ncell[1] < len(ys)):
                continue
            here, there = point(cell), point(ncell)
            if not _clear(here, there, boxes):
                continue
            step = abs(there[0] - here[0]) + abs(there[1] - here[1])
            cost = g + step + (_BEND_PENALTY if naxis != axis else 0.0)
            key = (ncell, naxis)
            if cost < best.get(key, math.inf) - _EPS:
                best[key] = cost
                came[key] = (cell, axis)
                heapq.heappush(open_heap, (cost + heuristic(ncell), cost, ncell, naxis))

    if found is None:
        return None

    path: List[Point] = []
    node: Optional[Tuple[Tuple[int, int], int]] = found
    while node is not None:
        path.append(point(node[0]))
        node = came[node]
    path.reverse()
    return _simplify([start, *path, end])


#: Оси, по которым можно попробовать увести связь, если прямой путь перекрыт.
_ALL_DIRECTIONS: Tuple[Direction, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _anchor_for(node: ProcessNode, direction: Direction,
                placed: Optional[Dict[str, Tuple[int, int]]], leaving: bool) -> Point:
    """Точка на грани, из которой линия уходит (или в которую приходит)."""
    if leaving:
        fx, fy = 0.5 + direction[0] * 0.5, 0.5 + direction[1] * 0.5
    else:
        fx, fy = 0.5 - direction[0] * 0.5, 0.5 - direction[1] * 0.5
    return anchor_point(node, fx, fy, placed)


def _vertical_link(
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
) -> Optional[Direction]:
    """Ось «сверху вниз», если фигуры стоят одна над другой и пересекаются по X.

    ``None`` — когда они разнесены вбок и вертикальная связь была бы кривее
    горизонтальной.
    """
    sx, sy = _origin(src, placed)
    tx, ty = _origin(tgt, placed)
    overlap = min(sx + src.geometry.width, tx + tgt.geometry.width) - max(sx, tx)
    if overlap < MIN_SEGMENT:
        return None
    if ty >= sy + src.geometry.height:
        return (0, 1)
    if sy >= ty + tgt.geometry.height:
        return (0, -1)
    return None



def _stub_reverses(start: Point, d0: Direction, end: Point) -> bool:
    """Ус уходит в сторону, противоположную цели.

    Так бывает, когда в стиле draw.io прописан якорь («выходить вправо»), а
    цель на самом деле слева: линия делает ус на 20 px вправо, разворачивается
    и идёт назад по себе же — прямо через фигуру, из которой только что вышла.
    На карте это выглядит как узел из слипшихся линий, и именно на такие места
    жалуются аналитики.

    Разворот считается настоящим, только если цель ЯВНО позади: сдвиг по
    перпендикуляру не должен перевешивать. Иначе под правило попадёт обычная
    связь к соседу сбоку, у которой ус чуть-чуть заходит за край, и разворотов
    станет больше, а не меньше — проверено замером.
    """
    along = (end[0] - start[0]) * d0[0] + (end[1] - start[1]) * d0[1]
    across = abs((end[0] - start[0]) * d0[1] - (end[1] - start[1]) * d0[0])
    return along < -STUB and abs(along) > across



def _unwind_bent_route(
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
    bends: Sequence[Point],
    d0: Direction,
    d1: Direction,
    obstacles: Optional[Obstacles],
    exclude: Sequence[str],
    fallback: List[Point],
) -> List[Point]:
    """Перебирает грани выхода и входа, пока ломаная не перестанет разворачиваться.

    Изломы аналитика остаются на месте — меняется только то, из какой грани
    линия выходит и в какую входит. Разворот почти всегда родом отсюда: ус
    уходит прочь от первого излома и тут же возвращается, пересекая фигуру, из
    которой вышел.
    """
    best = fallback
    best_cost = _route_cost(fallback, obstacles, exclude)
    for alt0 in _ALL_DIRECTIONS:
        for alt1 in _ALL_DIRECTIONS:
            start = _anchor_for(src, alt0, placed, leaving=True)
            end = _anchor_for(tgt, alt1, placed, leaving=False)
            route = _route_through_bends([start, *bends, end], alt0, alt1)
            cost = _route_cost(route, obstacles, exclude)
            if cost < best_cost:
                best, best_cost = route, cost
            if best_cost[:2] == (0, 0):
                return best
    return best


def _reroute_through_bends(
    start: Point,
    d0: Direction,
    bends: Sequence[Point],
    end: Point,
    d1: Direction,
    obstacles: Obstacles,
    exclude: Sequence[str],
    fallback: List[Point],
) -> List[Point]:
    """Маршрут по изломам аналитика, но в обход чужих фигур.

    Сначала пробуем сохранить его рисунок: каждый участок между соседними
    изломами прокладывается заново с обходом, а сами изломы остаются на месте.
    Если и так линия кого-то задевает — строим путь целиком заново, уже без
    изломов. Хуже потерять форму, чем оставить линию поперёк чужого шага.
    """
    stitched: List[Point] = [start]
    ok = True
    waypoints = [start, *bends, end]
    for index, (a, b) in enumerate(zip(waypoints, waypoints[1:])):
        first = index == 0
        last = index == len(waypoints) - 2
        leg_d0 = d0 if first else _axis_towards(a, b)
        leg_d1 = d1 if last else _axis_towards(a, b)
        leg = _astar_route(a, leg_d0, b, leg_d1, obstacles, exclude)
        if leg is None:
            # Прямой отрезок между изломами почти всегда косой, а связи в
            # студии имеют тип step: возвращаем колено, а не диагональ.
            elbow = (b[0], a[1]) if leg_d0[0] else (a[0], b[1])
            leg = _simplify([a, elbow, b])
        if _route_cost(leg, obstacles, exclude)[0]:
            ok = False
            break
        stitched.extend(leg[1:])
    if ok:
        cleaned = _simplify(stitched)
        if _route_cost(cleaned, obstacles, exclude)[0] == 0:
            return cleaned

    whole = _astar_route(start, d0, end, d1, obstacles, exclude)
    if whole is not None and _route_cost(whole, obstacles, exclude)[0] == 0:
        return whole
    return fallback


def _axis_towards(a: Point, b: Point) -> Direction:
    """Ось, по которой естественно идти от одной точки к другой."""
    if abs(b[0] - a[0]) >= abs(b[1] - a[1]):
        return (1, 0) if b[0] >= a[0] else (-1, 0)
    return (0, 1) if b[1] >= a[1] else (0, -1)


def _best_route(
    start: Point,
    d0: Direction,
    end: Point,
    d1: Direction,
    src: ProcessNode,
    tgt: ProcessNode,
    placed: Optional[Dict[str, Tuple[int, int]]],
    obstacles: Optional[Obstacles],
    exclude: Sequence[str],
    corridors: Optional[Corridors] = None,
) -> List[Point]:
    """Лучший маршрут: сначала по естественным граням, потом — в обход.

    Порядок важен. Сперва пробуем ту пару граней, которую диктует взаимное
    положение фигур: именно так связь нарисована в draw.io, и менять грань без
    нужды значит перерисовывать схему за аналитика. И только если по ней линия
    всё равно проходит сквозь чужую фигуру, перебираем остальные грани — уйти с
    правой грани на нижнюю лучше, чем прошить соседний шаг насквозь.
    """
    best = min(_candidate_routes(start, d0, end, d1),
               key=lambda r: _route_cost(r, obstacles, exclude, corridors))
    if obstacles is None:
        return best
    if _route_cost(best, obstacles, exclude)[:2] == (0, 0):
        return best

    # Заготовки не подошли — ищем путь по-настоящему.
    found = _astar_route(start, d0, end, d1, obstacles, exclude)
    if found is not None and _route_cost(found, obstacles, exclude)[0] == 0:
        return found

    best_cost = _route_cost(best, obstacles, exclude, corridors)
    for alt0 in _ALL_DIRECTIONS:
        for alt1 in _ALL_DIRECTIONS:
            if (alt0, alt1) == (d0, d1):
                continue
            alt_start = _anchor_for(src, alt0, placed, leaving=True)
            alt_end = _anchor_for(tgt, alt1, placed, leaving=False)
            for route in _candidate_routes(alt_start, alt0, alt_end, alt1):
                cost = _route_cost(route, obstacles, exclude, corridors)
                # Смена грани — уступка, и берём её только за меньшее число
                # пересечений, а не за пару пикселей длины.
                if cost[0] < best_cost[0] or (cost[0] == best_cost[0] and cost < best_cost):
                    best, best_cost = route, cost
        if best_cost[0] == 0:
            break
    return best


def _route_without_bends(start: Point, d0: Direction, end: Point, d1: Direction) -> List[Point]:
    """Классическая ломаная draw.io: ус — колено — ус."""
    a = (start[0] + d0[0] * STUB, start[1] + d0[1] * STUB)
    b = (end[0] - d1[0] * STUB, end[1] - d1[1] * STUB)
    horizontal_exit = d0[0] != 0
    horizontal_entry = d1[0] != 0

    middle: List[Point] = []
    if horizontal_exit and horizontal_entry:
        mid_x = (a[0] + b[0]) / 2
        middle = [(mid_x, a[1]), (mid_x, b[1])]
    elif not horizontal_exit and not horizontal_entry:
        mid_y = (a[1] + b[1]) / 2
        middle = [(a[0], mid_y), (b[0], mid_y)]
    elif horizontal_exit:
        middle = [(b[0], a[1])]
    else:
        middle = [(a[0], b[1])]

    return _simplify([start, a, *middle, b, end])


def _route_through_bends(points: Sequence[Point], d0: Direction, d1: Direction) -> List[Point]:
    """Проводит ортогональную ломаную через изломы, заданные в draw.io."""
    out: List[Point] = [points[0]]
    last = len(points) - 1
    for i in range(1, len(points)):
        prev, cur = out[-1], points[i]
        aligned = abs(prev[0] - cur[0]) < _EPS or abs(prev[1] - cur[1]) < _EPS
        if aligned:
            out.append(cur)
            continue
        if i == last:
            # Финальный отрезок должен входить в фигуру по своей оси.
            elbow = (prev[0], cur[1]) if d1[0] != 0 else (cur[0], prev[1])
        elif i == 1:
            elbow = (cur[0], prev[1]) if d0[0] != 0 else (prev[0], cur[1])
        else:
            prev2 = out[-2]
            was_horizontal = abs(prev2[1] - prev[1]) < _EPS
            elbow = (cur[0], prev[1]) if was_horizontal else (prev[0], cur[1])
        out.append(elbow)
        out.append(cur)
    return _simplify(out)


def _snap_to_pixel_grid(points: Sequence[Point]) -> List[Point]:
    """Округляет ломаную к целым и добивает выравнивание по осям.

    Ортогональность считалась в дробных координатах; после округления соседние
    точки могут разойтись на пиксель, и отрезок становится «почти
    горизонтальным» — в bpmn.io это видно как едва заметный скос.
    """
    snapped: List[Point] = [(float(round(x)), float(round(y))) for x, y in points]
    for i in range(1, len(snapped)):
        prev, cur = snapped[i - 1], snapped[i]
        dx, dy = abs(cur[0] - prev[0]), abs(cur[1] - prev[1])
        if dx == 0 or dy == 0:
            continue
        if dy <= 1:
            snapped[i] = (cur[0], prev[1])
        elif dx <= 1:
            snapped[i] = (prev[0], cur[1])
    return _simplify(snapped)


def orthogonal_waypoints(
    edge: ProcessEdge,
    src: Optional[ProcessNode],
    tgt: Optional[ProcessNode],
    placed: Optional[Dict[str, Tuple[int, int]]] = None,
    obstacles: Optional[Obstacles] = None,
    corridors: Optional[Corridors] = None,
) -> List[Point]:
    """Полная ломаная связи в абсолютных координатах карты.

    ``corridors`` копит уже проложенные связи: маршрут, повторяющий чужой,
    получает штраф и при равных прочих уходит на соседнюю линию сетки. Без
    этого две линии ложатся одна поверх другой и вторая пропадает с карты.
    """
    if src is None or tgt is None:
        return []

    # draw.io допускает якорь чуть за гранью фигуры (entryY=-0.017), и линия
    # там начинается «в воздухе». Импортёр ждёт точку на фигуре, поэтому долю
    # прижимаем к грани.
    def _frac(value: Optional[float]) -> Optional[float]:
        return None if value is None else min(1.0, max(0.0, value))

    exit_x = _frac(edge.exitX)
    exit_y = _frac(edge.exitY)
    entry_x = _frac(edge.entryX)
    entry_y = _frac(edge.entryY)

    d0 = exit_direction(edge, src, tgt, placed)
    d1 = entry_direction(edge, src, tgt, placed)

    # Пунктир к базе данных аналитик рисует одинаково: цилиндр стоит НАД шагом,
    # и связь идёт короткой вертикалью вниз, в верхнюю грань. Общее правило
    # выбирает ось по большему смещению центров, и при малейшем сдвиге вбок
    # линия уходила из боковой грани цилиндра и заворачивала — на карте это
    # выглядит петлёй там, где в draw.io прямая чёрточка.
    forced_vertical = False
    if edge.kind == 'association' and not edge.points:
        vertical = _vertical_link(src, tgt, placed)
        if vertical is not None:
            d0 = d1 = vertical
            # Ось поменяли — значит и якорь обязан переехать на ту грань, из
            # которой линия теперь выходит. Иначе конец остаётся на боковой
            # грани, а ус уходит вниз, и линия отрывается от фигуры.
            forced_vertical = True

    if forced_vertical:
        exit_x = exit_y = entry_x = entry_y = None

    # Если якорь не задан в стиле, берём середину грани, из которой выходим.
    if exit_x is None or exit_y is None:
        exit_x = 0.5 + d0[0] * 0.5
        exit_y = 0.5 + d0[1] * 0.5
    if entry_x is None or entry_y is None:
        entry_x = 0.5 - d1[0] * 0.5
        entry_y = 0.5 - d1[1] * 0.5

    start = anchor_point(src, exit_x, exit_y, placed)
    end = anchor_point(tgt, entry_x, entry_y, placed)

    # Якорь из стиля draw.io бывает противоречит расположению фигур. Доверяем
    # ему, пока он не разворачивает линию назад: связь, уходящая усом прочь от
    # цели, возвращается по себе же и пересекает собственную фигуру.
    if not edge.points:
        if _stub_reverses(start, d0, end):
            d0 = _dominant_axis(src, tgt, placed)
            start = anchor_point(src, 0.5 + d0[0] * 0.5, 0.5 + d0[1] * 0.5, placed)
        if _stub_reverses(end, (-d1[0], -d1[1]), start):
            d1 = _dominant_axis(src, tgt, placed)
            end = anchor_point(tgt, 0.5 - d1[0] * 0.5, 0.5 - d1[1] * 0.5, placed)

    if edge.points:
        # Изломы аналитика — его решение, как обвести схему, и по умолчанию мы
        # его повторяем. Но линия, идущая сквозь чужой шаг, читается как
        # ошибка независимо от того, кто её так нарисовал: если маршрут по
        # изломам задевает фигуру, к которой связь не подключена, он
        # перестраивается — сначала по тем же изломам, но с обходом между
        # ними, и лишь потом целиком заново.
        bends = [(float(p.x), float(p.y)) for p in edge.points]
        # Ус, направленный прочь от первого излома, разворачивает линию так же,
        # как и прочь от цели: проверяем по тому, куда аналитик повёл линию.
        if _stub_reverses(start, d0, bends[0]):
            d0 = _axis_towards(start, bends[0])
            start = anchor_point(src, 0.5 + d0[0] * 0.5, 0.5 + d0[1] * 0.5, placed)
        if _stub_reverses(end, (-d1[0], -d1[1]), bends[-1]):
            d1 = _axis_towards(bends[-1], end)
            end = anchor_point(tgt, 0.5 - d1[0] * 0.5, 0.5 - d1[1] * 0.5, placed)
        route = _route_through_bends([start, *bends, end], d0, d1)
        exclude = (src.id, tgt.id)
        if _doubles_back(route):
            route = _unwind_bent_route(
                src, tgt, placed, bends, d0, d1, obstacles, exclude, route)
        if obstacles is not None and _route_cost(route, obstacles, exclude)[0]:
            route = _reroute_through_bends(
                start, d0, bends, end, d1, obstacles, exclude, route)
    else:
        # Изломов аналитик не ставил — значит и лесенки в ломаной быть не
        # должно: если фигуры стоят друг напротив друга, связь прямая.
        start, end = _straighten(start, end, d0, d1, src, tgt, placed)
        exclude = (src.id, tgt.id)
        route = _best_route(
            start, d0, end, d1, src, tgt, placed, obstacles, exclude, corridors)
    return _drop_micro_jogs(_snap_to_pixel_grid(route), src, tgt, placed)


def point_stub(stub_id: str, x: float, y: float) -> ProcessNode:
    """Точечная «фигура» для трассировки конца связи, висящего на полосе."""
    return ProcessNode(
        id=stub_id,
        name='',
        type='textAnnotation',
        geometry=Geometry(x=int(round(x)) - 1, y=int(round(y)) - 1, width=2, height=2),
    )


def message_flow_endpoints(
    edge: ProcessEdge,
    node: ProcessNode,
    lane: ProcessNode,
    lane_is_source: bool,
) -> Tuple[ProcessNode, ProcessNode]:
    """Пара фигур для трассировки связи «шаг ↔ полоса внешнего участника».

    Полоса тянется на всю ширину карты, поэтому её центр как якорь не годится:
    линия ушла бы через полсхемы. Берём точку, которую нарисовал аналитик
    (свободный конец в draw.io), иначе — проекцию шага на ближайшую грань полосы.
    """
    free = edge.sourcePoint if lane_is_source else edge.targetPoint
    if free is not None:
        stub = point_stub(f'{edge.id}__lane', free.x, free.y)
    else:
        cx = node.geometry.x + node.geometry.width / 2
        lane_bottom = lane.geometry.y + lane.geometry.height
        y = lane_bottom if node.geometry.y >= lane_bottom else lane.geometry.y
        stub = point_stub(f'{edge.id}__lane', cx, y)
    return (stub, node) if lane_is_source else (node, stub)
