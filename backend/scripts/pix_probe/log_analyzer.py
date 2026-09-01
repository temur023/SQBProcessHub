"""Сбор и разбор журналов PIX Процессной студии вокруг одного импорта.

Задача инструмента — связать «студия отказала» с конкретной пробой и конкретным
исключением, не имея ни исходников студии, ни описания формата её журналов.

ПОЧЕМУ СНИМКИ, А НЕ РАЗБОР ВРЕМЕНИ

Напрашивающийся способ — прочитать журнал и отобрать записи по метке времени.
Он требует знать формат этой метки, а он у студии неизвестен: другой шаблон,
другая локаль, другой часовой пояс — и выборка молча окажется пустой или чужой.
Поэтому инструмент работает иначе: перед импортом снимает размеры всех файлов
журнала, после импорта дочитывает ровно то, что дописалось. Прирост файла
относится к тому, что произошло между двумя снимками, — и это верно при любом
формате записи.

Отсюда порядок работы: ``begin`` -> импорт руками в студии -> ``end``.

ГДЕ ЛЕЖАТ ЖУРНАЛЫ

Точный путь у платформы не подтверждён — эталонной установки студии здесь нет.
Приложения .NET под Windows обычно пишут в ``%LOCALAPPDATA%`` или
``%APPDATA%``, поэтому ``locate`` не угадывает один каталог, а обходит список
вероятных мест и показывает, что нашлось: имя файла, размер, когда изменён.
Аналитик выбирает нужный каталог глазами и передаёт его через ``--logs``;
найденный путь запоминается в ``session.json`` и дальше подставляется сам.

Если студия пишет ещё куда-то (журнал Windows, каталог установки), путь всегда
можно задать вручную — инструмент не настаивает на своей догадке.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

#: Вероятные места, где приложение .NET под Windows держит журналы.
#: Список — гипотеза, а не знание: см. заголовок модуля.
_LOG_HINTS: List[str] = [
    r'%LOCALAPPDATA%\PIX Robotics\PIX Studio\Logs',
    r'%LOCALAPPDATA%\PIX Robotics\PIX Studio',
    r'%LOCALAPPDATA%\PIX Robotics',
    r'%APPDATA%\PIX Robotics\PIX Studio\Logs',
    r'%APPDATA%\PIX Robotics',
    r'%LOCALAPPDATA%\PIX',
    r'%APPDATA%\PIX',
    r'%PROGRAMDATA%\PIX Robotics',
    r'%USERPROFILE%\Documents\PIX Robotics',
    r'%TEMP%',
]

#: Расширения, похожие на журнал.
_LOG_SUFFIXES = ('.log', '.txt', '.jsonl', '.json', '.etl')

#: Исключения .NET, ради которых всё и затевается. Список открытый: незнакомое
#: имя тоже попадёт в отчёт — см. `_EXCEPTION_RE`.
_INTERESTING = (
    'XmlException', 'XmlSchemaException', 'NullReferenceException',
    'InvalidOperationException', 'KeyNotFoundException', 'ArgumentException',
    'ArgumentNullException', 'FormatException', 'InvalidCastException',
    'SerializationException', 'IndexOutOfRangeException', 'NotSupportedException',
)

#: Любое имя вида ``…Exception`` — вместе с текстом до конца строки.
_EXCEPTION_RE = re.compile(r'(?P<name>\b[A-Z][A-Za-z0-9_.]*Exception)\b[:\s]*(?P<text>[^\r\n]*)')

#: Строка стека .NET.
_STACK_RE = re.compile(r'^\s*(?:at|в)\s+\S+', re.MULTILINE)

#: Сообщения студии, смысл которых уже известен по прежним отказам.
_KNOWN_MESSAGES: Dict[str, str] = {
    'notation element not found': 'pix_pmm_type_unknown',
    'connector source and target node cannot be the same': 'pix_pmm_self_loop',
}

_SESSION = 'session.json'


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def _session_path(work_dir: str) -> str:
    return os.path.join(work_dir, _SESSION)


def _load_session(work_dir: str) -> Dict:
    path = _session_path(work_dir)
    if not os.path.exists(path):
        return {'logs_dir': None, 'runs': []}
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _save_session(work_dir: str, session: Dict) -> None:
    os.makedirs(work_dir, exist_ok=True)
    with open(_session_path(work_dir), 'w', encoding='utf-8') as handle:
        json.dump(session, handle, ensure_ascii=False, indent=2)


def _log_files(logs_dir: str) -> List[str]:
    """Файлы, похожие на журнал, во всём поддереве каталога."""
    found: List[str] = []
    for root, _dirs, files in os.walk(logs_dir):
        for name in files:
            if name.lower().endswith(_LOG_SUFFIXES):
                found.append(os.path.join(root, name))
    return sorted(found)


def _snapshot(logs_dir: str) -> Dict[str, int]:
    """Размеры всех файлов журнала — точка отсчёта для следующего импорта."""
    return {path: os.path.getsize(path) for path in _log_files(logs_dir)}


def _read_growth(logs_dir: str, before: Dict[str, int]) -> Dict[str, str]:
    """Что дописалось в журналы после снимка.

    Файл, который с прошлого раза уменьшился, считается перезаписанным
    (ротация): читаем его целиком, иначе потеряли бы всю запись об отказе.
    """
    growth: Dict[str, str] = {}
    for path in _log_files(logs_dir):
        previous = before.get(path, 0)
        try:
            size = os.path.getsize(path)
            with open(path, 'rb') as handle:
                if size >= previous:
                    handle.seek(previous)
                chunk = handle.read()
        except OSError as exc:
            growth[path] = f'[файл не прочитан: {exc}]'
            continue
        text = chunk.decode('utf-8', 'replace').strip()
        if text:
            growth[path] = text
    return growth


def _extract(text: str) -> Dict:
    """Исключения, стек и знакомые сообщения из куска журнала."""
    exceptions: List[Dict[str, str]] = []
    seen = set()
    for match in _EXCEPTION_RE.finditer(text):
        name = match.group('name')
        message = match.group('text').strip()
        key = (name, message[:120])
        if key in seen:
            continue
        seen.add(key)
        exceptions.append({
            'type': name,
            'message': message[:400],
            'known_parser_error': name.split('.')[-1] in _INTERESTING,
        })

    lowered = text.lower()
    matched_rules = [rule for needle, rule in _KNOWN_MESSAGES.items() if needle in lowered]
    stack = _STACK_RE.findall(text)
    return {
        'exceptions': exceptions,
        'stack_frames': len(stack),
        'stack_head': stack[:8],
        'matched_known_rules': matched_rules,
        'lines': len(text.splitlines()),
    }


# ────────────────────────────── команды ────────────────────────────────────

def cmd_locate(args) -> int:
    """Показывает, где похоже лежат журналы студии."""
    print('Ищу журналы в вероятных местах (список — гипотеза, не знание):\n')
    found_any = False
    for hint in _LOG_HINTS:
        path = _expand(hint)
        if not os.path.isdir(path):
            print(f'  нет      {hint}')
            continue
        files = _log_files(path)
        if not files:
            print(f'  пусто    {hint}  ->  {path}')
            continue
        found_any = True
        print(f'  НАЙДЕНО  {hint}  ->  {path}  (файлов: {len(files)})')
        for item in sorted(files, key=os.path.getmtime, reverse=True)[:5]:
            changed = datetime.fromtimestamp(os.path.getmtime(item))
            print(f'             {os.path.basename(item):<44} '
                  f'{os.path.getsize(item):>9} Б   {changed:%Y-%m-%d %H:%M}')
    if not found_any:
        print('\nНичего не нашлось. Это не значит, что журналов нет: студия может')
        print('писать в каталог установки или в журнал событий Windows. Найдите')
        print('файл вручную (в студии обычно есть пункт «Открыть папку с логами»)')
        print('и передайте путь: log_analyzer.py begin <файл> --logs "<путь>"')
        return 1
    print('\nВыберите нужный каталог и передайте его в begin через --logs —')
    print('дальше он запомнится в session.json и будет подставляться сам.')
    return 0


def cmd_begin(args) -> int:
    """Снимок журналов перед импортом одной пробы."""
    session = _load_session(args.work_dir)
    logs_dir = _expand(args.logs or session.get('logs_dir') or '')
    if not logs_dir or not os.path.isdir(logs_dir):
        print('Не задан каталог журналов. Запустите locate и передайте --logs.')
        return 2

    session['logs_dir'] = logs_dir
    session['pending'] = {
        'case': args.case,
        'started': datetime.now().isoformat(timespec='seconds'),
        'snapshot': _snapshot(logs_dir),
    }
    _save_session(args.work_dir, session)
    print(f'Снимок сделан: файлов {len(session["pending"]["snapshot"])} в {logs_dir}')
    print(f'Проба: {args.case}')
    print('\nТеперь импортируйте этот файл в PIX Студии и вернитесь сюда:')
    print(f'  python log_analyzer.py end --result <ok|refused|crash>')
    return 0


def cmd_end(args) -> int:
    """Дочитывает прирост журналов и записывает результат пробы."""
    session = _load_session(args.work_dir)
    pending = session.get('pending')
    if not pending:
        print('Нет начатой пробы. Сначала выполните begin <файл>.')
        return 2

    logs_dir = session['logs_dir']
    growth = _read_growth(logs_dir, pending['snapshot'])
    joined = '\n'.join(growth.values())
    analysis = _extract(joined) if joined else {
        'exceptions': [], 'stack_frames': 0, 'stack_head': [],
        'matched_known_rules': [], 'lines': 0,
    }

    run = {
        'case': pending['case'],
        'started': pending['started'],
        'finished': datetime.now().isoformat(timespec='seconds'),
        'result': args.result,
        'note': args.note or '',
        'log_files_touched': list(growth),
        'analysis': analysis,
    }
    session.setdefault('runs', []).append(run)
    session.pop('pending', None)
    _save_session(args.work_dir, session)

    # Сырой прирост — рядом, чтобы можно было прочитать глазами.
    if joined:
        raw_dir = os.path.join(args.work_dir, 'raw')
        os.makedirs(raw_dir, exist_ok=True)
        stem = re.sub(r'[^A-Za-z0-9._-]+', '_', pending['case'])
        raw_path = os.path.join(raw_dir, f'{stem}.log')
        with open(raw_path, 'w', encoding='utf-8') as handle:
            for path, text in growth.items():
                handle.write(f'───── {path} ─────\n{text}\n\n')
        print(f'Прирост журналов сохранён: {raw_path}')
    else:
        print('Журналы не выросли: студия ничего не записала.')

    print(f'\nПроба: {run["case"]}   результат: {run["result"]}')
    if analysis['exceptions']:
        print('Исключения:')
        for item in analysis['exceptions'][:6]:
            mark = '← разбор' if item['known_parser_error'] else ''
            print(f'  {item["type"]}: {item["message"][:110]} {mark}')
    if analysis['matched_known_rules']:
        print(f'Узнанные сообщения студии -> правила профиля: '
              f'{", ".join(analysis["matched_known_rules"])}')
    if analysis['stack_frames']:
        print(f'Кадров стека: {analysis["stack_frames"]}')
    return 0


def cmd_report(args) -> int:
    """Сводка: проба -> вердикт студии -> что делать с правилом профиля."""
    session = _load_session(args.work_dir)
    runs = session.get('runs', [])
    if not runs:
        print('Проб ещё не проводилось.')
        return 1

    manifest_path = os.path.join(args.suite, 'manifest.json')
    rules: Dict[str, Dict] = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as handle:
            for case in json.load(handle)['cases']:
                rules[case['file']] = case

    baseline = next((r for r in runs if 'baseline' in r['case']), None)
    if baseline is None:
        print('ВНИМАНИЕ: контрольный файл (test_00_baseline) не проверялся.')
        print('Без него результаты остальных проб прочитать нельзя: непонятно,')
        print('отвергает студия отклонение или не принимает саму пробу.\n')
    elif baseline['result'] != 'ok':
        print('ВНИМАНИЕ: контрольный файл НЕ импортировался '
              f'({baseline["result"]}).')
        print('Значит, дело не в проверяемых правилах, а в самой пробе или в')
        print('способе импорта. Остальные строки отчёта ничего не доказывают.\n')

    header = f'{"проба":<38} {"студия":<10} {"правило профиля":<28} вывод'
    print(header)
    print('─' * len(header))
    for run in runs:
        case = rules.get(run['case'], {})
        rule = case.get('expected_rule', '?')
        result = run['result']
        if 'baseline' in run['case']:
            verdict = ('контроль в порядке' if result == 'ok'
                       else 'ПРОБА НЕПРИГОДНА — разбираться с окружением')
        elif result == 'ok':
            verdict = 'правило ЛИШНЕЕ: студия приняла файл — можно ослабить'
        elif result in ('refused', 'crash'):
            verdict = 'правило ПОДТВЕРЖДЕНО'
            if run['analysis']['exceptions']:
                verdict += f' ({run["analysis"]["exceptions"][0]["type"]})'
        else:
            verdict = 'результат не записан'
        print(f'{run["case"][:38]:<38} {result:<10} {rule:<28} {verdict}')

    print('\nЧто делать дальше:')
    print('  «правило ПОДТВЕРЖДЕНО» — в pix_spec_checker оставить как есть,')
    print('  в docstring правила заменить источник required на observed.')
    print('  «правило ЛИШНЕЕ» — правило можно понизить до warning или убрать:')
    print('  студия такой файл принимает, и держать его ошибкой значит зря')
    print('  останавливать выгрузку.')
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--work-dir', default=os.path.join(here, 'probe_results'),
                        help='куда складывать снимки и результаты')
    parser.add_argument('--suite', default=os.path.join(here, 'pix_test_suite'),
                        help='каталог с пробами (нужен manifest.json)')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('locate', help='найти каталоги журналов студии')

    begin = sub.add_parser('begin', help='снимок журналов перед импортом пробы')
    begin.add_argument('case', help='имя файла пробы, например test_01_custom_namespace.bpmn')
    begin.add_argument('--logs', help='каталог журналов студии')

    end = sub.add_parser('end', help='дочитать журналы после импорта')
    end.add_argument('--result', required=True, choices=['ok', 'refused', 'crash'],
                     help='ok — импортировалось; refused — студия отказала '
                          'с сообщением; crash — приложение упало')
    end.add_argument('--note', help='что показала студия на экране')

    sub.add_parser('report', help='сводка по всем пробам')

    args = parser.parse_args(argv)
    return {
        'locate': cmd_locate,
        'begin': cmd_begin,
        'end': cmd_end,
        'report': cmd_report,
    }[args.command](args)


if __name__ == '__main__':
    raise SystemExit(main())
