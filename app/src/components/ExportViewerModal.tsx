import React, { useCallback, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileCode,
  FileSearch,
  Loader2,
  Package,
  RefreshCw,
  Send,
  UploadCloud,
} from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { BusinessProcess, ProcessNode } from '@/types/process'
import { isTaskNode } from '@/types/process'
import { ProcessVisualizer } from '@/components/ProcessVisualizer'
import { StepMatrixTable } from '@/components/StepMatrixTable'
import { parseBpmnMap } from '@/lib/bpmn-import'
import { findMapPart, parsePmmMapXml } from '@/lib/pmm-import'
import { readZip } from '@/lib/zip'
import { formatDuration, generateBpmn2Xml } from '@/lib/bpmn-export'
import { ExportUnavailableError, fetchBpmnXml, fetchPmmPackage } from '@/lib/api'
import { toast } from 'sonner'

/**
 * Просмотр выгрузки прямо в системе.
 *
 * До этого проверить, что уедет в PIX Процессную студию, можно было только
 * скачав файл и открыв его в самой студии. Здесь сотрудник открывает `.bpmn`
 * или `.pmm` — свой или собранный из текущего процесса одной кнопкой — и видит
 * ровно ту карту, которую получит студия: те же дорожки, те же связи и те же
 * часы со временем у шагов. Карта читается на клиенте: разбор на бэкенде
 * достраивает недостающее, а для сверки выгрузки нужен файл без поправок.
 */

type OpenedFormat = 'bpmn' | 'pmm'

interface OpenedMap {
  process: BusinessProcess
  format: OpenedFormat
  fileName: string
  /** XML, который реально разобран: BPMN целиком либо карта из пакета. */
  source: string
  /** Части ZIP — показываем, из чего собран пакет. */
  parts?: string[]
}

interface ExportViewerModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Текущий процесс — источник для кнопок «проверить выгрузку». */
  process: BusinessProcess
  onLoadIntoWorkspace: (process: BusinessProcess) => void
}

async function openPmmBuffer(buffer: ArrayBuffer, fileName: string): Promise<OpenedMap> {
  const entries = await readZip(buffer)
  const part = findMapPart(entries)
  return {
    process: parsePmmMapXml(part.xml, fileName),
    format: 'pmm',
    fileName,
    source: part.xml,
    parts: entries.map((e) => e.name),
  }
}

export const ExportViewerModal: React.FC<ExportViewerModalProps> = ({
  open,
  onOpenChange,
  process,
  onLoadIntoWorkspace,
}) => {
  const [opened, setOpened] = useState<OpenedMap | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [tab, setTab] = useState<'map' | 'steps' | 'source'>('map')
  const [selectedNode, setSelectedNode] = useState<ProcessNode | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const run = useCallback(async (label: string, job: () => Promise<OpenedMap>) => {
    setBusy(label)
    setError(null)
    try {
      const result = await job()
      setOpened(result)
      setSelectedNode(null)
      setTab('map')
      toast.success(`Файл «${result.fileName}» открыт`, {
        description: `Шагов: ${result.process.nodes.filter((n) => isTaskNode(n.type)).length}, дорожек: ${result.process.lanes.length}.`,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      setOpened(null)
      toast.error('Файл открыть не удалось', { description: message, duration: 8000 })
    } finally {
      setBusy(null)
    }
  }, [])

  const openFile = useCallback(
    (file: File) => {
      const name = file.name
      const isPackage = /\.(pmm|zip)$/i.test(name)
      void run(name, async () => {
        if (isPackage) return openPmmBuffer(await file.arrayBuffer(), name)
        const text = await file.text()
        return { process: parseBpmnMap(text, name), format: 'bpmn' as const, fileName: name, source: text }
      })
    },
    [run],
  )

  const openCurrentBpmn = useCallback(() => {
    const fileName = `${process.passport.code}_PIX_Map.bpmn`
    void run(fileName, async () => {
      // Источник истины — бэкенд; клиентский генератор подхватывает офлайн.
      const xml = (await fetchBpmnXml(process.id)) ?? generateBpmn2Xml(process)
      return { process: parseBpmnMap(xml, fileName), format: 'bpmn' as const, fileName, source: xml }
    })
  }, [process, run])

  const openCurrentPmm = useCallback(() => {
    const fileName = `${process.passport.code}_PIX_Map.pmm`
    void run(fileName, async () => {
      try {
        return await openPmmBuffer(await fetchPmmPackage(process.id), fileName)
      } catch (err) {
        if (err instanceof ExportUnavailableError) throw new Error(err.message)
        throw err
      }
    })
  }, [process, run])

  const stats = useMemo(() => {
    if (!opened) return null
    const map = opened.process
    const steps = map.nodes.filter((n) => isTaskNode(n.type))
    const timed = steps.filter((n) => (n.slaMinutes || 0) > 0 || (n.waitMinutes || 0) > 0)
    const minutes = steps.reduce((acc, n) => acc + (n.slaMinutes || 0) + (n.waitMinutes || 0), 0)
    return {
      steps: steps.length,
      timed: timed.length,
      lanes: map.lanes.length,
      shapes: map.nodes.length,
      edges: map.edges.length,
      total: formatDuration(minutes) || '—',
    }
  }, [opened])

  const reset = () => {
    setOpened(null)
    setError(null)
    setSelectedNode(null)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="flex h-[92dvh] max-w-[96vw] flex-col overflow-hidden p-4 sm:max-w-[96vw] sm:p-6">
        <DialogHeader>
          <div className="flex items-start gap-2.5 pr-6">
            <div className="shrink-0 rounded-lg bg-sky-100 p-2 text-sky-600 dark:bg-sky-950/60">
              <FileSearch className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <DialogTitle className="text-base font-bold sm:text-xl">Просмотр выгрузки: BPMN и PMM</DialogTitle>
              <DialogDescription className="mt-0.5 text-xs text-muted-foreground">
                Откройте файл <strong>.bpmn</strong> или пакет <strong>.pmm</strong> и посмотрите карту так,
                как её получит PIX Процессная студия — вместе с часами и временем у шагов.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {/* Панель источников */}
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b pb-3">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs h-8"
            disabled={!!busy}
            onClick={() => fileInput.current?.click()}
          >
            <UploadCloud className="w-3.5 h-3.5 text-emerald-600" />
            Открыть файл…
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept=".bpmn,.xml,.pmm,.zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) openFile(file)
              e.target.value = ''
            }}
          />
          <div className="hidden h-4 w-px bg-border sm:block" />
          <span className="hidden text-[11px] text-muted-foreground lg:inline">
            Проверить выгрузку текущего процесса:
          </span>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs h-8"
            disabled={!!busy}
            onClick={openCurrentBpmn}
          >
            <FileCode className="w-3.5 h-3.5 text-indigo-500" />
            BPMN 2.0
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs h-8"
            disabled={!!busy}
            onClick={openCurrentPmm}
          >
            <Package className="w-3.5 h-3.5 text-sky-500" />
            PMM (PIX)
          </Button>
          {opened && (
            <>
              <div className="h-4 w-px bg-border" />
              <Button
                size="sm"
                className="gap-1.5 text-xs h-8 bg-emerald-600 hover:bg-emerald-700 text-white"
                onClick={() => {
                  onLoadIntoWorkspace(opened.process)
                  onOpenChange(false)
                  reset()
                }}
              >
                <Send className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Загрузить в рабочую область</span>
                <span className="sm:hidden">В работу</span>
              </Button>
              <Button variant="ghost" size="sm" className="gap-1.5 text-xs h-8" onClick={reset}>
                <RefreshCw className="w-3.5 h-3.5" />
                Очистить
              </Button>
            </>
          )}
          {busy && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {busy}
            </span>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 p-3 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-600 dark:text-red-300">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {!opened && !error && (
          <div
            className={`flex-1 min-h-0 rounded-xl border-2 border-dashed flex flex-col items-center justify-center gap-3 text-center px-6 transition-colors ${
              dragOver ? 'border-emerald-500 bg-emerald-500/10' : 'border-border'
            }`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              const file = e.dataTransfer.files?.[0]
              if (file) openFile(file)
            }}
          >
            <FileSearch className="w-10 h-10 text-muted-foreground" />
            <div className="text-sm font-medium">Перетащите сюда .bpmn или .pmm</div>
            <p className="text-xs text-muted-foreground max-w-lg">
              Файл разбирается прямо в браузере и ничего никуда не отправляет. Часы у шага в выгрузке —
              это граничный таймер BPMN (в .pmm — мелкая фигура-таймер у грани шага); здесь они
              возвращаются в поле времени шага, поэтому на карте снова видно, сколько занимает операция.
            </p>
            <p className="text-[11px] text-muted-foreground">
              Или соберите файл из текущего процесса кнопками выше.
            </p>
          </div>
        )}

        {opened && stats && (
          <>
            <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1.5 rounded-lg border bg-muted/40 px-2.5 py-2 text-[11px]">
              <Badge className={opened.format === 'bpmn' ? 'bg-indigo-600 text-white' : 'bg-sky-600 text-white'}>
                {opened.format === 'bpmn' ? 'BPMN 2.0' : 'PIX .pmm'}
              </Badge>
              <span className="font-mono text-muted-foreground">{opened.fileName}</span>
              <div className="h-3 w-px bg-border" />
              <span>Фигур: <strong>{stats.shapes}</strong></span>
              <span>Шагов: <strong>{stats.steps}</strong></span>
              <span>Дорожек: <strong>{stats.lanes}</strong></span>
              <span>Связей: <strong>{stats.edges}</strong></span>
              <div className="h-3 w-px bg-border" />
              <span
                className={`flex items-center gap-1 font-medium ${
                  stats.timed === stats.steps && stats.steps > 0
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : 'text-amber-600 dark:text-amber-400'
                }`}
              >
                {stats.timed === stats.steps && stats.steps > 0 ? (
                  <CheckCircle2 className="w-3.5 h-3.5" />
                ) : (
                  <AlertTriangle className="w-3.5 h-3.5" />
                )}
                Часы со временем: {stats.timed} из {stats.steps}
              </span>
              <span className="flex items-center gap-1 text-muted-foreground">
                <Clock className="w-3.5 h-3.5 text-amber-500" />
                Суммарно: <strong className="text-foreground">{stats.total}</strong>
              </span>
            </div>

            <Tabs
              value={tab}
              onValueChange={(v) => setTab(v as typeof tab)}
              className="flex-1 min-h-0 flex flex-col mt-1"
            >
              <TabsList className="grid w-full max-w-lg grid-cols-3">
                <TabsTrigger value="map" className="text-[11px]">
                  <span className="hidden sm:inline">Карта из файла</span>
                  <span className="sm:hidden">Карта</span>
                </TabsTrigger>
                <TabsTrigger value="steps" className="text-[11px]">
                  <span className="hidden sm:inline">Шаги и время</span>
                  <span className="sm:hidden">Шаги</span>
                </TabsTrigger>
                <TabsTrigger value="source" className="text-[11px]">
                  <span className="hidden sm:inline">Исходный XML</span>
                  <span className="sm:hidden">XML</span>
                </TabsTrigger>
              </TabsList>

              <TabsContent value="map" className="flex-1 min-h-0 mt-2 overflow-hidden">
                <ProcessVisualizer
                  process={opened.process}
                  onSelectNode={(node) => setSelectedNode(node)}
                  selectedNodeId={selectedNode?.id}
                />
              </TabsContent>

              <TabsContent value="steps" className="flex-1 min-h-0 mt-2 overflow-auto">
                <StepMatrixTable process={opened.process} onSelectNode={(node) => setSelectedNode(node)} />
              </TabsContent>

              <TabsContent value="source" className="flex-1 min-h-0 mt-2 flex flex-col gap-2 overflow-hidden">
                {opened.parts && (
                  <div className="text-[11px] text-muted-foreground">
                    Части пакета: <span className="font-mono">{opened.parts.join(', ')}</span>
                  </div>
                )}
                {/* Подложка следует за темой: в светлом интерфейсе чёрная
                    плита читалась как вставка из другого приложения. */}
                <div className="flex-1 overflow-auto rounded-lg border bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:bg-slate-950 dark:text-slate-200">
                  <pre>{opened.source}</pre>
                </div>
              </TabsContent>
            </Tabs>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
