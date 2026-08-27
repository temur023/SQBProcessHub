import React, { useState, useMemo } from 'react'
import {
  Search,
  Cpu,
  Clock,
  Server,
  FileCheck,
  Edit3,
  Sparkles,
  CheckCircle2,
  SlidersHorizontal,
} from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatTile } from '@/components/StatTile'
import type { BusinessProcess, ProcessNode, StepCategory } from '@/types/process'
import { isTaskNode, isArtifactNode } from '@/types/process'
import { formatDuration } from '@/lib/bpmn-export'

interface StepMatrixTableProps {
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
}

const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'Все типы операций' },
  { value: 'rpa_bot', label: 'Роботы PIX RPA' },
  { value: 'manual', label: 'Ручные задачи' },
  { value: 'approval', label: 'Согласования' },
  { value: 'validation', label: 'Проверки и скоринг' },
  { value: 'api_service', label: 'API АБС' },
]

/** Шаг с длительностью 2 часа и больше — кандидат в узкое место. */
const SLOW_STEP_MINUTES = 120

export const StepMatrixTable: React.FC<StepMatrixTableProps> = ({
  process,
  onSelectNode,
}) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string>('all')
  const [selectedLane, setSelectedLane] = useState<string>('all')

  const flowNodes = useMemo(() => {
    return process.nodes.filter((n) => n.type !== 'lane' && !isArtifactNode(n.type))
  }, [process.nodes])

  const filteredNodes = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    return flowNodes.filter((node) => {
      const matchSearch =
        !query ||
        node.name.toLowerCase().includes(query) ||
        (node.code && node.code.toLowerCase().includes(query)) ||
        (node.role && node.role.toLowerCase().includes(query)) ||
        (node.system && node.system.toLowerCase().includes(query))

      const matchCategory = selectedCategory === 'all' || node.category === selectedCategory
      const matchLane = selectedLane === 'all' || node.laneId === selectedLane

      return matchSearch && matchCategory && matchLane
    })
  }, [flowNodes, searchQuery, selectedCategory, selectedLane])

  // Aggregate Metrics
  const totalTasks = flowNodes.filter((n) => isTaskNode(n.type)).length
  const rpaTasks = flowNodes.filter((n) => n.category === 'rpa_bot').length
  const totalSlaMin = flowNodes.reduce((sum, n) => sum + (n.slaMinutes || 0), 0)
  const avgAutomation = Math.round(
    flowNodes.reduce((sum, n) => sum + (n.automationPotential || 0), 0) / (flowNodes.length || 1),
  )
  const filtered = filteredNodes.length !== flowNodes.length

  const getCategoryBadge = (category?: StepCategory) => {
    switch (category) {
      case 'rpa_bot':
        return (
          <Badge className="gap-1 bg-emerald-600 text-[10px] text-white hover:bg-emerald-700">
            <Cpu className="h-3 w-3" /> Робот PIX RPA
          </Badge>
        )
      case 'approval':
        return (
          <Badge className="gap-1 bg-purple-600 text-[10px] text-white hover:bg-purple-700">
            <FileCheck className="h-3 w-3" /> Согласование
          </Badge>
        )
      case 'validation':
        return (
          <Badge className="gap-1 bg-blue-600 text-[10px] text-white hover:bg-blue-700">
            <CheckCircle2 className="h-3 w-3" /> Проверка / скоринг
          </Badge>
        )
      case 'api_service':
        return (
          <Badge className="gap-1 bg-cyan-600 text-[10px] text-white hover:bg-cyan-700">
            <Server className="h-3 w-3" /> API АБС
          </Badge>
        )
      default:
        return (
          <Badge variant="outline" className="gap-1 text-[10px] text-muted-foreground">
            Ручная операция
          </Badge>
        )
    }
  }

  /**
   * Время шага. Пустое значение показываем прочерком, а не подставляем «30 мин»:
   * выдуманное число в регламенте неотличимо от норматива, снятого с карты, и
   * уезжает в выгрузку для PIX как настоящее.
   */
  const renderDuration = (node: ProcessNode) => {
    const st = formatDuration(node.slaMinutes)
    const wt = formatDuration(node.waitMinutes)
    if (!st && !wt) return <span className="text-muted-foreground">—</span>
    const slow = (node.slaMinutes || 0) >= SLOW_STEP_MINUTES
    return (
      <div className="inline-flex flex-col items-center gap-0.5">
        {st && (
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums ${
              slow
                ? 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
                : 'bg-muted text-foreground'
            }`}
          >
            {st}
          </span>
        )}
        {wt && (
          <span className="text-[10px] text-muted-foreground tabular-nums">ожидание {wt}</span>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* ── Сводка по регламенту ─────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-2.5 sm:gap-3 xl:grid-cols-4">
        <StatTile
          label="Всего шагов в процессе"
          value={totalTasks}
          hint="Определено по карте draw.io"
          icon={FileCheck}
          tone="sky"
        />
        <StatTile
          label="Роботизировано PIX RPA"
          value={rpaTasks}
          suffix={`(${Math.round((rpaTasks / (totalTasks || 1)) * 100)}%)`}
          hint="Шаги без участия сотрудника"
          icon={Cpu}
          tone="emerald"
        />
        <StatTile
          label="Суммарный норматив"
          value={formatDuration(totalSlaMin) || '—'}
          hint="Сумма времени операций по шагам"
          icon={Clock}
          tone="amber"
        />
        <StatTile
          label="Индекс роботизации"
          value={`${avgAutomation}%`}
          icon={Sparkles}
          tone="purple"
          footer={<Progress value={avgAutomation} className="h-1.5" />}
        />
      </div>

      {/* ── Поиск и фильтры ──────────────────────────────────────────────── */}
      <div className="flex flex-col gap-2.5 rounded-xl border bg-card p-3 shadow-sm lg:flex-row lg:items-center">
        <div className="relative lg:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Поиск по названию, коду, системе или роли…"
            className="h-9 pl-9 text-xs"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Поиск по шагам регламента"
          />
        </div>

        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center lg:ml-auto">
          <Select value={selectedCategory} onValueChange={setSelectedCategory}>
            <SelectTrigger className="h-9 w-full text-xs sm:w-52" aria-label="Тип операции">
              <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CATEGORY_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value} className="text-xs">
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={selectedLane} onValueChange={setSelectedLane}>
            <SelectTrigger className="h-9 w-full text-xs sm:w-60" aria-label="Подразделение">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all" className="text-xs">
                Все подразделения
              </SelectItem>
              {process.lanes.map((lane) => (
                <SelectItem key={lane.id} value={lane.id} className="text-xs">
                  {lane.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <p className="px-1 text-[11px] text-muted-foreground">
        Показано <strong className="tabular-nums text-foreground">{filteredNodes.length}</strong> из{' '}
        <strong className="tabular-nums text-foreground">{flowNodes.length}</strong> шагов
        {filtered && ' по заданным фильтрам'}
      </p>

      {/* ── Таблица регламента: широкий экран ────────────────────────────── */}
      <div className="hidden overflow-hidden rounded-xl border bg-card shadow-sm lg:block">
        <div className="max-h-[calc(100vh-24rem)] overflow-auto">
          <table className="w-full min-w-[1040px] text-left text-xs">
            {/* Заголовок липкий: в регламенте на сотню шагов колонки уезжают из
                виду на первом же экране прокрутки. */}
            <thead className="sticky top-0 z-10 border-b bg-muted/95 text-[10px] uppercase tracking-wider text-muted-foreground backdrop-blur">
              <tr>
                <th className="px-3.5 py-2.5 font-semibold">Код</th>
                <th className="px-3.5 py-2.5 font-semibold">Наименование шага</th>
                <th className="px-3.5 py-2.5 font-semibold">Тип операции</th>
                <th className="px-3.5 py-2.5 font-semibold">Подразделение / роль</th>
                <th className="px-3.5 py-2.5 font-semibold">ИТ-система</th>
                <th className="px-3.5 py-2.5 text-center font-semibold">Время</th>
                <th className="px-3.5 py-2.5 font-semibold">Потенциал RPA</th>
                <th className="w-10 px-3.5 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y">
              {filteredNodes.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-10 text-center text-muted-foreground">
                    Шаги по заданным критериям не найдены
                  </td>
                </tr>
              ) : (
                filteredNodes.map((node) => (
                  <tr
                    key={node.id}
                    onClick={() => onSelectNode(node)}
                    className="group cursor-pointer transition-colors odd:bg-muted/20 hover:bg-emerald-500/5"
                  >
                    <td className="px-3.5 py-2.5 font-mono font-semibold text-foreground">
                      {node.code || '—'}
                    </td>
                    <td className="px-3.5 py-2.5">
                      <div className="font-semibold text-foreground transition-colors group-hover:text-emerald-600 dark:group-hover:text-emerald-400">
                        {node.name}
                      </div>
                      {node.description && (
                        <p className="mt-0.5 line-clamp-1 max-w-md text-[11px] text-muted-foreground">
                          {node.description}
                        </p>
                      )}
                    </td>
                    <td className="px-3.5 py-2.5">{getCategoryBadge(node.category)}</td>
                    <td className="px-3.5 py-2.5">
                      <div className="font-medium text-foreground">
                        {node.laneName || 'Операционный блок'}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {node.role || 'Исполнитель'}
                      </div>
                    </td>
                    <td className="px-3.5 py-2.5">
                      <span className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                        {node.system || '—'}
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5 text-center">{renderDuration(node)}</td>
                    <td className="px-3.5 py-2.5">
                      <div className="flex items-center gap-2">
                        <Progress value={node.automationPotential || 0} className="h-1.5 w-16" />
                        <span className="text-[11px] font-semibold tabular-nums text-foreground">
                          {node.automationPotential || 0}%
                        </span>
                      </div>
                    </td>
                    <td className="px-3.5 py-2.5 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Открыть карточку шага «${node.name}»`}
                        className="h-7 w-7 p-0 text-muted-foreground group-hover:text-emerald-600"
                        onClick={(e) => {
                          e.stopPropagation()
                          onSelectNode(node)
                        }}
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Тот же регламент карточками: планшет и телефон ───────────────── */}
      {/* Таблица на восемь колонок в 400 px превращается в горизонтальную
          ленту, по которой невозможно читать: на узком экране каждый шаг
          показывается карточкой целиком. */}
      <div className="space-y-2.5 lg:hidden">
        {filteredNodes.length === 0 ? (
          <div className="rounded-xl border bg-card py-10 text-center text-xs text-muted-foreground shadow-sm">
            Шаги по заданным критериям не найдены
          </div>
        ) : (
          filteredNodes.map((node) => (
            <button
              key={node.id}
              type="button"
              onClick={() => onSelectNode(node)}
              className="w-full rounded-xl border bg-card p-3 text-left shadow-sm transition-colors hover:border-emerald-500/40 hover:bg-emerald-500/5"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-mono text-[10px] font-semibold text-muted-foreground">
                    {node.code || '—'}
                  </span>
                  <div className="text-sm font-semibold leading-snug text-foreground">
                    {node.name}
                  </div>
                </div>
                <div className="shrink-0">{renderDuration(node)}</div>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {getCategoryBadge(node.category)}
                <Badge variant="outline" className="text-[10px] text-muted-foreground">
                  {node.laneName || 'Операционный блок'}
                </Badge>
                {node.system && (
                  <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">
                    {node.system}
                  </Badge>
                )}
              </div>

              <div className="mt-2.5 flex items-center gap-2">
                <span className="shrink-0 text-[10px] text-muted-foreground">Потенциал RPA</span>
                <Progress value={node.automationPotential || 0} className="h-1.5 flex-1" />
                <span className="shrink-0 text-[11px] font-semibold tabular-nums text-foreground">
                  {node.automationPotential || 0}%
                </span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  )
}
