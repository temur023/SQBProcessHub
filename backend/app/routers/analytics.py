"""
SQB Process Hub — Analytics API Router
Process Mining metrics and conformance analysis
"""
from fastapi import APIRouter, HTTPException
from app.models.process import ProcessetMiningMetrics
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
        if n.type not in ('lane', 'startEvent', 'endEvent')
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

    total_sla = sum(n.slaMinutes or 0 for n in process.nodes if n.type not in ('lane',))
    target_minutes = process.passport.targetSlaHours * 60

    return {
        "processName": process.name,
        "targetSlaMinutes": target_minutes,
        "actualSlaMinutes": round(total_sla * 1.38),
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
            if n.type not in ('lane', 'startEvent', 'endEvent')
            and (n.slaMinutes or 0) >= 45
        ]
    }
