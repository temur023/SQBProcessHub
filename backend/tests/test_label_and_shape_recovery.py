"""Что импортёр восстанавливает с карты, а что обязан отбросить.

Фикстура повторяет приёмы, которыми аналитики банка пользуются в draw.io и
которые импорт раньше понимал неверно: подпись фигуры набрана отдельным
текстовым блоком рядом, заголовок дорожки — повёрнутым текстом внутри неё,
для наглядности рядом поставлена иконка из библиотеки, а время шага набрано
с разделителем тысяч и разрезано редактором посреди числа.
"""
import io
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import ARTIFACT_NODE_TYPES, NODE_TYPE_LABELS, Geometry, ProcessNode
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.exporters import generate_event_log_csv, generate_regulation_csv
from app.services.pmm_exporter import bpmn_notation, generate_pmm_zip, pix_element, pix_type
from app.services.drawio_parser import (
    clean_label,
    unsupported_shape,
    duration_minutes,
    is_duration_badge_name,
    parse_bpmn_xml,
    parse_drawio_xml,
)

#: Карта, где половина подписей живёт отдельно от своих фигур.
LOOSE_LABELS_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <!-- Дорожка-баннер: заголовок схемы, шагов внутри нет. -->
    <mxCell id="banner" value="" style="swimlane;html=1;startSize=20;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1400" height="60" as="geometry" />
    </mxCell>
    <mxCell id="banner_text" value="Kredit shartnomasi (As-is)" style="text;html=1;strokeColor=none;fillColor=none;fontSize=26;" vertex="1" parent="banner">
      <mxGeometry x="400" y="15" width="500" height="30" as="geometry" />
    </mxCell>

    <!-- Настоящая дорожка: подписи нет, вместо неё повёрнутый текст внутри. -->
    <mxCell id="lane_a" value="+" style="swimlane;html=1;startSize=100;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="60" width="1400" height="400" as="geometry" />
    </mxCell>
    <mxCell id="lane_a_title" value="Korporativ markazi RM xizmati" style="text;html=1;rotation=270;fontStyle=1;" vertex="1" parent="lane_a">
      <mxGeometry x="-90" y="150" width="260" height="40" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_a">
      <mxGeometry x="140" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="task_1" value="Hujjatlarni tekshirish&lt;div&gt;1 44&lt;span style=&quot;color:#000&quot;&gt;0 min&lt;/span&gt;&lt;/div&gt;" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="240" y="170" width="160" height="70" as="geometry" />
    </mxCell>

    <!-- Шлюз без подписи: вопрос набран текстом вплотную к ромбу. -->
    <mxCell id="gw_1" value="" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane_a">
      <mxGeometry x="470" y="180" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw_1_text" value="Barcha hujjatlar mavjudmi?" style="text;html=1;fontStyle=2;" vertex="1" parent="lane_a">
      <mxGeometry x="440" y="132" width="120" height="40" as="geometry" />
    </mxCell>

    <!-- Иконка из библиотеки: украшение, к которому кто-то подвёл линию. -->
    <mxCell id="clipart" value="" style="image;html=1;image=img/lib/allied_telesis/computer_and_terminals/VOIP_IP_phone.svg;" vertex="1" parent="lane_a">
      <mxGeometry x="600" y="300" width="60" height="60" as="geometry" />
    </mxCell>

    <!-- Сложный шлюз: в draw.io ромб со звёздочкой, не плюс. -->
    <mxCell id="gw_complex" value="" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=complex;" vertex="1" parent="lane_a">
      <mxGeometry x="900" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Фигура не из набора BPMN: смысл платформе неизвестен. -->
    <mxCell id="alien" value="Chuqur tahlil" style="shape=mxgraph.azure.compute.virtual_machine;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1020" y="170" width="120" height="70" as="geometry" />
    </mxCell>

    <!-- Шаг с составным номером: «84.1.» — это не «1.» -->
    <mxCell id="task_sub" value="84.1. Mavjud kreditlarni o'rganish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1180" y="170" width="180" height="70" as="geometry" />
    </mxCell>

    <!-- Номер без завершающей точки и число, номером не являющееся. -->
    <mxCell id="task_deep" value="37.1.1 Muzokaralar olib borish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1400" y="170" width="180" height="70" as="geometry" />
    </mxCell>
    <mxCell id="task_year" value="2026 yil hisobotini yopish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1620" y="170" width="180" height="70" as="geometry" />
    </mxCell>

    <!-- Событие с входом и выходом: промежуточное, а не второй старт. -->
    <mxCell id="mid_1" value="Qo'mita qarori qabul qilindi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=throwing;" vertex="1" parent="lane_a">
      <mxGeometry x="600" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Одноимённый шаг: в регламенте такие строки не отличить друг от друга. -->
    <mxCell id="task_2" value="Hujjatlarni tekshirish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="700" y="170" width="160" height="70" as="geometry" />
    </mxCell>

    <mxCell id="end_1" value="Jarayon tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane_a">
      <mxGeometry x="920" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Документ без подписи: имя набрано текстом под ним. -->
    <mxCell id="doc_1" value="" style="shape=mxgraph.bpmn.data2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="270" y="290" width="30" height="40" as="geometry" />
    </mxCell>
    <mxCell id="doc_1_text" value="Hujjatlar ro'yxati" style="text;html=1;strokeColor=none;fillColor=none;" vertex="1" parent="lane_a">
      <mxGeometry x="240" y="335" width="120" height="20" as="geometry" />
    </mxCell>

    <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="start_1" target="task_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_1" target="gw_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e3" value="Ha" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_1" target="mid_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e4" value="Yo'q" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_1" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e5" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="mid_1" target="task_2"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e8" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_2" target="gw_complex"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e9" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_complex" target="alien"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e10" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_complex" target="task_sub"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e11" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="alien" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e12" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_sub" target="task_deep"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e13" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_deep" target="task_year"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e14" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_year" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e6" style="html=1;dashed=1;" edge="1" parent="lane_a" source="doc_1" target="task_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e7" style="html=1;dashed=1;" edge="1" parent="lane_a" source="clipart" target="mid_1"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


def _pmm_map(process) -> ET.Element:
    """Разбор карты из пакета .pmm — того самого файла, который читает PIX."""
    package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(process)))
    name = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return ET.fromstring(package.read(name).decode('utf-8'))


class CleanLabelTests(unittest.TestCase):
    """Разметку draw.io снимаем так, чтобы не порвать слово и не порвать число."""

    def test_inline_tag_does_not_split_a_number(self):
        raw = '<div>Kutish vaqti</div><div>1 44<span style="color:#000">0 min</span></div>'
        self.assertEqual(clean_label(raw), 'Kutish vaqti 1 440 min')

    def test_inline_tag_does_not_split_a_word(self):
        self.assertEqual(clean_label('Loyi<span>ha</span> tahlili'), 'Loyiha tahlili')

    def test_block_tag_still_separates_lines(self):
        self.assertEqual(clean_label('Birinchi<br/>Ikkinchi'), 'Birinchi Ikkinchi')

    def test_thousands_separator_is_part_of_the_number(self):
        self.assertEqual(duration_minutes('Kutish vaqti 1 440 min'), 1440.0)
        self.assertEqual(duration_minutes('9 600 min'), 9600.0)
        self.assertEqual(duration_minutes('5 min'), 5.0)


class LooseLabelTests(unittest.TestCase):
    """Подпись, набранная отдельным блоком, принадлежит соседней фигуре."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_text_overlay_names_the_shape_next_to_it(self):
        self.assertEqual(self.by_id['gw_1'].name, 'Barcha hujjatlar mavjudmi?')
        self.assertEqual(self.by_id['doc_1'].name, "Hujjatlar ro'yxati")

    def test_text_overlay_never_becomes_a_step(self):
        for overlay_id in ('gw_1_text', 'doc_1_text', 'lane_a_title', 'banner_text'):
            self.assertNotIn(overlay_id, self.by_id, f'{overlay_id} попал на карту как фигура')

    def test_rotated_text_titles_its_lane(self):
        lane = next(l for l in self.process.lanes if l.id == 'lane_a')
        self.assertEqual(lane.name, 'Korporativ markazi RM xizmati')

    def test_banner_lane_without_steps_is_still_pruned(self):
        # Заголовок схемы тоже лежит внутри пустой swimlane-рамки: если раздать
        # заголовки до чистки, шапка останется на карте как подразделение.
        self.assertNotIn('banner', {l.id for l in self.process.lanes})

    def test_clipart_is_not_a_process_step(self):
        self.assertNotIn('clipart', self.by_id)

    def test_event_with_incoming_and_outgoing_is_intermediate(self):
        self.assertEqual(self.by_id['mid_1'].type, 'intermediateMessageEvent')
        starts = [n for n in self.process.nodes if n.type == 'startEvent']
        self.assertEqual([n.id for n in starts], ['start_1'])

    def test_duration_with_thousands_separator_reaches_the_step(self):
        task = self.by_id['task_1']
        self.assertEqual(task.slaMinutes, 1440)
        self.assertEqual(task.name, 'Hujjatlarni tekshirish')

    def test_step_number_reaches_every_export(self):
        # Номер шага — часть его названия, поэтому он обязан быть виден и в
        # регламенте, и в BPMN, и в журнале событий: по нему сверяют карту с
        # Методикой. Отдельной колонки для номера в выгрузках нет.
        numbered = self.by_id['task_sub'].name
        self.assertIn(numbered, generate_regulation_csv(self.process))
        self.assertIn('84.1.', generate_bpmn_xml(self.process))
        self.assertIn(numbered, generate_event_log_csv(self.process))

    def test_group_diagnostic_lists_every_shape_it_counts(self):
        # Замечание без адресата бесполезно: по строке отчёта холст обязан
        # подсветить те самые фигуры, о которых она говорит.
        duplicate = next(
            v for v in self.process.validation if v.code == 'duplicate_step_name'
        )
        self.assertEqual(sorted(duplicate.nodeIds or []), ['task_1', 'task_2'])

    def test_every_diagnostic_points_at_shapes_that_exist(self):
        for issue in self.process.validation:
            for node_id in issue.nodeIds or []:
                self.assertIn(node_id, self.by_id, f'{issue.code} ведёт в пустоту')


class UnknownShapeTests(unittest.TestCase):
    """Фигура, смысла которой платформа не знает, не проходит молча."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}
        cls.by_code = {v.code: v for v in cls.process.validation}

    def test_complex_gateway_is_not_a_parallel_one(self):
        # В draw.io это ромб со звёздочкой, а не с плюсом: у параллельного
        # шлюза срабатывают все ветки, у сложного условие задаётся отдельно.
        self.assertEqual(self.by_id['gw_complex'].type, 'complexGateway')

    def test_unknown_shape_raises_a_warning_naming_the_figure(self):
        issue = self.by_code['unsupported_shape']
        self.assertEqual(issue.level, 'warning')
        self.assertIn('mxgraph.azure.compute.virtual_machine', issue.message)
        self.assertIn('Chuqur tahlil', issue.message)
        self.assertEqual(issue.nodeIds, ['alien'])

    def test_unknown_shape_still_reaches_the_map(self):
        # Выбросить незнакомую фигуру нельзя: на ней держится ветка процесса.
        self.assertIn('alien', self.by_id)

    def test_known_bpmn_shapes_raise_nothing(self):
        for node_id in ('task_1', 'gw_1', 'gw_complex', 'start_1', 'end_1', 'doc_1'):
            self.assertIsNone(
                unsupported_shape(self.by_id[node_id].style),
                f'{node_id} объявлен нераспознанным, хотя это обычная фигура BPMN',
            )

    def test_skipped_clipart_is_reported(self):
        issue = self.by_code['clipart_skipped']
        self.assertEqual(issue.level, 'info')
        self.assertIn('1', issue.message)

    def test_compound_step_number_stays_whole(self):
        # Номер шага остаётся и в названии (так он написан на карте draw.io),
        # и в коде — целиком: «84.1» — это не «1», и не тот же шаг, что «84».
        step = self.by_id['task_sub']
        self.assertEqual(step.code, 'STEP-84.1')
        self.assertEqual(step.name, "84.1. Mavjud kreditlarni o'rganish")

    def test_number_without_trailing_dot_is_still_a_number(self):
        step = self.by_id['task_deep']
        self.assertEqual(step.code, 'STEP-37.1.1')
        self.assertEqual(step.name, '37.1.1 Muzokaralar olib borish')

    def test_plain_number_in_the_text_is_not_a_step_number(self):
        # «2026 yil» — это год в названии, а не шаг №2026.
        step = self.by_id['task_year']
        self.assertNotEqual(step.code, 'STEP-2026')
        self.assertEqual(step.name, '2026 yil hisobotini yopish')


class BpmnRoundTripTests(unittest.TestCase):
    """Выгрузка, открытая заново, обязана быть той же картой."""

    @classmethod
    def setUpClass(cls):
        cls.source = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')
        cls.reopened = parse_bpmn_xml(generate_bpmn_xml(cls.source), 'loose.bpmn')

    def test_artifacts_survive_the_round_trip(self):
        before = {n.name for n in self.source.nodes if n.type in ARTIFACT_NODE_TYPES}
        after = {n.name for n in self.reopened.nodes if n.type in ARTIFACT_NODE_TYPES}
        self.assertEqual(before, after)

    def test_associations_survive_the_round_trip(self):
        self.assertTrue(any(e.kind == 'association' for e in self.reopened.edges))

    def test_duration_badge_does_not_become_a_node(self):
        # Экспорт вешает время шага граничным таймером ради Процессной студии;
        # при обратном чтении он обязан раствориться обратно в ST шага.
        self.assertEqual(len(self.reopened.nodes), len(self.source.nodes))
        self.assertTrue(is_duration_badge_name('1 ч · ожидание 30 мин'))
        self.assertFalse(is_duration_badge_name("Qo'mita qarori"))

    def test_step_time_and_role_come_back_from_documentation(self):
        step = next(n for n in self.reopened.nodes if n.id == 'task_1')
        self.assertEqual(step.slaMinutes, 1440)
        self.assertEqual(step.role, 'Korporativ markazi RM xizmati')

    def test_passport_sla_matches_the_source(self):
        self.assertEqual(
            self.reopened.passport.targetSlaHours,
            self.source.passport.targetSlaHours,
        )


#: Карта с линией, замкнутой на саму фигуру: draw.io такое рисует молча.
SELF_LOOP_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="Ofis" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="300" as="geometry" />
    </mxCell>
    <mxCell id="s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="t" value="Hujjatlarni tekshirish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="200" y="110" width="160" height="70" as="geometry" />
    </mxCell>
    <mxCell id="e" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="460" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="f1" edge="1" parent="lane" source="s" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="lane" source="t" target="e"><mxGeometry relative="1" as="geometry" /></mxCell>
    <!-- Обе точки висят в пустоте рядом с одним и тем же шагом. -->
    <mxCell id="loose" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="215" y="150" as="sourcePoint" />
        <mxPoint x="250" y="150" as="targetPoint" />
      </mxGeometry>
    </mxCell>
    <!-- Явная петля: аналитик замкнул связь на ту же фигуру. -->
    <mxCell id="loop" edge="1" parent="lane" source="t" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


class SelfLoopTests(unittest.TestCase):
    """Связь фигуры с самой собой не должна доехать до PIX.

    Процессная студия из-за одной такой линии отказывается открыть карту
    целиком: «Connector source and target node cannot be the same».
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(SELF_LOOP_MAP, 'loop.drawio')

    def test_loose_ends_do_not_snap_onto_the_same_shape(self):
        # Оба конца линии висят в пустоте рядом с одним шагом: раньше
        # притягивались к нему же и давали петлю.
        snapped = [
            e for e in self.process.edges
            if e.id == 'loose' and e.sourceId == e.targetId
        ]
        self.assertEqual(snapped, [])

    def test_explicit_loop_is_reported_as_an_error(self):
        issue = next(v for v in self.process.validation if v.code == 'self_loop')
        self.assertEqual(issue.level, 'error')
        self.assertEqual(issue.nodeIds, ['t'])

    def test_bpmn_export_has_no_self_referencing_flow(self):
        xml = generate_bpmn_xml(self.process)
        pairs = re.findall(r'sourceRef="([^"]+)"\s+targetRef="([^"]+)"', xml)
        self.assertTrue(pairs)
        self.assertEqual([p for p in pairs if p[0] == p[1]], [])

    def test_pmm_export_has_no_self_referencing_connector(self):
        root = _pmm_map(self.process)
        pairs = [
            (c.get('sourceNodeId'), c.get('targetNodeId'))
            for c in root.iter('connector')
        ]
        self.assertTrue(pairs)
        self.assertEqual([p for p in pairs if p[0] == p[1]], [])


class PixNotationTests(unittest.TestCase):
    """Пакет .pmm должен говорить со студией её же словарём."""

    @classmethod
    def setUpClass(cls):
        cls.name, cls.elements = bpmn_notation()
        cls.process = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')

    def test_notation_name_is_written_the_way_the_studio_writes_it(self):
        # Каталог объявляет нотацию как «BPMN», а сама студия пишет в карту
        # «bpmn» (tests/fixtures/sap.pmm) — регистр она не различает. Держимся
        # её написания: расходиться с эталоном на первом атрибуте карты незачем.
        notation = _pmm_map(self.process).get('notation')
        self.assertEqual(notation, 'bpmn')
        self.assertEqual(notation.lower(), self.name.lower())

    def test_every_node_type_exists_in_the_notation(self):
        used = {n.get('type') for n in _pmm_map(self.process).iter('node')}
        self.assertTrue(used)
        self.assertEqual(used - self.elements, set())

    def test_every_type_the_exporter_can_produce_is_known(self):
        # Незнакомый тип валит импорт всего пакета, поэтому проверяем не то,
        # что попало в конкретную карту, а весь словарь экспортёра.
        for node_type in NODE_TYPE_LABELS:
            if node_type == 'lane':
                continue
            probe = ProcessNode(id='probe', name='probe', type=node_type, geometry=Geometry())
            self.assertIn(pix_type(probe), self.elements, node_type)

    def test_complex_gateway_keeps_its_own_element(self):
        probe = ProcessNode(id='gw', name='gw', type='complexGateway', geometry=Geometry())
        self.assertEqual(pix_type(probe), 'gateway_complex')

    def test_unknown_type_falls_back_to_a_known_one(self):
        self.assertEqual(pix_element('no_such_element'), 'task')


#: Карта с двумя дефектами, на которые жалуются аналитики: развилка, у которой
#: подписана одна ветка из двух, и фигура, забытая на холсте без связей.
HALF_LABELLED_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="RM" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1200" height="400" as="geometry" />
    </mxCell>
    <mxCell id="s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw" value="Bank tariflari mijozni qoniqtirdimi?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane">
      <mxGeometry x="200" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="ok" value="Hujjatlar toplamini taqdim etish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="340" y="110" width="160" height="70" as="geometry" />
    </mxCell>
    <mxCell id="no" value="Rad etildi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="340" y="260" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="done" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="600" y="120" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Развилка на три ветки: смысл третьей платформа угадывать не вправе. -->
    <mxCell id="gw3" value="Qaysi yo'nalish?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane">
      <mxGeometry x="760" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="w1" value="A" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="880" y="40" width="90" height="50" as="geometry" />
    </mxCell>
    <mxCell id="w2" value="B" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="880" y="120" width="90" height="50" as="geometry" />
    </mxCell>
    <mxCell id="w3" value="C" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="880" y="200" width="90" height="50" as="geometry" />
    </mxCell>

    <!-- Забытая на холсте фигура: ни входящих связей, ни исходящих. -->
    <mxCell id="orphan" value="Avtomat sms xabarnoma yuborish" style="label;whiteSpace=wrap;html=1;image=img/clipart/Gear_128x128.png;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="lane">
      <mxGeometry x="200" y="300" width="160" height="60" as="geometry" />
    </mxCell>

    <mxCell id="f1" edge="1" parent="lane" source="s" target="gw"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="lane" source="gw" target="ok"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f3" value="Yo'q" edge="1" parent="lane" source="gw" target="no"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f4" edge="1" parent="lane" source="ok" target="done"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f5" edge="1" parent="lane" source="done" target="gw3"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f6" value="A" edge="1" parent="lane" source="gw3" target="w1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f7" edge="1" parent="lane" source="gw3" target="w2"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f8" edge="1" parent="lane" source="gw3" target="w3"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


class GatewayBranchCompletionTests(unittest.TestCase):
    """Ветка «да» рядом с подписанной «нет» — единственное однозначное чтение."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(HALF_LABELLED_MAP, 'half.drawio')
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)
        cls.edges = {e.id: e for e in cls.process.edges}

    def test_opposite_condition_is_filled_in(self):
        # PIX BPM не автоматизирует ветку без условия, а на рисунке смысл
        # второй ветки очевиден из первой.
        self.assertEqual(self.edges['f2'].name, 'Ha')
        self.assertEqual(self.edges['f2'].condition, 'Ha')

    def test_completion_is_reported_not_hidden(self):
        issue = self.by_code['gateway_branch_completed'][0]
        self.assertEqual(issue.level, 'warning')
        self.assertIn('«Ha»', issue.message)
        self.assertEqual(issue.nodeId, 'gw')

    def test_binary_gateway_is_no_longer_an_error(self):
        unlabeled = self.by_code.get('gateway_branch_unlabeled', [])
        self.assertNotIn('gw', [i.nodeId for i in unlabeled])

    def test_three_way_gateway_is_not_guessed(self):
        # Угадать смысл третьей ветки нельзя: это остаётся ошибкой.
        self.assertIsNone(self.edges['f7'].name or None)
        unlabeled = self.by_code['gateway_branch_unlabeled']
        self.assertIn('gw3', [i.nodeId for i in unlabeled])

    def test_lonely_shape_is_reported_once(self):
        # Фигура без единой связи — не разрыв цепочки, а забытая фигура:
        # одно понятное сообщение вместо двух половинчатых.
        issue = self.by_code['isolated_node'][0]
        self.assertEqual(issue.level, 'error')
        self.assertEqual(issue.nodeId, 'orphan')
        self.assertNotIn('orphan', [i.nodeId for i in self.by_code.get('no_incoming', [])])
        self.assertNotIn('orphan', [i.nodeId for i in self.by_code.get('no_outgoing', [])])

    def test_completed_condition_reaches_the_exports(self):
        # Подставленное условие бесполезно, если оно не доехало до файла: PIX
        # читает ветку именно из выгрузки, а не с холста.
        self.assertIn('<bpmn:conditionExpression', generate_bpmn_xml(self.process))
        self.assertIn('name="Ha"', generate_bpmn_xml(self.process))
        texts = {c.get('Text') for c in _pmm_map(self.process).iter('connector')}
        self.assertIn('Ha', texts)


if __name__ == '__main__':
    unittest.main()
