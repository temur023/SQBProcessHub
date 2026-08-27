import os
import uuid
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from pathlib import Path
import json
import threading

from app.models.process import (
    BusinessProcess, PixRegistryRecord, ProcessValidation
)

router = APIRouter(prefix="/processes", tags=["processes"])

# In-memory store с file-persistence (replace with DB in production)
_process_store: dict[str, BusinessProcess] = {}
_store_lock = threading.Lock()
# SQB_PROCESS_STORE позволяет увести персист в сторону от боевого файла —
# тесты обязаны им пользоваться, иначе каждый прогон дописывает свои фикстуры
# в app/data/process_store.json и файл попадает в коммит.
_store_file = Path(
    os.environ.get("SQB_PROCESS_STORE")
    or Path(__file__).resolve().parent.parent / "data" / "process_store.json"
)


def _load_store():
    try:
        if _store_file.exists():
            data = json.loads(_store_file.read_text(encoding='utf-8'))
            for pid, raw in data.items():
                try:
                    _process_store[pid] = BusinessProcess.model_validate(raw)
                except Exception:
                    continue
    except Exception:
        pass


def _persist_store():
    try:
        _store_file.parent.mkdir(parents=True, exist_ok=True)
        dump = {pid: proc.model_dump(mode='json') for pid, proc in _process_store.items()}
        _store_file.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


# Загружаем сохранённые процессы при импорте модуля
_load_store()


# ──────────────── Response schemas ────────────────

class ProcessSummary(BaseModel):
    id: str
    name: str
    fileName: str
    passportCode: str
    passportStatus: str
    nodeCount: int
    laneCount: int
    conformanceRate: float
    slaHours: float

class CreateCaseBody(BaseModel):
    caseId: str
    assignedTo: str
    data: dict


# ──────────────── Endpoints ────────────────

@router.get("/", response_model=List[ProcessSummary], summary="List all loaded processes")
def list_processes():
    return [
        ProcessSummary(
            id=p.id,
            name=p.name,
            fileName=p.fileName,
            passportCode=p.passport.code,
            passportStatus=p.passport.status,
            nodeCount=len(p.nodes),
            laneCount=len(p.lanes),
            conformanceRate=p.miningMetrics.conformanceRate,
            slaHours=p.passport.targetSlaHours
        )
        for p in _process_store.values()
    ]


@router.get("/{process_id}", response_model=BusinessProcess, summary="Get full process by ID")
def get_process(process_id: str):
    proc = _process_store.get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return proc


@router.post("/", response_model=BusinessProcess, status_code=201, summary="Save a parsed process")
def save_process(process: BusinessProcess):
    if not process.id or not process.id.strip():
        process.id = f"proc_{uuid.uuid4().hex[:8]}"
    # Избегаем коллизии hardcoded sample id (proc-sqb-*) при повторном сохранении — генерируем уникальный id
    if process.id.startswith("proc-sqb-") and process.id in _process_store:
        existing = _process_store[process.id]
        # Если это не тот же самый объект (разный passport.code или разное кол-во нод) — клонируем под новым id
        if existing.passport.code != process.passport.code or len(existing.nodes) != len(process.nodes):
            process.id = f"proc_{uuid.uuid4().hex[:8]}"
    with _store_lock:
        _process_store[process.id] = process
        _persist_store()
    return process


@router.delete("/{process_id}", status_code=204, summary="Delete a process")
def delete_process(process_id: str):
    with _store_lock:
        if process_id not in _process_store:
            raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
        del _process_store[process_id]
        _persist_store()


@router.get("/{process_id}/validate", response_model=List[ProcessValidation], summary="Validate a process")
def validate_process(process_id: str):
    proc = _process_store.get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return proc.validation


@router.post("/{process_id}/registry/cases", response_model=PixRegistryRecord, status_code=201, summary="Create a new registry case")
def create_case(process_id: str, body: CreateCaseBody):
    proc = _process_store.get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")

    # Выбираем первый исполняемый шаг (только задачи), исключаем lane/start/end/gateway
    first_step = next((n for n in proc.nodes if n.type in ('userTask', 'serviceTask', 'task')), None)
    if not first_step:
        first_step = next((n for n in proc.nodes if n.type not in ('lane', 'startEvent', 'endEvent', 'exclusiveGateway', 'parallelGateway', 'inclusiveGateway')), None)
    record = PixRegistryRecord(
        id=f"rec-{uuid.uuid4().hex[:8]}",
        caseId=body.caseId,
        createdAt=datetime.now().strftime('%Y-%m-%d %H:%M'),
        status='in_progress',
        currentStepId=first_step.id if first_step else 'step-1',
        currentStepName=first_step.name if first_step else 'Первичный шаг',
        assignedTo=body.assignedTo,
        elapsedMinutes=0,
        data=body.data
    )
    with _store_lock:
        proc.registry.records.append(record)
        _persist_store()
    return record


@router.get("/{process_id}/registry/cases", response_model=List[PixRegistryRecord], summary="List all registry cases")
def list_cases(process_id: str):
    proc = _process_store.get(process_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")
    return proc.registry.records


def get_store() -> dict:
    """Utility accessor for the in-memory store (for use in other routers)."""
    return _process_store
