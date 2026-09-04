from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

NodeType = Literal[
    # События (2-ILOVA: Hodisa / Events)
    'startEvent',
    'endEvent',
    'intermediateTimerEvent',    # Kutish vaqti — ожидание внутри потока
    'intermediateMessageEvent',  # приём/отправка сообщения
    # Действия (2-ILOVA: Harakatlar / Activity)
    'task',
    'userTask',
    'serviceTask',               # PIX RPA or ABS integration
    'subProcess',
    # Шлюзы (2-ILOVA: Shlyuz / Gateway)
    'exclusiveGateway',          # XOR
    'parallelGateway',           # AND
    'inclusiveGateway',          # OR
    'complexGateway',            # сложное условие: в draw.io — ромб со звёздочкой
    # Артефакты (2-ILOVA: Artefaktlar / Artifacts)
    'dataStore',                 # IABS, EHA, EDO — информационная система
    'dataObject',                # Dalolatnoma, Yig'ma jild — документ
    'textAnnotation',            # текстовое примечание
    # Зоны ответственности
    'lane'                       # Swimlane
]

#: Как тип фигуры называется в интерфейсе и в отчёте о качестве импорта.
#: Двойник `NODE_TYPE_LABELS` из `app/src/types/process.ts`.
NODE_TYPE_LABELS: Dict[str, str] = {
    'startEvent': 'Стартовое событие',
    'endEvent': 'Завершение процесса',
    'intermediateTimerEvent': 'Ожидание (таймер)',
    'intermediateMessageEvent': 'Промежуточное событие-сообщение',
    'task': 'Пользовательская задача',
    'userTask': 'Ручная операция',
    'serviceTask': 'PIX RPA / Сервис АБС',
    'subProcess': 'Подпроцесс',
    'exclusiveGateway': 'Шлюз «ИЛИ» (XOR)',
    'parallelGateway': 'Шлюз «И» (AND)',
    'inclusiveGateway': 'Шлюз «И/ИЛИ» (OR)',
    'complexGateway': 'Сложный шлюз',
    'dataStore': 'Информационная система',
    'dataObject': 'Документ / объект данных',
    'textAnnotation': 'Примечание',
    'lane': 'Дорожка / Подразделение',
}

#: Узлы, которые участвуют в потоке управления (могут иметь sequenceFlow).
FLOW_NODE_TYPES = (
    'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
    'task', 'userTask', 'serviceTask', 'subProcess',
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway',
)

#: Узлы-артефакты: соединяются только ассоциациями, шагами процесса не являются.
ARTIFACT_NODE_TYPES = ('dataStore', 'dataObject', 'textAnnotation')

#: Узлы, которые считаются шагами регламента (4-ILOVA).
TASK_NODE_TYPES = ('task', 'userTask', 'serviceTask', 'subProcess')

#: Шлюзы — точки ветвления потока. Двойник `GATEWAY_NODE_TYPES` из
#: `app/src/types/process.ts`.
GATEWAY_NODE_TYPES = (
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway',
)

#: Вид соединения (2-ILOVA: Birlashtiruvchi elementlar / Flows).
#: ``annotationLine`` — оформительская линия draw.io, у которой хотя бы один
#: конец висит в пустоте (разделители этапов). В BPMN и PIX такой конструкции
#: нет, поэтому в выгрузку она не идёт, но на холсте рисуется.
EdgeKind = Literal['sequenceFlow', 'messageFlow', 'association', 'annotationLine']

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
    slaMinutes: Optional[int] = 30       # ST — время выполнения операции, мин (4-ILOVA)
    #: Время СНЯТО С КАРТЫ, а не подставлено импортом.
    #:
    #: Когда в подписи шага времени нет, импорт ставит правдоподобное значение
    #: по категории (ручная операция — час, согласование — три). Для расчёта SLA
    #: это разумно, но на карте такое время рисовать нельзя: аналитик видит у
    #: шага часы с цифрой «1 ч» и считает, что кто-то её замерил.
    slaMeasured: bool = False
    waitMinutes: Optional[int] = 0       # WT — время ожидания перед операцией, мин (4-ILOVA)
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
    kind: EdgeKind = 'sequenceFlow'
    sourceId: Optional[str] = None
    targetId: Optional[str] = None
    condition: Optional[str] = None
    probability: Optional[int] = 100
    points: List[ProcessEdgePoint] = Field(default_factory=list)
    #: Свободные концы линии в абсолютных координатах карты: заполняются, когда
    #: в draw.io конец не привязан к фигуре (mxPoint as="sourcePoint"/"targetPoint").
    sourcePoint: Optional[ProcessEdgePoint] = None
    targetPoint: Optional[ProcessEdgePoint] = None
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
    """Замечание к импортированной карте — то, что показывается сотруднику."""
    level: Literal['error', 'warning', 'info']
    message: str
    nodeId: Optional[str] = None
    #: Машинный код замечания: по нему UI группирует однотипные записи.
    code: Optional[str] = None
    #: Имя фигуры на карте — чтобы сотрудник нашёл её без поиска по id.
    nodeName: Optional[str] = None
    #: Все фигуры, которых касается замечание: по нему платформа подсвечивает
    #: на карте не одну фигуру, а всю группу («расширены 226 фигур» — какие?).
    nodeIds: Optional[List[str]] = None
    #: Что с этим делать: замечание без подсказки бесполезно.
    hint: Optional[str] = None

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
