"""Предпроверка исходной карты draw.io.

Проверяется не текст сообщений, а разделение, ради которого модуль написан:
что останавливает выгрузку, что доезжает предупреждением и что не должно
беспокоить сотрудника вовсе.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.drawio_precheck import (
    DrawioPrecheckError,
    ensure_exportable,
    precheck_drawio,
)

client = TestClient(app)

HEAD = ('<mxfile host="app.diagrams.net"><diagram name="Стр.1"><mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>')
TAIL = '</root></mxGraphModel></diagram></mxfile>'

LANE = 'swimlane;horizontal=0;startSize=30;'
TASK = 'rounded=1;whiteSpace=wrap;html=1;'
START = 'ellipse;html=1;'


def cell(cid, value, style, x, y, w, h, parent='1'):
    return (f'<mxCell id="{cid}" value="{value}" style="{style}" vertex="1" parent="{parent}">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def edge(eid, src, tgt, value='', style='edgeStyle=orthogonalEdgeStyle;', points=''):
    attrs = ''.join([
        f' source="{src}"' if src else '',
        f' target="{tgt}"' if tgt else '',
    ])
    return (f'<mxCell id="{eid}" value="{value}" style="{style}" edge="1" parent="1"{attrs}>'
            f'<mxGeometry relative="1" as="geometry">{points}</mxGeometry></mxCell>')


def sound_map(extra=''):
    """Схема без единого нарекания — основа для точечных дефектов."""
    return (HEAD
            + cell('L1', 'Фронт-офис', LANE, 0, 0, 800, 200)
            + cell('S', 'Заявка принята', START, 40, 60, 40, 40, 'L1')
            + cell('T', 'Проверка документов', TASK, 200, 50, 160, 60, 'L1')
            + edge('E1', 'S', 'T')
            + extra + TAIL)


def codes(result):
    return {p.code for p in result.problems}


# ── Здоровая карта ──────────────────────────────────────────────────────────

def test_sound_map_passes_without_a_word():
    result = precheck_drawio(sound_map(), 'ok.drawio')
    assert result.ok
    assert not result.problems
    assert 'пригодна для выгрузки' in result.message()


def test_counters_describe_the_page():
    result = precheck_drawio(sound_map(), 'ok.drawio')
    assert (result.steps, result.lanes, result.edges) == (2, 1, 1)
    assert result.page == 'Стр.1'


# ── Блокирующее: студия откажется открыть файл ──────────────────────────────

def test_self_loop_blocks_export():
    """Тот самый «Connector source and target node cannot be the same»."""
    result = precheck_drawio(sound_map(edge('E2', 'T', 'T')), 'loop.drawio')
    assert not result.ok
    assert 'edge_self_loop' in codes(result)


def test_duplicate_shape_id_blocks_export():
    broken = sound_map(cell('T', 'Копия шага', TASK, 500, 50, 160, 60, 'L1'))
    result = precheck_drawio(broken, 'dup.drawio')
    assert not result.ok
    assert 'duplicate_ids' in codes(result)


def test_zero_sized_shape_blocks_export():
    result = precheck_drawio(
        sound_map(cell('Z', 'Пустышка', TASK, 500, 50, 0, 0, 'L1')), 'zero.drawio')
    assert not result.ok
    assert 'zero_size' in codes(result)


def test_edge_label_is_not_a_zero_sized_shape():
    """Подпись «Да» на ветке шлюза размера не имеет — и это норма."""
    label = ('<mxCell id="LBL" value="Да" style="edgeLabel;" vertex="1" parent="E1">'
             '<mxGeometry relative="1" as="geometry"/></mxCell>')
    result = precheck_drawio(sound_map(label), 'label.drawio')
    assert 'zero_size' not in codes(result)


def test_edge_to_deleted_shape_blocks_export():
    result = precheck_drawio(sound_map(edge('E2', 'T', 'GHOST')), 'ghost.drawio')
    assert not result.ok
    assert 'edge_broken_ref' in codes(result)


def test_unreadable_file_blocks_export():
    result = precheck_drawio('<mxfile><diagram>', 'broken.drawio')
    assert not result.ok


def test_map_without_shapes_blocks_export():
    result = precheck_drawio(HEAD + TAIL, 'empty.drawio')
    assert not result.ok
    assert 'no_shapes' in codes(result)


def test_map_of_lanes_only_blocks_export():
    only_lanes = HEAD + cell('L1', 'Отдел', LANE, 0, 0, 800, 200) + TAIL
    result = precheck_drawio(only_lanes, 'lanes.drawio')
    assert not result.ok
    assert 'no_steps' in codes(result)


# ── Потеря содержимого: файл откроется, но карта неполная ───────────────────

def test_detached_arrow_warns_but_does_not_block():
    """PIX такой файл принимает — просто без этой связи."""
    far_away = ('<mxPoint x="9000" y="9000" as="sourcePoint"/>'
                '<mxPoint x="9400" y="9000" as="targetPoint"/>')
    result = precheck_drawio(sound_map(edge('E9', None, None, points=far_away)), 'lost.drawio')
    assert result.ok, 'висящая стрелка не мешает студии открыть файл'
    assert 'edge_detached' in codes(result)
    assert result.data_loss, 'но содержимое теряется, и это надо показать'


def test_data_loss_blocks_in_strict_mode():
    far_away = ('<mxPoint x="9000" y="9000" as="sourcePoint"/>'
                '<mxPoint x="9400" y="9000" as="targetPoint"/>')
    source = sound_map(edge('E9', None, None, points=far_away))
    assert not precheck_drawio(source, 'lost.drawio').blocking(strict=False)
    assert precheck_drawio(source, 'lost.drawio').blocking(strict=True)


def test_arrow_snapped_to_a_neighbour_is_only_a_note():
    """Конец брошен в паре пикселей от шага — платформа дотянет его сама."""
    near = '<mxPoint x="210" y="80" as="targetPoint"/>'
    result = precheck_drawio(sound_map(edge('E9', 'S', None, points=near)), 'snap.drawio')
    assert result.ok
    assert 'edge_snapped' in codes(result)
    assert not [p for p in result.data_loss if p.code == 'edge_dangling']


def test_line_without_arrowhead_is_decoration():
    """Выноска и подчёркивание группы — не потерянная связь."""
    decor = edge('E9', None, None, style='endArrow=none;html=1;',
                 points='<mxPoint x="9000" y="9000" as="sourcePoint"/>'
                        '<mxPoint x="9400" y="9000" as="targetPoint"/>')
    result = precheck_drawio(sound_map(decor), 'decor.drawio')
    assert result.ok
    assert 'decorative_line' in codes(result)
    assert not result.data_loss


def test_map_without_lanes_reports_missing_responsibility():
    laneless = (HEAD
                + cell('S', 'Старт', START, 40, 60, 40, 40)
                + cell('T', 'Шаг', TASK, 200, 50, 160, 60)
                + edge('E1', 'S', 'T') + TAIL)
    result = precheck_drawio(laneless, 'nolanes.drawio')
    assert 'no_lanes' in codes(result)
    assert result.ok, 'PIX откроет и такую карту — просто без ролей'
    assert result.data_loss


# ── Сообщение сотруднику ────────────────────────────────────────────────────

def test_message_follows_the_agreed_wording():
    result = precheck_drawio(sound_map(edge('E2', 'T', 'T')), 'loop.drawio')
    text = result.message()
    assert text.startswith('Внимание: при импорте в PIX возникнет ошибка, так как в draw.io файле')
    assert 'loop.drawio' in text


def test_message_lists_every_blocking_problem():
    broken = sound_map(
        edge('E2', 'T', 'T') + cell('Z', 'Пустышка', TASK, 500, 50, 0, 0, 'L1'))
    text = precheck_drawio(broken, 'two.drawio').message()
    assert 'блокирующие проблемы' in text
    assert '1.' in text and '2.' in text


def test_ensure_exportable_raises_with_the_same_text():
    source = sound_map(edge('E2', 'T', 'T'))
    with pytest.raises(DrawioPrecheckError) as caught:
        ensure_exportable(source, 'loop.drawio')
    assert 'возникнет ошибка' in str(caught.value)
    assert caught.value.result.errors


def test_ensure_exportable_returns_result_for_a_sound_map():
    assert ensure_exportable(sound_map(), 'ok.drawio').ok


# ── Импорт через API ────────────────────────────────────────────────────────

def test_api_rejects_a_map_the_studio_would_refuse():
    response = client.post('/api/v1/import/xml', json={
        'xml': sound_map(edge('E2', 'T', 'T')),
        'fileName': 'loop.drawio',
    })
    assert response.status_code == 422
    assert 'возникнет ошибка' in response.json()['detail']


def test_api_imports_a_lossy_map_and_reports_the_loss():
    far_away = ('<mxPoint x="9000" y="9000" as="sourcePoint"/>'
                '<mxPoint x="9400" y="9000" as="targetPoint"/>')
    response = client.post('/api/v1/import/xml', json={
        'xml': sound_map(edge('E9', None, None, points=far_away)),
        'fileName': 'lost.drawio',
    })
    assert response.status_code == 200
    notes = response.json()['validation']
    assert any(n['code'] == 'source_edge_detached' for n in notes)


def test_api_import_survives_a_sound_map():
    response = client.post('/api/v1/import/xml', json={
        'xml': sound_map(), 'fileName': 'ok.drawio',
    })
    assert response.status_code == 200
    assert response.json()['nodes']
