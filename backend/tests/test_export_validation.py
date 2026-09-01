"""Проверка выгрузок теми же правилами, по которым их читает PIX.

Смысл этих тестов — не «валидатор что-то возвращает», а «валидатор ловит ровно
те дефекты, из-за которых Процессная студия отказывалась открыть файл»:
незнакомый элемент нотации и связь, замкнутую на одну фигуру. Поэтому каждый
случай проверяется на намеренно испорченном пакете, а не только на исправном.
"""
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessEdge, ProcessNode
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.drawio_parser import parse_drawio_xml
from app.services.export_validation import (
    validate_bpmn_xml,
    validate_pmm_package,
    validate_process_exports,
)
from app.services.pmm_exporter import generate_pmm_zip

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

SIMPLE_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="Ofis" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="300" as="geometry" />
    </mxCell>
    <mxCell id="s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="t" value="1. Hujjatlarni tekshirish 15 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="200" y="110" width="160" height="70" as="geometry" />
    </mxCell>
    <mxCell id="gw" value="Kamchilik mavjudmi?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane">
      <mxGeometry x="420" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="e" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="560" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="ds" value="IABS" style="shape=datastore;html=1;" vertex="1" parent="lane">
      <mxGeometry x="230" y="230" width="60" height="40" as="geometry" />
    </mxCell>
    <mxCell id="f1" edge="1" parent="lane" source="s" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="lane" source="t" target="gw"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f3" value="Ha" edge="1" parent="lane" source="gw" target="e"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f4" value="Yo'q" edge="1" parent="lane" source="gw" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="a1" style="dashed=1;html=1;" edge="1" parent="lane" source="ds" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


def _repack(payload: bytes, replace: dict) -> bytes:
    """Пересобирает пакет, подменив содержимое частей: испорченный образец."""
    source = zipfile.ZipFile(io.BytesIO(payload))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name in source.namelist():
            body = source.read(name).decode('utf-8')
            for needle, value in replace.items():
                body = body.replace(needle, value)
            zf.writestr(name, body.encode('utf-8'))
    return out.getvalue()


def _map_part(payload: bytes) -> str:
    package = zipfile.ZipFile(io.BytesIO(payload))
    return next(n for n in package.namelist() if n.startswith('pm/maps/'))


class HealthyExportTests(unittest.TestCase):
    """Исправная карта проходит проверку без единого замечания."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(SIMPLE_MAP, 'simple.drawio')
        cls.pmm = generate_pmm_zip(cls.process)
        cls.bpmn = generate_bpmn_xml(cls.process)

    def test_pmm_package_passes(self):
        check = validate_pmm_package(self.pmm)
        self.assertEqual([p.message for p in check.problems], [])
        self.assertTrue(check.ok)

    def test_bpmn_passes(self):
        check = validate_bpmn_xml(self.bpmn)
        self.assertEqual([p.message for p in check.problems], [])

    def test_both_formats_are_checked(self):
        self.assertEqual(
            sorted(c.format for c in validate_process_exports(self.process)),
            ['bpmn', 'pmm'],
        )


class PmmDefectTests(unittest.TestCase):
    """Каждый дефект, из-за которого студия отвергала пакет, должен ловиться."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(SIMPLE_MAP, 'simple.drawio')
        cls.pmm = generate_pmm_zip(cls.process)

    def _codes(self, payload: bytes) -> set:
        return {p.code for p in validate_pmm_package(payload).problems}

    def test_unknown_notation_element_is_caught(self):
        # Ровно то, на чём студия говорит «Notation element not found
        # (Parameter 'type')»: тип фигуры не объявлен в нотации.
        broken = _repack(self.pmm, {'type="userTask"': 'type="dreamTask"'})
        self.assertIn('pmm_node_type_unknown', self._codes(broken))

    def test_unknown_notation_name_is_caught(self):
        broken = _repack(self.pmm, {'notation="bpmn"': 'notation="BPMN 2.0"'})
        codes = self._codes(broken)
        self.assertIn('pmm_notation_unknown', codes)

    def test_notation_name_is_matched_regardless_of_case(self):
        # Каталог объявляет «BPMN», студия пишет «bpmn» — обе записи означают
        # одну нотацию, и проверка, различающая их, забраковала бы выгрузку
        # самой студии (tests/fixtures/sap.pmm).
        for spelling in ('BPMN', 'bpmn', 'BpMn'):
            repacked = _repack(self.pmm, {'notation="bpmn"': f'notation="{spelling}"'})
            self.assertNotIn('pmm_notation_unknown', self._codes(repacked), spelling)

    def test_self_referencing_connector_is_caught(self):
        # Ровно то, на чём студия говорит «Connector source and target node
        # cannot be the same».
        package = zipfile.ZipFile(io.BytesIO(self.pmm))
        body = package.read(_map_part(self.pmm)).decode('utf-8')
        first = body.split('<connector ')[1]
        source = first.split('sourceNodeId="')[1].split('"')[0]
        target = first.split('targetNodeId="')[1].split('"')[0]
        broken = _repack(self.pmm, {f'targetNodeId="{target}"': f'targetNodeId="{source}"'})
        self.assertIn('pmm_connector_self_loop', self._codes(broken))

    def test_dangling_connector_is_caught(self):
        broken = _repack(self.pmm, {'targetNodeId="': 'targetNodeId="ghost-'})
        self.assertIn('pmm_connector_dangling', self._codes(broken))

    def test_missing_part_is_caught(self):
        source = zipfile.ZipFile(io.BytesIO(self.pmm))
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as zf:
            for name in source.namelist():
                if name == 'pm/configuration.xml':
                    continue
                zf.writestr(name, source.read(name))
        self.assertIn('pmm_part_missing', self._codes(out.getvalue()))

    def test_undeclared_part_is_caught(self):
        source = zipfile.ZipFile(io.BytesIO(self.pmm))
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as zf:
            for name in source.namelist():
                zf.writestr(name, source.read(name))
            zf.writestr('pm/maps/orphan_note.txt', b'note')
        self.assertIn('pmm_part_undeclared', self._codes(out.getvalue()))

    def test_broken_zip_is_caught(self):
        self.assertIn('pmm_not_a_zip', self._codes(b'not a zip at all'))

    def test_zero_sized_node_is_caught(self):
        broken = _repack(self.pmm, {'width="160"': 'width="0"'})
        self.assertIn('pmm_node_size', self._codes(broken))


class BpmnDefectTests(unittest.TestCase):
    """Дефекты, на которых спотыкаются импортёры BPMN (bpmn.io, PIX)."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(SIMPLE_MAP, 'simple.drawio')
        cls.bpmn = generate_bpmn_xml(cls.process)

    def _codes(self, xml: str) -> set:
        return {p.code for p in validate_bpmn_xml(xml).problems}

    def test_self_referencing_flow_is_caught(self):
        source = self.bpmn.split('<bpmn:sequenceFlow ')[1].split('sourceRef="')[1].split('"')[0]
        target = self.bpmn.split('<bpmn:sequenceFlow ')[1].split('targetRef="')[1].split('"')[0]
        broken = self.bpmn.replace(f'targetRef="{target}"', f'targetRef="{source}"', 1)
        self.assertIn('bpmn_flow_self_loop', self._codes(broken))

    def test_dangling_reference_is_caught(self):
        broken = self.bpmn.replace('targetRef="', 'targetRef="ghost-', 1)
        self.assertIn('bpmn_dangling_ref', self._codes(broken))

    def test_flow_into_artifact_is_caught(self):
        artifact = self.bpmn.split('<bpmn:dataStoreReference id="')[1].split('"')[0]
        broken = self.bpmn.replace('targetRef="', f'targetRef="{artifact}" ignored="', 1)
        self.assertIn('bpmn_flow_to_artifact', self._codes(broken))

    def test_broken_xml_is_caught(self):
        self.assertIn('bpmn_broken', self._codes('<bpmn:definitions>'))

    def test_identifier_must_be_an_xml_name(self):
        # BPMN требует xsd:ID: имя XML, а не произвольную строку. Импортёр
        # отвергает файл целиком, а не отдельную фигуру.
        broken = self.bpmn.replace('<bpmn:userTask id="', '<bpmn:userTask id="1 ', 1)
        self.assertIn('bpmn_id_not_ncname', self._codes(broken))


class RealMapExportTests(unittest.TestCase):
    """Обе карты банка обязаны выгружаться без единой ошибки."""

    def _assert_clean(self, filename: str):
        path = os.path.join(FIXTURES, filename)
        if not os.path.exists(path):
            self.skipTest(f'нет файла карты {filename}')
        process = parse_drawio_xml(open(path, encoding='utf-8').read(), filename)
        for check in validate_process_exports(process):
            self.assertEqual(
                [f'{p.code}: {p.message}' for p in check.errors], [],
                f'{filename} → .{check.format}',
            )

    def test_credit_term_change_map(self):
        self._assert_clean('Кредит_шартнома_муддатини_ўзгартириш_—КБ_7.drawio')

    def test_account_opening_map(self):
        self._assert_clean('Asosiy hisob raqam ochish yangi.drawio')


class GeneratedShapeCoverageTests(unittest.TestCase):
    """Любая фигура, которую платформа умеет рисовать, выгружается корректно."""

    def test_every_node_type_survives_both_exports(self):
        from app.models.process import NODE_TYPE_LABELS

        nodes = []
        edges = []
        previous = None
        for index, node_type in enumerate(NODE_TYPE_LABELS):
            if node_type == 'lane':
                continue
            node = ProcessNode(
                id=f'n{index}', name=f'Шаг {index}', type=node_type,
                geometry=Geometry(x=100 + index * 200, y=100, width=120, height=60),
            )
            nodes.append(node)
            if previous is not None and node_type not in ('dataStore', 'dataObject', 'textAnnotation'):
                edges.append(ProcessEdge(
                    id=f'e{index}', sourceId=previous.id, targetId=node.id, points=[],
                ))
                previous = node
            elif previous is None:
                previous = node

        from app.models.process import (
            BusinessProcess, PixRegistrySchema, ProcessPassport, ProcessetMiningMetrics,
        )
        process = BusinessProcess(
            id='p1', name='Все фигуры', fileName='all.drawio',
            passport=ProcessPassport(
                code='PRC-SQB-ALL', name='Все фигуры', version='1.0', status='draft',
                owner='SQB', department='SQB', category='SQB', targetSlaHours=1,
                description='', createdDate='2026-01-01', updatedDate='2026-01-01',
            ),
            nodes=nodes, edges=edges, lanes=[], validation=[],
            registry=PixRegistrySchema(id='r', name='r', code='R', description='', fields=[], records=[]),
            miningMetrics=ProcessetMiningMetrics(
                totalCases=1, conformanceRate=100, avgLeadTimeHours=1,
                targetLeadTimeHours=1, slaBreachRate=0, reworkRate=0,
                potentialRpaSavingsUzs=0, deviations=[],
            ),
        )
        for check in validate_process_exports(process):
            self.assertEqual(
                [f'{p.code}: {p.message}' for p in check.errors], [], f'.{check.format}',
            )


if __name__ == '__main__':
    unittest.main()
