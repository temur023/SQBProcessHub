import csv
import io
from datetime import datetime, timedelta
from app.models.process import ARTIFACT_NODE_TYPES, BusinessProcess, TASK_NODE_TYPES


def generate_event_log_csv(process: BusinessProcess) -> str:
    """Generates Infomaximum Processet compatible Event Log CSV (unified with frontend)."""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Унифицированный заголовок с фронтендом (processet-export.ts)
    writer.writerow(['Case_ID', 'Activity_Name', 'Step_Code', 'Start_Timestamp', 'End_Timestamp',
                     'Duration_Minutes', 'Resource', 'Department', 'System', 'Status', 'Cost_UZS',
                     'Is_Conformant', 'Deviation_Type'])

    def _esc(v: str) -> str:
        return (v or '').replace('"', '""')

    flow_tasks = [n for n in process.nodes if n.type in TASK_NODE_TYPES]
    # Синтетические кейсы как во фронтенде: минимум 30 для репрезентативности
    case_count = max(len(process.registry.records), 30)
    base = datetime(2026, 8, 1, 9, 0, 0)

    for c in range(1, case_count + 1):
        case_id = f"SQB-2026-{str(c).zfill(4)}"
        cursor = base + timedelta(hours=c * 3)
        has_deviation = c % 4 == 0
        has_rework = c % 6 == 0
        has_sla_breach = c % 5 == 0

        for idx, task in enumerate(flow_tasks):
            duration = task.slaMinutes or 30
            is_conformant = True
            deviation_type = 'None'
            if has_sla_breach and idx == len(flow_tasks) // 2:
                duration = int(duration * 4.5)
                is_conformant = False
                deviation_type = 'SLA_Breach'
            start = cursor
            end = cursor + timedelta(minutes=duration)
            cursor = end + timedelta(minutes=15)
            cost = task.costPerExecution or (500 if task.category == 'rpa_bot' else 25000)
            writer.writerow([
                case_id,
                task.name,
                task.code or f"STEP-{idx+1:02d}",
                start.isoformat(timespec='seconds'),
                end.isoformat(timespec='seconds'),
                round(duration),
                task.role or 'Сотрудник SQB',
                task.laneName or 'Операционный блок',
                task.system or 'АБС ЦФТ',
                'Completed',
                cost,
                'TRUE' if is_conformant else 'FALSE',
                deviation_type
            ])
            if has_rework and idx == 2:
                loop_start = cursor
                loop_end = cursor + timedelta(minutes=90)
                cursor = loop_end
                writer.writerow([
                    case_id,
                    f"[Возврат на доработку] {task.name}",
                    f"REWORK-{idx+1}",
                    loop_start.isoformat(timespec='seconds'),
                    loop_end.isoformat(timespec='seconds'),
                    90,
                    task.role or 'Сотрудник SQB',
                    task.laneName or 'Операционный блок',
                    task.system or 'АБС ЦФТ',
                    'Rework',
                    int(cost * 1.5),
                    'FALSE',
                    'Rework_Loop'
                ])
        if has_deviation:
            extra_start = cursor
            extra_end = cursor + timedelta(minutes=120)
            writer.writerow([
                case_id,
                "[Негласный шаг] Ручная сверка данных в Excel (вне регламента)",
                'UNPLANNED-EXCEL',
                extra_start.isoformat(timespec='seconds'),
                extra_end.isoformat(timespec='seconds'),
                120,
                'Сотрудник бэк-офиса',
                'Операционный блок',
                'MS Excel / Ручной ввод',
                'Completed',
                35000,
                'FALSE',
                'Redundant_Step'
            ])

    return output.getvalue()


def generate_regulation_csv(process: BusinessProcess) -> str:
    """Generates process regulation matrix as CSV (Excel-compatible; BOM добавляет роутер через utf-8-sig)."""
    output = io.StringIO()
    # Используем ; как разделитель для Excel RU и QUOTE_MINIMAL
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, delimiter=';', lineterminator='\n')

    # Колонки соответствуют таблице анализа AS-IS Методики (4-ILOVA).
    writer.writerow([
        '№ Шага', 'Код', 'Наименование операции', 'Тип операции',
        'Подразделение / Дорожка', 'Исполнитель / Роль', 'ИТ-Система',
        'ST — время операции (мин)', 'WT — время ожидания (мин)', 'TCT — итого (мин)',
        'Входящие документы / Данные', 'Результат операции (Выход)',
        'Потенциал роботизации (PIX RPA)'
    ])

    idx = 1
    # В регламент идут только операции: дорожки, шлюзы и артефакты
    # (хранилища данных, документы, примечания) шагами процесса не являются.
    skip = ('lane', 'exclusiveGateway', 'parallelGateway', 'inclusiveGateway') + ARTIFACT_NODE_TYPES
    for node in process.nodes:
        if node.type in skip:
            continue
        # Экранируем кавычки для csv
        writer.writerow([
            idx,
            node.code or f'STEP-{idx:02d}',
            node.name,
            node.category or node.type,
            node.laneName or 'Основное подразделение',
            node.role or 'Сотрудник банка',
            node.system or 'АБС ЦФТ',
            node.slaMinutes or 30,
            node.waitMinutes or 0,
            (node.slaMinutes or 30) + (node.waitMinutes or 0),
            ', '.join(node.inputArtifacts or []) or 'Заявка',
            ', '.join(node.outputArtifacts or []) or 'Статус/Документ',
            f"{node.automationPotential or 0}%"
        ])
        idx += 1

    return output.getvalue()
