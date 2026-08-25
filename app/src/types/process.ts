export type NodeType =
  | 'startEvent'
  | 'endEvent'
  | 'task'
  | 'userTask'
  | 'serviceTask' // PIX RPA Bot or ABS Integration
  | 'exclusiveGateway' // XOR
  | 'parallelGateway' // AND
  | 'inclusiveGateway' // OR
  | 'lane' // Department / Role Swimlane

export type StepCategory =
  | 'manual'
  | 'rpa_bot'
  | 'api_service'
  | 'approval'
  | 'validation'
  | 'notification'

export interface Geometry {
  x: number
  y: number
  width: number
  height: number
}

export interface ProcessField {
  id: string
  name: string
  code: string
  type: 'string' | 'number' | 'date' | 'boolean' | 'select' | 'file'
  required: boolean
  description?: string
  options?: string[]
  defaultValue?: string
}

export interface ProcessNode {
  id: string
  name: string
  type: NodeType
  category?: StepCategory
  code?: string // e.g. STEP-01
  laneId?: string
  laneName?: string
  role?: string // e.g. Кредитный эксперт, Робот PIX RPA
  system?: string // e.g. АБС ЦФТ, ЕПИГУ, SAP, CRM SQB
  slaMinutes?: number // Target SLA in minutes
  costPerExecution?: number // In UZS
  automationPotential?: number // 0-100%
  description?: string
  inputArtifacts?: string[]
  outputArtifacts?: string[]
  formFields?: ProcessField[]
  geometry: Geometry
  style: string
}

export interface ProcessEdgePoint {
  x: number
  y: number
}

export interface ProcessEdge {
  id: string
  name: string
  sourceId?: string
  targetId?: string
  condition?: string
  probability?: number // 0-100% for branch execution
  points: ProcessEdgePoint[]
  /** mxGraph perimeter constraints, 0..1 of the source/target box */
  exitX?: number
  exitY?: number
  entryX?: number
  entryY?: number
  /** Relative edge-label: x along the polyline (0..1), y perpendicular in px */
  labelX?: number
  labelY?: number
  /** draw.io style */
  style?: string
  dashed?: boolean
  dashPattern?: string
  edgeStyle?: string // e.g. orthogonalEdgeStyle
  strokeColor?: string
  strokeWidth?: number
}

export interface ProcessValidation {
  level: 'error' | 'warning' | 'info'
  message: string
  nodeId?: string
}

export interface ProcessPassport {
  code: string // e.g. PRC-SQB-CRED-001
  name: string
  version: string
  status: 'draft' | 'in_review' | 'approved' | 'active' | 'archived'
  owner: string // Владелец процесса
  department: string // Подразделение
  category: string // Направление (Кредитование, Операционный блок, Валютный контроль и т.д.)
  targetSlaHours: number
  description: string
  createdDate: string
  updatedDate: string
}

export interface PixRegistryRecord {
  id: string
  caseId: string
  createdAt: string
  status: 'in_progress' | 'completed' | 'rejected' | 'delayed'
  currentStepId: string
  currentStepName: string
  assignedTo: string
  elapsedMinutes: number
  data: Record<string, string | number | boolean>
}

export interface PixRegistrySchema {
  id: string
  name: string
  code: string
  description: string
  fields: ProcessField[]
  records: PixRegistryRecord[]
}

// Processet Process Mining comparison models
export interface ProcessetDeviation {
  id: string
  type: 'redundant_step' | 'sla_breach' | 'rework_loop' | 'unplanned_path' | 'bypass_step'
  title: string
  description: string
  severity: 'high' | 'medium' | 'low'
  affectedSteps: string[]
  occurrenceCount: number
  totalDelayHours: number
  financialImpactUzs: number
  rpaOpportunity?: string
}

export interface ProcessetMiningMetrics {
  totalCases: number
  conformanceRate: number // e.g. 74%
  avgLeadTimeHours: number
  targetLeadTimeHours: number
  slaBreachRate: number // e.g. 26%
  reworkRate: number // e.g. 18%
  potentialRpaSavingsUzs: number
  deviations: ProcessetDeviation[]
}

export interface BusinessProcess {
  id: string
  name: string
  fileName: string
  passport: ProcessPassport
  nodes: ProcessNode[]
  edges: ProcessEdge[]
  lanes: ProcessNode[]
  validation: ProcessValidation[]
  registry: PixRegistrySchema
  miningMetrics: ProcessetMiningMetrics
}

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  startEvent: 'Стартовое событие',
  endEvent: 'Завершение процесса',
  task: 'Пользовательская задача',
  userTask: 'Ручная операция',
  serviceTask: 'PIX RPA / Сервис АБС',
  exclusiveGateway: 'Шлюз «ИЛИ» (XOR)',
  parallelGateway: 'Шлюз «И» (AND)',
  inclusiveGateway: 'Шлюз «И/ИЛИ» (OR)',
  lane: 'Дорожка / Подразделение',
}
