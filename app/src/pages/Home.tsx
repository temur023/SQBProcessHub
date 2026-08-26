import { useState, useEffect } from 'react'
import { sqbCreditProcess, cloneSampleProcess } from '@/lib/sample-processes'
import type { BusinessProcess, ProcessNode } from '@/types/process'
import { Header } from '@/components/Header'
import { ProcessVisualizer } from '@/components/ProcessVisualizer'
import { StepMatrixTable } from '@/components/StepMatrixTable'
import { PixRegistryView } from '@/components/PixRegistryView'
import { ProcessetAnalyticsView } from '@/components/ProcessetAnalyticsView'
import { ProcessImportModal } from '@/components/ProcessImportModal'
import { NodeDetailDrawer } from '@/components/NodeDetailDrawer'
import { ExportDrawer } from '@/components/ExportDrawer'
import { generateProcessRegulationCsv, downloadFile } from '@/lib/processet-export'
import { saveProcessToBackend, listProcessesFromBackend, loadProcessFromBackend } from '@/lib/api'
import { analyzeProcessConformance } from '@/lib/conformance'
import { Toaster, toast } from 'sonner'
import { Database } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { isTaskNode } from '@/types/process'

export default function Home() {
  const [currentProcess, setCurrentProcess] = useState<BusinessProcess>(() => cloneSampleProcess(sqbCreditProcess))
  const [activeTab, setActiveTab] = useState<string>('visualizer')
  const [isImportOpen, setIsImportOpen] = useState(false)
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState<ProcessNode | null>(null)
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
    void saveProcessToBackend(newProcess)
    toast.success(`Бизнес-процесс «${newProcess.name}» успешно загружен!`, {
      description: `Создан регламент на ${newProcess.nodes.length} шагов и реестр PIX.`,
    })
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
      <Toaster position="top-right" richColors />

      {/* Backend process selector bar */}
      {(backendProcesses.length > 0) && (
        <div className="px-4 py-2 bg-muted/50 border-b border-border flex items-center gap-3 flex-wrap">
          <Database className="w-4 h-4 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground">Backend процессы:</span>
          <Select value={selectedBackendProcess} onValueChange={(v) => handleLoadBackendProcess(v)}>
            <SelectTrigger className="w-[280px] h-8 text-xs">
              <SelectValue placeholder="Выбрать процесс из бэкенда..." />
            </SelectTrigger>
            <SelectContent>
              {backendProcesses.map(p => (
                <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Main Bank Header */}
      <Header
        currentProcess={currentProcess}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenImport={() => setIsImportOpen(true)}
        onOpenExport={() => setIsExportOpen(true)}
        onExportExcel={handleExportExcel}
      />

      {/* Main Content Area */}
      <main className={`flex-1 min-h-0 container mx-auto px-4 py-4 max-w-7xl flex flex-col ${
        activeTab === 'visualizer' ? 'overflow-hidden' : 'overflow-auto'
      }`}>
        {activeTab === 'visualizer' && (
          <ProcessVisualizer
            process={currentProcess}
            onSelectNode={(node) => setSelectedNode(node)}
            selectedNodeId={selectedNode?.id}
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
