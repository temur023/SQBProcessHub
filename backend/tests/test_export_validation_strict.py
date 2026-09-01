"""Проверки BPMN, добавленные после разбора отказов PIX.

Каждый случай — дефект, на котором импортёр Процессной студии (и bpmn.io)
спотыкается, а прежний валидатор молчал: файл уезжал к сотруднику «валидным»
и разворачивался уже в студии, без указания фигуры.

Тесты идут от исправного файла и портят в нём ровно одно место: так видно,
что срабатывает именно проверяемое правило, а не соседнее.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.export_validation import validate_bpmn_xml

SOUND = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
  id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collab_1">
    <bpmn:participant id="Part_1" name="Банк" processRef="Process_1" />
  </bpmn:collaboration>
  <bpmn:process id="Process_1" name="Процесс" isExecutable="true">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_1" name="Фронт-офис">
        <bpmn:flowNodeRef>Start_1</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_1</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="Start_1" name="Заявка" />
    <bpmn:userTask id="Task_1" name="Проверка" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_1">
    <bpmndi:BPMNPlane id="Plane_1" bpmnElement="Collab_1">
      <bpmndi:BPMNShape id="Part_1_di" bpmnElement="Part_1" isHorizontal="true">
        <dc:Bounds x="0" y="0" width="800" height="200" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_1_di" bpmnElement="Lane_1" isHorizontal="true">
        <dc:Bounds x="30" y="0" width="770" height="200" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Start_1_di" bpmnElement="Start_1">
        <dc:Bounds x="60" y="80" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1">
        <dc:Bounds x="160" y="60" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="96" y="98" />
        <di:waypoint x="160" y="98" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
'''


def codes(xml):
    return {p.code for p in validate_bpmn_xml(xml).errors}


def test_reference_file_is_accepted():
    """Опора всех остальных случаев: исправный файл проходит без единой ошибки."""
    check = validate_bpmn_xml(SOUND)
    assert check.ok, [p.message for p in check.errors]


# ── Пространства имён ───────────────────────────────────────────────────────

def test_model_namespace_is_required():
    broken = SOUND.replace(
        'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        'xmlns:bpmn="http://example.com/not-bpmn"')
    assert 'bpmn_namespace' in codes(broken)


def test_target_namespace_is_required():
    assert 'bpmn_target_namespace' in codes(
        SOUND.replace(' targetNamespace="http://bpmn.io/schema/bpmn"', ''))


# ── Связывание узлов ────────────────────────────────────────────────────────

def test_sequence_flow_between_pools_is_rejected():
    """Между пулами ходит messageFlow; sequenceFlow импортёр не примет."""
    two_pools = SOUND.replace(
        '  <bpmndi:BPMNDiagram',
        '  <bpmn:process id="Process_2" name="Клиент" isExecutable="false">\n'
        '    <bpmn:userTask id="Task_2" name="Подписать" />\n'
        '  </bpmn:process>\n'
        '  <bpmndi:BPMNDiagram').replace(
        '    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />',
        '    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />\n'
        '    <bpmn:sequenceFlow id="Flow_X" sourceRef="Task_1" targetRef="Task_2" />')
    assert 'bpmn_flow_crosses_pool' in codes(two_pools)


def test_lane_listing_a_node_of_another_process_is_rejected():
    foreign = SOUND.replace(
        '  <bpmndi:BPMNDiagram',
        '  <bpmn:process id="Process_2" isExecutable="false">\n'
        '    <bpmn:userTask id="Task_2" name="Чужой" />\n'
        '  </bpmn:process>\n'
        '  <bpmndi:BPMNDiagram').replace(
        '        <bpmn:flowNodeRef>Task_1</bpmn:flowNodeRef>',
        '        <bpmn:flowNodeRef>Task_1</bpmn:flowNodeRef>\n'
        '        <bpmn:flowNodeRef>Task_2</bpmn:flowNodeRef>')
    assert 'bpmn_lane_foreign_node' in codes(foreign)


def test_participant_must_point_at_a_real_process():
    assert 'bpmn_participant_process' in codes(
        SOUND.replace('processRef="Process_1"', 'processRef="Process_MISSING"'))


def test_boundary_event_without_host_is_rejected():
    detached = SOUND.replace(
        '    <bpmn:userTask id="Task_1" name="Проверка" />',
        '    <bpmn:userTask id="Task_1" name="Проверка" />\n'
        '    <bpmn:boundaryEvent id="Bnd_1" name="Таймер" />')
    assert 'bpmn_boundary_detached' in codes(detached)


# ── Блок визуализации BPMNDI ────────────────────────────────────────────────

def test_diagram_block_is_required():
    start = SOUND.index('  <bpmndi:BPMNDiagram')
    end = SOUND.index('</bpmndi:BPMNDiagram>') + len('</bpmndi:BPMNDiagram>')
    assert 'bpmn_no_diagram' in codes(SOUND[:start] + SOUND[end:])


def test_plane_must_anchor_on_an_existing_element():
    assert 'bpmn_plane_anchor' in codes(
        SOUND.replace('bpmnElement="Collab_1"', 'bpmnElement="Nothing_1"'))


def test_shape_without_bounds_is_rejected():
    assert 'bpmn_shape_no_bounds' in codes(
        SOUND.replace('<dc:Bounds x="160" y="60" width="120" height="80" />', ''))


def test_zero_sized_shape_is_rejected():
    assert 'bpmn_shape_zero_size' in codes(
        SOUND.replace('width="120" height="80"', 'width="0" height="0"'))


def test_edge_with_a_single_waypoint_is_rejected():
    assert 'bpmn_edge_waypoints' in codes(
        SOUND.replace('        <di:waypoint x="160" y="98" />', ''))


def test_element_drawn_twice_is_rejected():
    """bpmn.io на втором BPMNShape того же элемента бросает исключение."""
    doubled = SOUND.replace(
        '      <bpmndi:BPMNEdge',
        '      <bpmndi:BPMNShape id="Task_1_di2" bpmnElement="Task_1">\n'
        '        <dc:Bounds x="400" y="60" width="120" height="80" />\n'
        '      </bpmndi:BPMNShape>\n'
        '      <bpmndi:BPMNEdge')
    assert 'bpmn_di_duplicate' in codes(doubled)


# ── Мусор в подписях ────────────────────────────────────────────────────────

def test_control_character_is_reported_before_the_parser_chokes():
    assert 'bpmn_control_chars' in codes(SOUND.replace('Проверка', 'Прове\x01рка'))
