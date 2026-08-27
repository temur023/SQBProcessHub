import React from 'react'
import {
  Download,
  AlertTriangle,
  RotateCcw,
  Sparkles,
  Clock,
  CheckCircle2,
  FileCode,
  ShieldAlert,
  Zap,
} from 'lucide-react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { StatTile } from '@/components/StatTile'
import { useIsDark } from '@/hooks/use-dark-mode'
import type { BusinessProcess, ProcessetDeviation } from '@/types/process'
import { isTaskNode } from '@/types/process'
import { generateBpmn2Xml } from '@/lib/bpmn-export'
import {
  generateProcessetEventLogCsv,
  downloadFile,
} from '@/lib/processet-export'

interface ProcessetAnalyticsViewProps {
  process: BusinessProcess
  onOpenExport?: () => void
}

export const ProcessetAnalyticsView: React.FC<ProcessetAnalyticsViewProps> = ({
  process,
}) => {
  const metrics = process.miningMetrics
  const isDark = useIsDark()

  /**
   * Оформление графика. Recharts проставляет цвета осей и подсказки инлайновым
   * стилем, `dark:` до них не доходит — в тёмной теме подписи оставались
   * почти чёрными на почти чёрном фоне, а подсказка приезжала белым блоком.
   */
  const chartTheme = isDark
    ? { axis: '#94a3b8', grid: '#334155', tooltipBg: '#0f172a', tooltipText: '#e2e8f0', tooltipBorder: '#334155' }
    : { axis: '#64748b', grid: '#e2e8f0', tooltipBg: '#ffffff', tooltipText: '#0f172a', tooltipBorder: '#e2e8f0' }

  const handleDownloadBpmn = () => {
    const xml = generateBpmn2Xml(process)
    downloadFile(
      xml,
      `${process.passport.code}_PIX_Map.bpmn`,
      'application/xml',
    )
  }

  const handleDownloadLogs = () => {
    const csv = generateProcessetEventLogCsv(process)
    downloadFile(
      csv,
      `${process.passport.code}_Processet_EventLogs.csv`,
      'text/csv',
    )
  }

  // Prepare chart data comparing Target SLA vs Actual Duration
  const chartData = process.nodes
    .filter((n) => isTaskNode(n.type))
    .slice(0, 6)
    .map((node, idx) => {
      const targetHours = Math.round(((node.slaMinutes || 30) / 60) * 10) / 10
      // Simulated realistic actual duration
      let actualHours = targetHours
      if (idx === 2 || idx === 3) actualHours = Math.round(targetHours * 3.4 * 10) / 10
      else if (idx === 1) actualHours = Math.round(targetHours * 0.4 * 10) / 10 // RPA is faster
      else actualHours = Math.round(targetHours * 1.25 * 10) / 10

      return {
        name: node.code || `Шаг ${idx + 1}`,
        fullName: node.name,
        'Эталон SLA (ч)': targetHours,
        'Факт Processet (ч)': actualHours,
      }
    })

  const getSeverityBadge = (severity: ProcessetDeviation['severity']) => {
    switch (severity) {
      case 'high':
        return (
          <Badge className="bg-rose-600/10 text-rose-700 dark:text-rose-400 border-rose-500/20 text-[10px]">
            Критическое отклонение
          </Badge>
        )
      case 'medium':
        return (
          <Badge className="bg-amber-600/10 text-amber-700 dark:text-amber-400 border-amber-500/20 text-[10px]">
            Средняя задержка
          </Badge>
        )
      default:
        return (
          <Badge className="bg-blue-600/10 text-blue-700 dark:text-blue-400 border-blue-500/20 text-[10px]">
            Низкое влияние
          </Badge>
        )
    }
  }

  const getTypeIcon = (type: ProcessetDeviation['type']) => {
    switch (type) {
      case 'redundant_step':
        return <AlertTriangle className="w-4 h-4 text-rose-500" />
      case 'sla_breach':
        return <Clock className="w-4 h-4 text-amber-500" />
      case 'rework_loop':
        return <RotateCcw className="w-4 h-4 text-purple-500" />
      default:
        return <ShieldAlert className="w-4 h-4 text-blue-500" />
    }
  }

  return (
    <div className="space-y-5">
      {/* ── Шапка раздела: что и с чем сверяется ─────────────────────────── */}
      {/* Тёмная заливка здесь намеренная в обеих темах: блок отделяет раздел
          сверки от остальной платформы, как обложка отчёта. */}
      <div className="relative overflow-hidden rounded-2xl border border-indigo-800/40 bg-gradient-to-br from-indigo-950 via-slate-900 to-purple-950 p-4 text-white shadow-lg sm:p-5">
        <div className="pointer-events-none absolute -bottom-10 -right-10 h-64 w-64 rounded-full bg-indigo-500/10 blur-3xl" />

        <div className="relative z-10 flex flex-col justify-between gap-4 lg:flex-row lg:items-center lg:gap-5">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge className="border-purple-400/30 bg-purple-500/20 px-2 py-0.5 text-[10px] text-purple-200 sm:text-xs">
                Infomaximum Processet Conformance Engine
              </Badge>
              <Badge variant="outline" className="border-slate-700 font-mono text-[10px] text-slate-300 sm:text-xs">
                {process.passport.code}
              </Badge>
            </div>
            <h2 className="text-lg font-bold leading-tight tracking-tight sm:text-2xl">
              Сверка эталона (Should-Be) с фактическими логами (As-Is)
            </h2>
            <p className="mt-1.5 max-w-2xl text-xs leading-relaxed text-slate-300 sm:text-sm">
              Диаграмма из <strong>Draw.io</strong> сформировала эталонную модель процесса.
              Система сверяет её с логами АБС ЦФТ и CRM в <strong>Processet</strong>,
              выявляя лишние шаги, задержки регламента и потенциал роботизации в <strong>PIX RPA</strong>.
            </p>
          </div>

          <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center">
            <Button
              onClick={handleDownloadBpmn}
              className="w-full gap-1.5 bg-indigo-600 text-xs font-medium text-white shadow-md hover:bg-indigo-500 sm:w-auto"
            >
              <FileCode className="h-4 w-4" />
              <span>BPMN 2.0 для Processet</span>
            </Button>
            <Button
              onClick={handleDownloadLogs}
              className="w-full gap-1.5 bg-purple-600 text-xs font-medium text-white shadow-md hover:bg-purple-500 sm:w-auto"
            >
              <Download className="h-4 w-4" />
              <span>Event Logs (CSV)</span>
            </Button>
          </div>
        </div>
      </div>

      {/* KPI Cards: Process Mining Metrics */}
      <div className="grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
        <StatTile
          label="Соответствие эталону"
          value={`${metrics.conformanceRate}%`}
          hint="Доля заявок строго по регламенту draw.io"
          icon={CheckCircle2}
          tone="emerald"
          footer={
            <span className="text-[11px] font-medium text-rose-500">
              −{Math.round(100 - metrics.conformanceRate)}% отклонений
            </span>
          }
        />
        <StatTile
          label="Фактический Lead Time"
          value={`${metrics.avgLeadTimeHours} ч`}
          suffix={`план ${metrics.targetLeadTimeHours} ч`}
          icon={Clock}
          tone="amber"
          footer={
            <span className="text-[11px] font-medium text-amber-600 dark:text-amber-400">
              +{(metrics.avgLeadTimeHours - metrics.targetLeadTimeHours).toFixed(1)} ч к регламенту
            </span>
          }
        />
        <StatTile
          label="Петли доработок и возвратов"
          value={`${metrics.reworkRate}%`}
          suffix="заявок с циклом"
          hint="Повторный сбор документов и перепроверка"
          icon={RotateCcw}
          tone="purple"
        />
        <StatTile
          label="Экономия с PIX RPA"
          value={`${(metrics.potentialRpaSavingsUzs / 1000000).toFixed(0)} млн`}
          suffix="UZS/год"
          hint="При роботизации ручных шагов"
          icon={Zap}
          tone="emerald"
        />
      </div>

      {/* ── Эталон против факта ──────────────────────────────────────────── */}
      {/* `items-start` вместо растяжки по высоте: колонки заполнены по-разному,
          и более короткая тянулась пустотой до высоты соседней. С `lg`, когда
          колонки встают рядом, списки внутри прокручиваются сами — иначе
          длинный процесс уводил бы график далеко за экран. На узком экране
          вложенная прокрутка только мешает: страница и так листается. */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        {/* Left Column: Should-Be (Эталон) */}
        <div className="p-4 rounded-xl border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between pb-2 border-b">
            <div className="flex items-center gap-2">
              <span className="h-3 w-3 rounded-full bg-emerald-500" />
              <h3 className="font-bold text-sm text-foreground">
                Should-Be (Эталон из Draw.io & PIX)
              </h3>
            </div>
            <Badge variant="outline" className="text-[10px] text-emerald-600 border-emerald-500/30">
              Целевой регламент
            </Badge>
          </div>

          <p className="text-xs text-muted-foreground">
            Утвержденный маршрут заявки без избыточных ручных проверок и с четкими SLA:
          </p>

          <div className="space-y-2 lg:max-h-[26rem] lg:overflow-y-auto lg:pr-1">
            {process.nodes
              .filter((n) => isTaskNode(n.type))
              .map((task, idx) => (
                <div
                  key={task.id}
                  className="p-2.5 rounded-lg border bg-muted/30 flex items-center justify-between text-xs"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-muted-foreground text-[10px]">
                      {task.code || `${idx + 1}.`}
                    </span>
                    <span className="font-medium text-foreground">{task.name}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge variant="secondary" className="text-[10px]">
                      {task.slaMinutes || 30} мин
                    </Badge>
                    <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                  </div>
                </div>
              ))}
          </div>
        </div>

        {/* Right Column: As-Is Deviations (Фактические отклонения из Processet) */}
        <div className="p-4 rounded-xl border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between pb-2 border-b">
            <div className="flex items-center gap-2">
              <span className="h-3 w-3 rounded-full bg-rose-500" />
              <h3 className="font-bold text-sm text-foreground">
                As-Is (Фактические отклонения Processet)
              </h3>
            </div>
            <Badge variant="outline" className="text-[10px] text-rose-600 border-rose-500/30">
              По данным логов
            </Badge>
          </div>

          <p className="text-xs text-muted-foreground">
            Узкие места, негласные шаги и повторные циклы, обнаруженные алгоритмами Process Mining:
          </p>

          <div className="space-y-2.5 lg:max-h-[26rem] lg:overflow-y-auto lg:pr-1">
            {metrics.deviations.map((dev) => (
              <div
                key={dev.id}
                className="p-3 rounded-lg border bg-muted/40 text-xs space-y-1.5 hover:bg-muted/70 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 font-semibold text-foreground">
                    {getTypeIcon(dev.type)}
                    <span>{dev.title}</span>
                  </div>
                  {getSeverityBadge(dev.severity)}
                </div>

                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  {dev.description}
                </p>

                <div className="pt-1 flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>Зафиксировано случаев: <strong className="text-foreground">{dev.occurrenceCount}</strong></span>
                  <span>Потери времени: <strong className="text-amber-600">+{dev.totalDelayHours}ч</strong></span>
                </div>

                {dev.rpaOpportunity && (
                  <div className="mt-1.5 p-2 rounded bg-emerald-500/10 border border-emerald-500/20 text-[11px] text-emerald-800 dark:text-emerald-300 flex items-start gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-emerald-600 shrink-0 mt-0.5" />
                    <span><strong>Решение в PIX RPA:</strong> {dev.rpaOpportunity}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Chart: Target SLA vs Actual Lead Time per Step */}
      <div className="p-4 rounded-xl border bg-card shadow-sm space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-bold text-sm text-foreground">
              Сравнение времени этапов: Норматив регламента vs Факт Processet
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Наглядно демонстрирует этапы с максимальным превышением нормативного времени
            </p>
          </div>
        </div>

        <div className="h-64 w-full pt-2">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} vertical={false} />
              <XAxis
                dataKey="name"
                fontSize={11}
                tickLine={false}
                axisLine={{ stroke: chartTheme.grid }}
                tick={{ fill: chartTheme.axis }}
              />
              <YAxis
                fontSize={11}
                tickLine={false}
                axisLine={false}
                tick={{ fill: chartTheme.axis }}
                label={{ value: 'Часы', angle: -90, position: 'insideLeft', fontSize: 10, fill: chartTheme.axis }}
              />
              <Tooltip
                cursor={{ fill: chartTheme.grid, opacity: 0.35 }}
                contentStyle={{
                  fontSize: '11px',
                  borderRadius: '8px',
                  background: chartTheme.tooltipBg,
                  border: `1px solid ${chartTheme.tooltipBorder}`,
                  color: chartTheme.tooltipText,
                }}
                labelStyle={{ color: chartTheme.tooltipText, fontWeight: 600 }}
                itemStyle={{ color: chartTheme.tooltipText }}
                formatter={(value: any) => [`${value} ч`, '']}
              />
              <Legend wrapperStyle={{ fontSize: '11px', color: chartTheme.axis }} />
              <Bar dataKey="Эталон SLA (ч)" fill="#10b981" radius={[4, 4, 0, 0]} maxBarSize={44} />
              <Bar dataKey="Факт Processet (ч)" fill="#f59e0b" radius={[4, 4, 0, 0]} maxBarSize={44} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
