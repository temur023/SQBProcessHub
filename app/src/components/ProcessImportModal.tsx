import React, { useState, useRef } from 'react'
import {
  UploadCloud,
  FileCode,
  Sparkles,
  AlertCircle,
  Building2,
  ArrowRight,
  FileCheck2,
  Server,
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
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { importDrawioFileApi, importDrawioXmlApi } from '@/lib/api'
import { SAMPLE_PROCESSES, cloneSampleProcess } from '@/lib/sample-processes'
import type { BusinessProcess } from '@/types/process'

interface ProcessImportModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onProcessLoaded: (process: BusinessProcess) => void
}

export const ProcessImportModal: React.FC<ProcessImportModalProps> = ({
  open,
  onOpenChange,
  onProcessLoaded,
}) => {
  const [activeTab, setActiveTab] = useState<'templates' | 'upload' | 'paste'>('upload')
  const [xmlText, setXmlText] = useState('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileUpload = async (file: File) => {
    if (file.size > 10 * 1024 * 1024) {
      setErrorMsg('Файл больше 10 МБ')
      return
    }
    setIsProcessing(true)
    setErrorMsg(null)
    try {
      const { process } = await importDrawioFileApi(file)
      onProcessLoaded(process)
      onOpenChange(false)
    } catch (err: any) {
      console.error('Import error:', err)
      setErrorMsg(err.message || 'Ошибка парсинга файла draw.io')
    } finally {
      setIsProcessing(false)
    }
  }

  const handleOpenBundledDrawio = async () => {
    setIsProcessing(true)
    setErrorMsg(null)
    try {
      const base = import.meta.env.BASE_URL || './'
      const res = await fetch(`${base}sqb_credit_process.drawio`)
      if (!res.ok) throw new Error('Не удалось загрузить process.drawio из public/')
      const xml = await res.text()
      const { process } = await importDrawioXmlApi(xml, 'process.drawio')
      onProcessLoaded(process)
      onOpenChange(false)
    } catch (err: any) {
      console.error('Bundled drawio import error:', err)
      setErrorMsg(err.message || 'Не удалось открыть process.drawio')
    } finally {
      setIsProcessing(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (file) handleFileUpload(file)
  }

  const handlePasteSubmit = async () => {
    if (!xmlText.trim()) return
    setIsProcessing(true)
    setErrorMsg(null)
    try {
      const { process } = await importDrawioXmlApi(xmlText, 'Pasted_Process.drawio')
      onProcessLoaded(process)
      onOpenChange(false)
    } catch (err: any) {
      console.error('Paste import error:', err)
      setErrorMsg(err.message || 'Не удалось распознать XML диаграммы')
    } finally {
      setIsProcessing(false)
    }
  }

  const handleSelectTemplate = (template: BusinessProcess) => {
    onProcessLoaded(cloneSampleProcess(template))
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl sm:max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600">
              <UploadCloud className="w-5 h-5" />
            </div>
            <div>
              <DialogTitle className="text-xl font-bold">Импорт диаграммы процесса</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                Загрузите диаграмму из Draw.io для автоматического создания бизнес-процесса, регламента PIX и эталона для Processet
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {/* Pipeline Info Banner */}
        <div className="p-3.5 rounded-xl bg-muted/70 border text-xs space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 font-medium text-foreground">
              <Sparkles className="w-4 h-4 text-amber-500" />
              <span>Сквозной пайплайн трансформации (FastAPI + React):</span>
            </div>
            <Badge variant="outline" className="text-[10px] text-emerald-600 border-emerald-500/30 flex items-center gap-1 font-mono">
              <Server className="w-3 h-3" /> Python Engine :8000
            </Badge>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2 pt-1 text-[11px]">
            <div className="flex items-center gap-2 p-2 rounded-lg bg-background border">
              <span className="h-5 w-5 rounded-full bg-blue-100 text-blue-700 font-bold flex items-center justify-center text-[10px] shrink-0">1</span>
              <div>
                <p className="font-semibold">Draw.io XML</p>
                <p className="text-muted-foreground text-[10px]">Парсинг фигур, дорожек, связей</p>
              </div>
            </div>
            <div className="flex items-center gap-2 p-2 rounded-lg bg-background border">
              <span className="h-5 w-5 rounded-full bg-emerald-100 text-emerald-700 font-bold flex items-center justify-center text-[10px] shrink-0">2</span>
              <div>
                <p className="font-semibold">Реестр PIX</p>
                <p className="text-muted-foreground text-[10px]">Паспорт, SLA, роли, АБС</p>
              </div>
            </div>
            <div className="flex items-center gap-2 p-2 rounded-lg bg-background border">
              <span className="h-5 w-5 rounded-full bg-purple-100 text-purple-700 font-bold flex items-center justify-center text-[10px] shrink-0">3</span>
              <div>
                <p className="font-semibold">Processet</p>
                <p className="text-muted-foreground text-[10px]">BPMN 2.0 XML + Event Log</p>
              </div>
            </div>
          </div>
        </div>

        {errorMsg && (
          <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-start justify-between gap-2">
            <div className="flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                <p className="font-medium">Ошибка импорта:</p>
                <p className="mt-0.5">{errorMsg}</p>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[10px] text-destructive hover:bg-destructive/10"
              onClick={() => setErrorMsg(null)}
            >
              Сбросить
            </Button>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as any)} className="w-full mt-2">
          <TabsList className="grid grid-cols-3 w-full h-auto p-1">
            <TabsTrigger value="upload" className="text-xs py-2 px-1 gap-1.5">
              <UploadCloud className="w-4 h-4 text-emerald-600 shrink-0" />
              <span>Загрузить .drawio</span>
            </TabsTrigger>
            <TabsTrigger value="templates" className="text-xs py-2 px-1 gap-1.5">
              <Building2 className="w-4 h-4 text-blue-600 shrink-0" />
              <span>Шаблоны SQB Банка</span>
            </TabsTrigger>
            <TabsTrigger value="paste" className="text-xs py-2 px-1 gap-1.5">
              <FileCode className="w-4 h-4 text-purple-600 shrink-0" />
              <span>Вставить XML</span>
            </TabsTrigger>
          </TabsList>

          {/* Upload File Tab */}
          <TabsContent value="upload" className="pt-3 space-y-3">
            <input
              type="file"
              ref={fileInputRef}
              accept=".drawio,.xml,.bpmn,.txt"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) handleFileUpload(file)
                e.target.value = ''
              }}
            />
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed rounded-xl p-8 text-center hover:border-emerald-500 hover:bg-muted/40 transition-colors cursor-pointer flex flex-col items-center justify-center gap-3 bg-muted/20"
            >
              <div className="h-14 w-14 rounded-full bg-emerald-100 dark:bg-emerald-950/60 flex items-center justify-center text-emerald-600">
                <UploadCloud className="w-7 h-7" />
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">
                  Перетащите файл .drawio или нажмите для выбора
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Поддерживаются сжатые и несжатые диаграммы Draw.io, схемы BPMN 2.0 (.drawio, .xml, .bpmn)
                </p>
              </div>
              <Button size="sm" variant="outline" className="mt-2" disabled={isProcessing}>
                {isProcessing ? 'Парсинг через FastAPI...' : 'Выбрать файл с диска'}
              </Button>
            </div>

            {/* Quick action: Load process.drawio directly */}
            <div className="p-3 rounded-lg bg-emerald-50/60 dark:bg-emerald-950/30 border border-emerald-500/20 flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs">
                <FileCheck2 className="w-4 h-4 text-emerald-600" />
                <span>
                  Файл <strong>process.drawio</strong> уже подготовлен в корне проекта
                </span>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="text-xs h-7 border-emerald-500/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/10"
                onClick={() => handleOpenBundledDrawio()}
                disabled={isProcessing}
              >
                Открыть process.drawio
              </Button>
            </div>
          </TabsContent>

          {/* Templates Tab */}
          <TabsContent value="templates" className="space-y-3 pt-3">
            <p className="text-xs text-muted-foreground">
              Выберите готовый регламент процесса SQB Банка для мгновенной демонстрации возможностей:
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {SAMPLE_PROCESSES.map((proc) => (
                <div
                  key={proc.id}
                  onClick={() => handleSelectTemplate(proc)}
                  className="p-3.5 rounded-xl border hover:border-emerald-500 hover:bg-emerald-500/5 transition-all cursor-pointer group flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {proc.passport.code}
                      </Badge>
                      <Badge className="bg-emerald-600/10 text-emerald-600 border-emerald-500/20 text-[10px]">
                        {proc.nodes.length} шагов • {proc.lanes.length} дорожки
                      </Badge>
                    </div>
                    <h4 className="font-bold text-sm text-foreground group-hover:text-emerald-600 transition-colors">
                      {proc.name}
                    </h4>
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                      {proc.passport.description}
                    </p>
                  </div>

                  <div className="mt-3 pt-2 border-t flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>SLA: <strong className="text-foreground">{proc.passport.targetSlaHours}ч</strong></span>
                    <span className="flex items-center gap-1 text-emerald-600 font-medium group-hover:translate-x-0.5 transition-transform">
                      Открыть процесс <ArrowRight className="w-3.5 h-3.5" />
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </TabsContent>

          {/* Paste XML Tab */}
          <TabsContent value="paste" className="space-y-3 pt-3">
            <p className="text-xs text-muted-foreground">
              Вставьте исходный XML диаграммы draw.io или BPMN 2.0:
            </p>
            <Textarea
              rows={8}
              placeholder="<mxfile host='app.diagrams.net'>..."
              value={xmlText}
              onChange={(e) => setXmlText(e.target.value)}
              className="font-mono text-xs"
            />
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                onClick={handlePasteSubmit}
                disabled={!xmlText.trim() || isProcessing}
                className="bg-emerald-600 hover:bg-emerald-700 text-white"
              >
                {isProcessing ? 'Обработка через FastAPI...' : 'Создать бизнес-процесс'}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
