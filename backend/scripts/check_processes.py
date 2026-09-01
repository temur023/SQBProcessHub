"""Прогон каталога карт draw.io через предпроверку, экспорт и проверку выгрузок.

Отвечает на вопрос «что из этой папки вообще доедет до PIX и почему нет» —
одним прогоном и без ручного открывания каждого файла в студии.

Порядок ровно тот же, что в бою (роутер ``import_export``):

1. :func:`precheck_drawio` смотрит на ИСХОДНИК. Блокирующая ошибка — файл
   дальше не идёт: собирать выгрузку из заведомо неверной схемы бессмысленно.
2. Парсер строит модель процесса.
3. Экспортёры собирают ``.bpmn`` и ``.pmm``.
4. :mod:`export_validation` проверяет ГОТОВЫЕ файлы по стандарту, а
   :mod:`pix_spec_checker` — строгим профилем PIX (он же собирает запасной
   XPDL 2.2 и проверяет его по стандарту WfMC).

Запуск::

    python -m scripts.check_processes D:\\projects\\sqb\\processes
    python -m scripts.check_processes <каталог> --save out/   # сложить выгрузки
    python -m scripts.check_processes <каталог> --verbose     # + предупреждения

Код возврата 1, если хоть один файл не годен для PIX, — чтобы прогон можно было
поставить в CI.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

# Скрипт запускают и как модуль, и файлом из корня backend.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import generate_bpmn_xml  # noqa: E402
from app.services.drawio_parser import parse_drawio_xml  # noqa: E402
from app.services.drawio_precheck import precheck_drawio  # noqa: E402
from app.services.export_validation import (  # noqa: E402
    validate_bpmn_xml,
    validate_pmm_package,
)
from app.services.pix_spec_checker import (  # noqa: E402
    validate_bpmn_for_pix,
    validate_pmm_for_pix,
    validate_xpdl,
)
from app.services.pmm_exporter import generate_pmm_zip  # noqa: E402
from app.services.xpdl_exporter import generate_xpdl  # noqa: E402

_RULE = '─' * 78


def _read(path: str) -> str:
    with open(path, 'rb') as handle:
        return handle.read().decode('utf-8', 'ignore')


def check_file(path: str, save_dir: Optional[str], verbose: bool, strict: bool) -> str:
    """Проверяет один файл. Возвращает итог: 'ok' | 'blocked' | 'export_error'."""
    name = os.path.basename(path)
    print(f'\n{_RULE}\n{name}')

    content = _read(path)
    pre = precheck_drawio(content, name)

    # ── 1. Исходник ─────────────────────────────────────────────────────────
    if pre.blocking(strict):
        print('  ПРЕДПРОВЕРКА: выгрузка остановлена')
        for line in pre.message(strict).splitlines():
            print(f'    {line}')
        return 'blocked'

    print(f'  предпроверка: пройдена '
          f'(фигур {pre.shapes}, шагов {pre.steps}, дорожек {pre.lanes}, '
          f'связей {pre.edges})')
    losses = pre.data_loss
    if losses:
        print(f'  ПОТЕРЯ СОДЕРЖИМОГО: {len(losses)} — карта загрузится, но не целиком')
        for problem in (losses if verbose else losses[:3]):
            print(f'    ! {problem.message}')
        if not verbose and len(losses) > 3:
            print(f'    ! … и ещё {len(losses) - 3} (--verbose покажет все)')
    other = [p for p in pre.warnings if not p.loses_data]
    if other:
        print(f'  предупреждений к исходнику: {len(other)}')
        for problem in (other if verbose else other[:2]):
            print(f'    · {problem.message}')
        if not verbose and len(other) > 2:
            print(f'    · … и ещё {len(other) - 2} (--verbose покажет все)')

    # ── 2-3. Модель и выгрузки ──────────────────────────────────────────────
    try:
        process = parse_drawio_xml(content, name)
        bpmn = generate_bpmn_xml(process)
        pmm = generate_pmm_zip(process)
        xpdl = generate_xpdl(process)
    except Exception as exc:  # noqa: BLE001 — отчёт не должен падать на файле
        print(f'  ЭКСПОРТ УПАЛ: {type(exc).__name__}: {exc}')
        return 'export_error'

    # ── 4. Готовые файлы: сначала стандарт, затем строгий профиль PIX ───────
    # Две колонки, потому что и вопроса два: «соответствует стандарту» и
    # «откроется именно в студии». Второй строже, и файл, валидный по BPMN 2.0,
    # всё ещё может не подойти PIX.
    sizes = {
        'bpmn': len(bpmn.encode('utf-8')),
        'pmm': len(pmm),
        'xpdl': len(xpdl.encode('utf-8')),
    }
    rows = [
        ('bpmn', validate_bpmn_xml(bpmn), validate_bpmn_for_pix(bpmn)),
        ('pmm', validate_pmm_package(pmm), validate_pmm_for_pix(pmm)),
        # У XPDL профиля PIX нет: что студия делает с этим форматом, неизвестно,
        # поэтому проверяется только соответствие стандарту WfMC.
        ('xpdl', validate_xpdl(xpdl), None),
    ]

    verdict = 'ok'
    print(f'  {"формат":<6} {"размер":>8}  {"стандарт":<14} {"профиль PIX":<14}')
    for fmt, standard, strict in rows:
        standard_mark = 'валиден' if standard.ok else f'ОТКЛОНЁН ({len(standard.errors)})'
        if strict is None:
            strict_mark = '—'
        else:
            strict_mark = 'валиден' if strict.ok else f'ОТКЛОНЁН ({len(strict.errors)})'
        print(f'  {fmt:<6} {sizes[fmt] // 1024:>5} КБ  {standard_mark:<14} {strict_mark:<14}')
        for check in (standard, strict):
            if check is None:
                continue
            for problem in check.errors:
                print(f'      {check.format} error {problem.code}: {problem.message}')
                verdict = 'export_error'
            if verbose:
                for problem in check.warnings:
                    print(f'      {check.format} warn  {problem.code}: {problem.message}')

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        stem = os.path.splitext(name)[0]
        with open(os.path.join(save_dir, stem + '.bpmn'), 'w', encoding='utf-8') as handle:
            handle.write(bpmn)
        with open(os.path.join(save_dir, stem + '.pmm'), 'wb') as handle:
            handle.write(pmm)
        with open(os.path.join(save_dir, stem + '.xpdl'), 'w', encoding='utf-8') as handle:
            handle.write(xpdl)
    return verdict


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('target', help='каталог с картами .drawio или один файл')
    parser.add_argument('--save', metavar='DIR', help='куда сложить собранные выгрузки')
    parser.add_argument('--verbose', action='store_true', help='показывать все предупреждения')
    parser.add_argument('--strict', action='store_true',
                        help='останавливать выгрузку и при потере содержимого')
    args = parser.parse_args(argv)

    if os.path.isdir(args.target):
        paths = [
            os.path.join(args.target, n)
            for n in sorted(os.listdir(args.target))
            if n.lower().endswith(('.drawio', '.xml'))
        ]
    else:
        paths = [args.target]

    if not paths:
        print(f'В «{args.target}» нет ни одного файла .drawio')
        return 2

    results = {p: check_file(p, args.save, args.verbose, args.strict) for p in paths}
    ok = [p for p, v in results.items() if v == 'ok']
    blocked = [p for p, v in results.items() if v == 'blocked']
    failed = [p for p, v in results.items() if v == 'export_error']
    lossy = [p for p in ok if precheck_drawio(_read(p), os.path.basename(p)).data_loss]

    print(f'\n{_RULE}\nИТОГ: файлов {len(paths)}')
    print(f'  выгрузка собрана и принята проверкой: {len(ok)}')
    print(f'      из них с потерей содержимого    : {len(lossy)}')
    for path in lossy:
        print(f'          {os.path.basename(path)}')
    print(f'  остановлены предпроверкой           : {len(blocked)}')
    for path in blocked:
        print(f'      {os.path.basename(path)}')
    print(f'  выгрузка отклонена проверкой        : {len(failed)}')
    for path in failed:
        print(f'      {os.path.basename(path)}')
    return 1 if (blocked or failed) else 0


if __name__ == '__main__':
    raise SystemExit(main())
