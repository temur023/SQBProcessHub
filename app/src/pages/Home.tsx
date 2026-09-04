import { useState, useEffect } from 'react'
import { sqbCreditProcess, cloneSampleProcess } from '@/lib/sample-processes'
import type { BusinessProcess, ProcessNode } from '@/types/process'
import { Header } from '@/components/Header'
import { ProcessVisualizer } from '@/components/ProcessVisualizer'
import { StepMatrixTable } from '@/components/StepMatrixTable'
import { PixRegistryView } from '@/components/PixRegistryView'
import { ProcessetAnalyticsView } from '@/components/ProcessetAnalyticsView'
import { ProcessImportModal } from '@/components/ProcessImportModal'
import { ImportReport } from '@/components/ImportReport'
import type { CanvasFocus } from '@/components/ProcessVisualizer'
import { NodeDetailDrawer } from '@/components/NodeDetailDrawer'
import { ExportDrawer } from '@/components/ExportDrawer'
import { generateProcessRegulationCsv, downloadFile } from '@/lib/processet-export'
import { saveProcessToBackend, listProcessesFromBackend, loadProcessFromBackend } from '@/lib/api'
import { analyzeProcessConformance } from '@/lib/conformance'
import { toast } from 'sonner'
import { Toaster } from '@/components/ui/sonner'
import { Database } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { isTaskNode } from '@/types/process'

export default function Home() {
  const [currentProcess, setCurrentProcess] = useState<BusinessProcess>(() => cloneSampleProcess(sqbCreditProcess))
  const [activeTab, setActiveTab] = useState<string>('visualizer')
  const [isImportOpen, setIsImportOpen] = useState(false)
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState<ProcessNode | null>(null)
  /** Замечание, на фигуры которого сейчас наведена карта. */
  const [canvasFocus, setCanvasFocus] = useState<CanvasFocus | null>(null)
  const [activeIssueKey, setActiveIssueKey] = useState<string>('')
  const [backendProcesses, setBackendProcesses] = useState<Array<{id: string, name: string}>>([])
  const [selectedBackendProcess, setSelectedBackendProcess] = useState<string>('')

  useEffect(() => {
    void saveProcessToBackend(currentProcess)
    // Persist the default template once so backend export endpoints resolve.
    // Subsequent edits save explicitly from handlers below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void listProcessesFromBackend().then(setBackendProcesses)
  }, [])

  const handleLoadBackendProcess = async (processId: string) => {
    if (!processId) return
    const proc = await loadProcessFromBackend(processId)
    if (proc) {
      handleProcessLoaded(proc)
      setSelectedBackendProcess(processId)
    }
  }

  const handleProcessLoaded = (newProcess: BusinessProcess) => {
    setCurrentProcess(newProcess)
    setSelectedNode(null)
    // Подсветка привязана к фигурам прошлой карты: на новой её id ничего не значат.
    setCanvasFocus(null)
    setActiveIssueKey('')
    void saveProcessToBackend(newProcess)
    const issues = newProcess.validation ?? []
    const errors = issues.filter((i) => i.level === 'error').length
    const warnings = issues.filter((i) => i.level === 'warning').length
    const summary = `Создан регламент на ${newProcess.nodes.length} шагов и реестр PIX.`
    // Замечания к карте показываем сразу: молча импортировать неполную схему
    // нельзя — ошибка дойдёт до регламента и до выгрузки в PIX.
    if (errors || warnings) {
      toast.warning(`Процесс «${newProcess.name}» загружен с замечаниями`, {
        description:
          `${summary} Ошибок: ${errors}, предупреждений: ${warnings}. ` +
          'Подробности — в панели «Проверка импорта» над картой.',
        duration: 8000,
      })
    } else {
      toast.success(`Бизнес-процесс «${newProcess.name}» успешно загружен!`, {
        description: summary,
      })
    }
  }

  const handleSaveNode = (updatedNode: ProcessNode) => {
    const updatedNodes = currentProcess.nodes.map((n) =>
      n.id === updatedNode.id ? updatedNode : n,
    )
    // Пересчитываем паспорт SLA и метрики conformance
    const totalSlaMin = updatedNodes
      .filter((n) => isTaskNode(n.type))
      .reduce((acc, n) => acc + (n.slaMinutes || 0), 0)
    const newTargetHours = Math.max(1, Math.round((totalSlaMin / 60) * 10) / 10)
    const interim: BusinessProcess = {
      ...currentProcess,
      nodes: updatedNodes,
      passport: { ...currentProcess.passport, targetSlaHours: newTargetHours, updatedDate: new Date().toISOString().split('T')[0] },
    }
    const updated: BusinessProcess = {
      ...interim,
      miningMetrics: analyzeProcessConformance(interim),
      validation: (() => {
        // Локальная валидация start/end + orphan
        const starts = updatedNodes.filter((n) => n.type === 'startEvent').length
        const ends = updatedNodes.filter((n) => n.type === 'endEvent').length
        const issues: BusinessProcess['validation'] = []
        if (starts === 0) issues.push({ level: 'error', message: 'Отсутствует стартовое событие процесса' })
        if (ends === 0) issues.push({ level: 'warning', message: 'Отсутствует событие успешного завершения' })
        // orphan check
        const edgeSrc = new Set(currentProcess.edges.map((e) => e.sourceId))
        const edgeTgt = new Set(currentProcess.edges.map((e) => e.targetId))
        for (const n of updatedNodes) {
          if (n.type === 'lane' || n.type === 'startEvent') {
            // start не требует входа
          } else if (!edgeTgt.has(n.id)) {
            issues.push({ level: 'error', message: `Шаг «${n.name}» не имеет входящих переходов`, nodeId: n.id })
          }
          if (n.type !== 'endEvent' && n.type !== 'lane' && !edgeSrc.has(n.id)) {
            issues.push({ level: 'warning', message: `Шаг «${n.name}» не имеет исходящих переходов`, nodeId: n.id })
          }
        }
        return issues
      })(),
    }
    setCurrentProcess(updated)
    void saveProcessToBackend(updated)
    setSelectedNode(null)
    toast.success(`Шаг «${updatedNode.name}» успешно обновлен`)
  }

  const handleExportExcel = () => {
    const csv = generateProcessRegulationCsv(currentProcess)
    downloadFile(
      csv,
      `${currentProcess.passport.code}_Регламент_процесса.csv`,
      'text/csv;charset=utf-8;',
    )
    toast.success('Таблица регламента скачана в формате Excel/CSV')
  }

  return (
    <div className="h-screen overflow-hidden bg-background text-foreground flex flex-col">
      {/* closeButton: у сообщения в углу должен быть крестик — часть
          уведомлений живёт до 8 секунд, и закрыть их было нечем. */}
      <Toaster position="top-right" richColors closeButton />

      {/* Main Bank Header */}
      <Header
        currentProcess={currentProcess}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenImport={() => setIsImportOpen(true)}
        onOpenExport={() => setIsExportOpen(true)}
        onExportExcel={handleExportExcel}
      />

      {/* Процессы, сохранённые на бэкенде. Строка идёт ПОСЛЕ шапки: сначала
          сотрудник видит, какой процесс открыт, и только потом — чем его
          заменить. Раньше выбор висел над логотипом и читался как главное
          на экране. */}
      {backendProcesses.length > 0 && (
        <div className="border-b bg-muted/40">
          <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center gap-2 px-3 py-1.5 sm:px-4">
            <Database className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
              Процессы на сервере:
            </span>
            <Select value={selectedBackendProcess} onValueChange={(v) => handleLoadBackendProcess(v)}>
              <SelectTrigger className="h-7 w-full text-xs sm:w-[320px]">
                <SelectValue placeholder="Открыть сохранённый процесс…" />
              </SelectTrigger>
              <SelectContent>
                {backendProcesses.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {/* Отчёт о качестве импортированной карты. Клик по замечанию не открывает
          карточку шага сразу: сотруднику сначала надо увидеть, ГДЕ на карте
          проблема, а карточка закрыла бы половину холста. */}
      <ImportReport
        process={currentProcess}
        activeIssueKey={activeIssueKey}
        onFocusIssue={(issue, nodeIds, key) => {
          setActiveTab('visualizer')
          setSelectedNode(null)
          setActiveIssueKey(key)
          setCanvasFocus((prev) => ({
            nodeIds,
            issue,
            nonce: (prev?.nonce ?? 0) + 1,
          }))
        }}
      />

      {/* Main Content Area */}
      {/* Карта занимает всю доступную высоту и прокручивается внутри себя;
          остальным разделам нужна обычная страничная прокрутка. */}
      <main
        className={`mx-auto flex w-full max-w-[1600px] flex-1 flex-col px-3 py-3 sm:px-4 sm:py-4 ${
          activeTab === 'visualizer' ? 'min-h-0 overflow-hidden' : 'overflow-y-auto'
        }`}
      >
        {activeTab === 'visualizer' && (
          <ProcessVisualizer
            process={currentProcess}
            onSelectNode={(node) => setSelectedNode(node)}
            selectedNodeId={selectedNode?.id}
            focus={canvasFocus ?? undefined}
            onClearFocus={() => {
              setCanvasFocus(null)
              setActiveIssueKey('')
            }}
          />
        )}

        {activeTab === 'matrix' && (
          <StepMatrixTable
            process={currentProcess}
            onSelectNode={(node) => setSelectedNode(node)}
          />
        )}

        {activeTab === 'registry' && (
          <PixRegistryView
            process={currentProcess}
            onUpdateProcess={(updated) => {
              setCurrentProcess(updated)
              void saveProcessToBackend(updated)
              toast.success('Реестр PIX обновлен')
            }}
          />
        )}

        {activeTab === 'processet' && (
          <ProcessetAnalyticsView
            process={currentProcess}
            onOpenExport={() => setIsExportOpen(true)}
          />
        )}
      </main>

      {/* Modals and Drawers */}
      <ProcessImportModal
        open={isImportOpen}
        onOpenChange={setIsImportOpen}
        onProcessLoaded={handleProcessLoaded}
      />

      <NodeDetailDrawer
        node={selectedNode}
        process={currentProcess}
        onClose={() => setSelectedNode(null)}
        onSaveNode={handleSaveNode}
      />

      <ExportDrawer
        open={isExportOpen}
        onOpenChange={setIsExportOpen}
        process={currentProcess}
      />
    </div>
  )
}
