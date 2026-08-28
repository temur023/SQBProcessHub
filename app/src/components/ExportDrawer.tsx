import React, { useEffect, useState } from 'react'
import {
  Download,
  Copy,
  Check,
  FileCode,
  FileSpreadsheet,
  Database,
  Sparkles,
  Package,
  ShieldCheck,
  ShieldAlert,
  Loader2,
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
import { triggerExportDownload, fetchBpmnXml, fetchExportCheck, ExportUnavailableError } from '@/lib/api'
import type { ExportCheckReport } from '@/lib/api'
import { toast } from 'sonner'

interface ExportDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  process: BusinessProcess
}

/**
 * Вердикт проверки выгрузки — теми же правилами, по которым файл читает PIX.
 *
 * Студия отказывается открыть пакет целиком из-за одного дефекта и называет
 * только код ошибки («Notation element not found»), без указания фигуры. Здесь
 * тот же список проверок прогоняется заранее, и, если что-то не так, сотрудник
 * видит конкретную фигуру, не выходя из системы.
 */
const ExportCheckBanner: React.FC<{
  report: ExportCheckReport | null
  loading: boolean
  format: string
}> = ({ report, loading, format }) => {
  if (loading) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        Проверяем файлы правилами PIX…
      </div>
    )
  }
  if (!report) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
        Проверка выгрузки недоступна: она требует запущенного бэкенда FastAPI.
      </div>
    )
  }

  // Показываем замечания того формата, который сотрудник сейчас смотрит, а при
  // отсутствии такого — все: вердикт «готово» должен относиться к обоим файлам.
  const scoped = report.formats.filter((f) => f.format === format)
  const shown = scoped.length ? scoped : report.formats
  const problems = shown.flatMap((f) => f.problems.map((p) => ({ ...p, format: f.format })))
  const errors = problems.filter((p) => p.level === 'error')

  if (!problems.length) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
        Проверено правилами PIX: {shown.map((f) => f.format.toUpperCase()).join(' и ')} —
        замечаний нет, файл загрузится в Процессную студию.
      </div>
    )
  }

  return (
    <div
      className={`mt-3 max-h-32 overflow-auto rounded-lg border px-3 py-2 text-xs ${
        errors.length
          ? 'border-red-500/40 bg-red-500/5'
          : 'border-amber-500/40 bg-amber-500/5'
      }`}
    >
      <div className={`flex items-center gap-2 font-medium ${errors.length ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400'}`}>
        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
        {errors.length
          ? `PIX не примет файл: ошибок ${errors.length}`
          : `Файл загрузится, но есть замечания: ${problems.length}`}
      </div>
      <ul className="mt-1 space-y-0.5">
        {problems.slice(0, 12).map((p, i) => (
          <li key={`${p.code}-${i}`} className="leading-snug text-muted-foreground">
            <span className="font-mono text-[10px] uppercase">{p.format}</span> · {p.message}
            {p.where && <span className="ml-1 font-mono text-[10px] opacity-70">({p.where})</span>}
          </li>
        ))}
        {problems.length > 12 && (
          <li className="text-muted-foreground/80">…и ещё {problems.length - 12}.</li>
        )}
      </ul>
    </div>
  )
}

export const ExportDrawer: React.FC<ExportDrawerProps> = ({
  open,
  onOpenChange,
  process,
}) => {
  const [activeTab, setActiveTab] = useState<'bpmn' | 'pmm' | 'logs' | 'pix' | 'excel'>('bpmn')
  const [copied, setCopied] = useState(false)
  // Предпросмотр обязан совпадать с тем, что реально скачается: берём BPMN с
  // бэкенда, а клиентский генератор оставляем только для офлайн-режима.
  const [serverBpmn, setServerBpmn] = useState<string | null>(null)
  // Проверка выгрузки правилами PIX: сотрудник должен видеть вердикт ДО того,
  // как понесёт файл в студию, — она отвергает пакет целиком и без адреса.
  // «Идёт проверка» выводим из того, чей ответ уже получен, а не отдельным
  // флагом: синхронный setState в эффекте гоняет лишние перерисовки.
  const [check, setCheck] = useState<ExportCheckReport | null>(null)
  const [checkedId, setCheckedId] = useState('')
  const checking = checkedId !== process.id

  useEffect(() => {
    if (!open) return
    let cancelled = false
    fetchBpmnXml(process.id).then((xml) => {
      if (!cancelled) setServerBpmn(xml)
    })
    fetchExportCheck(process.id).then((report) => {
      if (cancelled) return
      setCheck(report)
      setCheckedId(process.id)
    })
    return () => {
      cancelled = true
    }
  }, [open, process.id])

  const bpmnXml = serverBpmn ?? generateBpmn2Xml(process)
  const logsCsv = generateProcessetEventLogCsv(process)
  const pixJson = generatePixJson(process)
  const excelCsv = generateProcessRegulationCsv(process)
  const pmmManifest = [
    `${process.passport.code}_PIX_Map.pmm`,
    '├── main.xml',
    '├── pm/configuration.xml',
    `└── pm/maps/{code}.xml`,
    '',
    'Нативный пакет PIX Процессной студии: Types + каталог нотаций + карта BPMN.',
    'Дорожки — horizontalRoad, шаги вложены с относительными координатами,',
    'переходы — connector type=step на уровне Map. Скачивается ZIP .pmm.',
    '',
    'Время шага рисуется мелким таймером у его нижней грани — как на карте draw.io.',
    'Проверить файл, не выходя из системы, можно кнопкой «Просмотр BPMN / PMM».',
  ].join('\n')

  const getCurrentContent = () => {
    switch (activeTab) {
      case 'bpmn':
        return { content: bpmnXml, filename: `${process.passport.code}_PIX_Map.bpmn`, mime: 'application/xml', exportType: 'bpmn' as const }
      case 'pmm':
        return { content: pmmManifest, filename: `${process.passport.code}_PIX_Map.pmm`, mime: 'application/zip', exportType: 'pmm' as const }
      case 'logs':
        return { content: logsCsv, filename: `${process.passport.code}_EventLogs.csv`, mime: 'text/csv', exportType: 'event-log' as const }
      case 'pix':
        return { content: pixJson, filename: `${process.passport.code}_PIX_Schema.json`, mime: 'application/json', exportType: 'pix-json' as const }
      case 'excel':
        return { content: excelCsv, filename: `${process.passport.code}_Regulation.csv`, mime: 'text/csv;charset=utf-8;', exportType: 'regulation' as const }
    }
  }

  const handleCopy = () => {
    if (activeTab === 'pmm') return
    const { content } = getCurrentContent()
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = async () => {
    const { exportType, filename } = getCurrentContent()
    try {
      await triggerExportDownload(process, exportType)
      toast.success(`Файл «${filename}» выгружен`)
    } catch (err) {
      // Раньше отказ экспорта .pmm терялся в console.warn, и по кнопке просто
      // ничего не происходило.
      const message =
        err instanceof ExportUnavailableError
          ? err.message
          : `Не удалось выгрузить «${filename}»: ${err instanceof Error ? err.message : String(err)}`
      toast.error(message, { duration: 8000 })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[92dvh] max-w-4xl flex-col overflow-hidden p-4 sm:max-w-4xl sm:p-6">
        <DialogHeader>
          <div className="flex items-start gap-2.5 pr-6">
            <div className="shrink-0 rounded-lg bg-emerald-100 p-2 text-emerald-600 dark:bg-emerald-950/60">
              <Download className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <DialogTitle className="text-base font-bold sm:text-xl">Центр экспорта данных процесса</DialogTitle>
              <DialogDescription className="mt-0.5 text-xs text-muted-foreground">
                Карта процесса: BPMN 2.0 и нативный .pmm для PIX Процессной студии. Также Processet и Excel.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as any)} className="flex-1 flex flex-col overflow-hidden">
          <TabsList className="grid h-auto w-full grid-cols-2 gap-1 p-1 sm:grid-cols-3 lg:grid-cols-5">
            <TabsTrigger value="bpmn" className="text-[11px]">
              <FileCode className="w-3.5 h-3.5 mr-1 text-indigo-500" />
              BPMN 2.0
            </TabsTrigger>
            <TabsTrigger value="pmm" className="text-[11px]">
              <Package className="w-3.5 h-3.5 mr-1 text-sky-500" />
              PMM (PIX)
            </TabsTrigger>
            <TabsTrigger value="logs" className="text-[11px]">
              <Sparkles className="w-3.5 h-3.5 mr-1 text-purple-500" />
              Event Logs
            </TabsTrigger>
            <TabsTrigger value="pix" className="text-[11px]">
              <Database className="w-3.5 h-3.5 mr-1 text-emerald-500" />
              PIX Schema
            </TabsTrigger>
            <TabsTrigger value="excel" className="text-[11px]">
              <FileSpreadsheet className="w-3.5 h-3.5 mr-1 text-green-500" />
              Excel
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: BPMN 2.0 XML */}
          <TabsContent value="bpmn" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Карта в OMG BPMN 2.0: пул/дорожки, якоря рёбер, условия шлюзов, BPMNDI. Время шага едет
                видимым значком — граничный таймер с подписью у фигуры. Официальный импорт{' '}
                <strong>PIX Процессной студии</strong> (.bpmn).
              </span>
              <Badge className="shrink-0 bg-indigo-600 text-[10px] text-white">BPMN 2.0 XML</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{bpmnXml}</pre>
            </div>
          </TabsContent>

          <TabsContent value="pmm" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Нативный пакет <strong>PIX Процессной студии</strong> (.pmm = ZIP из main.xml, configuration.xml и карты BPMN).
                Открывается как проект студии; официальный обмен по-прежнему .bpmn / .vsdx.
              </span>
              <Badge className="bg-sky-600 text-white text-[10px] shrink-0">PMM ZIP</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{pmmManifest}</pre>
            </div>
          </TabsContent>

          {/* Tab 2: Event Logs */}
          <TabsContent value="logs" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Журнал событий (Case ID, Activity, Timestamps, Resource, Cost) для Process Mining анализа в <strong>Processet</strong>.
              </span>
              <Badge className="shrink-0 bg-purple-600 text-[10px] text-white">CSV Log</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{logsCsv}</pre>
            </div>
          </TabsContent>

          {/* Tab 3: PIX JSON */}
          <TabsContent value="pix" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Конфигурация процессов и реестров для прямого создания в <strong>PIX BPM & RPA</strong>.
              </span>
              <Badge className="shrink-0 bg-emerald-600 text-[10px] text-white">PIX JSON</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{pixJson}</pre>
            </div>
          </TabsContent>

          {/* Tab 4: Excel Regulation */}
          <TabsContent value="excel" className="flex-1 flex flex-col overflow-hidden mt-3 space-y-2">
            <div className="flex flex-col items-start justify-between gap-2 rounded-lg bg-muted/60 p-2.5 text-xs sm:flex-row sm:items-center">
              <span>
                Табличный регламент процесса с ролями, ИТ-системами, SLA и потенциалом роботизации для Excel.
              </span>
              <Badge className="shrink-0 bg-green-600 text-[10px] text-white">Excel CSV (BOM)</Badge>
            </div>
            <div className="max-h-[360px] flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
              <pre>{excelCsv}</pre>
            </div>
          </TabsContent>
        </Tabs>

        {/* Проверка выгрузки правилами PIX */}
        <ExportCheckBanner report={check} loading={checking} format={activeTab} />

        {/* Footer Actions */}
        <div className="flex flex-col gap-2.5 border-t pt-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 truncate text-xs text-muted-foreground">
            Файл: <strong className="text-foreground">{getCurrentContent().filename}</strong>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {activeTab !== 'pmm' && (
            <Button variant="outline" size="sm" onClick={handleCopy} className="flex-1 gap-1.5 text-xs sm:flex-none">
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              <span>{copied ? 'Скопировано!' : 'Копировать код'}</span>
            </Button>
            )}
            <Button
              size="sm"
              onClick={handleDownload}
              className="flex-1 gap-1.5 bg-emerald-600 text-xs text-white hover:bg-emerald-700 sm:flex-none"
            >
              <Download className="h-3.5 w-3.5" />
              <span>Скачать файл</span>
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
