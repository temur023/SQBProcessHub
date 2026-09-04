"""Генератор изоляционных проб для PIX Процессной студии.

Зачем. Строгий профиль в ``pix_spec_checker`` собран из эталонного каталога
студии, её же сообщений об отказах и требований команды. Часть правил — гипотезы:
проверить их, не имея исходников студии, можно только опытом. Этот скрипт готовит
опыт — набор файлов, каждый из которых отличается от заведомо исправного ровно
одним отклонением. Импортируя их по одному, аналитик получает ответ «студия
принимает или нет» отдельно по каждому правилу.

ЧТО ДЕЛАЕТ ОПЫТ ОСМЫСЛЕННЫМ

* **Контрольный файл.** ``test_00_baseline`` не содержит ни одного отклонения.
  Если студия отвергнет и его, опыт можно не продолжать: дело не в правилах, а в
  самой пробе (не та версия, не тот способ импорта, пустой каталог нотаций).
  Без контроля любой результат нечитаем.
* **Ровно одно отличие.** Каждая проба сверяется с контролем построчно, и число
  изменённых мест печатается в отчёте. Файл, отличающийся в двух местах, не
  отвечает ни на один вопрос.
* **Отклонение законно по стандарту.** Смысл пробы — узнать, где студия строже
  спецификации. Поэтому для каждого файла заранее посчитано, принимает ли его
  обычная проверка BPMN 2.0. Если не принимает, проба всё равно полезна, но
  проверяет уже не «PIX строже стандарта», а «PIX ловит нарушение стандарта» —
  и это помечено в манифесте отдельно.

Запуск::

    python generate_pix_tests.py                 # в каталог ./pix_test_suite
    python generate_pix_tests.py --out D:\\probe  # в указанный каталог
"""
from __future__ import annotations

import argparse
import difflib
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.export_validation import (  # noqa: E402
    validate_bpmn_xml,
    validate_pmm_package,
)
from app.services.pix_spec_checker import (  # noqa: E402
    validate_bpmn_for_pix,
    validate_pmm_for_pix,
)

# ── Контрольный BPMN ────────────────────────────────────────────────────────
# Минимальный, но полноценный: пул с дорожкой, три узла потока, два перехода и
# полный блок BPMNDI. Меньше нельзя — без пула и дорожки не проверить то, ради
# чего студия и нужна; больше не нужно — лишние фигуры только добавят поводов
# для отказа и запутают вывод.
BASELINE_BPMN = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  id="Definitions_probe"
  targetNamespace="http://bpmn.io/schema/bpmn"
  exporter="SQB Process Hub PIX probe"
  exporterVersion="1.0">

  <bpmn:collaboration id="Collaboration_probe">
    <bpmn:participant id="Participant_probe" name="Банк" processRef="Process_probe" />
  </bpmn:collaboration>

  <bpmn:process id="Process_probe" name="Проба импорта" processType="Private" isExecutable="true">
    <bpmn:laneSet id="LaneSet_probe">
      <bpmn:lane id="Lane_front" name="Фронт-офис">
        <bpmn:flowNodeRef>Start_1</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_1</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End_1</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="Start_1" name="Заявка принята">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_1" name="Проверка документов">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_1" name="Справка выдана">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="End_1" />
  </bpmn:process>

  <bpmndi:BPMNDiagram id="Diagram_probe">
    <bpmndi:BPMNPlane id="Plane_probe" bpmnElement="Collaboration_probe">
      <bpmndi:BPMNShape id="Participant_probe_di" bpmnElement="Participant_probe" isHorizontal="true">
        <dc:Bounds x="120" y="80" width="700" height="200" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_front_di" bpmnElement="Lane_front" isHorizontal="true">
        <dc:Bounds x="150" y="80" width="670" height="200" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Start_1_di" bpmnElement="Start_1">
        <dc:Bounds x="212" y="162" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1">
        <dc:Bounds x="320" y="140" width="160" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_1_di" bpmnElement="End_1">
        <dc:Bounds x="580" y="162" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="248" y="180" />
        <di:waypoint x="320" y="180" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint x="480" y="180" />
        <di:waypoint x="580" y="180" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
'''


# ── Отклонения BPMN: каждое трогает ровно одно место ────────────────────────

def _deviate_custom_namespace(xml: str) -> str:
    """Префикс ``mybpmn:`` вместо ``bpmn:``.

    По спецификации префикс произволен: значение несёт URI. Гипотеза профиля —
    что студия читает файл по имени тега с префиксом и чужой не узнаёт.
    """
    return (xml.replace('xmlns:bpmn=', 'xmlns:mybpmn=')
               .replace('<bpmn:', '<mybpmn:')
               .replace('</bpmn:', '</mybpmn:'))


def _deviate_float_coordinates(xml: str) -> str:
    """Дробная координата в ``dc:Bounds`` — обычный след правки мышью."""
    return xml.replace('<dc:Bounds x="320" y="140" width="160" height="80" />',
                       '<dc:Bounds x="320.7500000000002" y="140.25" '
                       'width="160.5" height="80.125" />')


def _deviate_missing_process_type(xml: str) -> str:
    return xml.replace(' processType="Private"', '')


def _deviate_missing_is_executable(xml: str) -> str:
    return xml.replace(' isExecutable="true"', '')


def _deviate_unlinked_shape(xml: str) -> str:
    """``BPMNShape`` указывает на фигуру, которой в модели нет."""
    return xml.replace('bpmnElement="Task_1">', 'bpmnElement="Task_MISSING">')


#: Проба: имя файла, о чём она, как портится контроль, какое правило профиля
#: обязана нарушить, что именно выясняет и сколько мест в тексте затрагивает.
#:
#: Последнее поле нужно из-за переименования префикса: логически это ОДНО
#: отклонение, но текстуально оно задевает каждый тег в файле. Считать такую
#: пробу неизолированной неверно, а молча пропускать проверку локальности для
#: всех — значит потерять её там, где она работает. Поэтому ожидание объявлено
#: для каждой пробы явно, и самопроверка сверяется именно с ним.
BPMN_CASES: List[Tuple[str, str, Callable[[str], str], str, str, Optional[int]]] = [
    (
        'test_00_baseline.bpmn',
        'Контроль: отклонений нет',
        lambda xml: xml,
        '—',
        'Должен импортироваться. Если нет — проба непригодна, дальше идти нет смысла.',
        0,
    ),
    (
        'test_01_custom_namespace.bpmn',
        'Префикс mybpmn: вместо bpmn:',
        _deviate_custom_namespace,
        'pix_ns_prefix_missing',
        'Проверяет, читает ли студия файл по URI (как велит стандарт) '
        'или по префиксу. Отклонение одно, но по природе своей затрагивает '
        'весь файл: префикс стоит в каждом теге.',
        None,
    ),
    (
        'test_02_float_coordinates.bpmn',
        'Дробные координаты в dc:Bounds',
        _deviate_float_coordinates,
        'pix_bounds_not_integer',
        'Проверяет, требует ли десериализатор int вместо double.',
        1,
    ),
    (
        'test_03_missing_process_type.bpmn',
        'Нет атрибута processType',
        _deviate_missing_process_type,
        'pix_process_type',
        'По стандарту атрибут необязателен (по умолчанию None). '
        'Проверяет, требует ли студия его явно.',
        1,
    ),
    (
        'test_04_missing_is_executable.bpmn',
        'Нет атрибута isExecutable',
        _deviate_missing_is_executable,
        'pix_process_is_executable',
        'По стандарту атрибут необязателен (по умолчанию false). '
        'Проверяет, требует ли студия его явно.',
        1,
    ),
    (
        'test_05_unlinked_shape.bpmn',
        'BPMNShape ссылается на несуществующий bpmnElement',
        _deviate_unlinked_shape,
        'pix_di_dangling',
        'Нарушает и стандарт. Проверяет, падает ли студия '
        '(NullReferenceException) или молча пропускает фигуру.',
        1,
    ),
]


# ── Контрольный PMM и отклонения ────────────────────────────────────────────

def _baseline_process():
    """Тот же процесс, что в контрольном BPMN, но в модели платформы."""
    from app.models.process import (
        BusinessProcess, Geometry, PixRegistrySchema, ProcessEdge,
        ProcessNode, ProcessPassport, ProcessetMiningMetrics,
    )

    lane = ProcessNode(
        id='Lane_front', name='Фронт-офис', type='lane',
        geometry=Geometry(x=120, y=80, width=700, height=200),
        style='swimlane;horizontal=0;',
    )
    nodes = [
        lane,
        ProcessNode(id='Start_1', name='Заявка принята', type='startEvent',
                    laneId='Lane_front', laneName='Фронт-офис',
                    geometry=Geometry(x=212, y=162, width=36, height=36)),
        ProcessNode(id='Task_1', name='Проверка документов', type='userTask',
                    laneId='Lane_front', laneName='Фронт-офис', slaMinutes=15, slaMeasured=True,
                    geometry=Geometry(x=320, y=140, width=160, height=80)),
        ProcessNode(id='End_1', name='Справка выдана', type='endEvent',
                    laneId='Lane_front', laneName='Фронт-офис',
                    geometry=Geometry(x=580, y=162, width=36, height=36)),
    ]
    return BusinessProcess(
        id='proc_probe', name='Проба импорта', fileName='probe.drawio',
        passport=ProcessPassport(
            code='PRC-PROBE-001', name='Проба импорта', owner='—',
            description='Контрольная карта для проверки импорта в PIX',
            version='1.0', targetSlaHours=1,
            createdDate='2026-01-01', updatedDate='2026-01-01',
        ),
        nodes=nodes, lanes=[lane],
        edges=[
            ProcessEdge(id='Flow_1', sourceId='Start_1', targetId='Task_1'),
            ProcessEdge(id='Flow_2', sourceId='Task_1', targetId='End_1'),
        ],
        registry=PixRegistrySchema(id='reg', code='REG_PROBE', name='Проба',
                                   description='—', fields=[], records=[]),
        miningMetrics=ProcessetMiningMetrics(),
        validation=[],
    )


def _repack(payload: bytes, part: str, content: str) -> bytes:
    """Пересобирает пакет, заменив одну часть."""
    source = zipfile.ZipFile(io.BytesIO(payload))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as out:
        for item in source.namelist():
            out.writestr(item, content if item == part else source.read(item))
    return buffer.getvalue()


def _map_part(payload: bytes) -> Tuple[str, str]:
    package = zipfile.ZipFile(io.BytesIO(payload))
    name = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return name, package.read(name).decode('utf-8')


def _deviate_pmm_out_of_bounds(xml: str) -> str:
    """Шаг уезжает вправо за границу своей дорожки.

    Координаты вложенной фигуры в ``.pmm`` отсчитываются от дорожки. Гипотеза:
    студия либо обрежет фигуру, либо отнесёт её к другой дорожке, либо откажет.
    """
    match = re.search(r'(<node type="task"[^>]*?\sx=")(-?\d+)(")', xml)
    if not match:
        raise RuntimeError('в контрольной карте нет узла task с координатой x')
    return xml[:match.start(2)] + '99000' + xml[match.end(2):]


def _deviate_pmm_unknown_type(xml: str) -> str:
    """Тип фигуры, которого нет в каталоге нотаций студии.

    Ожидаемое сообщение — то самое «Notation element not found (Parameter
    'type')». Проба подтверждает, что отказ даёт именно тип, а не что-то рядом.
    """
    return xml.replace('type="task"', 'type="megaUserTask"', 1)


def _deviate_pmm_label_placement_top(map_xml: str) -> str:
    """Ставит подпись шлюза над ромбом вместо «слева».

    Эталон студии (``tests/fixtures/sap.pmm``) знает у ``labelPlacement``
    ровно одно значение — ``Left``, и платформа пишет только его. На плотной
    карте банка подпись слева ложится на предыдущую фигуру: у двух соседних
    ромбов вопросы наезжают друг на друга. Значение ``Top`` напрашивается, но
    ничем не подтверждено — а неизвестное значение перечисления студия может и
    не пережить. Проба выясняет это ценой одного импорта.

    Атрибут ставится задаче, а не шлюзу: вопрос к студии — про само значение,
    и ответ от типа фигуры не зависит. Держать в контроле лишний ромб только
    ради этой пробы значило бы менять все остальные.
    """
    marker = '<node type="task"'
    start = map_xml.index(marker)
    return (map_xml[:start + len(marker)] + ' labelPlacement="Top"'
            + map_xml[start + len(marker):])


def _deviate_pmm_node_property(map_xml: str) -> str:
    """Ставит шагу длительность больше суток — в форме ``d.hh:mm:ss``.

    Имена свойств выяснены и закрыты: панель строит их по каталогу студии, и
    платформа пишет ``vremya_protsessa`` / ``system_process_time`` /
    ``vremya_ozhidaniya``. Осталось одно неподтверждённое правило — как
    записывать длительность от суток и больше.

    Вывод сделан из .NET, а не из наблюдения: ``TimeSpan.Parse("24:00:00")``
    падает, часы обязаны лежать в 0..23, а сутки выносятся отдельным полем
    через точку. Отсюда «1.00:00:00» вместо «24:00:00». В сегодняшних картах
    банка максимум 960 минут, так что вживую правило ещё ни разу не
    проверялось, — а первая же карта с двухдневным согласованием его заденет.
    """
    return map_xml.replace('value="00:15:00"', 'value="1.12:00:00"', 1)


PMM_CASES: List[Tuple[str, str, Optional[Callable[[str], str]], str, str, Optional[int]]] = [
    (
        'test_00_baseline.pmm',
        'Контроль: отклонений нет',
        None,
        '—',
        'Должен импортироваться. Если нет — проба непригодна.',
        0,
    ),
    (
        'test_06_pmm_out_of_bounds.pmm',
        'Координаты шага выходят за пределы дорожки',
        _deviate_pmm_out_of_bounds,
        'pix_pmm_child_outside',
        'Проверяет, обрежет ли студия фигуру, перенесёт в другую дорожку '
        'или откажется открыть карту.',
        1,
    ),
    (
        'test_08_pmm_label_placement_top.pmm',
        'Подпись шлюза сверху (labelPlacement="Top")',
        _deviate_pmm_label_placement_top,
        '?',
        'Примет ли студия значение, которого нет в её эталонной выгрузке? '
        'Если да — подписи шлюзов можно расставлять по свободному месту, и '
        'вопросы перестанут наезжать друг на друга.',
        1,
    ),
    (
        'test_09_pmm_node_property.pmm',
        'Длительность больше суток: «1.12:00:00»',
        _deviate_pmm_node_property,
        '?',
        'Примет ли студия сутки отдельным полем через точку? В контроле время '
        'обычное («00:15:00»), здесь — полтора суток. Если файл откроется и в '
        'панели свойств шага стоит 1 д 12 ч, правило верно; если нет — длинные '
        'согласования надо записывать иначе.',
        1,
    ),
    (
        'test_07_pmm_unknown_type.pmm',
        'Тип элемента отсутствует в pix_configuration.xml',
        _deviate_pmm_unknown_type,
        'pix_pmm_type_unknown',
        'Ожидается «Notation element not found (Parameter \'type\')».',
        1,
    ),
]


# ── Сборка и самопроверка ───────────────────────────────────────────────────

def _diff_places(baseline: str, variant: str) -> int:
    """Сколько мест изменено относительно контроля.

    Нужна не красота, а гарантия изоляции: проба с двумя отличиями не отвечает
    ни на один вопрос, и лучше узнать об этом здесь, чем после десяти импортов.
    """
    diff = difflib.SequenceMatcher(None, baseline.splitlines(), variant.splitlines())
    return sum(1 for tag, *_ in diff.get_opcodes() if tag != 'equal')


def build(out_dir: str) -> Dict:
    os.makedirs(out_dir, exist_ok=True)
    manifest: Dict = {
        'generated': datetime.now().isoformat(timespec='seconds'),
        'purpose': 'Изоляционные пробы для проверки строгого профиля PIX',
        'cases': [],
    }

    # ── BPMN ────────────────────────────────────────────────────────────────
    for filename, title, deviate, rule, question, expected_places in BPMN_CASES:
        content = deviate(BASELINE_BPMN)
        path = os.path.join(out_dir, filename)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(content)

        standard = validate_bpmn_xml(content)
        strict = validate_bpmn_for_pix(content)
        places = 0 if content == BASELINE_BPMN else _diff_places(BASELINE_BPMN, content)
        manifest['cases'].append({
            'file': filename,
            'format': 'bpmn',
            'title': title,
            'question': question,
            'expected_rule': rule,
            'changed_places': places,
            'expected_places': expected_places,
            'valid_by_standard': standard.ok,
            'standard_errors': [p.code for p in standard.errors],
            'rejected_by_pix_profile': not strict.ok,
            'pix_profile_errors': [p.code for p in strict.errors],
        })

    # ── PMM ─────────────────────────────────────────────────────────────────
    from app.services.pmm_exporter import generate_pmm_zip

    baseline_payload = generate_pmm_zip(_baseline_process())
    part_name, baseline_map = _map_part(baseline_payload)

    for filename, title, deviate, rule, question, expected_places in PMM_CASES:
        if deviate is None:
            payload, variant_map = baseline_payload, baseline_map
        else:
            variant_map = deviate(baseline_map)
            payload = _repack(baseline_payload, part_name, variant_map)
        path = os.path.join(out_dir, filename)
        with open(path, 'wb') as handle:
            handle.write(payload)

        standard = validate_pmm_package(payload)
        strict = validate_pmm_for_pix(payload)
        manifest['cases'].append({
            'file': filename,
            'format': 'pmm',
            'title': title,
            'question': question,
            'expected_rule': rule,
            'changed_places': 0 if deviate is None else _diff_places(
                baseline_map, variant_map),
            'expected_places': expected_places,
            'valid_by_standard': standard.ok,
            'standard_errors': [p.code for p in standard.errors],
            'rejected_by_pix_profile': not strict.ok,
            'pix_profile_errors': [p.code for p in strict.errors],
        })
        # Карта отдельным файлом: с ней удобнее смотреть, что именно изменено.
        with open(os.path.join(out_dir, filename + '.map.xml'), 'w',
                  encoding='utf-8') as handle:
            handle.write(variant_map)

    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return manifest


def _selfcheck(manifest: Dict) -> List[str]:
    """Годен ли набор к опыту.

    Проверяется то, без чего результаты нельзя будет прочитать: контроль чист,
    у каждой пробы ровно одно отличие, и профиль отвергает её именно за то
    правило, ради которого она сделана. Ошибка здесь означает, что сломан
    инструмент, а не студия.
    """
    problems: List[str] = []
    for case in manifest['cases']:
        name = case['file']
        if case['expected_rule'] == '?':
            # Проба-возможность: спрашивает, что студия ПРИНИМАЕТ, а не что
            # отвергает. Наш профиль обязан её пропускать — иначе выгрузка
            # никогда не сможет воспользоваться ответом, даже положительным.
            if case['rejected_by_pix_profile']:
                problems.append(
                    f'{name}: профиль отвергает пробу-возможность '
                    f'({", ".join(case["pix_profile_errors"])}) — ответ студии '
                    'нечего будет применить.')
            if case['changed_places'] != case.get('expected_places'):
                problems.append(
                    f'{name}: отличий от контроля {case["changed_places"]}, '
                    f'ожидалось {case.get("expected_places")}.')
            continue
        if case['expected_rule'] == '—':
            if case['rejected_by_pix_profile']:
                problems.append(
                    f'{name}: контрольный файл отвергнут собственным профилем '
                    f'({", ".join(case["pix_profile_errors"])}) — опыт бессмыслен.')
            if not case['valid_by_standard']:
                problems.append(f'{name}: контрольный файл невалиден по стандарту.')
            continue
        expected = case.get('expected_places')
        if expected is None:
            # Проба, которая по природе задевает весь файл (переименование
            # префикса). Локальность здесь не требуется, но полное совпадение
            # с контролем означало бы, что отклонение не внесено.
            if case['changed_places'] == 0:
                problems.append(f'{name}: файл совпадает с контролем — '
                                'отклонение не внесено.')
        elif case['changed_places'] != expected:
            problems.append(
                f'{name}: отличий от контроля {case["changed_places"]}, '
                f'ожидалось {expected} — проба не изолирует правило.')
        if not case['rejected_by_pix_profile']:
            problems.append(
                f'{name}: собственный профиль эту пробу пропускает, '
                f'а должен отвергать по правилу {case["expected_rule"]}.')
        elif case['expected_rule'] not in case['pix_profile_errors']:
            problems.append(
                f'{name}: профиль отверг файл, но не по правилу '
                f'{case["expected_rule"]}, а по '
                f'{", ".join(case["pix_profile_errors"])}.')
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'pix_test_suite'),
        help='каталог для проб (по умолчанию ./pix_test_suite рядом со скриптом)')
    args = parser.parse_args(argv)

    manifest = build(args.out)
    problems = _selfcheck(manifest)

    print(f'Пробы записаны в {args.out}\n')
    header = (f'{"файл":<38} {"откл.":>5} {"по стандарту":<14} '
              f'{"профиль PIX":<14} правило')
    print(header)
    print('─' * len(header))
    for case in manifest['cases']:
        if case.get('expected_places') is None:
            places = 'весь'
        else:
            places = str(case['changed_places']) if case['changed_places'] else '—'
        print('%-38s %5s %-14s %-14s %s' % (
            case['file'][:38],
            places,
            'валиден' if case['valid_by_standard'] else 'НЕВАЛИДЕН',
            'отвергает' if case['rejected_by_pix_profile'] else 'пропускает',
            case['expected_rule'],
        ))

    print('\nЧто это значит:')
    print('  «по стандарту: валиден» + «профиль PIX: отвергает» — проба выясняет,')
    print('  строже ли студия спецификации. Это и есть главные случаи.')
    print('  «по стандарту: НЕВАЛИДЕН» — проба выясняет, как студия ведёт себя')
    print('  на нарушении стандарта: отказом или падением.')

    if problems:
        print('\nНАБОР НЕ ГОДЕН К ОПЫТУ:')
        for line in problems:
            print(f'  · {line}')
        return 1
    print('\nСамопроверка пройдена: контроль чист, каждая проба отличается от него\nровно настолько, насколько объявлено, и отвергается своим правилом.\nНабор готов к импорту в студию.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
