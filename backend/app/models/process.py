from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

NodeType = Literal[
    'startEvent',
    'endEvent',
    'task',
    'userTask',
    'serviceTask',      # PIX RPA or ABS integration
    'exclusiveGateway', # XOR
    'parallelGateway',  # AND
    'inclusiveGateway', # OR
    'lane'              # Swimlane
]

StepCategory = Literal[
    'manual',
    'rpa_bot',
    'api_service',
    'approval',
    'validation',
    'notification'
]

class Geometry(BaseModel):
    x: int = 100
    y: int = 100
    width: int = 120
    height: int = 60

class ProcessField(BaseModel):
    id: str
    name: str
    code: str
    type: Literal['string', 'number', 'date', 'boolean', 'select', 'file'] = 'string'
    required: bool = True
    description: Optional[str] = None
    options: Optional[List[str]] = None
    defaultValue: Optional[str] = None

class ProcessNode(BaseModel):
    id: str
    name: str
    type: NodeType = 'userTask'
    category: Optional[StepCategory] = 'manual'
    code: Optional[str] = None  # e.g. STEP-01
    laneId: Optional[str] = None
    laneName: Optional[str] = None
    role: Optional[str] = None
    system: Optional[str] = None
    slaMinutes: Optional[int] = 30
    costPerExecution: Optional[int] = 5000
    automationPotential: Optional[int] = 50
    description: Optional[str] = None
    inputArtifacts: Optional[List[str]] = Field(default_factory=list)
    outputArtifacts: Optional[List[str]] = Field(default_factory=list)
    formFields: Optional[List[ProcessField]] = Field(default_factory=list)
    geometry: Geometry
    style: str = ''

class ProcessEdgePoint(BaseModel):
    x: int
    y: int

class ProcessEdge(BaseModel):
    id: str
    name: str = ''
    sourceId: Optional[str] = None
    targetId: Optional[str] = None
    condition: Optional[str] = None
    probability: Optional[int] = 100
    points: List[ProcessEdgePoint] = Field(default_factory=list)
    exitX: Optional[float] = None
    exitY: Optional[float] = None
    entryX: Optional[float] = None
    entryY: Optional[float] = None
    labelX: Optional[float] = None
    labelY: Optional[float] = None
    style: Optional[str] = None
    dashed: Optional[bool] = None
    dashPattern: Optional[str] = None
    edgeStyle: Optional[str] = None
    strokeColor: Optional[str] = None
    strokeWidth: Optional[float] = None

class ProcessValidation(BaseModel):
    level: Literal['error', 'warning', 'info']
    message: str
    nodeId: Optional[str] = None

class ProcessPassport(BaseModel):
    code: str = 'PRC-SQB-001'
    name: str
    version: str = '1.0'
    status: Literal['draft', 'in_review', 'approved', 'active', 'archived'] = 'draft'
    owner: str = 'Департамент бизнес-процессов АКБ «Узпромстройбанк»'
    department: str = 'Операционный блок'
    category: str = 'Банковские процессы'
    targetSlaHours: float = 24.0
    description: str = ''
    createdDate: str
    updatedDate: str

class PixRegistryRecord(BaseModel):
    id: str
    caseId: str
    createdAt: str
    status: Literal['in_progress', 'completed', 'rejected', 'delayed'] = 'in_progress'
    currentStepId: str
    currentStepName: str
    assignedTo: str
    elapsedMinutes: int = 0
    data: Dict[str, Any] = Field(default_factory=dict)

class PixRegistrySchema(BaseModel):
    id: str
    name: str
    code: str
    description: str
    fields: List[ProcessField] = Field(default_factory=list)
    records: List[PixRegistryRecord] = Field(default_factory=list)

class ProcessetDeviation(BaseModel):
    id: str
    type: Literal['redundant_step', 'sla_breach', 'rework_loop', 'unplanned_path', 'bypass_step']
    title: str
    description: str
    severity: Literal['high', 'medium', 'low'] = 'medium'
    affectedSteps: List[str] = Field(default_factory=list)
    occurrenceCount: int = 0
    totalDelayHours: float = 0.0
    financialImpactUzs: int = 0
    rpaOpportunity: Optional[str] = None

class ProcessetMiningMetrics(BaseModel):
    totalCases: int = 100
    conformanceRate: float = 80.0
    avgLeadTimeHours: float = 24.0
    targetLeadTimeHours: float = 24.0
    slaBreachRate: float = 20.0
    reworkRate: float = 15.0
    potentialRpaSavingsUzs: int = 50000000
    deviations: List[ProcessetDeviation] = Field(default_factory=list)

class BusinessProcess(BaseModel):
    id: str
    name: str
    fileName: str
    passport: ProcessPassport
    nodes: List[ProcessNode] = Field(default_factory=list)
    edges: List[ProcessEdge] = Field(default_factory=list)
    lanes: List[ProcessNode] = Field(default_factory=list)
    validation: List[ProcessValidation] = Field(default_factory=list)
    registry: PixRegistrySchema
    miningMetrics: ProcessetMiningMetrics
