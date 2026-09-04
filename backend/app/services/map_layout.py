"""Нормализация раскладки карты перед выгрузкой.

Карта приезжает из draw.io в абсолютных координатах, которые аналитик набрал
мышью. Для холста платформы этого достаточно — она рисует ровно то, что задано.
Импортёрам BPMN и Процессной студии — нет: они раскладывают дорожки по своим
правилам и, встретив геометрию, которая этим правилам противоречит, начинают
исправлять её сами. Результат непредсказуем: в PIX меньшие дорожки уходят под
большие и исчезают с карты.

Главное правило, которое здесь и восстанавливается: **дорожки замощают пул**.
Идут строго друг под другом, без щелей и нахлёстов, суммарной высотой в высоту
пула. В спецификации BPMN это не рекомендация, а свойство модели: дорожка —
раздел пула, а не самостоятельный прямоугольник где-то на холсте.

ПОЧЕМУ ПЕРЕКЛАДЫВАТЬ НАДО ВСЁ, А НЕ ТОЛЬКО ДОРОЖКИ

Сдвинув дорожку и оставив её шаги на месте, мы получим карту хуже исходной:
шаги окажутся в чужих дорожках, а связи будут вести в пустоту. Поэтому сдвиг
дорожки переносится на всё, что в ней лежит, — и на фигуры, и на изломы линий,
и на свободные концы. Технически это кусочно-постоянное отображение оси Y:
внутри полосы каждой дорожки сдвиг один и тот же, а сама функция ``remap``
строится один раз и применяется ко всем координатам разом. Так раскладка не
может «поехать» частично — либо переносится вся полоса, либо ничего.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from app.models.process import BusinessProcess, Geometry, ProcessEdge, ProcessNode

#: Запас между нижним краем содержимого дорожки и её границей, px. Без него
#: значок длительности последнего шага упирается в линию соседней дорожки.
_LANE_PADDING = 24

#: Минимальная высота дорожки: пустая полоса участника всё равно должна быть
#: видна и подписана.
_MIN_LANE_HEIGHT = 80


class YRemap:
    """Кусочно-постоянный сдвиг оси Y: полоса дорожки -> её новое место.

    Внутри полосы сдвиг одинаков для всего — фигур, изломов, свободных концов.
    Координата выше первой дорожки едет вместе с первой, ниже последней — с
    последней: иначе заголовок карты и подписи под нижней дорожкой оторвались
    бы от содержимого.
    """

    def __init__(self, bands: Sequence[Tuple[float, float, float]]):
        #: (низ полосы, верх полосы, сдвиг) в СТАРЫХ координатах.
        self._bands = sorted(bands, key=lambda b: b[0])

    def __call__(self, y: float) -> float:
        if not self._bands:
            return y
        for low, high, shift in self._bands:
            if low <= y < high:
                return y + shift
        if y < self._bands[0][0]:
            return y + self._bands[0][2]
        return y + self._bands[-1][2]

    @property
    def identity(self) -> bool:
        return all(abs(shift) < 0.5 for _, _, shift in self._bands)


def _content_height(lane: ProcessNode, members: Sequence[ProcessNode]) -> int:
    """Высота дорожки, при которой её содержимое помещается целиком."""
    if not members:
        return max(lane.geometry.height, _MIN_LANE_HEIGHT)
    bottom = max(n.geometry.y + n.geometry.height for n in members)
    needed = int(round(bottom - lane.geometry.y)) + _LANE_PADDING
    return max(lane.geometry.height, needed, _MIN_LANE_HEIGHT)


def build_lane_stack(process: BusinessProcess) -> Tuple[List[ProcessNode], YRemap]:
    """Дорожки, выстроенные в сплошную стопку, и сдвиг оси Y для остального.

    Возвращает НОВЫЕ дорожки (копии) и функцию переноса координат. Высоты
    сохраняются, если содержимое помещается: раскладку аналитика мы не
    перерисовываем, а только убираем щели и нахлёсты между полосами.
    """
    lanes = sorted(process.lanes or [], key=lambda l: (l.geometry.y, l.geometry.x))
    if not lanes:
        return [], YRemap(())

    members: Dict[str, List[ProcessNode]] = {l.id: [] for l in lanes}
    for node in process.nodes:
        if node.laneId in members:
            members[node.laneId].append(node)

    stacked: List[ProcessNode] = []
    bands: List[Tuple[float, float, float]] = []
    cursor = float(lanes[0].geometry.y)
    for lane in lanes:
        height = _content_height(lane, members[lane.id])
        shift = cursor - lane.geometry.y
        copy = lane.model_copy(deep=True)
        copy.geometry = Geometry(
            x=lane.geometry.x,
            y=int(round(cursor)),
            width=lane.geometry.width,
            height=height,
        )
        stacked.append(copy)
        # Полоса берётся по СТАРОЙ высоте дорожки: по ней содержимое и
        # распределено. Выросшая высота добавляет место снизу, внутрь полосы.
        bands.append((float(lane.geometry.y),
                      float(lane.geometry.y + max(lane.geometry.height, height)),
                      shift))
        cursor += height
    return stacked, YRemap(bands)


def _move_node(node: ProcessNode, remap: YRemap) -> ProcessNode:
    geo = node.geometry
    new_y = int(round(remap(geo.y)))
    if new_y == geo.y:
        return node
    copy = node.model_copy(deep=True)
    copy.geometry = Geometry(x=geo.x, y=new_y, width=geo.width, height=geo.height)
    return copy


def _move_edge(edge: ProcessEdge, remap: YRemap) -> ProcessEdge:
    """Изломы и свободные концы едут вместе с полосой, в которой нарисованы."""
    changed = False
    points = []
    for point in edge.points:
        new_y = int(round(remap(point.y)))
        changed = changed or new_y != point.y
        points.append(point.model_copy(update={'y': new_y}))

    def _free(end):
        nonlocal changed
        if end is None:
            return None
        new_y = int(round(remap(end.y)))
        changed = changed or new_y != end.y
        return end.model_copy(update={'y': new_y})

    source_point = _free(edge.sourcePoint)
    target_point = _free(edge.targetPoint)
    if not changed:
        return edge
    return edge.model_copy(update={
        'points': points,
        'sourcePoint': source_point,
        'targetPoint': target_point,
    })


def stack_lanes(process: BusinessProcess) -> BusinessProcess:
    """Копия процесса, в которой дорожки замощают пул без щелей и нахлёстов.

    Ничего не делает, если дорожек нет или они уже лежат стопкой: лишняя копия
    большой модели на каждой выгрузке ни к чему.
    """
    stacked, remap = build_lane_stack(process)
    if not stacked or remap.identity:
        return process

    moved = process.model_copy()
    moved.lanes = stacked
    moved.nodes = [_move_node(n, remap) for n in process.nodes]
    moved.edges = [_move_edge(e, remap) for e in process.edges]
    return moved


def pool_bounds(lanes: Sequence[ProcessNode]) -> Optional[Tuple[int, int, int, int]]:
    """Рамка пула по его дорожкам: ровно их объединение, без запаса.

    Пул, объявленный шире своих дорожек, оставляет вдоль края полосу, которая
    не принадлежит ни одной из них: импортёр показывает её пустой строкой, а
    фигуру, попавшую туда, — вне всяких дорожек.
    """
    if not lanes:
        return None
    x = min(l.geometry.x for l in lanes)
    y = min(l.geometry.y for l in lanes)
    right = max(l.geometry.x + l.geometry.width for l in lanes)
    bottom = max(l.geometry.y + l.geometry.height for l in lanes)
    return int(x), int(y), int(right - x), int(bottom - y)
