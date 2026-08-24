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

if __name__ == "__main__":
    unittest.main()
