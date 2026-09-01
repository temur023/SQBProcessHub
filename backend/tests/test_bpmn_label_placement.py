"""Куда выгрузка кладёт подписи, которые BPMN рисует вне фигуры.

Подпись события, ветки шлюза и времени шага bpmn.io печатает отдельным блоком
по координатам из ``BPMNLabel``. Пока в списке занятых мест были только фигуры
и линии, такой блок садился на заголовок дорожки — полосу, в которой редактор
печатает повёрнутое название, — и два текста читались один поверх другого.
Ровно это и было видно в выгруженной карте.

Карта в фикстуре нарочно тесная: событие стоит у самого левого края дорожки,
а сообщение приходит из полосы внешнего участника — оба тянут подпись в
заголовочную полосу.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import (
    LANE_BORDER,
    LANE_HEADER,
    POOL_HEADER,
    generate_bpmn_xml,
)
from app.services.drawio_parser import parse_drawio_xml

CROWDED_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane_client" value="Mijoz" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="90" as="geometry" />
    </mxCell>

    <mxCell id="lane_office" value="Devonxona xizmati" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="90" width="900" height="220" as="geometry" />
    </mxCell>
    <mxCell id="start_1" value="Xat keldi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_office">
      <mxGeometry x="40" y="30" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_1" value="Xatni qabul qilib oladi&#10;5 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="120" y="20" width="160" height="60" as="geometry" />
    </mxCell>
    <mxCell id="gw_1" value="To'g'ri yo'naltirilganmi?" style="rhombus;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="330" y="30" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_2" value="Rezolyutsiya tayyorlanadi&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="420" y="20" width="160" height="60" as="geometry" />
    </mxCell>

    <mxCell id="lane_deputy" value="Boshqaruv Raisi o'rinbosari" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="310" width="900" height="200" as="geometry" />
    </mxCell>
    <mxCell id="timer_1" value="Ожидание 60 мин" style="shape=mxgraph.bpmn.event;html=1;symbol=timer;outline=standard;" vertex="1" parent="lane_deputy">
      <mxGeometry x="45" y="20" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_3" value="Xatning mazmuni bilan tanishib chiqadi&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_deputy">
      <mxGeometry x="130" y="10" width="180" height="60" as="geometry" />
    </mxCell>

    <mxCell id="flow_msg" value="xat beradi" style="dashed=1;html=1;" edge="1" parent="1" source="lane_client" target="start_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_1" value="" style="html=1;" edge="1" parent="1" source="start_1" target="task_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_2" value="" style="html=1;" edge="1" parent="1" source="task_1" target="gw_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_3" value="Ha" style="html=1;" edge="1" parent="1" source="gw_1" target="task_2">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_4" value="Yo'q" style="html=1;" edge="1" parent="1" source="gw_1" target="timer_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_5" value="" style="html=1;" edge="1" parent="1" source="timer_1" target="task_3">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""

_DI = re.compile(
    r'<bpmndi:BPMN(Shape|Edge) id="[^"]+" bpmnElement="([^"]+)"[^>]*>(.*?)</bpmndi:BPMN\1>',
    re.S,
)
_BOUNDS = re.compile(r'<dc:Bounds x="(-?\d+)" y="(-?\d+)" width="(\d+)" height="(\d+)"')
_LABEL = re.compile(r'<bpmndi:BPMNLabel>\s*<dc:Bounds x="(-?\d+)" y="(-?\d+)" width="(\d+)" height="(\d+)"', re.S)


#: Меньше пары пикселей по строке текста глаз не замечает — это не наложение.
TOUCH = 40


def _overlap(a, b):
    dx = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    dy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def _collides(a, b):
    return _overlap(a, b) > TOUCH


def _diagram(xml):
    """Фигуры, подписи и признак «это дорожка» из BPMNDI."""
    shapes, labels = [], []
    for kind, element, body in _DI.findall(xml):
        bounds = _BOUNDS.search(body)
        if kind == 'Shape' and bounds:
            shapes.append((element, tuple(int(v) for v in bounds.groups())))
        label = _LABEL.search(body)
        if label:
            labels.append((element, tuple(int(v) for v in label.groups())))
    containers = set(re.findall(r'<bpmn:lane id="([^"]+)"', xml))
    containers |= set(re.findall(r'<bpmn:participant id="([^"]+)"', xml))
    return shapes, labels, containers


class BpmnLabelPlacementTests(unittest.TestCase):
    def setUp(self):
        process = parse_drawio_xml(CROWDED_MAP, 'crowded.drawio')
        self.xml = generate_bpmn_xml(process)
        self.shapes, self.labels, self.containers = _diagram(self.xml)

    def test_map_has_external_labels(self):
        """Без подписей вне фигур проверять было бы нечего."""
        self.assertGreater(len(self.labels), 3, self.xml)

    def test_labels_avoid_lane_headers(self):
        """Подпись не садится в полосу с названием дорожки или пула."""
        hits = []
        for owner, box in self.labels:
            for element, (x, y, _w, h) in self.shapes:
                if element not in self.containers:
                    continue
                header = max(POOL_HEADER, LANE_HEADER)
                if _collides(box, (x, y, header, h)):
                    hits.append((owner, element))
        self.assertEqual(hits, [], f'подписи легли на заголовок дорожки: {hits}')

    def test_labels_avoid_lane_dividers(self):
        """Подпись не ложится на линию между дорожками — иначе она перечёркнута."""
        hits = []
        for owner, box in self.labels:
            for element, (x, y, w, h) in self.shapes:
                if element not in self.containers:
                    continue
                for edge_y in (y, y + h):
                    if _collides(box, (x, edge_y - LANE_BORDER, w, 2 * LANE_BORDER)):
                        hits.append((owner, element))
        self.assertEqual(hits, [], f'подписи легли на разделитель дорожек: {hits}')

    def test_labels_do_not_stack_on_each_other(self):
        """Две подписи не печатаются одна поверх другой."""
        hits = []
        for i, (owner_a, box_a) in enumerate(self.labels):
            for owner_b, box_b in self.labels[i + 1:]:
                if _collides(box_a, box_b):
                    hits.append((owner_a, owner_b))
        self.assertEqual(hits, [], f'подписи наложились друг на друга: {hits}')

    def test_labels_stay_off_foreign_shapes(self):
        """Подпись не накрывает чужую фигуру — её текст рисуется внутри."""
        hits = []
        for owner, box in self.labels:
            for element, sbox in self.shapes:
                if element in self.containers or element == owner:
                    continue
                if _collides(box, sbox):
                    hits.append((owner, element))
        self.assertEqual(hits, [], f'подписи накрыли фигуры: {hits}')


if __name__ == '__main__':
    unittest.main()
