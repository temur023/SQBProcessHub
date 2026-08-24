import io
from fastapi import APIRouter, File, UploadFile, HTTPException, Body
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.exporters import generate_event_log_csv, generate_regulation_csv
from app.models.process import BusinessProcess
from app.routers.processes import get_store

router = APIRouter(prefix="/import", tags=["import & export"])


class XmlImportBody(BaseModel):
    xml: str
    fileName: str = "Pasted_Process.drawio"


# ──────────── Import ────────────

@router.post(
    "/file",
    response_model=BusinessProcess,
    summary="Upload a .drawio / .bpmn / .xml file and parse it into a BusinessProcess"
)
async def import_file(file: UploadFile = File(...)):
    allowed = {'.drawio', '.xml', '.bpmn', '.txt'}
    ext = '.' + (file.filename or '').rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {', '.join(allowed)}")

    content = await file.read()
    text = content.decode('utf-8', errors='ignore')

    if not text.strip():
        raise HTTPException(400, "Uploaded file is empty")

    try:
        process = parse_drawio_xml(text, file.filename or 'process.drawio')
    except Exception as e:
        raise HTTPException(422, f"Ошибка парсинга: {str(e)}")

    # Auto-save to in-memory store
    get_store()[process.id] = process
    return process


@router.post(
    "/xml",
    response_model=BusinessProcess,
    summary="Parse draw.io / BPMN XML from raw string body"
)
def import_xml(body: XmlImportBody):
    if not body.xml.strip():
        raise HTTPException(400, "XML body is empty")
    try:
        process = parse_drawio_xml(body.xml, body.fileName)
    except Exception as e:
        raise HTTPException(422, f"Ошибка парсинга: {str(e)}")

    get_store()[process.id] = process
    return process


# ──────────── Export ────────────

@router.get(
    "/{process_id}/export/bpmn",
    summary="Export process as BPMN 2.0 XML for Infomaximum Processet"
)
def export_bpmn(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    xml = generate_bpmn_xml(process)
    filename = f"{process.passport.code}_{process.name.replace(' ', '_')}.bpmn"
    return Response(
        content=xml.encode('utf-8'),
        media_type='application/xml',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
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
    filename = f"{process.passport.code}_EventLog.csv"
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
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
    filename = f"{process.passport.code}_Regulation.csv"
    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


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
    filename = f"{process.passport.code}_PIX_Registry.json"
    return Response(
        content=json.dumps(registry_data, ensure_ascii=False, indent=2).encode('utf-8'),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )
