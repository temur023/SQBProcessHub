"""Как карта выглядит после импорта в Процессную студию.

Проверки здесь — про геометрию, а не про формат: файл может быть безупречен по
схеме и при этом открываться в студии кашей. Аналитик жаловался ровно на три
вещи, и каждой отвечает свой раздел: линии не доходят до фигур, подписи лежат
поверх линий, фигуры налезают друг на друга.

Эталон один и тот же — выгрузка самой PIX ``tests/fixtures/sap.pmm``: 66 фигур,
на каждый тип ровно один размер, самый короткий отрезок ломаной 8 px.
"""
import io
import os
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import step_duration_text
from app.services.layout import wrapped_line_count
from app.services.edge_routing import MIN_SEGMENT, orthogonal_waypoints
from app.services.pmm_exporter import (
    canonical_size,
    generate_pmm_zip,
    label_position,
    map_label,
    normalize_geometry,
    separate_artifacts,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def _map_root(process):
    package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(process)))
    part = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return ET.fromstring(package.read(part))


def _boxes(root):
    """Абсолютные рамки всех фигур: дети дорожек лежат в относительных."""
    out = {}

    def walk(element, ox, oy):
        for child in element:
            if child.tag != 'node':
                continue
            x = float(child.get('x', 0)) + ox
            y = float(child.get('y', 0)) + oy
            out[child.get('id')] = {
                'box': (x, y, float(child.get('width', 0)), float(child.get('height', 0))),
                'type': child.get('type'),
                'label': child.get('label') or '',
            }
            walk(child, x, y)

    walk(root, 0.0, 0.0)
    return out


def _waypoints(connector):
    return [
        (float(w.get('x')), float(w.get('y')))
        for w in sorted(connector.findall('waypoint'), key=lambda w: int(w.get('index', 0)))
    ]


def _gap(point, box):
    x, y = point
    bx, by, bw, bh = box
    dx = max(bx - x, 0.0, x - (bx + bw))
    dy = max(by - y, 0.0, y - (by + bh))
    return (dx * dx + dy * dy) ** 0.5


DRAWIO = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="lane" value="Операционный отдел" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="1400" height="400" as="geometry" />
        </mxCell>
        <mxCell id="s" value="Начало" style="ellipse;fillColor=#10b981;" vertex="1" parent="lane">
          <mxGeometry x="80" y="120" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Проверка документов 5 мин" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="220" y="105" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="g" value="Всё верно?" style="rhombus;" vertex="1" parent="lane">
          <mxGeometry x="480" y="115" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Оформление 10 мин" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="620" y="105" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="db" value="IABS" style="shape=cylinder3;" vertex="1" parent="lane">
          <mxGeometry x="300" y="200" width="80" height="40" as="geometry" />
        </mxCell>
        <mxCell id="e" value="Готово" style="ellipse;fillColor=#059669;" vertex="1" parent="lane">
          <mxGeometry x="900" y="120" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="f1" edge="1" source="s" target="t1" parent="lane" />
        <mxCell id="f2" edge="1" source="t1" target="g" parent="lane" />
        <mxCell id="f3" value="Да" edge="1" source="g" target="t2" parent="lane">
          <mxGeometry relative="1" as="geometry"><mxPoint as="offset" /></mxGeometry>
        </mxCell>
        <mxCell id="f4" edge="1" source="t2" target="e" parent="lane" />
        <mxCell id="a1" style="dashed=1;" edge="1" source="db" target="t1" parent="lane" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


class CanonicalSizeTest(unittest.TestCase):
    """Фигура обязана быть того размера, которым её рисует студия.

    Студия рисует событие шаблоном в 48 px и шлюз в 60 px, а линию тянет к той
    рамке, которую объявили мы. Пока рамка и шаблон расходятся, стрелка не
    доходит до фигуры или въезжает внутрь неё — с этого и начинается «карта
    рисуется некрасиво».
    """

    def setUp(self):
        self.process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        self.root = _map_root(self.process)
        self.boxes = _boxes(self.root)

    def test_events_gateways_and_storage_match_the_studio_template(self):
        expected = {
            'start_event_none': (48, 48),
            'end_event_none': (48, 48),
            'gateway_xor': (60, 60),
            'boundary_non_interrupting_event_timer': (48, 48),
        }
        seen = {}
        for item in self.boxes.values():
            if item['type'] in expected:
                seen.setdefault(item['type'], set()).add(
                    (int(item['box'][2]), int(item['box'][3])))
        for kind, size in expected.items():
            self.assertIn(kind, seen, f'{kind} не попал в карту')
            self.assertEqual(seen[kind], {size}, kind)

    def test_artifact_keeps_the_size_the_analyst_drew(self):
        """Цилиндр базы рисуется таким, каким его нарисовали.

        Приведение к 62 × 56 из выгрузки студии делало его вдвое крупнее
        нарисованного (в картах банка 30 × 25 … 50 × 36) и заметнее самого
        шага, к которому он относится. Подпись у артефакта печатается снаружи,
        так что места под текст внутри ему не нужно.
        """
        self.assertIsNone(canonical_size('dataStorage'))
        sizes = {
            (int(i['box'][2]), int(i['box'][3]))
            for i in self.boxes.values() if i['type'] == 'dataStorage'
        }
        self.assertEqual(sizes, {(80, 40)}, 'размер цилиндра из draw.io не сохранён')

    def test_task_keeps_the_size_the_analyst_drew(self):
        # Размер задачи несёт смысл — длину подписи; шаблоном он не задан.
        self.assertIsNone(canonical_size('userTask'))
        widths = {
            int(i['box'][2]) for i in self.boxes.values() if i['type'] == 'task'
        }
        self.assertIn(180, widths)

    def test_resize_happens_around_the_centre(self):
        # Иначе схема поедет: шлюз, выросший с 50 до 60, утянул бы за собой
        # линию, которая к нему подходит.
        node = ProcessNode(id='g', name='', type='exclusiveGateway',
                           geometry=Geometry(x=100, y=100, width=50, height=50))
        process = self.process.model_copy()
        process.nodes = [node]
        moved = normalize_geometry(process).nodes[0].geometry
        self.assertEqual((moved.width, moved.height), (60, 60))
        self.assertEqual(moved.x + moved.width / 2, 125)
        self.assertEqual(moved.y + moved.height / 2, 125)

    def test_original_process_is_not_mutated(self):
        before = [(n.geometry.width, n.geometry.height) for n in self.process.nodes]
        normalize_geometry(self.process)
        after = [(n.geometry.width, n.geometry.height) for n in self.process.nodes]
        self.assertEqual(before, after)


class ConnectorGeometryTest(unittest.TestCase):
    """Линия обязана начинаться и кончаться на фигуре и идти по осям."""

    def setUp(self):
        self.process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        self.root = _map_root(self.process)
        self.boxes = _boxes(self.root)

    def test_every_polyline_touches_both_shapes(self):
        for connector in self.root.findall('connector'):
            route = _waypoints(connector)
            if not route:
                continue
            source = self.boxes[connector.get('sourceNodeId')]['box']
            target = self.boxes[connector.get('targetNodeId')]['box']
            self.assertLessEqual(_gap(route[0], source), 1.0, connector.get('id'))
            self.assertLessEqual(_gap(route[-1], target), 1.0, connector.get('id'))

    def test_no_segment_is_diagonal(self):
        # Связи в PIX имеют тип step: косой отрезок в них выглядит поломкой.
        for connector in self.root.findall('connector'):
            route = _waypoints(connector)
            for (x1, y1), (x2, y2) in zip(route, route[1:]):
                self.assertTrue(
                    abs(x2 - x1) < 0.5 or abs(y2 - y1) < 0.5,
                    f'{connector.get("id")}: косой отрезок {(x1, y1)}→{(x2, y2)}',
                )

    def test_shapes_facing_each_other_are_joined_by_one_straight_line(self):
        by_id = {n.id: n for n in normalize_geometry(self.process).nodes}
        edge = next(e for e in self.process.edges if e.sourceId == 't1' and e.targetId == 'g')
        route = orthogonal_waypoints(edge, by_id['t1'], by_id['g'])
        self.assertEqual(len(route), 2, f'ожидалась прямая, получено {route}')
        self.assertEqual(route[0][1], route[1][1])

    def test_no_micro_staircase_between_the_ends(self):
        for connector in self.root.findall('connector'):
            route = _waypoints(connector)
            for index, (a, b) in enumerate(zip(route, route[1:])):
                length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
                if 0 < index < len(route) - 2:
                    self.assertGreaterEqual(
                        length, MIN_SEGMENT,
                        f'{connector.get("id")}: ступенька {length} px',
                    )


class LabelPlacementTest(unittest.TestCase):
    """Подпись связи должна стоять там, где её поставил аналитик."""

    def test_analyst_position_is_carried_over(self):
        from app.models.process import ProcessEdge

        # draw.io хранит долю вдоль связи в labelX: -1 — начало, +1 — конец.
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=-1.0), None), 5)
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=0.0), None), 50)
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=1.0), None), 95)

    def test_gateway_branch_defaults_to_the_gateway_end(self):
        from app.models.process import ProcessEdge

        gateway = ProcessNode(id='g', name='', type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=60, height=60))
        # У шлюза от одной точки расходится несколько линий; их середины
        # попадают в гущу схемы, и «Ha» с «Yo'q» ложатся поверх фигур.
        self.assertEqual(label_position(ProcessEdge(id='e'), gateway), 20)

    def test_plain_connector_keeps_the_middle(self):
        from app.models.process import ProcessEdge

        task = ProcessNode(id='t', name='', type='userTask',
                           geometry=Geometry(x=0, y=0, width=180, height=80))
        self.assertEqual(label_position(ProcessEdge(id='e'), task), 50)

    def test_absolute_label_coordinate_is_ignored(self):
        from app.models.process import ProcessEdge

        # За пределами [-1, 1] это уже не доля, а абсолютная координата.
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=340.0), None), 50)


class ShapeSeparationTest(unittest.TestCase):
    """Фигуры не должны налезать друг на друга."""

    def test_artifact_is_pushed_out_of_a_step(self):
        process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        normalized = normalize_geometry(process)
        storage = next(n for n in normalized.nodes if n.type == 'dataStore')
        task = next(n for n in normalized.nodes if n.id == 't1')
        # Ставим цилиндр заведомо внутрь шага.
        storage.geometry = Geometry(
            x=task.geometry.x + 10, y=task.geometry.y + 10, width=62, height=56)

        moved = separate_artifacts(normalized)
        after = next(n for n in moved.nodes if n.type == 'dataStore').geometry
        overlap_x = min(after.x + after.width, task.geometry.x + task.geometry.width) - \
            max(after.x, task.geometry.x)
        overlap_y = min(after.y + after.height, task.geometry.y + task.geometry.height) - \
            max(after.y, task.geometry.y)
        self.assertTrue(overlap_x <= 0 or overlap_y <= 0,
                        f'артефакт всё ещё внутри шага: {after}')

    def test_steps_are_not_moved(self):
        # Шаг стоит в потоке: его место аналитик выбирал осознанно.
        process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        normalized = normalize_geometry(process)
        before = {n.id: (n.geometry.x, n.geometry.y) for n in normalized.nodes
                  if n.type == 'userTask'}
        after = {n.id: (n.geometry.x, n.geometry.y) for n in separate_artifacts(normalized).nodes
                 if n.type == 'userTask'}
        self.assertEqual(before, after)


class StudioExportStaysValidTest(unittest.TestCase):
    """Эталон студии обязан проходить наши же правила геометрии."""

    def test_our_threshold_is_close_to_the_studios_own(self):
        package = zipfile.ZipFile(os.path.join(FIXTURES, 'sap.pmm'))
        root = ET.fromstring(package.read('pm/maps/sap.xml'))
        shortest = None
        for connector in root.findall('connector'):
            route = _waypoints(connector)
            for a, b in zip(route, route[1:]):
                length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
                # Совпавшие точки студия пишет и сама (у неё они расходятся на
                # 5e-05 px): это не отрезок, а дубль, и порога он не касается.
                if length < 0.5:
                    continue
                shortest = length if shortest is None else min(shortest, length)
        # Отсюда и взят порог MIN_SEGMENT: у студии самый короткий настоящий
        # отрезок — 6 px, мы держим 8. Если эталон окажется заметно мельче,
        # порог придётся пересмотреть: он не должен перекладывать ломаную,
        # которую сама студия считает нормальной.
        self.assertIsNotNone(shortest)
        self.assertGreaterEqual(shortest, 6.0)
        self.assertLessEqual(MIN_SEGMENT - shortest, 4.0)


class MapLabelTest(unittest.TestCase):
    """Подпись на карте студии должна повторять исходную карту draw.io."""

    def test_generated_gateway_name_does_not_reach_the_map(self):
        # В draw.io безымянный шлюз нарисован пустым ромбом: вопрос стоит на
        # ветках. Подставленное «Условие» — надпись, которой в эталоне нет.
        gateway = ProcessNode(id='g', name='Условие', type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=60, height=60))
        self.assertEqual(map_label(gateway), '')

    def test_real_gateway_question_is_kept(self):
        gateway = ProcessNode(id='g', name="Hujjatlar to'liqmi?", type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=60, height=60))
        self.assertEqual(map_label(gateway), "Hujjatlar to'liqmi?")

    def test_generated_task_name_is_kept(self):
        # Пустой прямоугольник хуже условного имени: по нему шаг не найти ни в
        # регламенте, ни в отчёте о качестве импорта.
        task = ProcessNode(id='t', name='Операция STEP-07', type='userTask',
                           geometry=Geometry(x=0, y=0, width=160, height=80))
        self.assertEqual(map_label(task), 'Операция STEP-07')

    def test_waiting_event_carries_its_minutes(self):
        # В draw.io подпись набрана в две строки: «Kutish vaqti» и «15 min».
        # Импорт разбирает вторую строку в минуты и убирает из имени — на карте
        # студии оставалось голое «Kutish vaqti» без единой цифры.
        wait = ProcessNode(id='w', name='Kutish vaqti', type='intermediateTimerEvent',
                           slaMinutes=15, geometry=Geometry(x=0, y=0, width=48, height=48))
        self.assertEqual(map_label(wait), 'Kutish vaqti 15 мин')

    def test_trailing_colon_does_not_survive(self):
        wait = ProcessNode(id='w', name='Kutish vaqti :', type='intermediateTimerEvent',
                           slaMinutes=30, geometry=Geometry(x=0, y=0, width=48, height=48))
        self.assertEqual(map_label(wait), 'Kutish vaqti 30 мин')

    def test_waiting_event_that_already_shows_time_is_left_alone(self):
        wait = ProcessNode(id='w', name='2-3 kun', type='intermediateTimerEvent',
                           slaMinutes=30, geometry=Geometry(x=0, y=0, width=48, height=48))
        self.assertEqual(map_label(wait), '2-3 kun')


class DurationBadgePlacementTest(unittest.TestCase):
    """Значок длительности обязан быть виден целиком."""

    def test_badge_sits_entirely_below_the_step(self):
        # Раньше кружок сидел на нижней грани, и верхняя половина уходила под
        # заливку шага: в студии от значка была видна одна нижняя дуга.
        process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        boxes = _boxes(_map_root(process))
        badges = [i for i in boxes.values()
                  if i['type'] == 'boundary_non_interrupting_event_timer']
        steps = [i for i in boxes.values() if i['type'] == 'task']
        self.assertTrue(badges)
        for badge in badges:
            bx, by, bw, bh = badge['box']
            for step in steps:
                sx, sy, sw, sh = step['box']
                overlap_x = min(bx + bw, sx + sw) - max(bx, sx)
                overlap_y = min(by + bh, sy + sh) - max(by, sy)
                self.assertTrue(
                    overlap_x <= 0 or overlap_y <= 0,
                    f'значок «{badge["label"]}» заходит на шаг «{step["label"]}»')



class ClientTouchpointTest(unittest.TestCase):
    """Пунктиры к полосе клиента не должны сходиться в одну точку."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="client" value="Mijoz" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="1400" height="120" as="geometry" />
        </mxCell>
        <mxCell id="lane" value="Фронт-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="120" width="1400" height="300" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Приём заявки" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="200" y="80" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Выдача справки" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="800" y="80" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="m1" style="dashed=1;" edge="1" source="t1" target="client" parent="1" />
        <mxCell id="m2" style="dashed=1;" edge="1" source="t2" target="client" parent="1" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def setUp(self):
        self.root = _map_root(parse_drawio_xml(self.MAP, 'client.drawio'))

    def test_no_connector_ends_in_the_lane_itself(self):
        # В собственных выгрузках студии таких связей нет ни одной: линию к
        # дорожке она цепляет за её центр, и все пунктиры к клиенту сходились
        # в одну точку, сливаясь в жирную линию.
        containers = {
            n.get('id') for n in self.root.iter('node')
            if n.get('type') in ('horizontalRoad', 'emptyPool')
        }
        for connector in self.root.findall('connector'):
            self.assertNotIn(connector.get('sourceNodeId'), containers)
            self.assertNotIn(connector.get('targetNodeId'), containers)

    def test_each_touchpoint_gets_its_own_marker(self):
        markers = [n for n in self.root.iter('node')
                   if n.get('type') == 'intermediate_event_catch_message']
        self.assertEqual(len(markers), 2, 'у каждой связи с клиентом свой маркер')
        xs = {n.get('x') for n in markers}
        self.assertEqual(len(xs), 2, f'маркеры встали в одну точку: {xs}')

    def test_marker_lives_inside_the_client_row(self):
        client = next(n for n in self.root.iter('node') if n.get('label') == 'Mijoz')
        inside = [n for n in client.findall('node')
                  if n.get('type') == 'intermediate_event_catch_message']
        self.assertEqual(len(inside), 2)


class GatewayLabelPlacementTest(unittest.TestCase):
    """Вопрос шлюза стоит НАД ромбом, а не сбоку."""

    def test_label_goes_above_the_diamond(self):
        # Слева от ромба на плотной карте стоит предыдущий шаг, и вопрос
        # ложился прямо на его текст. Сверху свободно всегда: поток идёт по
        # горизонтали.
        process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        gateway = next(n for n in _map_root(process).iter('node')
                       if n.get('type') == 'gateway_xor')
        self.assertEqual(gateway.get('labelPlacement'), 'Top')


class LabelFitTest(unittest.TestCase):
    """Подпись обязана помещаться в рамку тем кеглем, которым рисует студия."""

    def test_box_grows_for_a_long_label(self):
        long_text = ' '.join(['Hujjatlarni tekshirish va tasdiqlash'] * 3)
        drawio = DRAWIO.replace('Проверка документов 5 мин', long_text)
        process = parse_drawio_xml(drawio, 'fit.drawio')
        step = next(n for n in _map_root(process).iter('node')
                    if n.get('type') == 'task')
        need = wrapped_line_count(
            step.get('label'), float(step.get('width')) - 20, 14.0) * 14.0 * 1.25 + 20
        self.assertLessEqual(need, float(step.get('height')) + 1,
                             'подпись не помещается в рамку')

    def test_growth_does_not_push_a_step_onto_its_neighbour(self):
        long_text = ' '.join(['Hujjatlarni tekshirish'] * 4)
        drawio = DRAWIO.replace('Проверка документов 5 мин', long_text)
        boxes = _boxes(_map_root(parse_drawio_xml(drawio, 'fit.drawio')))
        steps = [i['box'] for i in boxes.values() if i['type'] == 'task']
        for i in range(len(steps)):
            for j in range(i + 1, len(steps)):
                a, b = steps[i], steps[j]
                overlap_x = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                overlap_y = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                self.assertTrue(overlap_x <= 0 or overlap_y <= 0,
                                'выросший шаг наехал на соседний')



class ShapeClassificationTest(unittest.TestCase):
    """Фигуры, из-за которых шаги собирались в кучу."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="t" value="Проверка документов" style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="200" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="note" value="Balansdan tashqari h/r ochish" style="shape=callout;whiteSpace=wrap;html=1;perimeter=calloutPerimeter;" vertex="1" parent="1">
          <mxGeometry x="200" y="60" width="200" height="80" as="geometry" />
        </mxCell>
        <mxCell id="icon" value="" style="strokeColor=none;fillColor=#505050;shape=mxgraph.office.devices.phone_traditional;" vertex="1" parent="1">
          <mxGeometry x="450" y="60" width="60" height="60" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def setUp(self):
        self.process = parse_drawio_xml(self.MAP, 'shapes.drawio')
        self.by_id = {n.id: n for n in self.process.nodes}

    def test_callout_is_a_note_not_a_step(self):
        # Нераспознанная выноска становилась полноразмерной задачей и ложилась
        # поверх соседнего ряда — отсюда «куча» на карте студии.
        self.assertEqual(self.by_id['note'].type, 'textAnnotation')

    def test_office_clipart_is_not_a_step(self):
        # Иконка телефона из библиотеки Office превращалась в «Операцию
        # STEP-NN» с выдуманным временем.
        self.assertNotIn('icon', self.by_id)

    def test_no_phantom_steps_are_invented(self):
        names = [n.name for n in self.process.nodes]
        self.assertFalse([n for n in names if n.startswith('Операция STEP-')], names)


class MeasuredDurationTest(unittest.TestCase):
    """Время рисуется только там, где оно было в draw.io."""

    def _process(self, with_badge: bool):
        badge = ('<mxCell id="b" value="30 мин" style="shape=mxgraph.bpmn.timer_start;" '
                 'vertex="1" parent="1">'
                 '<mxGeometry x="360" y="268" width="24" height="24" as="geometry" />'
                 '</mxCell>') if with_badge else ''
        return parse_drawio_xml(
            ShapeClassificationTest.MAP.replace('</root>', badge + '</root>'), 'd.drawio')

    def test_step_without_a_clock_gets_no_badge(self):
        step = next(n for n in self._process(False).nodes if n.type in ('task', 'userTask'))
        self.assertGreater(step.slaMinutes or 0, 0, 'для SLA время всё же подставляется')
        self.assertFalse(step.slaMeasured)
        self.assertEqual(step_duration_text(step), '')

    def test_step_with_a_clock_keeps_it(self):
        step = next(n for n in self._process(True).nodes if n.type in ('task', 'userTask'))
        self.assertTrue(step.slaMeasured)
        self.assertEqual(step_duration_text(step), '30 мин')

    def test_map_shows_a_badge_only_for_a_measured_step(self):
        without = _map_root(self._process(False))
        with_clock = _map_root(self._process(True))
        badge = 'boundary_non_interrupting_event_timer'
        self.assertEqual([n for n in without.iter('node') if n.get('type') == badge], [])
        self.assertTrue([n for n in with_clock.iter('node') if n.get('type') == badge])


class TaskIconTest(unittest.TestCase):
    """У шага не должно быть значка в левом верхнем углу."""

    def test_every_action_is_a_flat_task(self):
        # Студия рисует у пользовательской задачи человечка, у сервисной —
        # шестерёнку, и рисует их поверх первых букв подписи: «1.Eksport…»
        # превращается в «👤Eksport…». В draw.io у шагов иконок нет.
        types = {n.get('type') for n in _map_root(
            parse_drawio_xml(DRAWIO, 'geometry.drawio')).iter('node')}
        self.assertIn('task', types)
        self.assertNotIn('userTask', types)
        self.assertNotIn('serviceTask', types)



class DocumentIconTest(unittest.TestCase):
    """Лист бумаги рядом с шагом — документ процесса, а не украшение."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="t" value="Проверка документов" style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="200" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="doc" value="" style="strokeColor=none;fillColor=#505050;shape=mxgraph.office.concepts.documents;" vertex="1" parent="1">
          <mxGeometry x="260" y="100" width="28" height="34" as="geometry" />
        </mxCell>
        <mxCell id="phone" value="" style="strokeColor=none;fillColor=#505050;shape=mxgraph.office.devices.phone_traditional;" vertex="1" parent="1">
          <mxGeometry x="500" y="100" width="60" height="60" as="geometry" />
        </mxCell>
        <mxCell id="a1" style="dashed=1;" edge="1" source="doc" target="t" parent="1" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def setUp(self):
        self.by_id = {n.id: n for n in parse_drawio_xml(self.MAP, 'doc.drawio').nodes}

    def test_document_survives(self):
        # Правило «вся библиотека Office — клипарт» съедало вместе с телефоном
        # и лист бумаги: на карте исчезал документ, который аналитик привязал
        # к шагу пунктиром.
        self.assertIn('doc', self.by_id)
        self.assertEqual(self.by_id['doc'].type, 'dataObject')

    def test_phone_is_still_dropped(self):
        self.assertNotIn('phone', self.by_id)

    def test_document_keeps_its_small_size(self):
        geo = self.by_id['doc'].geometry
        self.assertEqual((geo.width, geo.height), (28, 34))



if __name__ == '__main__':
    unittest.main()
