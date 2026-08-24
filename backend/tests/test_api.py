import unittest
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
        # Absolute = local + lane (0,20) + pool (10,20) => (50, 80)
        self.assertEqual(by_id["task_a"]["geometry"]["x"], 50)
        self.assertEqual(by_id["task_a"]["geometry"]["y"], 80)
        self.assertEqual(by_id["task_a"]["geometry"]["width"], 120)
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

if __name__ == "__main__":
    unittest.main()
