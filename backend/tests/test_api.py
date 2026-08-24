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
        self.assertIn("Потенциал PIX RPA", reg_res.text)

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
