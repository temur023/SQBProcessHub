import csv
import io
from datetime import datetime
from app.models.process import BusinessProcess

def generate_event_log_csv(process: BusinessProcess) -> str:
    """Generates XES-compatible Event Log CSV for Infomaximum Processet."""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)

    writer.writerow(['Case_ID', 'Activity_Name', 'Activity_Code', 'Role', 'Resource',
                     'Start_Timestamp', 'End_Timestamp', 'Duration_Min', 'Cost_UZS',
                     'System', 'Is_Conformant', 'Status', 'Lane'])

    base_time = datetime(2026, 8, 1, 9, 0, 0)
    offset_min = 0

    flow_nodes = [n for n in process.nodes if n.type not in ('lane',)]
    for record in process.registry.records:
        elapsed = offset_min
        for node in flow_nodes:
            if node.type in ('exclusiveGateway', 'parallelGateway', 'inclusiveGateway'):
                continue
            sla = node.slaMinutes or 30
            start = base_time.replace(hour=9) if elapsed < 0 else datetime(2026, 8, 1, 9, 0)
            start_str = datetime.now().replace(microsecond=0).isoformat()
            end_str = datetime.now().replace(microsecond=0).isoformat()
            conformant = 'TRUE'
            writer.writerow([
                record.caseId,
                node.name,
                node.code or '',
                node.role or '',
                node.role or 'Сотрудник',
                start_str,
                end_str,
                sla,
                node.costPerExecution or 0,
                node.system or '',
                conformant,
                record.status,
                node.laneName or ''
            ])
        offset_min += 1

    return output.getvalue()


def generate_regulation_csv(process: BusinessProcess) -> str:
    """Generates process regulation matrix as CSV (Excel-compatible with UTF-8 BOM)."""
    output = io.StringIO()
    # UTF-8 BOM for Excel compatibility
    output.write('\ufeff')
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)

    writer.writerow([
        '№', 'Код шага', 'Наименование операции', 'Тип', 'Исполнитель (Роль)',
        'Дорожка (Подразделение)', 'ИТ-система', 'SLA (мин)', 'SLA (ч)',
        'Себестоимость (UZS)', 'Потенциал PIX RPA (%)', 'Категория'
    ])

    idx = 1
    for node in process.nodes:
        if node.type in ('lane', 'exclusiveGateway', 'parallelGateway', 'inclusiveGateway'):
            continue
        writer.writerow([
            idx,
            node.code or '-',
            node.name,
            node.type,
            node.role or '-',
            node.laneName or '-',
            node.system or '-',
            node.slaMinutes or 0,
            round((node.slaMinutes or 0) / 60, 2),
            node.costPerExecution or 0,
            node.automationPotential or 0,
            node.category or '-'
        ])
        idx += 1

    return output.getvalue()
