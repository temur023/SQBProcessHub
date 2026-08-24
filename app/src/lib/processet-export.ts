import type { BusinessProcess } from '@/types/process'

/**
 * Generates Infomaximum Processet compatible Event Log in CSV format.
 * Format: Case ID, Activity, Start Timestamp, End Timestamp, Resource, Role, Status, Cost UZS, Duration Min, Deviation Flag
 */
export function generateProcessetEventLogCsv(process: BusinessProcess): string {
  const headers = [
    'Case_ID',
    'Activity_Name',
    'Step_Code',
    'Start_Timestamp',
    'End_Timestamp',
    'Duration_Minutes',
    'Resource',
    'Department',
    'System',
    'Status',
    'Cost_UZS',
    'Is_Conformant',
    'Deviation_Type',
  ]

  const rows: string[] = [headers.join(',')]

  // Generate synthetic logs based on process steps and realistic bank case simulations
  const baseDate = new Date('2026-08-01T09:00:00Z')
  const caseCount = Math.max(process.registry.records.length, 30)

  for (let c = 1; c <= caseCount; c++) {
    const caseId = `SQB-2026-${String(c).padStart(4, '0')}`
    let currentCaseTime = new Date(baseDate.getTime() + c * 3600000 * 3)

    // Check if this case has deviations (e.g. 25% of cases have deviations in bank processes)
    const hasDeviation = c % 4 === 0
    const hasReworkLoop = c % 6 === 0
    const hasSlaBreach = c % 5 === 0

    const flowTasks = process.nodes.filter(
      (n) => n.type === 'task' || n.type === 'userTask' || n.type === 'serviceTask',
    )

    flowTasks.forEach((task, idx) => {
      let durationMinutes = task.slaMinutes || 30
      let isConformant = true
      let deviationType = 'None'

      if (hasSlaBreach && idx === Math.floor(flowTasks.length / 2)) {
        // SLA breach
        durationMinutes = durationMinutes * 4.5
        isConformant = false
        deviationType = 'SLA_Breach'
      }

      const startTime = new Date(currentCaseTime)
      const endTime = new Date(startTime.getTime() + durationMinutes * 60000)
      currentCaseTime = new Date(endTime.getTime() + 15 * 60000) // 15 min handover

      const cost = task.costPerExecution || (task.category === 'rpa_bot' ? 500 : 25000)

      rows.push(
        [
          caseId,
          `"${escapeCsv(task.name)}"`,
          task.code || `STEP-${idx + 1}`,
          startTime.toISOString(),
          endTime.toISOString(),
          Math.round(durationMinutes),
          `"${escapeCsv(task.role || 'Сотрудник SQB')}"`,
          `"${escapeCsv(task.laneName || 'Операционный блок')}"`,
          `"${escapeCsv(task.system || 'АБС ЦФТ')}"`,
          'Completed',
          cost,
          isConformant ? 'TRUE' : 'FALSE',
          deviationType,
        ].join(','),
      )

      // Inject rework loop in log for realism
      if (hasReworkLoop && idx === 2) {
        const loopStart = new Date(currentCaseTime)
        const loopEnd = new Date(loopStart.getTime() + 90 * 60000)
        currentCaseTime = loopEnd
        rows.push(
          [
            caseId,
            `"[Возврат на доработку] ${escapeCsv(task.name)}"`,
            `REWORK-${idx + 1}`,
            loopStart.toISOString(),
            loopEnd.toISOString(),
            90,
            `"${escapeCsv(task.role || 'Сотрудник SQB')}"`,
            `"${escapeCsv(task.laneName || 'Операционный блок')}"`,
            `"${escapeCsv(task.system || 'АБС ЦФТ')}"`,
            'Rework',
            cost * 1.5,
            'FALSE',
            'Rework_Loop',
          ].join(','),
        )
      }
    })

    // Add redundant step for non-conformant cases
    if (hasDeviation) {
      const extraStart = new Date(currentCaseTime)
      const extraEnd = new Date(extraStart.getTime() + 120 * 60000)
      rows.push(
        [
          caseId,
          '"[Негласный шаг] Ручная сверка данных в Excel (вне регламента)"',
          'UNPLANNED-EXCEL',
          extraStart.toISOString(),
          extraEnd.toISOString(),
          120,
          '"Сотрудник бэк-офиса"',
          '"Операционный блок"',
          '"MS Excel / Ручной ввод"',
          'Completed',
          35000,
          'FALSE',
          'Redundant_Step',
        ].join(','),
      )
    }
  }

  return rows.join('\n')
}

/**
 * Exports PIX BPM / Registry JSON format
 */
export function generatePixJson(process: BusinessProcess): string {
  const pixData = {
    schemaVersion: '2.4.0',
    platform: 'PIX RPA & Processes',
    organization: 'АКБ «Узпромстройбанк» (SQB Bank)',
    process: {
      code: process.passport.code,
      name: process.passport.name,
      version: process.passport.version,
      status: process.passport.status,
      category: process.passport.category,
      owner: process.passport.owner,
      department: process.passport.department,
      targetSlaHours: process.passport.targetSlaHours,
      description: process.passport.description,
      steps: process.nodes.map((n) => ({
        id: n.id,
        code: n.code,
        name: n.name,
        type: n.type,
        category: n.category,
        role: n.role,
        department: n.laneName,
        system: n.system,
        slaMinutes: n.slaMinutes,
        costPerExecutionUzs: n.costPerExecution,
        automationPotentialPct: n.automationPotential,
        inputArtifacts: n.inputArtifacts,
        outputArtifacts: n.outputArtifacts,
        formFields: n.formFields,
      })),
      transitions: process.edges.map((e) => ({
        id: e.id,
        name: e.name,
        sourceStep: e.sourceId,
        targetStep: e.targetId,
        condition: e.condition,
      })),
      registrySchema: {
        code: process.registry.code,
        name: process.registry.name,
        fields: process.registry.fields,
        recordsCount: process.registry.records.length,
      },
    },
  }

  return JSON.stringify(pixData, null, 2)
}

/**
 * Generates Excel-ready CSV for Process Regulation / Matrix (Регламент процесса банка)
 */
export function generateProcessRegulationCsv(process: BusinessProcess): string {
  const headers = [
    '№ Шага',
    'Код',
    'Наименование операции',
    'Тип операции',
    'Подразделение / Дорожка',
    'Исполнитель / Роль',
    'ИТ-Система',
    'Норматив SLA (мин)',
    'Входящие документы / Данные',
    'Результат операции (Выход)',
    'Потенциал роботизации (PIX RPA)',
  ]

  const rows: string[] = [headers.join(';')]

  let stepNum = 1
  process.nodes
    .filter((n) => n.type !== 'lane' && n.type !== 'exclusiveGateway' && n.type !== 'parallelGateway' && n.type !== 'inclusiveGateway')
    .forEach((node) => {
      const num = stepNum++
      rows.push(
        [
          num,
          node.code || `STEP-${String(num).padStart(2, '0')}`,
          `"${escapeCsv(node.name)}"`,
          `"${escapeCsv(node.category || node.type)}"`,
          `"${escapeCsv(node.laneName || 'Основное подразделение')}"`,
          `"${escapeCsv(node.role || 'Сотрудник банка')}"`,
          `"${escapeCsv(node.system || 'АБС ЦФТ')}"`,
          node.slaMinutes || 30,
          `"${escapeCsv((node.inputArtifacts || []).join(', ') || 'Заявка')}"`,
          `"${escapeCsv((node.outputArtifacts || []).join(', ') || 'Статус/Документ')}"`,
          `${node.automationPotential || 0}%`,
        ].join(';'),
      )
    })

  return '\uFEFF' + rows.join('\n') // UTF-8 BOM for Excel Cyrillic compatibility
}

function escapeCsv(str: string): string {
  if (!str) return ''
  return str.replace(/"/g, '""').replace(/\n/g, ' ')
}

export function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
