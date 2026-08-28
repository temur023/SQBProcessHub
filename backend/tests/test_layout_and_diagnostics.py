"""Читаемость карты после импорта и отчёт о её качестве сотруднику.

Фикстура повторяет то, на что жалуются аналитики после выгрузки в bpmn.io:
таймер нарисован прямоугольной рамкой (значок часов растягивается в эллипс),
подпись шага не помещается в фигуру и уезжает под маркер задачи, хранилище
данных лежит поверх шага, а у шлюза не подписана ветка.
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.layout import (
    external_label_candidates,
    label_height,
    label_size,
    normalize_layout,
    wrapped_line_count,
)

BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
}

MESSY_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane_a" value="Ofis" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="1400" height="360" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=general;outline=standard;verticalLabelPosition=bottom;verticalAlign=top;" vertex="1" parent="lane_a">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_long" value="Mijozga hisob raqam ochilganligi to'g'risida EHA dasturida xat rasmiylashtirish va jo'natish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="180" y="105" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="ds_iabs" value="IABS" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;verticalAlign=bottom;" vertex="1" parent="lane_a">
      <mxGeometry x="200" y="120" width="60" height="40" as="geometry" />
    </mxCell>
    <mxCell id="timer_wide" value="10 min" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=timer;outline=catching;aspect=fixed;labelPosition=left;align=right;verticalLabelPosition=middle;verticalAlign=middle;" vertex="1" parent="lane_a">
      <mxGeometry x="420" y="120" width="80" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw_1" value="Hujjatlar to'liqmi?" style="shape=mxgraph.bpmn.gateway2;rhombus;html=1;gwType=exclusive;verticalLabelPosition=top;verticalAlign=bottom;" vertex="1" parent="lane_a">
      <mxGeometry x="600" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_b" value="Rad javob berish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="760" y="105" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=terminate2;outline=end;" vertex="1" parent="lane_a">
      <mxGeometry x="980" y="120" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="start_1" target="task_long">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_long" target="timer_wide">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f3" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="timer_wide" target="gw_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f4" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_1" target="task_b">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f5" value="To'liq" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_1" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f6" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_b" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="a1" style="dashed=1;endArrow=open;" edge="1" parent="lane_a" source="ds_iabs" target="task_long">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class TextMetricsTest(unittest.TestCase):
    """Оценка размеров подписи — основа всей подгонки фигур."""

    def test_wrapping_grows_with_narrower_box(self):
        text = "Mijozga hisob raqam ochilganligi to'g'risida xat rasmiylashtirish"
        self.assertGreater(wrapped_line_count(text, 80), wrapped_line_count(text, 240))

    def test_marker_reserves_room_above_and_below(self):
        # Маркер задачи занимает левый верхний угол; подпись центрируется, и без
        # запаса первая строка уезжает под маркер.
        with_marker = label_height('Qisqa', 120, True)
        without = label_height('Qisqa', 120, False)
        self.assertGreaterEqual(with_marker - without, 30)

    def test_short_label_gets_a_snug_box(self):
        # Рамка в 90 px под «To'liq» — это 55 px лишнего места, из-за которых
        # подпись ветки ложилась на соседний шаг.
        width, _ = label_size("To'liq")
        self.assertLess(width, 60)


class LayoutNormalizationTest(unittest.TestCase):
    """Геометрия карты после импорта должна быть читаемой."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MESSY_MAP, "messy.drawio")
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_event_frame_becomes_square(self):
        # 80×50 в draw.io — это круг 50 px и место под подпись слева; импортёр
        # BPMN растянул бы круг в эллипс вместе со значком часов.
        timer = self.by_id["timer_wide"]
        self.assertEqual(timer.geometry.width, timer.geometry.height)
        self.assertEqual(timer.geometry.width, 50)

    def test_event_stays_centred_on_its_original_spot(self):
        timer = self.by_id["timer_wide"]
        # Исходный центр: x 460 + дорожка 40 = 500, y 145 + 40 = 185.
        self.assertEqual(timer.geometry.x + timer.geometry.width / 2, 500)
        self.assertEqual(timer.geometry.y + timer.geometry.height / 2, 185)

    def test_long_label_fits_into_its_shape(self):
        for node in self.process.nodes:
            if node.type not in ('task', 'userTask', 'serviceTask', 'subProcess'):
                continue
            need = label_height(node.name, node.geometry.width, True)
            self.assertLessEqual(need, node.geometry.height + 0.5, node.name)

    def test_artifact_no_longer_lies_on_the_step(self):
        store = self.by_id["ds_iabs"]
        task = self.by_id["task_long"]
        sg, tg = store.geometry, task.geometry
        overlap_x = min(sg.x + sg.width, tg.x + tg.width) - max(sg.x, tg.x)
        overlap_y = min(sg.y + sg.height, tg.y + tg.height) - max(sg.y, tg.y)
        self.assertFalse(overlap_x > 0 and overlap_y > 0, 'цилиндр перекрывает подпись шага')

    def test_nothing_escapes_its_lane(self):
        lane = self.process.lanes[0]
        lg = lane.geometry
        for node in self.process.nodes:
            if node.laneId != lane.id:
                continue
            g = node.geometry
            self.assertGreaterEqual(g.x, lg.x, node.name)
            self.assertGreaterEqual(g.y, lg.y, node.name)
            self.assertLessEqual(g.x + g.width, lg.x + lg.width, node.name)
            self.assertLessEqual(g.y + g.height, lg.y + lg.height, node.name)

    def test_normalization_is_idempotent(self):
        before = [(n.id, n.geometry.model_dump()) for n in self.process.nodes]
        normalize_layout(self.process.nodes, self.process.lanes)
        after = [(n.id, n.geometry.model_dump()) for n in self.process.nodes]
        self.assertEqual(before, after)


class ExternalLabelPlacementTest(unittest.TestCase):
    """Выносные подписи ставим туда, куда их поставил аналитик."""

    @staticmethod
    def _node(style):
        return ProcessNode(
            id='n', name='Ожидание 10 мин', type='intermediateTimerEvent',
            geometry=Geometry(x=200, y=200, width=50, height=50), style=style,
        )

    def test_draw_io_label_position_wins(self):
        # У таймера подпись уведена влево, чтобы не лечь на вертикальную связь.
        left = external_label_candidates(self._node('labelPosition=left;align=right;'))[0]
        self.assertLess(left[0] + left[2], 200)

        above = external_label_candidates(self._node('verticalLabelPosition=top;'))[0]
        self.assertLess(above[1] + above[3], 200)

        below = external_label_candidates(self._node(''))[0]
        self.assertGreater(below[1], 250)

    def test_narrower_variants_are_offered_as_a_fallback(self):
        node = ProcessNode(
            id='gw', type='exclusiveGateway',
            name="Milliy va xorijiy ro'yxatga mos kelishi FISH va tug'.sana (AML/CFT)",
            geometry=Geometry(x=0, y=0, width=50, height=50), style='',
        )
        widths = {box[2] for box in external_label_candidates(node)}
        self.assertGreater(len(widths), 1, 'должен быть запасной, более узкий вариант рамки')


class BpmnLabelGeometryTest(unittest.TestCase):
    """Импортёр не должен сам решать, где стоять подписи."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MESSY_MAP, "messy.drawio")
        cls.root = ET.fromstring(generate_bpmn_xml(cls.process))
        cls.plane = cls.root.find(".//bpmndi:BPMNPlane", BPMN_NS)

    def _bounds(self, el):
        b = el.find("dc:Bounds", BPMN_NS)
        return tuple(float(b.get(k)) for k in ("x", "y", "width", "height"))

    def test_events_and_gateways_carry_label_bounds(self):
        proc = self.root.find("bpmn:process", BPMN_NS)
        external = {
            el.get("id") for el in proc
            if el.tag.split("}")[-1] in (
                "startEvent", "endEvent", "intermediateCatchEvent",
                "exclusiveGateway", "dataStoreReference",
            )
        }
        self.assertTrue(external)
        for shape in self.plane.findall("bpmndi:BPMNShape", BPMN_NS):
            if shape.get("bpmnElement") in external:
                self.assertIsNotNone(
                    shape.find("bpmndi:BPMNLabel", BPMN_NS),
                    shape.get("bpmnElement"),
                )

    def test_no_label_is_buried_under_a_shape(self):
        shapes = []
        labels = []
        for shape in self.plane.findall("bpmndi:BPMNShape", BPMN_NS):
            element = shape.get("bpmnElement")
            if not element.startswith("Participant") and not element.endswith("lane_a"):
                shapes.append(self._bounds(shape))
            label = shape.find("bpmndi:BPMNLabel", BPMN_NS)
            if label is not None:
                labels.append(self._bounds(label))
        for edge in self.plane.findall("bpmndi:BPMNEdge", BPMN_NS):
            label = edge.find("bpmndi:BPMNLabel", BPMN_NS)
            if label is not None:
                labels.append(self._bounds(label))
        self.assertTrue(labels)

        for lx, ly, lw, lh in labels:
            covered = 0.0
            for sx, sy, sw, sh in shapes:
                dx = min(lx + lw, sx + sw) - max(lx, sx)
                dy = min(ly + lh, sy + sh) - max(ly, sy)
                if dx > 0 and dy > 0:
                    covered = max(covered, dx * dy / (lw * lh))
            self.assertLessEqual(covered, 0.5, f'подпись {(lx, ly, lw, lh)} скрыта фигурой')


class ImportDiagnosticsTest(unittest.TestCase):
    """Сотрудник должен увидеть, где карта неполна и что платформа достроила."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MESSY_MAP, "messy.drawio")
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)

    def test_every_issue_is_addressed_and_actionable(self):
        self.assertTrue(self.process.validation)
        for issue in self.process.validation:
            self.assertTrue(issue.code, issue.message)
            self.assertTrue(issue.message)
            if issue.level in ('error', 'warning'):
                self.assertTrue(issue.hint, f'{issue.code}: нет подсказки, что делать')

    def test_second_branch_of_a_binary_gateway_is_completed(self):
        # У развилки «да/нет» аналитик подписывает одну ветку: на рисунке и так
        # понятно, что вторая — противоположная. PIX так не умеет, поэтому
        # условие подставляется, а сотруднику об этом говорится.
        self.assertNotIn('gateway_branch_unlabeled', self.by_code)
        issue = self.by_code['gateway_branch_completed'][0]
        self.assertEqual(issue.level, 'warning')
        self.assertIn("To'liq emas", issue.message)

    def test_completed_condition_reaches_the_branch(self):
        branch = next(e for e in self.process.edges if e.id == 'f4')
        self.assertEqual(branch.name, "To'liq emas")
        self.assertEqual(branch.condition, "To'liq emas")

    def test_step_without_a_timer_badge_is_reported(self):
        self.assertIn('no_step_time', self.by_code)
        issue = self.by_code['no_step_time'][0]
        self.assertIn('подставлено', issue.message)
        self.assertTrue(issue.nodeId, 'замечание должно вести к конкретному шагу')

    def test_repairs_are_reported_as_info(self):
        for code in ('geometry_squared', 'geometry_fitted', 'geometry_moved'):
            self.assertIn(code, self.by_code, code)
            self.assertEqual(self.by_code[code][0].level, 'info')

    def test_timer_name_is_not_reported_as_missing_caption(self):
        # «Ожидание 10 мин» — это длительность таймера, а не отсутствие подписи.
        generated = self.by_code.get('generated_name', [])
        self.assertFalse(
            [i for i in generated if 'Ожидание' in i.message],
            'подпись таймера не должна считаться дефектом',
        )


if __name__ == "__main__":
    unittest.main()
