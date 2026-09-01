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
import { Spinner } from '@/components/ui/spinner'
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
      <DialogContent className="max-h-[92dvh] max-w-3xl overflow-y-auto p-4 sm:max-w-3xl sm:p-6">
        <DialogHeader>
          <div className="flex items-start gap-2.5 pr-6">
            <div className="shrink-0 rounded-lg bg-emerald-100 p-2 text-emerald-600 dark:bg-emerald-950/60">
              <UploadCloud className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <DialogTitle className="text-base font-bold sm:text-xl">Импорт диаграммы процесса</DialogTitle>
              <DialogDescription className="mt-0.5 text-xs text-muted-foreground">
                Загрузите диаграмму из Draw.io для автоматического создания бизнес-процесса, регламента PIX и эталона для Processet
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {/* Pipeline Info Banner */}
        <div className="space-y-2 rounded-xl border bg-muted/70 p-3 text-xs sm:p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 font-medium text-foreground">
              <Sparkles className="h-4 w-4 shrink-0 text-amber-500" />
              <span>Сквозной пайплайн трансформации (FastAPI + React):</span>
            </div>
            <Badge variant="outline" className="flex items-center gap-1 border-emerald-500/30 font-mono text-[10px] text-emerald-600">
              <Server className="h-3 w-3" /> Python Engine :8000
            </Badge>
          </div>
          <div className="grid grid-cols-1 gap-2 pt-1 text-[11px] sm:grid-cols-3">
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
          <div className="flex items-start justify-between gap-2 rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-xs text-destructive">
            <div className="flex min-w-0 items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="min-w-0">
                <p className="font-medium">Ошибка импорта:</p>
                {/* whitespace-pre-line: предпроверка перечисляет находки
                    построчно, и без сохранения переносов список склеивается
                    в одну строку, из которой ничего не вычитать. */}
                <p className="mt-0.5 whitespace-pre-line break-words">{errorMsg}</p>
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
          {/* Подписи короче ниже `sm`: полные в треть телефонного экрана
              не помещаются и рвут вкладку на три строки. */}
          <TabsList className="grid h-auto w-full grid-cols-3 p-1">
            <TabsTrigger value="upload" className="gap-1.5 px-1 py-2 text-xs">
              <UploadCloud className="h-4 w-4 shrink-0 text-emerald-600" />
              <span className="hidden sm:inline">Загрузить .drawio</span>
              <span className="sm:hidden">Файл</span>
            </TabsTrigger>
            <TabsTrigger value="templates" className="gap-1.5 px-1 py-2 text-xs">
              <Building2 className="h-4 w-4 shrink-0 text-blue-600" />
              <span className="hidden sm:inline">Шаблоны SQB Банка</span>
              <span className="sm:hidden">Шаблоны</span>
            </TabsTrigger>
            <TabsTrigger value="paste" className="gap-1.5 px-1 py-2 text-xs">
              <FileCode className="h-4 w-4 shrink-0 text-purple-600" />
              <span className="hidden sm:inline">Вставить XML</span>
              <span className="sm:hidden">XML</span>
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
              role="button"
              tabIndex={isProcessing ? -1 : 0}
              aria-disabled={isProcessing}
              aria-label="Выбрать файл диаграммы с диска"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                if (isProcessing) {
                  e.preventDefault()
                  return
                }
                handleDrop(e)
              }}
              onClick={() => !isProcessing && fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (isProcessing) return
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  fileInputRef.current?.click()
                }
              }}
              className={`flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-muted/20 p-6 text-center transition-colors sm:p-8 ${
                isProcessing
                  ? 'cursor-wait opacity-70'
                  : 'cursor-pointer hover:border-emerald-500 hover:bg-muted/40'
              }`}
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-950/60 sm:h-14 sm:w-14">
                {isProcessing ? (
                  <Spinner className="h-6 w-6" />
                ) : (
                  <UploadCloud className="h-6 w-6 sm:h-7 sm:w-7" />
                )}
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">
                  {isProcessing
                    ? 'Разбираем диаграмму…'
                    : 'Перетащите файл .drawio или нажмите для выбора'}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {isProcessing
                    ? 'Фигуры, дорожки и связи раскладываются в шаги регламента и реестр PIX.'
                    : 'Поддерживаются сжатые и несжатые диаграммы Draw.io, схемы BPMN 2.0 (.drawio, .xml, .bpmn)'}
                </p>
              </div>
              <Button size="sm" variant="outline" className="mt-2" disabled={isProcessing} tabIndex={-1}>
                {isProcessing ? 'Подождите…' : 'Выбрать файл с диска'}
              </Button>
            </div>

            {/* Quick action: Load process.drawio directly */}
            <div className="flex flex-col items-start justify-between gap-2.5 rounded-lg border border-emerald-500/20 bg-emerald-50/60 p-3 dark:bg-emerald-950/30 sm:flex-row sm:items-center">
              <div className="flex items-center gap-2 text-xs">
                <FileCheck2 className="h-4 w-4 shrink-0 text-emerald-600" />
                <span>
                  Файл <strong>process.drawio</strong> уже подготовлен в корне проекта
                </span>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="h-7 w-full shrink-0 border-emerald-500/40 text-xs text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300 sm:w-auto"
                onClick={() => handleOpenBundledDrawio()}
                disabled={isProcessing}
              >
                {isProcessing && <Spinner className="mr-1.5 h-3 w-3" />}
                Открыть process.drawio
              </Button>
            </div>
          </TabsContent>

          {/* Templates Tab */}
          <TabsContent value="templates" className="space-y-3 pt-3">
            <p className="text-xs text-muted-foreground">
              Выберите готовый регламент процесса SQB Банка для мгновенной демонстрации возможностей:
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
                className="w-full gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700 sm:w-auto"
              >
                {isProcessing && <Spinner className="h-3.5 w-3.5" />}
                {isProcessing ? 'Разбираем…' : 'Создать бизнес-процесс'}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
