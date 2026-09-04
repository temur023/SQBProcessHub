"""Регрессия на конвенции Методики SQB (2-ILOVA / 4-ILOVA) при экспорте в PIX.

Фикстура повторяет то, как реально нарисованы карты банка:

* время операции (ST) стоит отдельной мелкой фигурой-таймером рядом с шагом
  и не соединено рёбрами;
* время ожидания (WT) — таймер-обработчик ВНУТРИ потока;
* информационные системы и документы — хранилища данных и объекты данных,
  подключённые пунктирной ассоциацией;
* дорожка может быть без заголовка;
* шаг может выступать за границу своей дорожки.
"""
import os
import sys
import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessEdge, ProcessEdgePoint, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import (
    duration_label,
    generate_bpmn_xml,
    iso_duration,
    step_duration_text,
)
from app.services.edge_routing import orthogonal_waypoints
from app.services.pmm_exporter import generate_pmm_zip, map_slug

BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
}

DC_BOUNDS = "{http://www.omg.org/spec/DD/20100524/DC}Bounds"

METHODOLOGY_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="pool" value="SQB" style="swimlane;html=1;childLayout=stackLayout;horizontal=0;startSize=20;" vertex="1" parent="1">
      <mxGeometry x="10" y="20" width="900" height="460" as="geometry" />
    </mxCell>

    <mxCell id="lane_a" value="Lane A" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
      <mxGeometry x="0" y="20" width="900" height="140" as="geometry" />
    </mxCell>
    <mxCell id="lane_b" value="" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
      <mxGeometry x="0" y="160" width="900" height="140" as="geometry" />
    </mxCell>
    <mxCell id="lane_banner" value="" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
      <mxGeometry x="0" y="300" width="900" height="60" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Mijoz bankka tashrif buyurdi" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_a">
      <mxGeometry x="30" y="50" width="48" height="48" as="geometry" />
    </mxCell>
    <mxCell id="task_a" value="Hujjatlarni qabul qilish" style="shape=mxgraph.bpmn.task2;html=1;whiteSpace=wrap;" vertex="1" parent="lane_a">
      <mxGeometry x="120" y="40" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="badge_a" value="5 min" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=timer;outline=standard;" vertex="1" parent="lane_a">
      <mxGeometry x="185" y="110" width="20" height="20" as="geometry" />
    </mxCell>
    <mxCell id="ds_iabs" value="IABS" style="shape=datastore;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="300" y="40" width="60" height="60" as="geometry" />
    </mxCell>
    <mxCell id="gw_1" value="Hujjatlar to'liqmi?" style="shape=mxgraph.bpmn.gateway2;rhombus;html=1;symbol=none;outline=none;" vertex="1" parent="lane_a">
      <mxGeometry x="420" y="45" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="timer_wait" value="Kutish vaqti 30 min" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=timer;outline=catching;" vertex="1" parent="lane_a">
      <mxGeometry x="520" y="45" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="doc_1" value="Dalolatnoma" style="shape=mxgraph.bpmn.data2;html=1;" vertex="1" parent="lane_b">
      <mxGeometry x="150" y="30" width="60" height="60" as="geometry" />
    </mxCell>
    <mxCell id="task_b" value="Dalolatnoma tuzish" style="shape=mxgraph.bpmn.task2;html=1;whiteSpace=wrap;" vertex="1" parent="lane_b">
      <mxGeometry x="300" y="30" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="badge_b" value="10 min" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=timer;outline=standard;" vertex="1" parent="lane_b">
      <mxGeometry x="365" y="100" width="20" height="20" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Jarayon tugadi" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=terminate2;outline=end;" vertex="1" parent="lane_b">
      <mxGeometry x="700" y="50" width="48" height="48" as="geometry" />
    </mxCell>
    <mxCell id="task_c" value="Rad javob berish" style="shape=mxgraph.bpmn.task2;html=1;whiteSpace=wrap;" vertex="1" parent="lane_b">
      <mxGeometry x="820" y="30" width="120" height="80" as="geometry" />
    </mxCell>

    <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="start_1" target="task_a">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_a" target="gw_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e3" value="Ha" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_1" target="timer_wait">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e4" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="timer_wait" target="task_b">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e5" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_b" source="task_b" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e6" style="dashed=1;endArrow=open;" edge="1" parent="lane_a" source="ds_iabs" target="task_a">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e7" style="dashed=1;endArrow=open;" edge="1" parent="lane_b" source="doc_1" target="task_b">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e8" value="Yo'q" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="gw_1" target="task_c">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e9" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="task_c" target="end_1">
      <mxGeometry relative="1" as="geometry">
        <Array as="points">
          <mxPoint x="880" y="330" />
        </Array>
      </mxGeometry>
    </mxCell>

    <mxCell id="e10" style="dashed=1;endArrow=open;" edge="1" parent="1" source="task_a">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="190" y="240" as="targetPoint" />
      </mxGeometry>
    </mxCell>
    <mxCell id="e11" style="dashed=1;endArrow=none;" edge="1" parent="1" source="task_b">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="2400" y="-600" as="targetPoint" />
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>"""


class MethodologyImportTest(unittest.TestCase):
    """Словарь Методики должен доживать до модели без потерь."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(METHODOLOGY_MAP, "methodology.drawio")
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_duration_badges_become_step_time_not_nodes(self):
        # Бейдж длительности — это ST шага, а не стартовое событие на карте.
        self.assertNotIn("badge_a", self.by_id)
        self.assertNotIn("badge_b", self.by_id)
        self.assertEqual(self.by_id["task_a"].slaMinutes, 5)
        self.assertEqual(self.by_id["task_b"].slaMinutes, 10)

    def test_inflow_timer_stays_an_intermediate_event(self):
        timer = self.by_id["timer_wait"]
        self.assertEqual(timer.type, "intermediateTimerEvent")
        self.assertEqual(timer.slaMinutes, 30)

    def test_start_and_end_events_keep_their_degree(self):
        self.assertEqual(self.by_id["start_1"].type, "startEvent")
        self.assertEqual(self.by_id["end_1"].type, "endEvent")
        starts = [n for n in self.process.nodes if n.type == "startEvent"]
        self.assertEqual(len(starts), 1, "таймеры не должны превращаться в старты")

    def test_artifacts_are_typed_not_turned_into_tasks(self):
        self.assertEqual(self.by_id["ds_iabs"].type, "dataStore")
        self.assertEqual(self.by_id["doc_1"].type, "dataObject")
        tasks = [n for n in self.process.nodes if n.type in ("task", "userTask", "serviceTask")]
        self.assertEqual({t.id for t in tasks}, {"task_a", "task_b", "task_c"})

    def test_artifact_links_are_associations(self):
        kinds = {e.id: e.kind for e in self.process.edges}
        self.assertEqual(kinds["e6"], "association")
        self.assertEqual(kinds["e7"], "association")
        self.assertEqual(kinds["e1"], "sequenceFlow")

    def test_system_and_documents_come_from_the_map(self):
        self.assertEqual(self.by_id["task_a"].system, "IABS")
        self.assertEqual(self.by_id["task_b"].inputArtifacts, ["Dalolatnoma"])
        self.assertEqual(self.by_id["task_a"].outputArtifacts, ["Dalolatnoma"])

    def test_edge_without_binding_snaps_to_the_shape_under_it(self):
        # В draw.io конец связи может быть не привязан к фигуре, а задан точкой.
        # Такие линии редактор рисует, а мы раньше выбрасывали целиком.
        edges = {e.id: e for e in self.process.edges}
        self.assertIn("e10", edges, "линия со свободным концом потеряна при импорте")
        self.assertEqual(edges["e10"].targetId, "doc_1")
        self.assertEqual(edges["e10"].kind, "association")

    def test_line_hanging_in_the_void_is_kept_as_decoration(self):
        edges = {e.id: e for e in self.process.edges}
        self.assertIn("e11", edges)
        self.assertEqual(edges["e11"].kind, "annotationLine")
        self.assertEqual(edges["e11"].sourceId, "task_b")
        self.assertIsNone(edges["e11"].targetId)
        self.assertIsNotNone(edges["e11"].targetPoint, "координата свободного конца потеряна")

    def test_dashed_styling_survives_the_import(self):
        dashed = [e for e in self.process.edges if e.dashed]
        self.assertEqual({e.id for e in dashed}, {"e6", "e7", "e10", "e11"})

    def test_untitled_lane_with_content_survives_empty_one_does_not(self):
        names = [l.name for l in self.process.lanes]
        self.assertIn("Lane A", names)
        self.assertEqual(len(self.process.lanes), 2, names)
        self.assertTrue(
            any(n.startswith("Дорожка") for n in names),
            f"безымянная дорожка с содержимым потеряна: {names}",
        )
        self.assertTrue(all(n.laneId for n in self.process.nodes), "остались шаги без дорожки")


#: Идентификатор ячейки draw.io: длинная мешанина из букв, цифр и дефисов.
_CELL_ID_RE = re.compile(r'^[A-Za-z0-9_-]{12,}$')


def _looks_like_cell_id(text: str) -> bool:
    return bool(_CELL_ID_RE.match(text)) and any(ch.isdigit() for ch in text)


def _tag_name(el):
    return el.tag.rsplit('}', 1)[-1]


class BpmnExportTest(unittest.TestCase):
    """BPMN 2.0 должен быть валидной схемой, а не просто well-formed XML."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(METHODOLOGY_MAP, "methodology.drawio")
        cls.xml = generate_bpmn_xml(cls.process)
        cls.root = ET.fromstring(cls.xml)
        cls.proc = cls.root.find("bpmn:process", BPMN_NS)
        cls.tag_of = {el.get("id"): el.tag.split("}")[-1] for el in cls.proc if el.get("id")}

    def test_artifacts_use_artifact_elements(self):
        tags = set(self.tag_of.values())
        self.assertIn("dataStoreReference", tags)
        self.assertIn("dataObjectReference", tags)

    def test_data_links_are_data_associations_not_plain_ones(self):
        """Хранилище и документ подключаются к шагу как ДАННЫЕ, а не артефакт.

        ``bpmn:association`` по спецификации соединяет артефакт — текстовое
        примечание или группу. Хранилище данных артефактом не является, и
        Процессная студия обычную ассоциацию к нему не рисует: на карте связи
        «шаг ↔ база» просто пропадали. Правильная конструкция —
        ``dataInputAssociation`` / ``dataOutputAssociation`` внутри шага.
        """
        inputs = self.proc.findall(".//bpmn:dataInputAssociation", BPMN_NS)
        outputs = self.proc.findall(".//bpmn:dataOutputAssociation", BPMN_NS)
        self.assertEqual(len(inputs) + len(outputs), 3,
                         "e6, e7 и e10 — все три связи с данными")
        # Ни одна не должна остаться обычной ассоциацией.
        self.assertEqual(self.proc.findall("bpmn:association", BPMN_NS), [])

        data_ids = {
            el.get("id")
            for name in ("dataStoreReference", "dataObjectReference")
            for el in self.proc.findall(f"bpmn:{name}", BPMN_NS)
        }
        for assoc in inputs:
            source = assoc.find("bpmn:sourceRef", BPMN_NS)
            self.assertIsNotNone(source, "у входной связи нет sourceRef")
            self.assertIn(source.text.strip(), data_ids)
            # targetRef указывает на property-заглушку самого шага, а не на шаг.
            target = assoc.find("bpmn:targetRef", BPMN_NS)
            self.assertIsNotNone(target)
            self.assertTrue(target.text.strip().endswith("_target"))
        for assoc in outputs:
            target = assoc.find("bpmn:targetRef", BPMN_NS)
            self.assertIsNotNone(target, "у выходной связи нет targetRef")
            self.assertIn(target.text.strip(), data_ids)

    def test_data_associations_live_inside_an_activity(self):
        """Спецификация разрешает их только у активности, и порядок задан XSD."""
        activity_tags = {"task", "userTask", "serviceTask", "subProcess"}
        for name in ("dataInputAssociation", "dataOutputAssociation"):
            for assoc in self.proc.findall(f".//bpmn:{name}", BPMN_NS):
                parent = next(
                    el for el in self.proc.iter()
                    if assoc in list(el)
                )
                self.assertIn(_tag_name(parent), activity_tags)
                children = [_tag_name(c) for c in parent]
                # incoming/outgoing из tFlowNode идут раньше, чем данные из
                # tActivity: иначе файл невалиден по схеме.
                for earlier in ("incoming", "outgoing"):
                    if earlier in children:
                        self.assertLess(children.index(earlier), children.index(name))

    def test_sequence_flows_connect_only_flow_nodes(self):
        flow_tags = {
            "startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent",
            "task", "userTask", "serviceTask", "subProcess",
            "exclusiveGateway", "parallelGateway", "inclusiveGateway",
        }
        for flow in self.proc.findall("bpmn:sequenceFlow", BPMN_NS):
            self.assertIn(self.tag_of.get(flow.get("sourceRef")), flow_tags)
            self.assertIn(self.tag_of.get(flow.get("targetRef")), flow_tags)

    def test_event_degrees_are_valid(self):
        incoming, outgoing = set(), set()
        for flow in self.proc.findall("bpmn:sequenceFlow", BPMN_NS):
            incoming.add(flow.get("targetRef"))
            outgoing.add(flow.get("sourceRef"))
        for node_id, tag in self.tag_of.items():
            if tag == "startEvent":
                self.assertNotIn(node_id, incoming, "у стартового события есть входящий переход")
            if tag == "endEvent":
                self.assertNotIn(node_id, outgoing, "у конечного события есть исходящий переход")

    def test_lane_references_only_flow_nodes(self):
        refs = [r.text for r in self.proc.iter("{%s}flowNodeRef" % BPMN_NS["bpmn"])]
        self.assertTrue(refs)
        self.assertEqual(len(refs), len(set(refs)))
        for ref in refs:
            self.assertNotIn(self.tag_of.get(ref), ("dataStoreReference", "dataObjectReference"))

    def test_xsd_element_order_artifacts_last(self):
        order = [el.tag.split("}")[-1] for el in self.proc]
        trailing = {"textAnnotation", "association", "group"}
        first = next((i for i, t in enumerate(order) if t in trailing), len(order))
        self.assertTrue(all(t in trailing for t in order[first:]), order)

    def test_decoration_line_is_not_exported(self):
        # e11 висит в пустоте: в BPMN такой конструкции нет.
        ids = {el.get("id") for el in self.proc}
        self.assertNotIn("e11", ids)

    def test_diagram_edges_are_orthogonal(self):
        plane = self.root.find(".//bpmndi:BPMNPlane", BPMN_NS)
        edges = plane.findall("bpmndi:BPMNEdge", BPMN_NS)
        self.assertTrue(edges)
        for edge in edges:
            points = [
                (float(w.get("x")), float(w.get("y")))
                for w in edge.findall("{http://www.omg.org/spec/DD/20100524/DI}waypoint")
            ]
            self.assertGreaterEqual(len(points), 2)
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                self.assertTrue(
                    abs(x1 - x2) < 0.5 or abs(y1 - y2) < 0.5,
                    f"диагональный отрезок в {edge.get('bpmnElement')}: {(x1, y1)} -> {(x2, y2)}",
                )

    def test_timer_event_carries_iso_duration(self):
        catching = self.proc.findall("bpmn:intermediateCatchEvent", BPMN_NS)
        durations = [
            d.text
            for el in catching
            for d in el.iter("{%s}timeDuration" % BPMN_NS["bpmn"])
        ]
        self.assertEqual(durations, ["PT30M"])

    def test_step_duration_is_visible_on_the_diagram(self):
        """Часы у шага и время под ними обязаны доживать до схемы.

        До этого ST/WT уходили только в ``documentation``: в Процессной студии
        карта открывалась без единой цифры, и сотрудник сверял длительность по
        отдельному регламенту.
        """
        markers = {
            el.get("attachedToRef"): el
            for el in self.proc.findall("bpmn:boundaryEvent", BPMN_NS)
        }
        marker = markers.get("task_a")
        self.assertIsNotNone(marker, "у шага с ST нет значка длительности")
        self.assertEqual(marker.get("name"), "5 мин")
        self.assertEqual(
            marker.get("cancelActivity"), "false",
            "значок обязан быть некрывающим: он помечает время, а не прерывает шаг",
        )
        self.assertIsNotNone(marker.find("bpmn:timerEventDefinition", BPMN_NS))
        self.assertEqual(
            marker.findtext("bpmn:timerEventDefinition/bpmn:timeDuration", None, BPMN_NS),
            "PT5M",
        )

    def test_duration_marker_sits_in_the_corner_of_its_step(self):
        marker = next(
            el for el in self.proc.findall("bpmn:boundaryEvent", BPMN_NS)
            if el.get("attachedToRef") == "task_a"
        )
        step = next(n for n in self.process.nodes if n.id == "task_a")
        plane = self.root.find(".//bpmndi:BPMNPlane", BPMN_NS)
        shape = next(
            s for s in plane.findall("bpmndi:BPMNShape", BPMN_NS)
            if s.get("bpmnElement") == marker.get("id")
        )
        box = shape.find(DC_BOUNDS)
        cx = float(box.get("x")) + float(box.get("width")) / 2
        cy = float(box.get("y")) + float(box.get("height")) / 2
        self.assertAlmostEqual(
            cy, step.geometry.y + step.geometry.height, delta=1,
            msg="часы должны сидеть на нижней грани шага",
        )
        self.assertTrue(
            step.geometry.x + step.geometry.width / 2 <= cx <= step.geometry.x + step.geometry.width,
            f"значок ушёл из правого нижнего угла шага: {cx}",
        )
        self.assertIsNotNone(
            shape.find("bpmndi:BPMNLabel", BPMN_NS),
            "время без рамки подписи импортёр разложит в столбец поверх соседей",
        )

    def test_duration_marker_does_not_change_the_flow(self):
        marker_ids = {el.get("id") for el in self.proc.findall("bpmn:boundaryEvent", BPMN_NS)}
        self.assertTrue(marker_ids, "значков длительности нет вовсе")
        for flow in self.proc.findall("bpmn:sequenceFlow", BPMN_NS):
            self.assertNotIn(flow.get("sourceRef"), marker_ids)
            self.assertNotIn(flow.get("targetRef"), marker_ids)

    def test_duration_marker_joins_the_lane_of_its_step(self):
        refs = {r.text for r in self.proc.iter("{%s}flowNodeRef" % BPMN_NS["bpmn"])}
        for el in self.proc.findall("bpmn:boundaryEvent", BPMN_NS):
            if el.get("attachedToRef") in refs:
                self.assertIn(
                    el.get("id"), refs,
                    "дорожка без ссылки на значок теряет часы своего шага",
                )

    def test_duration_label_formatting(self):
        self.assertEqual(duration_label(45), "45 мин")
        self.assertEqual(duration_label(60), "1 ч")
        self.assertEqual(duration_label(90), "1 ч 30 мин")
        self.assertEqual(duration_label(2880), "2 дн")
        self.assertEqual(duration_label(0), "")
        self.assertEqual(duration_label(None), "")

    def test_wait_time_joins_the_step_duration(self):
        node = ProcessNode(
            id="s", name="Шаг", type="userTask", slaMinutes=30, waitMinutes=15,
            slaMeasured=True,
            geometry=Geometry(x=0, y=0, width=120, height=80), style="",
        )
        self.assertEqual(step_duration_text(node), "30 мин · ожидание 15 мин")
        node.slaMinutes = 0
        self.assertEqual(step_duration_text(node), "ожидание 15 мин")

        # Время, подставленное импортом, значком часов не рисуется: в draw.io
        # у такого шага часов нет, и на карте студии их быть не должно.
        node.slaMinutes = 60
        node.slaMeasured = False
        self.assertEqual(step_duration_text(node), "")

    def test_iso_duration_formatting(self):
        self.assertEqual(iso_duration(45), "PT45M")
        self.assertEqual(iso_duration(120), "PT2H")
        self.assertEqual(iso_duration(2880), "P2D")
        self.assertEqual(iso_duration(0), "PT1M")

    def test_every_element_has_diagram_geometry(self):
        plane = self.root.find(".//bpmndi:BPMNPlane", BPMN_NS)
        shapes = {s.get("bpmnElement") for s in plane.findall("bpmndi:BPMNShape", BPMN_NS)}
        edges = {e.get("bpmnElement") for e in plane.findall("bpmndi:BPMNEdge", BPMN_NS)}
        for node_id, tag in self.tag_of.items():
            if tag == "laneSet":
                continue
            expected = edges if tag in ("sequenceFlow", "association") else shapes
            self.assertIn(node_id, expected, f"{tag} {node_id} без геометрии")


class EdgeRoutingTest(unittest.TestCase):
    """Связи должны идти по осям, как в draw.io, а не диагональю."""

    @staticmethod
    def _node(node_id, x, y, w=120, h=80):
        return ProcessNode(
            id=node_id, name=node_id, type="userTask",
            geometry=Geometry(x=x, y=y, width=w, height=h),
        )

    @staticmethod
    def _is_orthogonal(route):
        return all(
            abs(route[i][0] - route[i + 1][0]) < 0.5 or abs(route[i][1] - route[i + 1][1]) < 0.5
            for i in range(len(route) - 1)
        )

    def test_every_route_is_orthogonal(self):
        cases = [
            ({}, self._node("a", 0, 0), self._node("b", 300, 0)),
            ({}, self._node("a", 0, 0), self._node("b", 300, 200)),
            ({}, self._node("a", 0, 0), self._node("b", 0, 300)),
            ({}, self._node("a", 400, 0), self._node("b", 0, 200)),
            (dict(exitX=0.5, exitY=1.0, entryX=0.5, entryY=0.0), self._node("a", 0, 0), self._node("b", 300, 200)),
            (dict(points=[ProcessEdgePoint(x=200, y=40)]), self._node("a", 0, 0), self._node("b", 400, 300)),
        ]
        for kwargs, src, tgt in cases:
            route = orthogonal_waypoints(ProcessEdge(id="e", **kwargs), src, tgt)
            self.assertGreaterEqual(len(route), 2)
            self.assertTrue(self._is_orthogonal(route), f"{kwargs} -> {route}")

    def test_offset_route_gets_a_z_elbow(self):
        route = orthogonal_waypoints(
            ProcessEdge(id="e"), self._node("a", 0, 0), self._node("b", 300, 200)
        )
        # Выход вправо, колено по середине зазора, вход слева.
        self.assertEqual(route[0], (120.0, 40.0))
        self.assertEqual(route[-1], (300.0, 240.0))
        self.assertEqual(len(route), 4)

    def test_route_snaps_to_whole_pixels(self):
        route = orthogonal_waypoints(
            ProcessEdge(id="e"),
            self._node("a", 0, 0, w=121, h=81),
            self._node("b", 301, 201, w=121, h=81),
        )
        for x, y in route:
            self.assertEqual(x, round(x))
            self.assertEqual(y, round(y))
        self.assertTrue(self._is_orthogonal(route))


class PmmExportTest(unittest.TestCase):
    """Пакет .pmm должен совпадать по соглашениям с выгрузкой самой PIX."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(METHODOLOGY_MAP, "methodology.drawio")
        payload = generate_pmm_zip(cls.process)
        cls.zf = zipfile.ZipFile(io.BytesIO(payload))
        cls.map_part = [n for n in cls.zf.namelist() if n.startswith("pm/maps/")][0]
        cls.root = ET.fromstring(cls.zf.read(cls.map_part).decode("utf-8"))
        cls.roads = [n for n in cls.root.findall("node") if n.get("type") == "horizontalRoad"]

    def test_map_name_matches_part_and_is_readable(self):
        self.assertEqual(self.root.get("name"), os.path.basename(self.map_part)[:-4])
        self.assertEqual(self.root.get("name"), map_slug(self.process))
        self.assertNotIn("PRC-SQB", self.root.get("name"))

    def test_configuration_is_the_reference_catalog(self):
        conf = ET.fromstring(self.zf.read("pm/configuration.xml").decode("utf-8"))
        vocabulary = {e.get("name") for e in conf.find("notation[@name='BPMN']").findall("element")}
        used = {n.get("type") for n in self.root.iter("node")}
        self.assertTrue(used <= vocabulary, used - vocabulary)
        self.assertEqual(len(conf.findall("notation")), 9)

    def test_pix_types_for_methodology_shapes(self):
        used = {n.get("type") for n in self.root.iter("node")}
        self.assertIn("dataStorage", used)
        self.assertIn("dataObject", used)
        self.assertIn("intermediate_event_catch_timer", used)
        self.assertIn("boundary_non_interrupting_event_timer", used)

    def test_connector_label_uses_text_attribute(self):
        connectors = self.root.findall("connector")
        self.assertTrue(any(c.get("Text") == "Ha" for c in connectors))
        self.assertTrue(all(c.get("label") is None for c in connectors))

    def test_connector_anchors_use_only_numbers_the_studio_confirmed(self):
        # Номера якорей сняты с выгрузки самой студии (tests/fixtures/sap.pmm).
        # Список якорей фигуры по одному эталону не восстанавливается целиком —
        # у грани их несколько, — поэтому для неподтверждённых граней атрибут
        # не пишем вовсе: студия выберет точку примыкания сама, как делает и в
        # собственной выгрузке.
        for c in self.root.findall("connector"):
            self.assertIn(c.get("sourcePoint"), {None, "0", "6"}, c.get("id"))
            self.assertIn(c.get("targetPoint"), {None, "1", "3", "4", "6"}, c.get("id"))

    def test_artifact_links_are_dotted(self):
        dotted = [c for c in self.root.findall("connector") if c.get("lineStyle") == "dotted"]
        self.assertEqual(len(dotted), 3, "оформительская линия e11 в карту не идёт, ассоциации идут")
        for c in dotted:
            # `arrow` в выгрузке студии не встречается ни разу: пунктирным
            # связям там отвечает `arrowLine`, а незнакомый маркер студия
            # отбрасывает вместе со связью.
            self.assertEqual(c.findtext("MarkerEnd"), "arrowLine")
            # Стиль линии живёт только в атрибуте: дочернего <lineStyle>
            # у студии нет ни у одной связи.
            self.assertIsNone(c.find("lineStyle"))

    def test_nodes_are_clamped_into_their_lane(self):
        for road in self.roads:
            rw, rh = float(road.get("width")), float(road.get("height"))
            for child in road.findall("node"):
                x, y = float(child.get("x")), float(child.get("y"))
                w, h = float(child.get("width")), float(child.get("height"))
                self.assertGreaterEqual(x, 0, child.get("label"))
                self.assertGreaterEqual(y, 0, child.get("label"))
                self.assertLessEqual(x + w, rw, child.get("label"))
                self.assertLessEqual(y + h, rh, child.get("label"))

    def test_step_duration_is_drawn_on_the_pix_map(self):
        """Часы со временем шага обязаны доехать до карты студии.

        Без них .pmm открывается как схема без единой цифры: длительность
        операции остаётся только в отдельном регламенте.
        """
        timers = [
            n for road in self.roads for n in road.findall("node")
            if n.get("type") == "boundary_non_interrupting_event_timer"
        ]
        labels = {n.get("label") for n in timers}
        self.assertIn("5 мин", labels, "время шага «Hujjatlarni qabul qilish» потерялось")
        self.assertIn("10 мин", labels, "время шага «Dalolatnoma tuzish» потерялось")

    def test_duration_marker_is_a_boundary_event_not_an_intermediate_one(self):
        """Значок длительности — граничное событие, и это не косметика.

        Промежуточное событие обязано стоять в потоке, поэтому студия писала на
        каждом значке «отсутствует входящий поток управления» и столько же раз
        про исходящий: на карте из трёхсот шагов список замечаний упирался в
        «99+», и настоящих ошибок за ними видно не было.
        """
        by_label = {
            n.get("label"): n.get("type")
            for road in self.roads for n in road.findall("node")
        }
        self.assertEqual(by_label.get("5 мин"), "boundary_non_interrupting_event_timer")
        # Собственное ожидание карты промежуточным и остаётся: оно в потоке.
        # Подпись при этом несёт своё время, как и на исходной карте draw.io.
        waiting = [label for label, kind in by_label.items()
                   if kind == "intermediate_event_catch_timer"]
        self.assertTrue(
            any(label.startswith("Kutish vaqti") for label in waiting),
            f'ожидание потерялось: {waiting}')
        self.assertTrue(
            all(any(ch.isdigit() for ch in label) for label in waiting),
            f'у ожидания пропало время: {waiting}')

    def test_duration_marker_is_not_connected_to_anything(self):
        marker_ids = {
            n.get("id") for road in self.roads for n in road.findall("node")
            if n.get("type") == "boundary_non_interrupting_event_timer"
        }
        self.assertTrue(marker_ids)
        for connector in self.root.findall("connector"):
            self.assertNotIn(connector.get("sourceNodeId"), marker_ids)
            self.assertNotIn(connector.get("targetNodeId"), marker_ids)

    def test_decoration_line_is_not_in_the_map(self):
        # Идентификаторы в .pmm — uuid5 от исходных, поэтому сверяем по числу:
        # в карту идут все связи, кроме оформительских.
        expected = [e for e in self.process.edges if e.kind != "annotationLine"]
        decoration = [e for e in self.process.edges if e.kind == "annotationLine"]
        self.assertTrue(decoration, "фикстура должна содержать оформительскую линию")
        self.assertEqual(len(self.root.findall("connector")), len(expected))

    def test_no_flow_node_floats_outside_the_lanes(self):
        loose = [
            n for n in self.root.findall("node")
            if n.get("type") not in ("horizontalRoad", "emptyPool")
        ]
        self.assertEqual(loose, [])

    def test_waypoints_form_a_complete_polyline(self):
        absolute = {}
        for road in self.roads:
            for child in road.findall("node"):
                absolute[child.get("id")] = (
                    float(road.get("x")) + float(child.get("x")),
                    float(road.get("y")) + float(child.get("y")),
                    float(child.get("width")),
                    float(child.get("height")),
                )
        routed = [c for c in self.root.findall("connector") if c.find("waypoint") is not None]
        self.assertTrue(routed, "ломаная с изломами из draw.io потеряна")
        for c in routed:
            points = c.findall("waypoint")
            self.assertEqual(
                [int(p.get("index")) for p in points],
                list(range(len(points))),
                "индексы waypoint должны идти подряд с нуля",
            )
            for node_id, wp in ((c.get("sourceNodeId"), points[0]),
                                (c.get("targetNodeId"), points[-1])):
                x, y, w, h = absolute[node_id]
                px, py = float(wp.get("x")), float(wp.get("y"))
                self.assertTrue(
                    x - 1 <= px <= x + w + 1 and y - 1 <= py <= y + h + 1,
                    f"конец ломаной {px},{py} не лежит на узле {x},{y},{w},{h}",
                )


#: Карта банка так, как её рисует аналитик: полоса клиента без единого шага,
#: пунктир от шагов банка к ней, врезка с перечнем документов, таймер ожидания
#: без подписи (вся подпись — длительность) и вторая страница в том же файле.
CLIENT_TOUCHPOINT_MAP = """<mxfile host="test" pages="2">
  <diagram name="AS IS" id="p1">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="client" value="Mijoz" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="800" height="80" as="geometry" />
        </mxCell>
        <mxCell id="bank" value="Bank" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="40" y="160" width="800" height="200" as="geometry" />
        </mxCell>
        <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=general;outline=standard;" vertex="1" parent="bank">
          <mxGeometry x="60" y="60" width="48" height="48" as="geometry" />
        </mxCell>
        <mxCell id="task_1" value="Hujjatlarni qabul qilish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="bank">
          <mxGeometry x="160" y="50" width="120" height="80" as="geometry" />
        </mxCell>
        <mxCell id="wait_1" value="10 min" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=timer;outline=catching;" vertex="1" parent="bank">
          <mxGeometry x="330" y="65" width="40" height="40" as="geometry" />
        </mxCell>
        <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=terminate2;outline=end;" vertex="1" parent="bank">
          <mxGeometry x="430" y="60" width="48" height="48" as="geometry" />
        </mxCell>
        <mxCell id="note_1" value="Ustav; Tasischilar qarori; Rahbar pasporti" style="text;html=1;whiteSpace=wrap;strokeColor=default;fillColor=none;dashed=1;" vertex="1" parent="bank">
          <mxGeometry x="160" y="150" width="200" height="40" as="geometry" />
        </mxCell>
        <mxCell id="banner" value="Jarayon xaritasi (AS IS)" style="text;html=1;strokeColor=none;fillColor=none;fontSize=26;" vertex="1" parent="1">
          <mxGeometry x="300" y="0" width="400" height="30" as="geometry" />
        </mxCell>

        <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="bank" source="start_1" target="task_1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="f2" value="Ha" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="bank" source="task_1" target="wait_1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="f3" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="bank" source="wait_1" target="end_1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="touch_1" style="edgeStyle=orthogonalEdgeStyle;exitX=0.5;exitY=0;dashed=1;dashPattern=8 8;" edge="1" parent="bank" source="task_1">
          <mxGeometry relative="1" as="geometry">
            <mxPoint x="220" y="-40" as="targetPoint" />
          </mxGeometry>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
  <diagram name="TO BE" id="p2">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="tobe_task" value="Boshqa sahifadagi qadam" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="120" height="80" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


class ClientTouchpointTest(unittest.TestCase):
    """Полоса клиента, её пунктир и подписи фигур обязаны дожить до выгрузок."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(CLIENT_TOUCHPOINT_MAP, "touchpoints.drawio")
        cls.by_id = {n.id: n for n in cls.process.nodes}
        cls.xml = generate_bpmn_xml(cls.process)
        cls.root = ET.fromstring(cls.xml)
        payload = generate_pmm_zip(cls.process)
        zf = zipfile.ZipFile(io.BytesIO(payload))
        map_part = [n for n in zf.namelist() if n.startswith("pm/maps/")][0]
        cls.map = ET.fromstring(zf.read(map_part).decode("utf-8"))

    # ── импорт ──────────────────────────────────────────────────────────────
    def test_only_the_first_page_is_imported(self):
        # Страницы файла — варианты одного процесса (AS-IS / TO-BE). Склейка
        # накладывала их друг на друга; берём первую, как и сам draw.io.
        self.assertNotIn("tobe_task", self.by_id)
        self.assertIn("task_1", self.by_id)

    def test_timer_without_a_caption_gets_a_readable_name(self):
        timer = self.by_id["wait_1"]
        self.assertEqual(timer.type, "intermediateTimerEvent")
        self.assertEqual(timer.name, "Ожидание 10 мин")

    def test_bordered_text_box_survives_as_an_annotation(self):
        note = self.by_id["note_1"]
        self.assertEqual(note.type, "textAnnotation")
        self.assertIn("Ustav", note.name)

    def test_diagram_banner_is_still_decoration(self):
        self.assertNotIn("banner", self.by_id)

    def test_dashed_line_to_the_client_lane_is_a_message_flow(self):
        edge = next(e for e in self.process.edges if e.id == "touch_1")
        self.assertEqual(edge.kind, "messageFlow")
        self.assertEqual(edge.targetId, "client")

    # ── BPMN ────────────────────────────────────────────────────────────────
    def test_empty_lane_becomes_its_own_named_participant(self):
        collab = self.root.find("bpmn:collaboration", BPMN_NS)
        self.assertIsNotNone(collab)
        parts = collab.findall("bpmn:participant", BPMN_NS)
        self.assertIn("Mijoz", {p.get("name") for p in parts})
        client = next(p for p in parts if p.get("name") == "Mijoz")

        # Пул без processRef импортёр считает свёрнутым и печатает имя по
        # центру полосы — на карте в 4620 px подпись уезжает за экран, и строка
        # выглядит безымянной. Раскрытый пул держит имя в шапке слева.
        ref = client.get("processRef")
        self.assertIsNotNone(ref, "у полосы участника должен быть собственный процесс")
        own = [
            p for p in self.root.findall("bpmn:process", BPMN_NS) if p.get("id") == ref
        ]
        self.assertEqual(len(own), 1, "процесс участника не объявлен")
        self.assertEqual(len(list(own[0])), 0, "процесс внешнего участника пуст")

        lane_names = {l.get("name") for l in self.root.iter("{%s}lane" % BPMN_NS["bpmn"])}
        self.assertNotIn("Mijoz", lane_names, "полоса клиента не должна остаться дорожкой пула")
        self.assertIn("Bank", lane_names)

    def test_touchpoint_is_exported_as_a_message_flow_with_geometry(self):
        collab = self.root.find("bpmn:collaboration", BPMN_NS)
        flows = collab.findall("bpmn:messageFlow", BPMN_NS)
        self.assertEqual(len(flows), 1)
        client_id = next(
            p.get("id") for p in collab.findall("bpmn:participant", BPMN_NS)
            if p.get("name") == "Mijoz"
        )
        self.assertEqual(flows[0].get("targetRef"), client_id)
        plane = self.root.find(".//bpmndi:BPMNPlane", BPMN_NS)
        di = {e.get("bpmnElement") for e in plane.findall("bpmndi:BPMNEdge", BPMN_NS)}
        shapes = {s.get("bpmnElement") for s in plane.findall("bpmndi:BPMNShape", BPMN_NS)}
        self.assertIn(flows[0].get("id"), di)
        self.assertIn(client_id, shapes)

    def test_pool_lanes_tile_without_gaps(self):
        plane = self.root.find(".//bpmndi:BPMNPlane", BPMN_NS)
        bounds = {}
        for shape in plane.findall("bpmndi:BPMNShape", BPMN_NS):
            b = shape.find("{http://www.omg.org/spec/DD/20100524/DC}Bounds")
            bounds[shape.get("bpmnElement")] = tuple(
                float(b.get(k)) for k in ("x", "y", "width", "height")
            )
        lane_ids = [l.get("id") for l in self.root.iter("{%s}lane" % BPMN_NS["bpmn"])]
        pool_id = next(
            p.get("id") for p in self.root.iter("{%s}participant" % BPMN_NS["bpmn"])
            if p.get("processRef")
        )
        px, py, pw, ph = bounds[pool_id]
        boxes = sorted((bounds[i] for i in lane_ids), key=lambda b: b[1])
        self.assertAlmostEqual(boxes[0][1], py)
        self.assertAlmostEqual(boxes[-1][1] + boxes[-1][3], py + ph)
        for a, b in zip(boxes, boxes[1:]):
            self.assertAlmostEqual(a[1] + a[3], b[1], msg="разрыв между дорожками пула")
        self.assertEqual({(b[0], b[2]) for b in boxes}, {(px + 30, pw - 30)})

    def test_named_flows_carry_a_label_box(self):
        plane = self.root.find(".//bpmndi:BPMNPlane", BPMN_NS)
        named = [f for f in self.root.iter("{%s}sequenceFlow" % BPMN_NS["bpmn"]) if f.get("name")]
        self.assertTrue(named, "фикстура должна содержать подписанный переход")
        for flow in named:
            di = next(
                e for e in plane.findall("bpmndi:BPMNEdge", BPMN_NS)
                if e.get("bpmnElement") == flow.get("id")
            )
            self.assertIsNotNone(di.find("bpmndi:BPMNLabel", BPMN_NS))

    # ── .pmm ────────────────────────────────────────────────────────────────
    def test_client_row_and_its_touchpoint_stay_on_the_pix_map(self):
        roads = [n for n in self.map.findall("node") if n.get("type") == "horizontalRoad"]
        self.assertEqual({r.get("label") for r in roads}, {"Mijoz", "Bank"})
        client = next(r for r in roads if r.get("label") == "Mijoz")
        # Линия ведётся не в саму полосу, а к маркеру сообщения на её границе.
        # В собственных выгрузках студии связей, упирающихся в дорожку, нет ни
        # одной: такую линию она цепляет за центр фигуры, и все пунктиры к
        # клиенту сходились в одну точку посреди схемы.
        self.assertEqual(
            [c for c in self.map.findall("connector")
             if client.get("id") in (c.get("sourceNodeId"), c.get("targetNodeId"))],
            [], "связь не должна упираться в полосу")
        inside = [n for n in client.findall("node")
                  if n.get("type") == "intermediate_event_catch_message"]
        self.assertEqual(len(inside), 1, "маркер контакта с клиентом потерян")
        touching = [
            c for c in self.map.findall("connector")
            if inside[0].get("id") in (c.get("sourceNodeId"), c.get("targetNodeId"))
        ]
        self.assertEqual(len(touching), 1, "линия к маркеру клиента потеряна")
        # Точка контакта с клиентом — поток сообщений, и студия рисует его
        # так же, как BPMN: штриховая линия, кружок в начале, открытая
        # стрелка в конце (единственная dashed-связь в tests/fixtures/sap.pmm).
        self.assertEqual(touching[0].get("lineStyle"), "dashed")
        self.assertEqual(touching[0].findtext("MarkerStart"), "circle")
        self.assertEqual(touching[0].findtext("MarkerEnd"), "arrowEmpty")
        self.assertIsNotNone(touching[0].find("waypoint"))

    def test_every_connector_carries_its_own_polyline(self):
        # Без waypoint студия трассирует связь сама и кладёт линии друг на друга.
        cons = self.map.findall("connector")
        self.assertTrue(cons)
        for c in cons:
            points = c.findall("waypoint")
            self.assertGreaterEqual(len(points), 2, c.get("id"))
            self.assertEqual([int(p.get("index")) for p in points], list(range(len(points))))
            coords = [(float(p.get("x")), float(p.get("y"))) for p in points]
            for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
                self.assertTrue(abs(x1 - x2) < 0.5 or abs(y1 - y2) < 0.5, c.get("id"))

    def test_map_labels_never_fall_back_to_cell_ids(self):
        """Подпись — это текст аналитика, а не идентификатор ячейки draw.io.

        Пустая подпись дефектом не является и ставится намеренно: у безымянного
        шлюза её нет и в draw.io, у маркера контакта с клиентом — тоже. Ловим
        именно подстановку id, из-за которой на карте появлялись надписи вида
        «7x07wl9l_jNJTChX3Y1P-25».
        """
        for node in self.map.iter("node"):
            label = (node.get("label") or "").strip()
            self.assertNotEqual(label, node.get("id"), "подписью стал id фигуры")
            self.assertFalse(
                label and _looks_like_cell_id(label), f'подпись похожа на id: {label}')
            self.assertNotRegex(label, r"Операция [A-Za-z0-9_-]{8,}")


if __name__ == "__main__":
    unittest.main()
