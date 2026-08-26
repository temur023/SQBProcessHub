import React, { useState } from 'react'
import {
  Download,
  Copy,
  Check,
  FileCode,
  FileSpreadsheet,
  Database,
  Sparkles,
} from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { BusinessProcess } from '@/types/process'
import { generateBpmn2Xml } from '@/lib/bpmn-export'
import {
  generateProcessetEventLogCsv,
  generatePixJson,
  generateProcessRegulationCsv,
} from '@/lib/processet-export'
import { triggerExportDownload } from '@/lib/api'

interface ExportDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  process: BusinessProcess
}

export const ExportDrawer: React.FC<ExportDrawerProps> = ({
  open,
  onOpenChange,
  process,
}) => {
  const [activeTab, setActiveTab] = useState<'bpmn' | 'logs' | 'pix' | 'excel'>('bpmn')
  const [copied, setCopied] = useState(false)

  const bpmnXml = generateBpmn2Xml(process)
  const logsCsv = generateProcessetEventLogCsv(process)
  const pixJson = generatePixJson(process)
  const excelCsv = generateProcessRegulationCsv(process)

  const getCurrentContent = () => {
    switch (activeTab) {
      case 'bpmn':
        return { content: bpmnXml, filename: `${process.passport.code}_PIX_Map.bpmn`, mime: 'application/xml', exportType: 'bpmn' as const }
      case 'logs':
        return { content: logsCsv, filename: `${process.passport.code}_EventLogs.csv`, mime: 'text/csv', exportType: 'event-log' as const }
      case 'pix':
        return { content: pixJson, filename: `${process.passport.code}_PIX_Schema.json`, mime: 'application/json', exportType: 'pix-json' as const }
      case 'excel':
        return { content: excelCsv, filename: `${process.passport.code}_Regulation.csv`, mime: 'text/csv;charset=utf-8;', exportType: 'regulation' as const }
    }
  }

  const handleCopy = () => {
    const { content } = getCurrentContent()
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = async () => {
    const { exportType } = getCurrentContent()
    await triggerExportDownload(process, exportType)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl sm:max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600">
              <Download className="w-5 h-5" />
            </div>
            <div>
              <DialogTitle className="text-xl font-bold">Центр экспорта данных процесса</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                Карта процесса: BPMN 2.0 для импорта в PIX Процессную студию. Также Processet и Excel.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as any)} className="flex-1 flex flex-col overflow-hidden">
          <TabsList className="grid grid-cols-4 w-full">
            <TabsTrigger value="bpmn" className="text-xs">
              <FileCode className="w-3.5 h-3.5 mr-1 text-indigo-500" />
              1. BPMN 2.0 (PIX)
            </TabsTrigger>
            <TabsTrigger value="logs" className="text-xs">
              <Sparkles className="w-3.5 h-3.5 mr-1 text-purple-500" />
              2. Event Logs (Processet)
            </TabsTrigger>
            <TabsTrigger value="pix" className="text-xs">
              <Database className="w-3.5 h-3.5 mr-1 text-emerald-500" />
              3. PIX BPM Schema
            </TabsTrigger>
            <TabsTrigger value="excel" className="text-xs">
              <FileSpreadsheet className="w-3.5 h-3.5 mr-1 text-green-500" />
              4. Регламент Excel
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: BPMN 2.0 XML */}
          <TabsContent value="bpmn" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="p-2.5 rounded-lg bg-muted/60 text-xs flex items-center justify-between">
              <span>
                Карта в OMG BPMN 2.0: пул/дорожки, якоря рёбер, условия шлюзов, BPMNDI. Официальный импорт <strong>PIX Процессной студии</strong> (.bpmn).
              </span>
              <Badge className="bg-indigo-600 text-white text-[10px]">BPMN 2.0 XML</Badge>
            </div>
            <div className="flex-1 overflow-auto rounded-lg border bg-slate-950 p-3 text-slate-200 font-mono text-[11px] max-h-[360px]">
              <pre>{bpmnXml}</pre>
            </div>
          </TabsContent>

          {/* Tab 2: Event Logs */}
          <TabsContent value="logs" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="p-2.5 rounded-lg bg-muted/60 text-xs flex items-center justify-between">
              <span>
                Журнал событий (Case ID, Activity, Timestamps, Resource, Cost) для Process Mining анализа в <strong>Processet</strong>.
              </span>
              <Badge className="bg-purple-600 text-white text-[10px]">CSV Log</Badge>
            </div>
            <div className="flex-1 overflow-auto rounded-lg border bg-slate-950 p-3 text-slate-200 font-mono text-[11px] max-h-[360px]">
              <pre>{logsCsv}</pre>
            </div>
          </TabsContent>

          {/* Tab 3: PIX JSON */}
          <TabsContent value="pix" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="p-2.5 rounded-lg bg-muted/60 text-xs flex items-center justify-between">
              <span>
                Конфигурация процессов и реестров для прямого создания в <strong>PIX BPM & RPA</strong>.
              </span>
              <Badge className="bg-emerald-600 text-white text-[10px]">PIX JSON</Badge>
            </div>
            <div className="flex-1 overflow-auto rounded-lg border bg-slate-950 p-3 text-slate-200 font-mono text-[11px] max-h-[360px]">
              <pre>{pixJson}</pre>
            </div>
          </TabsContent>

          {/* Tab 4: Excel Regulation */}
          <TabsContent value="excel" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="p-2.5 rounded-lg bg-muted/60 text-xs flex items-center justify-between">
              <span>
                Табличный регламент процесса с ролями, ИТ-системами, SLA и потенциалом роботизации для Excel.
              </span>
              <Badge className="bg-green-600 text-white text-[10px]">Excel CSV (BOM)</Badge>
            </div>
            <div className="flex-1 overflow-auto rounded-lg border bg-slate-950 p-3 text-slate-200 font-mono text-[11px] max-h-[360px]">
              <pre>{excelCsv}</pre>
            </div>
          </TabsContent>
        </Tabs>

        {/* Footer Actions */}
        <div className="pt-3 border-t flex items-center justify-between">
          <div className="text-xs text-muted-foreground">
            Файл: <strong className="text-foreground">{getCurrentContent().filename}</strong>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleCopy} className="text-xs gap-1.5">
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? 'Скопировано!' : 'Копировать код'}</span>
            </Button>
            <Button
              size="sm"
              onClick={handleDownload}
              className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs gap-1.5"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Скачать файл</span>
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
