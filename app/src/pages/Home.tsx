import { useState, useEffect } from 'react'
import { sqbCreditProcess } from '@/lib/sample-processes'
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
import { saveProcessToBackend } from '@/lib/api'
import { Toaster, toast } from 'sonner'

export default function Home() {
  const [currentProcess, setCurrentProcess] = useState<BusinessProcess>(sqbCreditProcess)
  const [activeTab, setActiveTab] = useState<string>('visualizer')
  const [isImportOpen, setIsImportOpen] = useState(false)
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState<ProcessNode | null>(null)

  useEffect(() => {
    void saveProcessToBackend(currentProcess)
    // Persist the default template once so backend export endpoints resolve.
    // Subsequent edits save explicitly from handlers below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
    const updated = {
      ...currentProcess,
      nodes: updatedNodes,
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
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <Toaster position="top-right" richColors />

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
      <main className="flex-1 container mx-auto px-4 py-5 max-w-7xl">
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
