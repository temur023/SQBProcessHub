import io
import re
from urllib.parse import quote

from fastapi import APIRouter, File, UploadFile, HTTPException, Body
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.pmm_exporter import generate_pmm_zip
from app.services.exporters import generate_event_log_csv, generate_regulation_csv
from app.services.export_validation import (
    ExportCheck,
    summary_line,
    validate_bpmn_xml,
    validate_pmm_package,
)
from app.models.process import BusinessProcess
from app.routers.processes import get_store, _persist_store, _store_lock

router = APIRouter(prefix="/import", tags=["import & export"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _sanitize_filename(filename: str) -> str:
    """Удаляет path traversal и недопустимые символы, гарантирует безопасное имя файла."""
    # Убираем директории: оставляем только basename
    basename = filename.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    # Заменяем недопустимые символы, убираем ведущие точки/дефисы (скрытые файлы)
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', basename).strip('._')
    # Ограничиваем длину
    safe = safe[:120] if len(safe) > 120 else safe
    return safe or 'export'


def attachment_headers(filename: str) -> dict:
    ascii_name = _sanitize_filename(filename)
    # quote для RFC5987 filename* (UTF-8)
    quoted = quote(_sanitize_filename(filename), safe='')
    # На случай если исходное имя содержало не-ASCII, также экранируем для filename*
    utf8_quoted = quote(filename, safe='')
    # Используем sanitized ascii_name для fallback filename, оригинальное quote для filename*
    return {
        'Content-Disposition': f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_quoted}"
    }


class XmlImportBody(BaseModel):
    xml: str
    fileName: str = "Pasted_Process.drawio"


def _check_headers(check: ExportCheck, base: dict) -> dict:
    """Итог проверки файла — в заголовках ответа рядом с самим файлом.

    Скачивание не блокируем: файл нужен сотруднику в любом случае, а если в нём
    что-то не так, платформа скажет об этом раньше, чем PIX, — и адресно.
    """
    return {
        **base,
        'X-Export-Check': summary_line([check]),
        'X-Export-Check-Errors': str(len(check.errors)),
    }


# ──────────── Import ────────────

@router.post(
    "/file",
    response_model=BusinessProcess,
    summary="Upload a .drawio / .bpmn / .xml file and parse it into a BusinessProcess"
)
async def import_file(file: UploadFile = File(...)):
    allowed = {'.drawio', '.xml', '.bpmn', '.txt'}
    raw_name = file.filename or ''
    if '.' in raw_name:
        ext = '.' + raw_name.rsplit('.', 1)[-1].lower()
        # нормализуем: убираем пробелы, оставляем только точку+расширение
        ext = '.' + ext.lstrip('.').strip()
    else:
        ext = ''
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type '{ext or '(no extension)'}'. Allowed: {', '.join(sorted(allowed))}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл слишком большой (макс. 10 МБ)")

    text = content.decode('utf-8', errors='ignore')

    if not text.strip():
        raise HTTPException(400, "Uploaded file is empty")

    try:
        process = parse_drawio_xml(text, file.filename or 'process.drawio')
    except Exception as e:
        raise HTTPException(422, f"Ошибка парсинга: {str(e)}")

    # Auto-save to in-memory store с персистом
    with _store_lock:
        get_store()[process.id] = process
        _persist_store()
    return process


@router.post(
    "/xml",
    response_model=BusinessProcess,
    summary="Parse draw.io / BPMN XML from raw string body"
)
def import_xml(body: XmlImportBody):
    if not body.xml.strip():
        raise HTTPException(400, "XML body is empty")
    if len(body.xml.encode('utf-8')) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "XML слишком большой (макс. 10 МБ)")
    try:
        process = parse_drawio_xml(body.xml, body.fileName)
    except Exception as e:
        raise HTTPException(422, f"Ошибка парсинга: {str(e)}")

    with _store_lock:
        get_store()[process.id] = process
        _persist_store()
    return process


# ──────────── Export ────────────

@router.get(
    "/{process_id}/export/bpmn",
    summary="Export process map as BPMN 2.0 XML for PIX Process Studio / Processet"
)
def export_bpmn(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    xml = generate_bpmn_xml(process)
    filename = f"{_sanitize_filename(process.passport.code)}_PIX_Map.bpmn"
    return Response(
        content=xml.encode('utf-8'),
        media_type='application/xml',
        headers=_check_headers(validate_bpmn_xml(xml), attachment_headers(filename)),
    )


@router.get(
    "/{process_id}/export/pmm",
    summary="Export process map as PIX Process Studio native .pmm package"
)
def export_pmm(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    payload = generate_pmm_zip(process)
    filename = f"{_sanitize_filename(process.passport.code)}_PIX_Map.pmm"
    return Response(
        content=payload,
        media_type='application/zip',
        headers=_check_headers(validate_pmm_package(payload), attachment_headers(filename)),
    )


@router.get(
    "/{process_id}/export/event-log",
    summary="Export Event Log CSV for Processet (XES-compatible)"
)
def export_event_log(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    csv_data = generate_event_log_csv(process)
    filename = f"{_sanitize_filename(process.passport.code)}_EventLog.csv"
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type='text/csv',
        headers=attachment_headers(filename)
    )


@router.get(
    "/{process_id}/export/regulation",
    summary="Export process regulation matrix as Excel-compatible CSV"
)
def export_regulation(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    csv_data = generate_regulation_csv(process)
    filename = f"{_sanitize_filename(process.passport.code)}_Regulation.csv"
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type='text/csv',
        headers=attachment_headers(filename)
    )


@router.get(
    "/{process_id}/export/check",
    summary="Проверить выгрузки процесса теми же правилами, по которым их читает PIX"
)
def check_exports(process_id: str):
    """Отчёт о готовности файлов к загрузке в PIX Процессную студию.

    Студия отвергает пакет целиком из-за одного дефекта и называет только код
    ошибки. Здесь те же проверки делаются заранее и с указанием фигуры.
    """
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    checks = [
        validate_bpmn_xml(generate_bpmn_xml(process)),
        validate_pmm_package(generate_pmm_zip(process)),
    ]
    return {
        'processId': process_id,
        'ok': all(c.ok for c in checks),
        'summary': summary_line(checks),
        'formats': [
            {
                'format': c.format,
                'ok': c.ok,
                'errors': len(c.errors),
                'warnings': len(c.warnings),
                'problems': [
                    {'level': p.level, 'code': p.code, 'message': p.message, 'where': p.where}
                    for p in c.problems
                ],
            }
            for c in checks
        ],
    }


@router.get(
    "/{process_id}/export/pix-json",
    summary="Export PIX BPM Registry JSON for PIX platform import"
)
def export_pix_json(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    import json
    registry_data = process.registry.model_dump()
    registry_data['processCode'] = process.passport.code
    registry_data['processName'] = process.name
    filename = f"{_sanitize_filename(process.passport.code)}_PIX_Registry.json"
    return Response(
        content=json.dumps(registry_data, ensure_ascii=False, indent=2).encode('utf-8'),
        media_type='application/json',
        headers=attachment_headers(filename)
    )
