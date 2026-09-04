"""Набор изоляционных проб для PIX.

Пробы уезжают к аналитику и импортируются руками — ошибка в них обнаружится
только там, после десятка бесполезных импортов. Поэтому набор проверяется здесь:
контроль чист, каждая проба отличается от него ровно настолько, насколько
объявлено, и отвергается своим правилом.

Отдельно проверяется цикл ``log_analyzer``: снимок, дочитывание прироста,
разбор исключения. Студии на машине сборки нет, поэтому журнал поддельный —
проверяется инструмент, а не студия.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROBE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'pix_probe')
sys.path.insert(0, PROBE_DIR)

from generate_pix_tests import _selfcheck, build  # noqa: E402


@pytest.fixture(scope='module')
def suite(tmp_path_factory):
    out = tmp_path_factory.mktemp('pix_suite')
    return str(out), build(str(out))


# ── Набор проб ──────────────────────────────────────────────────────────────

def test_suite_is_fit_for_the_experiment(suite):
    """Главный тест: без этого опыт невозможно прочитать."""
    _out, manifest = suite
    assert _selfcheck(manifest) == []


def test_every_requested_case_is_present(suite):
    out, _manifest = suite
    expected = {
        'test_00_baseline.bpmn',
        'test_01_custom_namespace.bpmn',
        'test_02_float_coordinates.bpmn',
        'test_03_missing_process_type.bpmn',
        'test_04_missing_is_executable.bpmn',
        'test_05_unlinked_shape.bpmn',
        'test_00_baseline.pmm',
        'test_06_pmm_out_of_bounds.pmm',
        'test_07_pmm_unknown_type.pmm',
    }
    assert expected <= set(os.listdir(out))


def test_baseline_is_accepted_by_our_own_profile(suite):
    """Контроль обязан проходить наш профиль — иначе он не контроль."""
    _out, manifest = suite
    for case in manifest['cases']:
        if case['expected_rule'] == '—':
            assert case['valid_by_standard'], case['file']
            assert not case['rejected_by_pix_profile'], case['pix_profile_errors']


def test_deviations_stay_legal_by_the_standard_where_that_is_the_point(suite):
    """Пробы, ради которых всё затевалось: стандарт принимает, профиль — нет.

    Если такая проба вдруг станет невалидной по стандарту, она перестанет
    отвечать на свой вопрос («строже ли студия спецификации») и превратится в
    проверку совсем другого.
    """
    _out, manifest = suite
    by_file = {c['file']: c for c in manifest['cases']}
    for name in ('test_01_custom_namespace.bpmn',
                 'test_02_float_coordinates.bpmn',
                 'test_03_missing_process_type.bpmn',
                 'test_04_missing_is_executable.bpmn',
                 'test_06_pmm_out_of_bounds.pmm'):
        case = by_file[name]
        assert case['valid_by_standard'], (name, case['standard_errors'])
        assert case['rejected_by_pix_profile'], name


def test_generated_bpmn_files_are_well_formed(suite):
    import xml.etree.ElementTree as ET

    out, _manifest = suite
    for name in os.listdir(out):
        if name.endswith('.bpmn'):
            ET.parse(os.path.join(out, name))


def test_generated_pmm_files_are_readable_packages(suite):
    import zipfile

    out, _manifest = suite
    for name in os.listdir(out):
        if not name.endswith('.pmm'):
            continue
        package = zipfile.ZipFile(os.path.join(out, name))
        assert package.testzip() is None, name
        assert 'main.xml' in package.namelist()
        assert 'pm/configuration.xml' in package.namelist()


# ── Разбор журналов ─────────────────────────────────────────────────────────

def _analyzer(work_dir, suite_dir, *args):
    return subprocess.run(
        [sys.executable, os.path.join(PROBE_DIR, 'log_analyzer.py'),
         '--work-dir', str(work_dir), '--suite', str(suite_dir), *args],
        capture_output=True, text=True, encoding='utf-8',
        env=dict(os.environ, PYTHONIOENCODING='utf-8'))


def test_analyzer_pairs_an_exception_with_the_case(tmp_path, suite):
    """Полный цикл на поддельном журнале: снимок -> прирост -> исключение."""
    suite_dir, _manifest = suite
    logs = tmp_path / 'Logs'
    logs.mkdir()
    journal = logs / 'PIXStudio.log'
    journal.write_text('INFO Studio started\n', encoding='utf-8')
    work = tmp_path / 'results'

    started = _analyzer(work, suite_dir, 'begin', 'test_07_pmm_unknown_type.pmm',
                        '--logs', str(logs))
    assert started.returncode == 0, started.stdout + started.stderr

    with journal.open('a', encoding='utf-8') as handle:
        handle.write(
            "ERROR Import failed\n"
            "System.Collections.Generic.KeyNotFoundException: "
            "Notation element not found (Parameter 'type')\n"
            "   at PIX.Studio.Import.NotationResolver.Resolve(String type)\n")

    finished = _analyzer(work, suite_dir, 'end', '--result', 'crash')
    assert finished.returncode == 0, finished.stdout + finished.stderr

    session = json.loads((work / 'session.json').read_text(encoding='utf-8'))
    run = session['runs'][-1]
    assert run['case'] == 'test_07_pmm_unknown_type.pmm'
    assert run['result'] == 'crash'
    assert run['analysis']['exceptions'][0]['type'].endswith('KeyNotFoundException')
    assert run['analysis']['exceptions'][0]['known_parser_error']
    # Знакомое сообщение студии само указывает на правило профиля.
    assert 'pix_pmm_type_unknown' in run['analysis']['matched_known_rules']
    assert run['analysis']['stack_frames'] >= 1


def test_analyzer_reads_nothing_when_the_studio_stayed_silent(tmp_path, suite):
    suite_dir, _manifest = suite
    logs = tmp_path / 'Logs'
    logs.mkdir()
    (logs / 'PIXStudio.log').write_text('INFO started\n', encoding='utf-8')
    work = tmp_path / 'results'

    _analyzer(work, suite_dir, 'begin', 'test_00_baseline.bpmn', '--logs', str(logs))
    finished = _analyzer(work, suite_dir, 'end', '--result', 'ok')
    assert finished.returncode == 0

    session = json.loads((work / 'session.json').read_text(encoding='utf-8'))
    assert session['runs'][-1]['analysis']['exceptions'] == []


def test_manual_record_needs_no_logs(tmp_path, suite):
    """Опыт должен ставиться и тогда, когда Python рядом со студией не запустить.

    На корпоративной машине его может не быть; результаты вносятся руками, а
    вердикт по правилу обязан получаться тот же, что и по журналу.
    """
    suite_dir, _manifest = suite
    work = tmp_path / 'results'

    done = _analyzer(work, suite_dir, 'record', 'test_07_pmm_unknown_type.pmm',
                     '--result', 'refused',
                     '--message', "Notation element not found (Parameter 'type')")
    assert done.returncode == 0, done.stdout + done.stderr

    session = json.loads((work / 'session.json').read_text(encoding='utf-8'))
    run = session['runs'][-1]
    assert run['case'] == 'test_07_pmm_unknown_type.pmm'
    assert run['result'] == 'refused'
    assert run['source'] == 'manual'
    # Сообщение с экрана разбирается тем же разбором, что и журнал.
    assert 'pix_pmm_type_unknown' in run['analysis']['matched_known_rules']


def test_manual_record_replaces_a_repeated_case(tmp_path, suite):
    """Пробу переставляют, когда сомневаются в результате; двух записей быть не должно."""
    suite_dir, _manifest = suite
    work = tmp_path / 'results'

    _analyzer(work, suite_dir, 'record', 'test_02_float_coordinates.bpmn',
              '--result', 'refused')
    _analyzer(work, suite_dir, 'record', 'test_02_float_coordinates.bpmn',
              '--result', 'ok', '--note', 'пересмотрел: карта всё-таки открылась')

    session = json.loads((work / 'session.json').read_text(encoding='utf-8'))
    cases = [r['case'] for r in session['runs']]
    assert cases.count('test_02_float_coordinates.bpmn') == 1
    assert session['runs'][-1]['result'] == 'ok'


def test_report_reads_manually_recorded_probes(tmp_path, suite):
    """Сводка обязана строиться и по записанным руками пробам, с пометкой об этом."""
    suite_dir, _manifest = suite
    work = tmp_path / 'results'

    _analyzer(work, suite_dir, 'record', 'test_00_baseline.bpmn', '--result', 'ok')
    _analyzer(work, suite_dir, 'record', 'test_02_float_coordinates.bpmn', '--result', 'ok')

    report = _analyzer(work, suite_dir, 'report')
    assert report.returncode == 0, report.stdout + report.stderr
    assert 'контроль в порядке' in report.stdout
    assert 'правило ЛИШНЕЕ' in report.stdout
    assert 'записано со слов' in report.stdout


def test_report_warns_when_the_control_failed(tmp_path, suite):
    """Провалившийся контроль обесценивает весь опыт — отчёт обязан это сказать."""
    suite_dir, _manifest = suite
    logs = tmp_path / 'Logs'
    logs.mkdir()
    (logs / 'PIXStudio.log').write_text('INFO started\n', encoding='utf-8')
    work = tmp_path / 'results'

    _analyzer(work, suite_dir, 'begin', 'test_00_baseline.bpmn', '--logs', str(logs))
    _analyzer(work, suite_dir, 'end', '--result', 'refused')
    report = _analyzer(work, suite_dir, 'report')

    assert 'контрольный файл НЕ импортировался' in report.stdout
    assert 'ничего не доказывают' in report.stdout


def test_report_calls_an_accepted_deviation_a_redundant_rule(tmp_path, suite):
    """Студия приняла файл — значит, правило профиля лишнее, и так и надо сказать."""
    suite_dir, _manifest = suite
    logs = tmp_path / 'Logs'
    logs.mkdir()
    (logs / 'PIXStudio.log').write_text('INFO started\n', encoding='utf-8')
    work = tmp_path / 'results'

    for case, result in (('test_00_baseline.bpmn', 'ok'),
                         ('test_02_float_coordinates.bpmn', 'ok')):
        _analyzer(work, suite_dir, 'begin', case, '--logs', str(logs))
        _analyzer(work, suite_dir, 'end', '--result', result)

    report = _analyzer(work, suite_dir, 'report')
    assert 'контроль в порядке' in report.stdout
    assert 'ЛИШНЕЕ' in report.stdout
