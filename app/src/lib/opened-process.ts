import type {
  BusinessProcess,
  PixRegistrySchema,
  ProcessEdge,
  ProcessNode,
  ProcessPassport,
} from '@/types/process'
import { isTaskNode } from '@/types/process'
import { analyzeProcessConformance } from './conformance'
import { collectImportDiagnostics } from './diagnostics'

/**
 * Сборка `BusinessProcess` для карты, открытой из готового файла выгрузки.
 *
 * Общая часть читателей `.bpmn` и `.pmm`: паспорт, реестр и метрики нужны
 * модели, но к содержимому выгрузки отношения не имеют. Геометрия при этом
 * НЕ нормализуется (в отличие от импорта draw.io): смысл просмотра выгрузки в
 * том, чтобы увидеть файл ровно таким, каким его получит Процессная студия, а
 * не таким, каким его перерисовала бы платформа.
 */
export interface OpenedProcessInput {
  fileName: string
  processName: string
  /** Код паспорта, если он читается из файла. */
  passportCode?: string
  /** Откуда карта: попадает в описание паспорта. */
  sourceLabel: string
  nodes: ProcessNode[]
  lanes: ProcessNode[]
  edges: ProcessEdge[]
}

function randomSuffix(): string {
  return (crypto.randomUUID?.() ?? Math.random().toString(16).slice(2)).slice(0, 8).toUpperCase()
}

export function assembleOpenedProcess(input: OpenedProcessInput): BusinessProcess {
  const { fileName, processName, sourceLabel, nodes, lanes, edges } = input
  const steps = nodes.filter((n) => isTaskNode(n.type))
  const totalMinutes = steps.reduce((acc, n) => acc + (n.slaMinutes || 0) + (n.waitMinutes || 0), 0)
  const firstStep = steps[0] || nodes[0]

  const code =
    input.passportCode && /^PRC-/.test(input.passportCode)
      ? input.passportCode
      : `PRC-SQB-${randomSuffix()}`

  const passport: ProcessPassport = {
    code,
    name: processName,
    version: '1.0',
    status: 'draft',
    owner: 'Департамент бизнес-процессов АКБ «Узпромстройбанк»',
    department: lanes[0]?.name || 'Операционный блок',
    category: 'Банковские процессы',
    targetSlaHours: Math.max(1, Math.round((totalMinutes / 60) * 10) / 10),
    description: `Просмотр выгрузки ${sourceLabel}: файл ${fileName}.`,
    createdDate: new Date().toISOString().split('T')[0],
    updatedDate: new Date().toISOString().split('T')[0],
  }

  const registry: PixRegistrySchema = {
    id: `reg-${randomSuffix()}`,
    code: `REG_${passport.code.replace(/[^a-zA-Z0-9_]/g, '_')}`,
    name: `Реестр: ${processName}`,
    description: `Реестр заявок по процессу ${processName}`,
    fields: [
      { id: 'f1', code: 'case_number', name: 'Номер заявки', type: 'string', required: true },
      { id: 'f2', code: 'client_inn', name: 'ИНН клиента', type: 'string', required: true },
      { id: 'f3', code: 'status', name: 'Статус', type: 'select', required: true, options: ['В работе', 'Одобрено', 'Отклонено'] },
    ],
    records: [],
  }

  const process: BusinessProcess = {
    id: `opened_${randomSuffix().toLowerCase()}`,
    name: processName,
    fileName,
    passport,
    nodes,
    edges,
    lanes,
    validation: collectImportDiagnostics(nodes, lanes, edges),
    registry,
    miningMetrics: {
      totalCases: 0,
      conformanceRate: 0,
      avgLeadTimeHours: passport.targetSlaHours,
      targetLeadTimeHours: passport.targetSlaHours,
      slaBreachRate: 0,
      reworkRate: 0,
      potentialRpaSavingsUzs: 0,
      deviations: [],
    },
  }
  process.miningMetrics = analyzeProcessConformance(process)
  if (firstStep) {
    registry.records.push({
      id: 'rec-preview',
      caseId: 'PREVIEW-0001',
      createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
      status: 'in_progress',
      currentStepId: firstStep.id,
      currentStepName: firstStep.name,
      assignedTo: firstStep.role || 'Сотрудник банка',
      elapsedMinutes: 0,
      data: { case_number: 'PREVIEW-0001', client_inn: '—', status: 'В работе' },
    })
  }
  return process
}
