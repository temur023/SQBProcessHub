import React, { useState, useEffect } from 'react'
import {
  FileCode2,
  UploadCloud,
  FileSpreadsheet,
  Layers,
  Cpu,
  TrendingUp,
  Download,
  Clock,
  Sparkles,
  Server,
  FileSearch,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { BusinessProcess } from '@/types/process'
import { checkBackendHealth } from '@/lib/api'

interface HeaderProps {
  currentProcess: BusinessProcess
  activeTab: string
  setActiveTab: (tab: string) => void
  onOpenImport: () => void
  onOpenExport: () => void
  onOpenViewer: () => void
  onExportExcel: () => void
}

export const Header: React.FC<HeaderProps> = ({
  currentProcess,
  activeTab,
  setActiveTab,
  onOpenImport,
  onOpenExport,
  onOpenViewer,
  onExportExcel,
}) => {
  const [isBackendOnline, setIsBackendOnline] = useState<boolean | null>(null)

  useEffect(() => {
    checkBackendHealth().then((status) => setIsBackendOnline(status))
    const timer = setInterval(() => {
      checkBackendHealth().then((status) => setIsBackendOnline(status))
    }, 15000)
    return () => clearInterval(timer)
  }, [])

  const rpaCount = currentProcess.nodes.filter((n) => n.category === 'rpa_bot').length
  const totalTasks = currentProcess.nodes.filter(
    (n) => n.type === 'task' || n.type === 'userTask' || n.type === 'serviceTask',
  ).length

  return (
    <header className="border-b bg-card text-card-foreground shadow-sm sticky top-0 z-40">
      {/* Top Banner with SQB Bank Branding & Process Info */}
      <div className="container mx-auto px-4 py-3">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          {/* Logo & Process Title */}
          <div className="flex items-center gap-3">
            <div className="h-11 w-11 rounded-xl bg-gradient-to-br from-emerald-600 via-teal-700 to-cyan-800 flex items-center justify-center text-white font-bold shadow-md shadow-emerald-700/20">
              <span className="text-sm tracking-tighter">SQB</span>
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-bold text-lg text-foreground tracking-tight">
                  SQB Process Hub
                </span>
                <Badge variant="outline" className="bg-emerald-500/10 text-emerald-600 border-emerald-500/30 text-xs font-semibold">
                  PIX & Processet Bridge
                </Badge>
                {isBackendOnline ? (
                  <Badge className="bg-emerald-600/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/40 text-[10px] flex items-center gap-1 font-mono">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    FastAPI :8000
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-slate-400 border-slate-300 dark:border-slate-700 text-[10px] flex items-center gap-1">
                    <Server className="w-2.5 h-2.5" />
                    Local Engine
                  </Badge>
                )}
                <Badge variant="secondary" className="text-xs font-mono">
                  {currentProcess.passport.code}
                </Badge>
                <Badge className="bg-blue-600 hover:bg-blue-700 text-white text-xs">
                  v{currentProcess.passport.version}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5 max-w-xl">
                {currentProcess.passport.name} • {currentProcess.passport.owner}
              </p>
            </div>
          </div>

          {/* Quick Metrics & Actions */}
          <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
            <div className="hidden md:flex items-center gap-4 px-3 py-1.5 rounded-lg bg-muted/60 text-xs text-muted-foreground mr-2 border">
              <div className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-amber-500" />
                <span>SLA: <strong className="text-foreground">{currentProcess.passport.targetSlaHours}ч</strong></span>
              </div>
              <div className="h-3 w-px bg-border" />
              <div className="flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5 text-emerald-500" />
                <span>PIX RPA: <strong className="text-emerald-600 dark:text-emerald-400">{rpaCount}/{totalTasks}</strong></span>
              </div>
              <div className="h-3 w-px bg-border" />
              <div className="flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-blue-500" />
                <span>Сверка: <strong className="text-foreground">{currentProcess.miningMetrics.conformanceRate}%</strong></span>
              </div>
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={onOpenImport}
              className="gap-1.5 text-xs font-medium h-9 border-dashed hover:border-solid hover:bg-muted"
            >
              <UploadCloud className="w-4 h-4 text-emerald-600" />
              <span>Импорт Draw.io</span>
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={onOpenViewer}
              className="gap-1.5 text-xs font-medium h-9 hover:bg-muted"
              title="Открыть готовый .bpmn или .pmm и увидеть карту так, как её получит PIX"
            >
              <FileSearch className="w-4 h-4 text-sky-600" />
              <span className="hidden sm:inline">Просмотр BPMN / PMM</span>
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={onExportExcel}
              className="gap-1.5 text-xs font-medium h-9 hover:bg-muted"
              title="Скачать таблицу регламента в формате Excel/CSV"
            >
              <FileSpreadsheet className="w-4 h-4 text-green-600" />
              <span className="hidden sm:inline">Регламент Excel</span>
            </Button>

            <Button
              size="sm"
              onClick={onOpenExport}
              className="gap-1.5 text-xs font-medium h-9 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-700 hover:to-teal-700 text-white shadow-sm"
            >
              <Download className="w-4 h-4" />
              <span>Экспорт Processet / PIX</span>
            </Button>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex items-center gap-1 mt-3 border-t pt-2 overflow-x-auto no-scrollbar">
          <button
            onClick={() => setActiveTab('visualizer')}
            className={`flex items-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap ${
              activeTab === 'visualizer'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>1. Карта процесса (BPMN)</span>
            <Badge variant="outline" className={`ml-1 text-[10px] py-0 px-1.5 ${activeTab === 'visualizer' ? 'bg-primary-foreground/20 text-white border-transparent' : ''}`}>
              {currentProcess.nodes.length}
            </Badge>
          </button>

          <button
            onClick={() => setActiveTab('matrix')}
            className={`flex items-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap ${
              activeTab === 'matrix'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <FileSpreadsheet className="w-3.5 h-3.5" />
            <span>2. Регламент шагов & Роли</span>
            <Badge variant="outline" className={`ml-1 text-[10px] py-0 px-1.5 ${activeTab === 'matrix' ? 'bg-primary-foreground/20 text-white border-transparent' : ''}`}>
              {totalTasks}
            </Badge>
          </button>

          <button
            onClick={() => setActiveTab('registry')}
            className={`flex items-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap ${
              activeTab === 'registry'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <FileCode2 className="w-3.5 h-3.5" />
            <span>3. PIX Реестры & Данные</span>
            <Badge variant="outline" className={`ml-1 text-[10px] py-0 px-1.5 ${activeTab === 'registry' ? 'bg-primary-foreground/20 text-white border-transparent' : ''}`}>
              {currentProcess.registry.records.length}
            </Badge>
          </button>

          <button
            onClick={() => setActiveTab('processet')}
            className={`flex items-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap ${
              activeTab === 'processet'
                ? 'bg-gradient-to-r from-indigo-600 to-purple-600 text-white shadow-sm'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            }`}
          >
            <TrendingUp className="w-3.5 h-3.5 text-purple-400" />
            <span>4. Сверка Processet (Should-Be vs As-Is)</span>
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-purple-500"></span>
            </span>
          </button>
        </div>
      </div>
    </header>
  )
}
