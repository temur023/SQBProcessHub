"""Строгий профиль PIX: что он ловит сверх обычной проверки BPMN.

Главные тесты здесь — не «валидатор находит ошибку», а «валидатор находит её
там, где обычная проверка стандарта молчит». Каждый такой случай помечен
``test_bpmnio_passes_but_pix_rejects_*``: файл валиден по BPMN 2.0, его примет
bpmn.io — и всё равно он не должен уехать в студию.

Все случаи строятся от исправного файла, у которого портится ровно одно место:
так видно, что срабатывает именно проверяемое правило.
"""
import io
import os
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.drawio_parser import parse_drawio_xml
from app.services.export_validation import validate_bpmn_xml, validate_pmm_package
from app.services.pix_spec_checker import (
    validate_bpmn_for_pix,
    validate_pmm_for_pix,
    validate_for_pix,
    validate_xpdl,
)
from app.services.pmm_exporter import generate_pmm_zip
from app.services.xpdl_exporter import generate_xpdl

client = TestClient(app)

SOURCE_MAP = """<mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Фронт-офис" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="300" as="geometry"/>
  </mxCell>
  <mxCell id="s" value="Заявка принята" style="ellipse;html=1;" vertex="1" parent="lane">
    <mxGeometry x="60" y="120" width="50" height="50" as="geometry"/>
  </mxCell>
  <mxCell id="t" value="Проверка документов 15 min" style="rounded=1;html=1;" vertex="1" parent="lane">
    <mxGeometry x="200" y="110" width="160" height="70" as="geometry"/>
  </mxCell>
  <mxCell id="g" value="Решение принято?" style="rhombus;html=1;" vertex="1" parent="lane">
    <mxGeometry x="430" y="115" width="60" height="60" as="geometry"/>
  </mxCell>
  <mxCell id="e" value="Справка выдана" style="ellipse;html=1;strokeWidth=3;" vertex="1" parent="lane">
    <mxGeometry x="600" y="120" width="50" height="50" as="geometry"/>
  </mxCell>
  <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="s" target="t">
    <mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="t" target="g">
    <mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f3" value="Да" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="g" target="e">
    <mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel>"""


@pytest.fixture(scope='module')
def process():
    return parse_drawio_xml(SOURCE_MAP, 'reference.drawio')


@pytest.fixture(scope='module')
def bpmn(process):
    return generate_bpmn_xml(process)


def codes(check):
    return {p.code for p in check.errors}


def repack(payload: bytes, part: str, content: str) -> bytes:
    """Пересобирает .pmm, заменив одну часть, — так портится ровно одно место."""
    source = zipfile.ZipFile(io.BytesIO(payload))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as out:
        for item in source.namelist():
            out.writestr(item, content if item == part else source.read(item))
    return buffer.getvalue()


def map_part(payload: bytes):
    package = zipfile.ZipFile(io.BytesIO(payload))
    name = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return name, package.read(name).decode('utf-8')


#: Пакет, выгруженный самой PIX Процессной студией: карта «sap» на 66 фигур и
#: 50 связей. Лежит в репозитории как эталон формата — по нему сверяются и
#: правила профиля, и то, что пишет наш экспорт.
STUDIO_PACKAGE = (
    Path(__file__).resolve().parent / 'fixtures' / 'sap.pmm'
).read_bytes()


# ── Опора: выгрузку самой студии профиль обязан принимать ──────────────────

def test_studio_own_package_passes_pix_profile():
    """Карта, сделанная и сохранённая в PIX, должна проходить без замечаний.

    Это единственная проверка здесь, где эталон не наш: ``sap.pmm`` выгружен
    самой Процессной студией. Правило, которое бракует такой файл, ошибочно по
    определению — оно останавливало бы выгрузку карты, которую студия открывает.
    Так снялись требование точного регистра имени нотации (студия пишет
    ``notation="bpmn"``, каталог объявляет ``BPMN``) и требование целых
    координат ``waypoint`` (студия пишет ``x="1704.0000385955093"``).
    """
    check = validate_pmm_for_pix(STUDIO_PACKAGE)
    assert check.errors == [], [f'{p.code}: {p.message}' for p in check.errors]
    assert check.warnings == [], [f'{p.code}: {p.message}' for p in check.warnings]
    assert check.ok


def test_studio_own_package_passes_the_standard_check_too():
    """Тот же файл — и обычной проверкой .pmm, не только строгим профилем."""
    check = validate_pmm_package(STUDIO_PACKAGE)
    assert check.errors == [], [f'{p.code}: {p.message}' for p in check.errors]


# ── Опора: наша штатная выгрузка проходит строгий профиль ───────────────────

def test_reference_export_passes_pix_profile(process):
    for check in validate_for_pix(process):
        assert check.ok, (check.format, [p.message for p in check.errors])


# ── Расхождения: bpmn.io принимает, PIX — нет ───────────────────────────────

def test_bpmnio_passes_but_pix_rejects_foreign_namespace_prefix(bpmn):
    """Стандарт разрешает любой префикс; профиль выгрузки — только «bpmn»."""
    renamed = (bpmn.replace('xmlns:bpmn=', 'xmlns:b=')
                   .replace('<bpmn:', '<b:').replace('</bpmn:', '</b:'))
    assert validate_bpmn_xml(renamed).ok, 'по стандарту файл остаётся валидным'
    assert 'pix_ns_prefix_missing' in codes(validate_bpmn_for_pix(renamed))


def test_bpmnio_passes_but_pix_rejects_fractional_bounds(bpmn):
    """Дробная координата — обычный результат правки мышью в draw.io."""
    broken = bpmn.replace('<dc:Bounds x="0"', '<dc:Bounds x="0.5000000000002"', 1)
    assert validate_bpmn_xml(broken).ok
    assert 'pix_bounds_not_integer' in codes(validate_bpmn_for_pix(broken))


def test_bpmnio_passes_but_pix_rejects_fractional_waypoint(bpmn):
    marker = '<di:waypoint x="'
    at = bpmn.index(marker) + len(marker)
    end = bpmn.index('"', at)
    broken = bpmn[:at] + bpmn[at:end] + '.75' + bpmn[end:]
    assert validate_bpmn_xml(broken).ok
    assert 'pix_waypoint_not_integer' in codes(validate_bpmn_for_pix(broken))


def test_bpmnio_passes_but_pix_rejects_missing_process_type(bpmn):
    """processType по стандарту необязателен, профиль PIX требует явно."""
    broken = bpmn.replace(' processType="Private"', '')
    assert validate_bpmn_xml(broken).ok
    assert 'pix_process_type' in codes(validate_bpmn_for_pix(broken))


def test_bpmnio_passes_but_pix_rejects_missing_is_executable(bpmn):
    broken = bpmn.replace(' isExecutable="true"', '')
    assert validate_bpmn_xml(broken).ok
    assert 'pix_process_is_executable' in codes(validate_bpmn_for_pix(broken))


def test_bpmnio_passes_but_pix_rejects_diagram_without_id(bpmn):
    start = bpmn.index('<bpmndi:BPMNDiagram id="')
    end = bpmn.index('"', start + len('<bpmndi:BPMNDiagram id="')) + 1
    broken = bpmn[:start] + '<bpmndi:BPMNDiagram' + bpmn[end:]
    assert validate_bpmn_xml(broken).ok
    assert 'pix_diagram_id' in codes(validate_bpmn_for_pix(broken))


# ── Прочие правила профиля BPMN ─────────────────────────────────────────────

def test_wrong_process_type_value_is_rejected(bpmn):
    broken = bpmn.replace('processType="Private"', 'processType="Executable"')
    assert 'pix_process_type' in codes(validate_bpmn_for_pix(broken))


def test_empty_target_namespace_is_rejected(bpmn):
    broken = bpmn.replace('targetNamespace="http://bpmn.io/schema/bpmn"',
                          'targetNamespace=""')
    assert 'pix_target_namespace' in codes(validate_bpmn_for_pix(broken))


def test_node_without_a_shape_is_rejected(bpmn):
    start = bpmn.index('<bpmndi:BPMNShape id="s_di"')
    end = bpmn.index('</bpmndi:BPMNShape>', start) + len('</bpmndi:BPMNShape>')
    assert 'pix_node_not_drawn' in codes(validate_bpmn_for_pix(bpmn[:start] + bpmn[end:]))


def test_shape_drawn_twice_is_rejected(bpmn):
    start = bpmn.index('<bpmndi:BPMNShape id="t_di"')
    end = bpmn.index('</bpmndi:BPMNShape>', start) + len('</bpmndi:BPMNShape>')
    twice = bpmn[:end] + bpmn[start:end].replace('id="t_di"', 'id="t_di2"') + bpmn[end:]
    assert 'pix_di_duplicate' in codes(validate_bpmn_for_pix(twice))


# ── Профиль .pmm ────────────────────────────────────────────────────────────

def test_reference_package_passes_pix_profile(process):
    assert validate_pmm_for_pix(generate_pmm_zip(process)).ok


def test_type_outside_the_studio_catalogue_is_rejected(process):
    """То самое «Notation element not found (Parameter 'type')»."""
    payload = generate_pmm_zip(process)
    name, xml = map_part(payload)
    broken = repack(payload, name, xml.replace('type="task"', 'type="megaTask"', 1))
    assert 'pix_pmm_type_unknown' in codes(validate_pmm_for_pix(broken))


def test_non_uuid_identifier_is_rejected(process):
    payload = generate_pmm_zip(process)
    name, xml = map_part(payload)
    first = xml.index('id="') + len('id="')
    end = xml.index('"', first)
    broken = repack(payload, name, xml[:first] + 'node-1' + xml[end:])
    assert 'pix_pmm_id_not_uuid' in codes(validate_pmm_for_pix(broken))


def test_fractional_geometry_is_rejected(process):
    import re as _re

    payload = generate_pmm_zip(process)
    name, xml = map_part(payload)
    # Портим первую же координату фигуры, какой бы она ни была: привязываться к
    # конкретному числу нельзя — раскладка карты меняется вместе с вёрсткой.
    broken_xml = _re.sub(r'x="(\d+)"', lambda m: f'x="{m.group(1)}.5"', xml, count=1)
    assert broken_xml != xml, 'в карте не нашлось ни одной координаты x'
    assert 'pix_pmm_geometry_not_integer' in codes(
        validate_pmm_for_pix(repack(payload, name, broken_xml)))


def test_child_outside_its_lane_is_rejected(process):
    """Шаг, выехавший за границу дорожки, в студии попадает в чужую дорожку."""
    payload = generate_pmm_zip(process)
    name, xml = map_part(payload)
    # Двигаем первый вложенный шаг далеко вправо от дорожки.
    at = xml.index('type="task"')
    head = xml.rindex('<node', 0, at)
    tail = xml.index('/>', at)
    moved = xml[:head] + xml[head:tail].replace('x="', 'x="99000', 1) + xml[tail:]
    assert 'pix_pmm_child_outside' in codes(validate_pmm_for_pix(repack(payload, name, moved)))


def test_connector_label_attribute_is_rejected(process):
    """Студия читает подпись связи из Text; label она игнорирует."""
    payload = generate_pmm_zip(process)
    name, xml = map_part(payload)
    broken = xml.replace('<connector ', '<connector label="Да" ', 1)
    assert 'pix_pmm_connector_label' in codes(
        validate_pmm_for_pix(repack(payload, name, broken)))


def test_self_looped_connector_is_rejected(process):
    """«Connector source and target node cannot be the same»."""
    payload = generate_pmm_zip(process)
    name, xml = map_part(payload)
    at = xml.index('<connector ')
    end = xml.index('>', at)
    piece = xml[at:end]
    src = piece.split('sourceNodeId="')[1].split('"')[0]
    tgt = piece.split('targetNodeId="')[1].split('"')[0]
    broken = xml[:at] + piece.replace(tgt, src) + xml[end:]
    assert 'pix_pmm_self_loop' in codes(validate_pmm_for_pix(repack(payload, name, broken)))


def test_package_without_configuration_is_rejected(process):
    payload = generate_pmm_zip(process)
    source = zipfile.ZipFile(io.BytesIO(payload))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as out:
        for item in source.namelist():
            if item != 'pm/configuration.xml':
                out.writestr(item, source.read(item))
    assert 'pix_pmm_no_config' in codes(validate_pmm_for_pix(buffer.getvalue()))


# ── Запасной формат XPDL ────────────────────────────────────────────────────

def test_xpdl_export_is_valid(process):
    assert validate_xpdl(generate_xpdl(process)).ok


def test_xpdl_carries_lanes_activities_and_transitions(process):
    xml = generate_xpdl(process)
    assert '<xpdl:Lane ' in xml and 'Фронт-офис' in xml
    assert xml.count('<xpdl:Activity ') == len(
        [n for n in process.nodes if n.type != 'lane'])
    assert '<xpdl:Transition ' in xml
    assert '<xpdl:Route GatewayType="Exclusive"' in xml
    assert '<xpdl:StartEvent Trigger="None"' in xml


def test_xpdl_keeps_step_duration(process):
    """15 минут с карты должны доехать штатным полем, а не примечанием."""
    assert '<xpdl:WorkingTime>900</xpdl:WorkingTime>' in generate_xpdl(process)


def test_xpdl_with_dangling_transition_is_rejected(process):
    broken = generate_xpdl(process).replace('To="Act_e"', 'To="Act_missing"')
    assert 'xpdl_transition_end' in codes(validate_xpdl(broken))


def test_xpdl_with_wrong_version_is_rejected(process):
    broken = generate_xpdl(process).replace('>2.2<', '>1.0<')
    assert 'xpdl_version' in codes(validate_xpdl(broken))


def test_xpdl_with_foreign_namespace_is_rejected(process):
    broken = generate_xpdl(process).replace(
        'xmlns:xpdl="http://www.wfmc.org/2009/XPDL2.2"',
        'xmlns:xpdl="http://example.com/xpdl"')
    assert 'xpdl_namespace' in codes(validate_xpdl(broken))


# ── API ─────────────────────────────────────────────────────────────────────

def test_api_serves_xpdl_and_reports_its_check():
    created = client.post('/api/v1/import/xml',
                          json={'xml': SOURCE_MAP, 'fileName': 'reference.drawio'})
    assert created.status_code == 200
    process_id = created.json()['id']

    response = client.get(f'/api/v1/import/{process_id}/export/xpdl')
    assert response.status_code == 200
    assert response.headers['X-Export-Check'] == 'ok'
    assert b'<xpdl:Package' in response.content


def test_api_reports_pix_profile_alongside_the_standard_one():
    created = client.post('/api/v1/import/xml',
                          json={'xml': SOURCE_MAP, 'fileName': 'reference.drawio'})
    process_id = created.json()['id']
    for kind in ('bpmn', 'pmm'):
        response = client.get(f'/api/v1/import/{process_id}/export/{kind}')
        assert response.status_code == 200
        assert response.headers['X-Pix-Check'] == 'ok', kind
        assert response.headers['X-Pix-Check-Errors'] == '0', kind
