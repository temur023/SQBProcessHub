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
import unittest
import xml.etree.ElementTree as ET
import zipfile
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessEdge, ProcessEdgePoint, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import generate_bpmn_xml, iso_duration
from app.services.edge_routing import orthogonal_waypoints
from app.services.pmm_exporter import generate_pmm_zip, map_slug

BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
}

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

    def test_associations_replace_sequence_flows_to_artifacts(self):
        assoc = self.proc.findall("bpmn:association", BPMN_NS)
        self.assertEqual(len(assoc), 3, "e6, e7 и e10 — все три связи с артефактами")
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
        durations = [d.text for d in self.proc.iter("{%s}timeDuration" % BPMN_NS["bpmn"])]
        self.assertEqual(durations, ["PT30M"])

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

    def test_connector_label_uses_text_attribute(self):
        connectors = self.root.findall("connector")
        self.assertTrue(any(c.get("Text") == "Ha" for c in connectors))
        self.assertTrue(all(c.get("label") is None for c in connectors))

    def test_connector_anchor_is_left_to_the_studio(self):
        for c in self.root.findall("connector"):
            self.assertIsNone(c.get("targetPoint"))
            self.assertIsNone(c.get("sourcePoint"))

    def test_artifact_links_are_dotted(self):
        dotted = [c for c in self.root.findall("connector") if c.get("lineStyle") == "dotted"]
        self.assertEqual(len(dotted), 3, "оформительская линия e11 в карту не идёт, ассоциации идут")
        for c in dotted:
            self.assertEqual(c.findtext("MarkerEnd"), "arrowLine")

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


if __name__ == "__main__":
    unittest.main()
