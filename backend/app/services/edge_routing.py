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

from typing import Dict, List, Optional, Sequence, Tuple

from app.models.process import ProcessEdge, ProcessNode

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
            same_x = abs(prev[0] - pt[0]) < _EPS and abs(pt[0] - nxt[0]) < _EPS
            same_y = abs(prev[1] - pt[1]) < _EPS and abs(pt[1] - nxt[1]) < _EPS
            if same_x or same_y:
                continue
        cleaned.append(pt)
    return cleaned


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
) -> List[Point]:
    """Полная ломаная связи в абсолютных координатах карты."""
    if src is None or tgt is None:
        return []

    exit_x = edge.exitX if edge.exitX is not None else None
    exit_y = edge.exitY if edge.exitY is not None else None
    entry_x = edge.entryX if edge.entryX is not None else None
    entry_y = edge.entryY if edge.entryY is not None else None

    d0 = exit_direction(edge, src, tgt, placed)
    d1 = entry_direction(edge, src, tgt, placed)

    # Если якорь не задан в стиле, берём середину грани, из которой выходим.
    if exit_x is None or exit_y is None:
        exit_x = 0.5 + d0[0] * 0.5
        exit_y = 0.5 + d0[1] * 0.5
    if entry_x is None or entry_y is None:
        entry_x = 0.5 - d1[0] * 0.5
        entry_y = 0.5 - d1[1] * 0.5

    start = anchor_point(src, exit_x, exit_y, placed)
    end = anchor_point(tgt, entry_x, entry_y, placed)

    if edge.points:
        bends = [(float(p.x), float(p.y)) for p in edge.points]
        return _snap_to_pixel_grid(_route_through_bends([start, *bends, end], d0, d1))
    return _snap_to_pixel_grid(_route_without_bends(start, d0, end, d1))
