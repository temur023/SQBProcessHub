"""Раскладка дорожек и обход фигур — рефакторинг геометрии выгрузки.

Три вещи, которые ломались молча и обнаруживались только глазами в PIX:
дорожки не замощали пул, связи с данными выгружались не тем элементом, а
линии шли сквозь чужие фигуры. Каждой отвечает свой раздел.
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessEdge, ProcessNode
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.drawio_parser import parse_bpmn_xml, parse_drawio_xml
import app.services.edge_routing as er
from app.services.edge_routing import (
    Corridors,
    Obstacles,
    build_obstacles,
    orthogonal_waypoints,
)
from app.services.map_layout import build_lane_stack, pool_bounds, stack_lanes

BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
DI = 'http://www.omg.org/spec/BPMN/20100524/DI'
DC = 'http://www.omg.org/spec/DD/20100524/DC'


# Дорожки нарисованы со щелью в 120 px и разной шириной — ровно то, что
# получается, когда аналитик двигает полосы мышью.
GAPPED = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="laneA" value="Фронт-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="1200" height="300" as="geometry" />
        </mxCell>
        <mxCell id="laneB" value="Бэк-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="420" width="1000" height="260" as="geometry" />
        </mxCell>
        <mxCell id="s" value="Начало" style="ellipse;fillColor=#10b981;" vertex="1" parent="laneA">
          <mxGeometry x="80" y="120" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Проверка 5 мин" style="rounded=1;" vertex="1" parent="laneA">
          <mxGeometry x="220" y="105" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Оформление 3 мин" style="rounded=1;" vertex="1" parent="laneB">
          <mxGeometry x="220" y="100" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="db" value="IABS" style="shape=datastore;" vertex="1" parent="laneA">
          <mxGeometry x="640" y="60" width="80" height="60" as="geometry" />
        </mxCell>
        <mxCell id="f1" edge="1" source="s" target="t1" parent="laneA" />
        <mxCell id="f2" edge="1" source="t1" target="t2" parent="1">
          <mxGeometry relative="1" as="geometry">
            <Array as="points"><mxPoint x="500" y="145" /><mxPoint x="500" y="540" /></Array>
          </mxGeometry>
        </mxCell>
        <mxCell id="a1" style="dashed=1;" edge="1" source="db" target="t1" parent="laneA" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def _on_polyline(point, route, tol=4.0):
    """Проходит ли ломаная через точку — не обязательно своей вершиной."""
    px, py = point
    for (x1, y1), (x2, y2) in zip(route, route[1:]):
        if not (min(x1, x2) - tol <= px <= max(x1, x2) + tol):
            continue
        if not (min(y1, y2) - tol <= py <= max(y1, y2) + tol):
            continue
        if abs(x1 - x2) < 0.5 and abs(px - x1) <= tol:
            return True
        if abs(y1 - y2) < 0.5 and abs(py - y1) <= tol:
            return True
    return False


def _shapes(xml: str):
    root = ET.fromstring(xml.encode('utf-8'))
    out = {}
    for shape in root.iter(f'{{{DI}}}BPMNShape'):
        bounds = shape.find(f'{{{DC}}}Bounds')
        if bounds is not None:
            out[shape.get('bpmnElement')] = tuple(
                float(bounds.get(k)) for k in ('x', 'y', 'width', 'height'))
    return root, out


class LaneStackTest(unittest.TestCase):
    """Дорожки обязаны замощать пул: щель и нахлёст импортёр правит по-своему."""

    def setUp(self):
        self.process = parse_drawio_xml(GAPPED, 'lanes.drawio')

    def test_source_map_really_has_a_gap(self):
        # Без этого тест ничего не проверяет: щель должна быть в исходнике.
        lanes = sorted(self.process.lanes, key=lambda l: l.geometry.y)
        self.assertGreater(
            lanes[1].geometry.y - (lanes[0].geometry.y + lanes[0].geometry.height), 100)

    def test_lanes_become_contiguous(self):
        stacked, _ = build_lane_stack(self.process)
        for above, below in zip(stacked, stacked[1:]):
            self.assertEqual(below.geometry.y, above.geometry.y + above.geometry.height)

    def test_pool_is_exactly_the_union_of_its_lanes(self):
        stacked, _ = build_lane_stack(self.process)
        x, y, w, h = pool_bounds(stacked)
        self.assertEqual(y, stacked[0].geometry.y)
        self.assertEqual(y + h, stacked[-1].geometry.y + stacked[-1].geometry.height)
        self.assertEqual(h, sum(l.geometry.height for l in stacked))

    def test_content_moves_with_its_lane(self):
        # Сдвинув дорожку и оставив шаги на месте, мы получили бы карту хуже
        # исходной: шаги оказались бы в чужих дорожках.
        moved = stack_lanes(self.process)
        lane_of = {l.id: l for l in moved.lanes}
        for node in moved.nodes:
            if not node.laneId:
                continue
            lane = lane_of[node.laneId]
            self.assertGreaterEqual(node.geometry.y, lane.geometry.y - 1)
            self.assertLessEqual(
                node.geometry.y + node.geometry.height,
                lane.geometry.y + lane.geometry.height + 1,
                node.name)

    def test_bends_move_with_their_band(self):
        # Излом, нарисованный в нижней дорожке, обязан уехать вместе с ней:
        # иначе связь пойдёт мимо своей же фигуры.
        before = {e.id: [(p.x, p.y) for p in e.points] for e in self.process.edges}
        moved = stack_lanes(self.process)
        after = {e.id: [(p.x, p.y) for p in e.points] for e in moved.edges}
        bent = [eid for eid, pts in before.items() if len(pts) >= 2]
        self.assertTrue(bent, 'фикстура должна содержать связь с изломами')
        for eid in bent:
            self.assertNotEqual(before[eid], after[eid], 'изломы не переехали')

    def test_lanes_tile_the_pool_in_the_exported_di(self):
        _root, shapes = _shapes(generate_bpmn_xml(self.process))
        root = ET.fromstring(generate_bpmn_xml(self.process).encode('utf-8'))
        lanes = [shapes[l.get('id')] for l in root.iter(f'{{{BPMN}}}lane')
                 if l.get('id') in shapes]
        lanes.sort(key=lambda b: b[1])
        self.assertGreaterEqual(len(lanes), 2)
        for above, below in zip(lanes, lanes[1:]):
            self.assertAlmostEqual(below[1], above[1] + above[3], delta=1)
        pools = [shapes[p.get('id')] for p in root.iter(f'{{{BPMN}}}participant')
                 if p.get('id') in shapes]
        self.assertTrue(pools)
        self.assertAlmostEqual(pools[0][1], lanes[0][1], delta=1)
        self.assertAlmostEqual(pools[0][1] + pools[0][3], lanes[-1][1] + lanes[-1][3], delta=1)

    def test_already_stacked_map_is_returned_untouched(self):
        once = stack_lanes(self.process)
        twice = stack_lanes(once)
        self.assertIs(twice, once, 'повторная укладка не должна копировать модель')


class DataAssociationTest(unittest.TestCase):
    """Хранилище подключается к шагу как данные, а не как артефакт."""

    def setUp(self):
        self.process = parse_drawio_xml(GAPPED, 'lanes.drawio')
        self.xml = generate_bpmn_xml(self.process)

    def test_link_to_the_store_is_a_data_association(self):
        self.assertIn('<bpmn:dataInputAssociation', self.xml)
        self.assertNotIn('<bpmn:association', self.xml)

    def test_round_trip_keeps_the_link(self):
        # Собственную выгрузку платформа обязана прочитать обратно: иначе при
        # повторном импорте со всех шагов отваливаются системы и документы.
        reopened = parse_bpmn_xml(self.xml, 'lanes.bpmn')
        kinds = [e.kind for e in reopened.edges]
        self.assertIn('association', kinds)
        stores = {n.id for n in reopened.nodes if n.type == 'dataStore'}
        self.assertTrue(stores)
        linked = {e.sourceId for e in reopened.edges if e.kind == 'association'}
        linked |= {e.targetId for e in reopened.edges if e.kind == 'association'}
        self.assertTrue(stores & linked, 'хранилище потеряло связь при обратном чтении')


class ObstacleRoutingTest(unittest.TestCase):
    """Линия обходит чужую фигуру, а не проходит сквозь неё."""

    def _pair(self):
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=400, y=0, width=100, height=60))
        return left, right

    def test_direct_route_is_kept_when_nothing_blocks(self):
        left, right = self._pair()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, left, right, None, build_obstacles([left, right]))
        self.assertEqual(len(route), 2, f'прямой путь свободен, а получили {route}')

    def test_route_goes_around_a_shape_in_the_way(self):
        left, right = self._pair()
        wall = ProcessNode(id='w', name='W', type='userTask',
                           geometry=Geometry(x=200, y=-40, width=100, height=140))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        obstacles = build_obstacles([left, right, wall])
        route = orthogonal_waypoints(edge, left, right, None, obstacles)
        self.assertEqual(obstacles.hits(route, ('a', 'b')), 0,
                         f'линия всё ещё идёт сквозь фигуру: {route}')

    def test_analyst_bends_are_translated_as_drawn(self):
        """Изломы аналитика — его решение, как обвести схему, и мы их не трогаем.

        Проверяем не «точка есть среди waypoint», а «линия через неё проходит»:
        излом, попавший в середину прямого участка, вершиной быть не обязан.
        """
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=400, y=300, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 250, 'y': 30}, {'x': 250, 'y': 330}])
        route = orthogonal_waypoints(edge, left, right)
        for bend in ((250, 30), (250, 330)):
            self.assertTrue(_on_polyline(bend, route),
                            f'излом {bend} потерялся: {route}')

    def test_a_turn_back_bend_is_not_simplified_away(self):
        # Разворот (сосед по одну сторону и по другую совпадают) внутренней
        # точкой прямого отрезка не является: это вершина обхода.
        left, right = self._pair()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 250, 'y': -140}])
        route = orthogonal_waypoints(edge, left, right)
        self.assertTrue(any(y <= -130 for _x, y in route),
                        f'вершина обхода стёрта: {route}')

    def test_a_shape_owns_its_badge(self):
        # Значок длительности принадлежит шагу: связь, идущая в этот шаг,
        # проходит рядом со значком по праву и пересечением не считается.
        obstacles = Obstacles(
            [('duration:a', (0.0, 60.0, 48.0, 48.0))], {'duration:a': 'a'})
        route = [(24.0, 0.0), (24.0, 120.0)]
        self.assertEqual(obstacles.hits(route, ('a',)), 0)
        self.assertEqual(obstacles.hits(route, ('zzz',)), 1)


class PierceToleranceTest(unittest.TestCase):
    """Насколько глубоко линии позволено зайти в фигуру."""

    def test_a_line_just_inside_a_shape_counts_as_a_pierce(self):
        """Допуск — на округление, а не на «немного внутри».

        Проверка сжимала рамку на весь запас обхода (10 px) и потому не
        замечала линию, врезавшуюся в фигуру на девять пикселей: у значка
        времени в 48 px «неприкосновенной» оставалась середина 28×28.
        Трассировщик считал такие маршруты чистыми и обход не искал.
        """
        box = (0.0, 0.0, 48.0, 48.0)
        # Линия в пяти пикселях от края — уже внутри фигуры.
        self.assertTrue(er._segment_in_box((-20.0, 5.0), (80.0, 5.0), box))
        # Ровно по грани — ещё нет.
        self.assertFalse(er._segment_in_box((-20.0, 0.0), (80.0, 0.0), box))

    def test_clearance_is_wider_than_tolerance(self):
        # Обходим с запасом, а засчитываем пересечение по факту.
        self.assertGreater(er._CLEARANCE, er._PIERCE_TOLERANCE)


class AstarRoutingTest(unittest.TestCase):
    """Поиск пути там, где заготовок не хватает."""

    def _wall_of_three(self):
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=600, y=0, width=100, height=60))
        # Сплошная стена из трёх фигур между ними: ни одно колено не пройдёт.
        wall = [
            ProcessNode(id=f'w{i}', name='W', type='userTask',
                        geometry=Geometry(x=200 + i * 110, y=-160, width=100, height=380))
            for i in range(3)
        ]
        return left, right, wall

    def test_route_goes_around_a_wall(self):
        left, right, wall = self._wall_of_three()
        obstacles = build_obstacles([left, right, *wall])
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, left, right, None, obstacles)
        self.assertEqual(obstacles.hits(route, ('a', 'b')), 0,
                         f'линия прошла сквозь стену: {route}')
        self.assertGreater(len(route), 2, 'обход не может быть прямой линией')

    def test_route_stays_orthogonal(self):
        left, right, wall = self._wall_of_three()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, left, right, None,
                                     build_obstacles([left, right, *wall]))
        for (x1, y1), (x2, y2) in zip(route, route[1:]):
            self.assertTrue(abs(x2 - x1) < 0.5 or abs(y2 - y1) < 0.5,
                            f'косой отрезок в обходе: {route}')

    def test_analyst_bends_give_way_to_a_pierced_shape(self):
        """Изломы аналитика повторяем, пока линия никого не задевает.

        Линия поперёк чужого шага читается как ошибка независимо от того, кто
        её так нарисовал, поэтому маршрут перестраивается.
        """
        left, right, wall = self._wall_of_three()
        obstacles = build_obstacles([left, right, *wall])
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 350, 'y': 30}])
        route = orthogonal_waypoints(edge, left, right, None, obstacles)
        self.assertEqual(obstacles.hits(route, ('a', 'b')), 0,
                         f'излом протащил линию сквозь фигуру: {route}')


class StubDirectionTest(unittest.TestCase):
    """Ус не должен уходить в сторону, противоположную цели."""

    def test_anchor_from_the_style_does_not_turn_the_line_back(self):
        # В стиле draw.io написано «выходить вправо», а цель слева: без
        # проверки линия делала ус вправо, разворачивалась и шла назад по себе
        # же — прямо через фигуру, из которой только что вышла.
        source = ProcessNode(id='a', name='A', type='userTask',
                             geometry=Geometry(x=400, y=0, width=100, height=60))
        target = ProcessNode(id='b', name='B', type='userTask',
                             geometry=Geometry(x=0, y=0, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b', exitX=1.0, exitY=0.5)
        route = orthogonal_waypoints(edge, source, target)
        self.assertLessEqual(route[1][0], route[0][0] + 0.5,
                             f'ус ушёл от цели: {route}')

    def test_a_genuine_side_anchor_is_respected(self):
        # Цель сбоку, а не позади: якорь аналитика остаётся в силе.
        source = ProcessNode(id='a', name='A', type='userTask',
                             geometry=Geometry(x=0, y=0, width=100, height=60))
        target = ProcessNode(id='b', name='B', type='userTask',
                             geometry=Geometry(x=400, y=0, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b', exitX=1.0, exitY=0.5)
        route = orthogonal_waypoints(edge, source, target)
        self.assertGreater(route[1][0], route[0][0], f'ус развернулся зря: {route}')


class CorridorTest(unittest.TestCase):
    """Две связи не должны лечь одна поверх другой."""

    def test_overlap_is_measured_by_length(self):
        corridors = Corridors()
        corridors.occupy([(0.0, 100.0), (200.0, 100.0)])
        # Полное совпадение.
        self.assertAlmostEqual(corridors.overlap([(50.0, 100.0), (150.0, 100.0)]), 100.0)
        # Соседняя линия — уже не совпадение.
        self.assertEqual(corridors.overlap([(50.0, 116.0), (150.0, 116.0)]), 0.0)
        # Пересечение под прямым углом наложением не является.
        self.assertEqual(corridors.overlap([(100.0, 0.0), (100.0, 200.0)]), 0.0)

    def test_second_line_prefers_a_free_corridor(self):
        # Один и тот же пролёт, две связи: вторая обязана уйти в сторону.
        top = ProcessNode(id='a', name='A', type='userTask',
                          geometry=Geometry(x=0, y=0, width=100, height=200))
        bottom = ProcessNode(id='b', name='B', type='userTask',
                             geometry=Geometry(x=400, y=0, width=100, height=200))
        obstacles = build_obstacles([top, bottom])
        corridors = Corridors()
        first = orthogonal_waypoints(
            ProcessEdge(id='e1', sourceId='a', targetId='b'), top, bottom, None,
            obstacles, corridors)
        corridors.occupy(first)
        second = orthogonal_waypoints(
            ProcessEdge(id='e2', sourceId='a', targetId='b',
                        points=[{'x': 250, 'y': 260}]),
            top, bottom, None, obstacles, corridors)
        self.assertLess(corridors.overlap(second), sum(
            abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(second, second[1:])),
            'вторая связь целиком легла на первую')



if __name__ == '__main__':
    unittest.main()
