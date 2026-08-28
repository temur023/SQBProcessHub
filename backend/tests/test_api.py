import os
import tempfile
import unittest

# Уводим персист процессов в временный файл ДО импорта приложения: иначе
# каждый прогон дописывает свои фикстуры в app/data/process_store.json.
os.environ.setdefault(
    "SQB_PROCESS_STORE",
    os.path.join(tempfile.gettempdir(), "sqb_process_store_test.json"),
)

from fastapi.testclient import TestClient
from app.main import app

class TestSQBProcessHubApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": "1.0.0"})

    def test_root_endpoint(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("docs", response.json())

    def test_import_xml_and_export_lifecycle(self):
        drawio_xml = """<mxfile host="app.diagrams.net">
          <diagram id="test-1" name="Кредитный конвейер">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="start_1" value="Поступление заявки" style="ellipse;fillColor=#10b981;" vertex="1" parent="1">
                  <mxGeometry x="100" y="100" width="48" height="48" as="geometry" />
                </mxCell>
                <mxCell id="step_1" value="[PIX RPA] STEP-01: Авто-скоринг KATM" style="rounded=1;fillColor=#dcfce7;" vertex="1" parent="1">
                  <mxGeometry x="200" y="90" width="180" height="70" as="geometry" />
                </mxCell>
                <mxCell id="end_1" value="Кредит выдан" style="ellipse;fillColor=#059669;" vertex="1" parent="1">
                  <mxGeometry x="450" y="100" width="48" height="48" as="geometry" />
                </mxCell>
                <mxCell id="e1" edge="1" source="start_1" target="step_1" parent="1" />
                <mxCell id="e2" edge="1" source="step_1" target="end_1" parent="1" />
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>"""

        # 1. Test XML Import
        import_res = self.client.post("/api/v1/import/xml", json={
            "xml": drawio_xml,
            "fileName": "credit_process.drawio"
        })
        self.assertEqual(import_res.status_code, 200)
        proc = import_res.json()
        proc_id = proc["id"]

        self.assertEqual(proc["name"], "credit_process")
        self.assertEqual(len(proc["nodes"]), 3)
        self.assertEqual(proc["nodes"][1]["category"], "rpa_bot")
        by_id = {n["id"]: n["type"] for n in proc["nodes"]}
        self.assertEqual(by_id["start_1"], "startEvent")
        self.assertEqual(by_id["end_1"], "endEvent")
        self.assertEqual(by_id["step_1"], "serviceTask")

        # 2. Test Get Process by ID
        get_res = self.client.get(f"/api/v1/processes/{proc_id}")
        self.assertEqual(get_res.status_code, 200)

        # 3. Test Processet BPMN 2.0 Export
        bpmn_res = self.client.get(f"/api/v1/import/{proc_id}/export/bpmn")
        self.assertEqual(bpmn_res.status_code, 200)
        self.assertIn("bpmn:definitions", bpmn_res.text)
        self.assertIn("bpmndi:BPMNDiagram", bpmn_res.text)

        # 4. Test Event Log CSV Export for Processet
        log_res = self.client.get(f"/api/v1/import/{proc_id}/export/event-log")
        self.assertEqual(log_res.status_code, 200)
        self.assertIn("Case_ID", log_res.text)
        log_rows = [row for row in log_res.text.splitlines() if row and not row.startswith('"Case_ID"')]
        self.assertGreaterEqual(len(log_rows), 2)
        starts = [row.split(",")[5] for row in log_rows]
        self.assertGreater(len(set(starts)), 1)

        # 5. Test Regulation CSV Export
        reg_res = self.client.get(f"/api/v1/import/{proc_id}/export/regulation")
        self.assertEqual(reg_res.status_code, 200)
        self.assertIn("Потенциал роботизации (PIX RPA)", reg_res.text)

        # 6. Test PIX Registry Case Creation
        case_res = self.client.post(f"/api/v1/processes/{proc_id}/registry/cases", json={
            "caseId": "SQB-2026-TEST01",
            "assignedTo": "Кредитный эксперт филиала",
            "data": {"client_inn": "301928374", "amount_uzs": 500000000}
        })
        self.assertEqual(case_res.status_code, 201)

        # 7. Test Analytics & Process Mining Metrics
        mining_res = self.client.get(f"/api/v1/analytics/{proc_id}/mining")
        self.assertEqual(mining_res.status_code, 200)
        self.assertIn("conformanceRate", mining_res.json())

        # 8. Test RPA Candidates
        rpa_res = self.client.get(f"/api/v1/analytics/{proc_id}/rpa-candidates")
        self.assertEqual(rpa_res.status_code, 200)
        self.assertTrue(len(rpa_res.json()) >= 1)

    def test_nested_timer_icons_are_not_start_events(self):
        drawio_xml = """<mxfile host="app.diagrams.net">
          <diagram id="uz-1" name="Korporativ">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="lane_rm" value="RM" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="1">
                  <mxGeometry x="40" y="40" width="900" height="160" as="geometry" />
                </mxCell>
                <mxCell id="lane_is" value="I servis" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="1">
                  <mxGeometry x="40" y="200" width="900" height="160" as="geometry" />
                </mxCell>
                <mxCell id="start_1" value="Boshlanish" style="ellipse;fillColor=#10b981;" vertex="1" parent="lane_rm">
                  <mxGeometry x="40" y="50" width="48" height="48" as="geometry" />
                </mxCell>
                <mxCell id="step_1" value="Murojaatni qabul qilish" style="rounded=1;" vertex="1" parent="lane_rm">
                  <mxGeometry x="140" y="40" width="180" height="70" as="geometry" />
                </mxCell>
                <mxCell id="timer_child" value="" style="ellipse;html=1;aspect=fixed;shape=mxgraph.bpmn.timer;symbol=timer;" vertex="1" parent="step_1">
                  <mxGeometry x="-8" y="-8" width="20" height="20" as="geometry" />
                </mxCell>
                <mxCell id="clock_sib" value="" style="ellipse;html=1;aspect=fixed;" vertex="1" parent="lane_rm">
                  <mxGeometry x="300" y="30" width="20" height="20" as="geometry" />
                </mxCell>
                <mxCell id="end_1" value="Tugashi" style="ellipse;fillColor=#059669;" vertex="1" parent="lane_is">
                  <mxGeometry x="500" y="50" width="48" height="48" as="geometry" />
                </mxCell>
                <mxCell id="e1" edge="1" source="start_1" target="step_1" parent="1" />
                <mxCell id="e2" edge="1" source="step_1" target="end_1" parent="1" />
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>"""

        import_res = self.client.post("/api/v1/import/xml", json={
            "xml": drawio_xml,
            "fileName": "korporativ.drawio",
        })
        self.assertEqual(import_res.status_code, 200, import_res.text)
        proc = import_res.json()
        ids = {n["id"] for n in proc["nodes"]}
        self.assertIn("start_1", ids)
        self.assertIn("step_1", ids)
        self.assertIn("end_1", ids)
        self.assertNotIn("timer_child", ids)
        self.assertNotIn("clock_sib", ids)
        starts = [n for n in proc["nodes"] if n["type"] == "startEvent"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["id"], "start_1")
        lane_names = {l["name"] for l in proc["lanes"]}
        self.assertIn("RM", lane_names)
        self.assertIn("I servis", lane_names)

    def test_mxgraph_absolute_coords_and_edge_anchors(self):
        drawio_xml = """<mxfile host="app.diagrams.net">
          <diagram id="geo-1" name="Geo">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="pool" value="Pool" style="swimlane;html=1;childLayout=stackLayout;horizontal=0;startSize=20;" vertex="1" parent="1">
                  <mxGeometry x="10" y="20" width="600" height="200" as="geometry" />
                </mxCell>
                <mxCell id="lane_a" value="Lane A" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
                  <mxGeometry x="0" y="20" width="600" height="180" as="geometry" />
                </mxCell>
                <mxCell id="task_a" value="Task A" style="rounded=1;align=left;verticalAlign=top;spacing=4;" vertex="1" parent="lane_a">
                  <mxGeometry x="40" y="40" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="task_b" value="Task B" style="rounded=1;" vertex="1" parent="lane_a">
                  <mxGeometry x="260" y="40" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="e_ab" value="" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="lane_a" source="task_a" target="task_b">
                  <mxGeometry relative="1" as="geometry">
                    <Array as="points">
                      <mxPoint x="200" y="70" />
                    </Array>
                  </mxGeometry>
                </mxCell>
                <mxCell id="e_ab_label" value="Ha" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e_ab">
                  <mxGeometry x="0.5" y="-12" relative="1" as="geometry">
                    <mxPoint x="0" y="0" as="offset" />
                  </mxGeometry>
                </mxCell>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>"""

        import_res = self.client.post("/api/v1/import/xml", json={
            "xml": drawio_xml,
            "fileName": "geo.drawio",
        })
        self.assertEqual(import_res.status_code, 200, import_res.text)
        proc = import_res.json()
        by_id = {n["id"]: n for n in proc["nodes"]}
        self.assertIn("task_a", by_id)
        # Absolute = local + lane (0,20) + pool (10,20) => (50, 80).
        # Рамку после разбора подгоняют под подпись, поэтому сверяем центр:
        # именно он обязан остаться там, где фигуру поставил аналитик.
        geo_a = by_id["task_a"]["geometry"]
        self.assertEqual(geo_a["x"], 50)
        self.assertEqual(geo_a["width"], 120)
        self.assertEqual(geo_a["y"] + geo_a["height"] / 2, 80 + 60 / 2)
        self.assertEqual(by_id["task_b"]["geometry"]["x"], 270)
        edges = {e["id"]: e for e in proc["edges"]}
        self.assertIn("e_ab", edges)
        edge = edges["e_ab"]
        self.assertEqual(edge["exitX"], 1)
        self.assertEqual(edge["exitY"], 0.5)
        self.assertEqual(edge["entryX"], 0)
        self.assertEqual(edge["entryY"], 0.5)
        self.assertEqual(edge["name"], "Ha")
        self.assertEqual(edge["labelX"], 0.5)
        self.assertEqual(edge["labelY"], -12)
        # Waypoint 200,70 is in lane space: + pool(10,20) + lane(0,20) = 210, 110
        self.assertEqual(len(edge["points"]), 1)
        self.assertEqual(edge["points"][0]["x"], 210)
        self.assertEqual(edge["points"][0]["y"], 110)

    def test_green_end_event_and_question_task_not_gateway(self):
        from app.services.drawio_parser import classify_vertex
        self.assertEqual(
            classify_vertex('ellipse;fillColor=#059669;', 'Кредит выдан', True, False, 'end_1'),
            'endEvent',
        )
        self.assertEqual(
            classify_vertex('ellipse;fillColor=#10b981;', 'Поступление заявки', False, True, 'start_1'),
            'startEvent',
        )
        self.assertEqual(
            classify_vertex('rounded=1;', 'Документы полные?', True, True, 'step_docs'),
            'userTask',
        )

    def test_bpmn_import_and_xml_escape(self):
        bpmn = """<?xml version="1.0" encoding="UTF-8"?>
        <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                          xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                          xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                          id="Defs_1" targetNamespace="http://bpmn.io/schema/bpmn">
          <bpmn:process id="Process_1" name="Onboarding &amp; KYC" isExecutable="true">
            <bpmn:startEvent id="s1" name="Старт"/>
            <bpmn:userTask id="t1" name="Проверка ИНН &amp; паспорт"/>
            <bpmn:endEvent id="e1" name="Готово"/>
            <bpmn:sequenceFlow id="f1" sourceRef="s1" targetRef="t1"/>
            <bpmn:sequenceFlow id="f2" sourceRef="t1" targetRef="e1"/>
          </bpmn:process>
        </bpmn:definitions>"""

        import_res = self.client.post("/api/v1/import/xml", json={
            "xml": bpmn,
            "fileName": "onboarding.bpmn"
        })
        self.assertEqual(import_res.status_code, 200, import_res.text)
        proc = import_res.json()
        self.assertGreaterEqual(len(proc["nodes"]), 3)
        types = {n["type"] for n in proc["nodes"]}
        self.assertIn("startEvent", types)
        self.assertIn("endEvent", types)
        self.assertIn("userTask", types)

        bpmn_res = self.client.get(f"/api/v1/import/{proc['id']}/export/bpmn")
        self.assertEqual(bpmn_res.status_code, 200)
        self.assertIn("Проверка ИНН &amp; паспорт", bpmn_res.text)
        self.assertNotIn('name="Проверка ИНН & паспорт"', bpmn_res.text)
        self.assertIn("<bpmn:incoming>f1</bpmn:incoming>", bpmn_res.text)

    def test_mxgraph_bpmn_stencil_classification(self):
        from app.services.drawio_parser import classify_vertex
        self.assertEqual(
            classify_vertex('shape=mxgraph.bpmn.event;outline=standard;symbol=general;', 'Boshlanish', False, True, 'ev1'),
            'startEvent',
        )
        self.assertEqual(
            classify_vertex('shape=mxgraph.bpmn.event;outline=end;symbol=terminate;', 'Tugashi', True, False, 'ev2'),
            'endEvent',
        )
        self.assertEqual(
            classify_vertex('shape=mxgraph.bpmn.task2;whiteSpace=wrap;taskMarker=user;', 'Mijoz ehtiyojini aniqlash', True, True, 't1'),
            'userTask',
        )
        self.assertEqual(
            classify_vertex('shape=mxgraph.bpmn.task2;taskMarker=service;', 'Avto-skoring', True, True, 't2'),
            'serviceTask',
        )
        self.assertEqual(
            classify_vertex('shape=mxgraph.bpmn.gateway2;gwType=exclusive;', 'Risk?', True, True, 'g1'),
            'exclusiveGateway',
        )
        self.assertEqual(
            classify_vertex('shape=mxgraph.bpmn.gateway2;gwType=parallel;', '+', True, True, 'g2'),
            'parallelGateway',
        )

    def test_bpmn_export_waypoints_conditions_and_collaboration(self):
        drawio_xml = """<mxfile host="app.diagrams.net">
          <diagram id="geo-2" name="Geo">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="pool" value="Pool" style="swimlane;html=1;childLayout=stackLayout;horizontal=0;startSize=20;" vertex="1" parent="1">
                  <mxGeometry x="10" y="20" width="600" height="200" as="geometry" />
                </mxCell>
                <mxCell id="lane_a" value="Lane A" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
                  <mxGeometry x="0" y="20" width="600" height="180" as="geometry" />
                </mxCell>
                <mxCell id="task_a" value="Task A" style="rounded=1;" vertex="1" parent="lane_a">
                  <mxGeometry x="40" y="40" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="task_b" value="Task B" style="rounded=1;" vertex="1" parent="lane_a">
                  <mxGeometry x="260" y="40" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="e_ab" value="" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="lane_a" source="task_a" target="task_b">
                  <mxGeometry relative="1" as="geometry">
                    <Array as="points">
                      <mxPoint x="200" y="70" />
                    </Array>
                  </mxGeometry>
                </mxCell>
                <mxCell id="e_ab_label" value="Ha" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e_ab">
                  <mxGeometry x="0.5" y="-12" relative="1" as="geometry" />
                </mxCell>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>"""

        import_res = self.client.post("/api/v1/import/xml", json={
            "xml": drawio_xml,
            "fileName": "geo_export.drawio",
        })
        self.assertEqual(import_res.status_code, 200, import_res.text)
        proc_id = import_res.json()["id"]
        bpmn_res = self.client.get(f"/api/v1/import/{proc_id}/export/bpmn")
        self.assertEqual(bpmn_res.status_code, 200)
        xml = bpmn_res.text
        self.assertIn("bpmn:collaboration", xml)
        self.assertIn("bpmn:participant", xml)
        self.assertIn("Lane A", xml)
        # Ломаная выходит из правой грани task_a и входит в левую грань task_b.
        # Излом draw.io на (210,110) лежит ровно на этом отрезке, поэтому
        # трассировщик его убирает — геометрия линии от этого не меняется.
        self.assertIn('<di:waypoint x="170" y="110" />', xml)
        self.assertIn('<di:waypoint x="270" y="110" />', xml)
        self.assertIn("conditionExpression", xml)
        self.assertIn("Ha", xml)
        disp = bpmn_res.headers.get("content-disposition", "") or bpmn_res.headers.get("Content-Disposition", "")
        self.assertIn("PIX_Map.bpmn", disp)

    def test_pmm_export_three_xml_package(self):
        import io
        import re
        import zipfile
        import xml.etree.ElementTree as ET

        drawio_xml = """<mxfile host="app.diagrams.net">
          <diagram id="pmm-1" name="PMM">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="pool" value="Pool" style="swimlane;html=1;childLayout=stackLayout;horizontal=0;startSize=20;" vertex="1" parent="1">
                  <mxGeometry x="10" y="20" width="700" height="220" as="geometry" />
                </mxCell>
                <mxCell id="lane_a" value="Lane A" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
                  <mxGeometry x="0" y="20" width="700" height="200" as="geometry" />
                </mxCell>
                <mxCell id="start_1" value="Старт" style="ellipse;fillColor=#10b981;" vertex="1" parent="lane_a">
                  <mxGeometry x="30" y="70" width="48" height="48" as="geometry" />
                </mxCell>
                <mxCell id="task_a" value="Task A" style="rounded=1;" vertex="1" parent="lane_a">
                  <mxGeometry x="120" y="60" width="120" height="60" as="geometry" />
                </mxCell>
                <mxCell id="gw_1" value="Risk?" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;" vertex="1" parent="lane_a">
                  <mxGeometry x="280" y="62" width="50" height="50" as="geometry" />
                </mxCell>
                <mxCell id="step_rpa" value="[PIX RPA] Авто-скоринг" style="rounded=1;fillColor=#dcfce7;" vertex="1" parent="lane_a">
                  <mxGeometry x="370" y="60" width="160" height="60" as="geometry" />
                </mxCell>
                <mxCell id="end_1" value="Готово" style="ellipse;fillColor=#059669;" vertex="1" parent="lane_a">
                  <mxGeometry x="580" y="70" width="48" height="48" as="geometry" />
                </mxCell>
                <mxCell id="e_st" edge="1" source="start_1" target="task_a" parent="lane_a" />
                <mxCell id="e_tg" edge="1" source="task_a" target="gw_1" parent="lane_a" />
                <mxCell id="e_yes" value="" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="lane_a" source="gw_1" target="step_rpa">
                  <mxGeometry relative="1" as="geometry">
                    <Array as="points">
                      <mxPoint x="345" y="87" />
                    </Array>
                  </mxGeometry>
                </mxCell>
                <mxCell id="e_yes_label" value="Ha" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e_yes">
                  <mxGeometry x="0.5" y="-12" relative="1" as="geometry" />
                </mxCell>
                <mxCell id="e_rpa_end" edge="1" source="step_rpa" target="end_1" parent="lane_a" />
                <mxCell id="e_no" value="Yo'q" style="dashed=1;dashPattern=8 8;" edge="1" parent="lane_a" source="gw_1" target="end_1" />
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>"""

        import_res = self.client.post("/api/v1/import/xml", json={
            "xml": drawio_xml,
            "fileName": "pmm_export.drawio",
        })
        self.assertEqual(import_res.status_code, 200, import_res.text)
        proc = import_res.json()
        proc_id = proc["id"]

        missing = self.client.get("/api/v1/import/does-not-exist/export/pmm")
        self.assertEqual(missing.status_code, 404)

        pmm_res = self.client.get(f"/api/v1/import/{proc_id}/export/pmm")
        self.assertEqual(pmm_res.status_code, 200)
        disp = pmm_res.headers.get("content-disposition", "") or pmm_res.headers.get("Content-Disposition", "")
        self.assertIn("PIX_Map.pmm", disp)

        zf = zipfile.ZipFile(io.BytesIO(pmm_res.content))
        names = set(zf.namelist())
        self.assertIn("main.xml", names)
        self.assertIn("pm/configuration.xml", names)
        map_names = [n for n in names if n.startswith("pm/maps/") and n.endswith(".xml")]
        self.assertEqual(len(map_names), 1)
        self.assertEqual(len(names), 3)

        main_xml = zf.read("main.xml").decode("utf-8")
        conf_xml = zf.read("pm/configuration.xml").decode("utf-8")
        map_xml = zf.read(map_names[0]).decode("utf-8")

        self.assertIn('PartName="/pm/configuration.xml"', main_xml)
        self.assertIn(f'PartName="/{map_names[0]}"', main_xml)
        ET.fromstring(main_xml)

        conf = ET.fromstring(conf_xml)
        self.assertEqual(conf.tag, "configuration")
        self.assertTrue(any(el.get("name") == "BPMN" for el in conf.findall("notation")))
        bpmn = next(el for el in conf.findall("notation") if el.get("name") == "BPMN")
        bpmn_els = {el.get("name") for el in bpmn.findall("element")}
        for needed in ("horizontalRoad", "emptyPool", "task", "userTask", "serviceTask", "gateway_xor", "start_event_none", "end_event_none"):
            self.assertIn(needed, bpmn_els)
        self.assertIn("Горизонтальный пул", conf_xml)
        self.assertTrue(len(conf.findall("propertyTemplate")) >= 10)

        root = ET.fromstring(map_xml)
        self.assertEqual(root.tag, "Map")
        # Имя нотации должно совпадать с каталогом студии буква в букву:
        # написанное на глаз «bpmn» вместо «BPMN» студия не находит и отвергает
        # весь пакет («Notation element not found (Parameter 'type')»).
        notation_names = {n.get("name") for n in conf.findall("notation")}
        self.assertIn(root.get("notation"), notation_names)
        allowed = {
            e.get("name")
            for n in conf.findall("notation")
            if n.get("name") == root.get("notation")
            for e in n.findall("element")
        }
        used_types = {node.get("type") for node in root.iter("node")}
        self.assertTrue(used_types)
        self.assertEqual(used_types - allowed, set(), "тип фигуры вне нотации студии")
        nodes = root.findall("node")
        types = {n.get("type") for n in nodes}
        self.assertIn("horizontalRoad", types)
        self.assertIn("emptyPool", types)

        road = next(n for n in nodes if n.get("type") == "horizontalRoad")
        self.assertEqual(road.get("label"), "Lane A")
        self.assertEqual(road.get("fill_color"), "var(--bg-accent-road-node)")
        children = list(road)
        child_types = {c.get("type") for c in children}
        self.assertIn("start_event_none", child_types)
        self.assertIn("userTask", child_types)
        self.assertIn("gateway_xor", child_types)
        self.assertIn("serviceTask", child_types)
        self.assertIn("end_event_none", child_types)

        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
        for n in nodes + children:
            self.assertTrue(uuid_re.match(n.get("id") or ""), n.get("id"))

        # Nested coords are relative to the road origin (lane abs = pool 10,20 + lane 0,20 => 10,40).
        # Высоту рамки подгоняют под подпись, поэтому сверяем центр по вертикали.
        task = next(c for c in children if c.get("type") == "userTask")
        self.assertEqual(int(task.get("x")), 120)
        self.assertEqual(
            int(task.get("y")) + int(task.get("height")) / 2, 60 + 60 / 2
        )

        connectors = root.findall("connector")
        self.assertGreaterEqual(len(connectors), 5)
        self.assertTrue(all(c.get("type") == "step" for c in connectors))
        # Якорь связи больше не захардкожен: PIX сама трассирует связь, если
        # sourcePoint/targetPoint не заданы (в эталоне так у 30 связей из 50).
        self.assertTrue(all(c.get("targetPoint") is None for c in connectors))
        self.assertTrue(any(c.get("lineStyle") == "dotted" for c in connectors))
        dotted = next(c for c in connectors if c.get("lineStyle") == "dotted")
        # Маркер конца — из словаря React Flow, на котором построен холст студии:
        # нестандартное значение она молча отбрасывает вместе со связью.
        self.assertEqual(dotted.findtext("MarkerEnd"), "arrow")
        # Стиль линии дублируется дочерним элементом рядом с color/fontSize —
        # как атрибут студия его не читала, и пунктир приходил сплошным.
        self.assertEqual(dotted.findtext("lineStyle"), "dotted")
        solid = next(c for c in connectors if c.get("lineStyle") == "solid")
        self.assertEqual(solid.findtext("MarkerStart"), "line")
        self.assertEqual(solid.findtext("MarkerEnd"), "arrowclosed")
        # Подпись связи PIX читает из Text, а не из label.
        labeled = next((c for c in connectors if c.get("Text") == "Ha"), None)
        self.assertIsNotNone(labeled)
        self.assertTrue(all(c.get("label") is None for c in connectors))
        self.assertTrue(any(c.find("waypoint") is not None for c in connectors))
        for c in connectors:
            self.assertTrue(uuid_re.match(c.get("id") or ""), c.get("id"))
            self.assertTrue(uuid_re.match(c.get("sourceNodeId") or ""))
            self.assertTrue(uuid_re.match(c.get("targetNodeId") or ""))

if __name__ == "__main__":
    unittest.main()
