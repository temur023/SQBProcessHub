"""
SQB Process Hub — Analytics API Router
Process Mining metrics and conformance analysis
"""
from fastapi import APIRouter, HTTPException
from app.models.process import ProcessetMiningMetrics, TASK_NODE_TYPES
from app.routers.processes import get_store
from app.services.conformance_engine import analyze_process_conformance

router = APIRouter(prefix="/analytics", tags=["analytics & mining"])


@router.get(
    "/{process_id}/mining",
    response_model=ProcessetMiningMetrics,
    summary="Get Process Mining metrics (conformance, bottlenecks, rework, ROI)"
)
def get_mining_metrics(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")
    return process.miningMetrics


@router.post(
    "/{process_id}/mining/recalculate",
    response_model=ProcessetMiningMetrics,
    summary="Re-run conformance analysis with latest data"
)
def recalculate_metrics(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    process.miningMetrics = analyze_process_conformance(
        process.nodes, process.passport, len(process.registry.records)
    )
    return process.miningMetrics


@router.get(
    "/{process_id}/rpa-candidates",
    summary="Return top RPA automation candidates sorted by potential"
)
def get_rpa_candidates(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    candidates = [
        {
            "nodeId": n.id,
            "code": n.code,
            "name": n.name,
            "automationPotential": n.automationPotential or 0,
            "slaMinutes": n.slaMinutes or 0,
            "costPerExecution": n.costPerExecution or 0,
            "role": n.role or "",
            "system": n.system or "",
            "annualSavingsUzs": (n.costPerExecution or 0) * 22 * 12  # estimate: 22 cases/month
        }
        for n in process.nodes
        if n.type not in ('lane', 'startEvent', 'endEvent', 'exclusiveGateway', 'parallelGateway', 'inclusiveGateway')
        and (n.automationPotential or 0) >= 50
    ]
    candidates.sort(key=lambda x: x["automationPotential"], reverse=True)
    return candidates


@router.get(
    "/{process_id}/sla-report",
    summary="Return SLA compliance report per node"
)
def get_sla_report(process_id: str):
    process = get_store().get(process_id)
    if not process:
        raise HTTPException(404, f"Process '{process_id}' not found")

    # Суммируем только исполняемые задачи (без lane/start/end/gateway)
    task_nodes = [n for n in process.nodes if n.type in TASK_NODE_TYPES]
    total_sla = sum(n.slaMinutes or 0 for n in task_nodes)
    target_minutes = process.passport.targetSlaHours * 60
    # Фактический SLA = сумма по задачам, целевой = из паспорта
    actual_minutes = total_sla
    # Оценка с учётом отклонений (бывший *1.38 заменён на прозрачный расчёт)
    estimated_with_breach = round(total_sla * (1 + process.miningMetrics.slaBreachRate / 100)) if total_sla else 0

    return {
        "processName": process.name,
        "targetSlaMinutes": target_minutes,
        "actualSlaMinutes": actual_minutes,
        "estimatedSlaWithBreachMinutes": estimated_with_breach,
        "totalTaskSlaMinutes": total_sla,
        # deprecated alias для обратной совместимости
        "totalTargetSlaMinutes": total_sla,
        "conformanceRate": process.miningMetrics.conformanceRate,
        "slaBreachRate": process.miningMetrics.slaBreachRate,
        "bottleneckSteps": [
            {
                "nodeId": n.id,
                "code": n.code,
                "name": n.name,
                "slaMinutes": n.slaMinutes,
                "slaHours": round((n.slaMinutes or 0) / 60, 2),
                "breachRisk": "high" if (n.slaMinutes or 0) >= 180 else "medium" if (n.slaMinutes or 0) >= 60 else "low"
            }
            for n in process.nodes
            if n.type in TASK_NODE_TYPES
            and (n.slaMinutes or 0) >= 45
        ]
    }
