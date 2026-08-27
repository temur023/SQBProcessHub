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
} from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import type { BusinessProcess, ProcessNode, StepCategory } from '@/types/process'
import { isTaskNode, isArtifactNode } from '@/types/process'

interface StepMatrixTableProps {
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
}

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
    return flowNodes.filter((node) => {
      const matchSearch =
        node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (node.code && node.code.toLowerCase().includes(searchQuery.toLowerCase())) ||
        (node.role && node.role.toLowerCase().includes(searchQuery.toLowerCase())) ||
        (node.system && node.system.toLowerCase().includes(searchQuery.toLowerCase()))

      const matchCategory =
        selectedCategory === 'all' || node.category === selectedCategory
      const matchLane =
        selectedLane === 'all' || node.laneId === selectedLane

      return matchSearch && matchCategory && matchLane
    })
  }, [flowNodes, searchQuery, selectedCategory, selectedLane])

  // Aggregate Metrics
  const totalTasks = flowNodes.filter(
    (n) => isTaskNode(n.type),
  ).length
  const rpaTasks = flowNodes.filter((n) => n.category === 'rpa_bot').length
  const totalSlaMin = flowNodes.reduce((sum, n) => sum + (n.slaMinutes || 0), 0)
  const avgAutomation = Math.round(
    flowNodes.reduce((sum, n) => sum + (n.automationPotential || 0), 0) / (flowNodes.length || 1),
  )

  const getCategoryBadge = (category?: StepCategory) => {
    switch (category) {
      case 'rpa_bot':
        return (
          <Badge className="bg-emerald-600 hover:bg-emerald-700 text-white gap-1 text-[10px]">
            <Cpu className="w-3 h-3" /> Робот PIX RPA
          </Badge>
        )
      case 'approval':
        return (
          <Badge className="bg-purple-600 hover:bg-purple-700 text-white gap-1 text-[10px]">
            <FileCheck className="w-3 h-3" /> Согласование
          </Badge>
        )
      case 'validation':
        return (
          <Badge className="bg-blue-600 hover:bg-blue-700 text-white gap-1 text-[10px]">
            <CheckCircle2 className="w-3 h-3" /> Проверка / Скоринг
          </Badge>
        )
      case 'api_service':
        return (
          <Badge className="bg-cyan-600 hover:bg-cyan-700 text-white gap-1 text-[10px]">
            <Server className="w-3 h-3" /> API АБС
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

  return (
    <div className="space-y-4">
      {/* Top Aggregate KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="p-4 rounded-xl border bg-card shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Всего шагов в процессе</p>
            <h3 className="text-2xl font-bold text-foreground mt-0.5">{totalTasks}</h3>
            <p className="text-[11px] text-emerald-600 mt-1">Определено из Draw.io</p>
          </div>
          <div className="h-10 w-10 rounded-xl bg-blue-100 dark:bg-blue-950/60 flex items-center justify-center text-blue-600">
            <FileCheck className="w-5 h-5" />
          </div>
        </div>

        <div className="p-4 rounded-xl border bg-card shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Роботизировано PIX RPA</p>
            <h3 className="text-2xl font-bold text-emerald-600 mt-0.5">
              {rpaTasks} <span className="text-xs font-normal text-muted-foreground">({Math.round((rpaTasks / (totalTasks || 1)) * 100)}%)</span>
            </h3>
            <p className="text-[11px] text-muted-foreground mt-1">Автоматические шаги</p>
          </div>
          <div className="h-10 w-10 rounded-xl bg-emerald-100 dark:bg-emerald-950/60 flex items-center justify-center text-emerald-600">
            <Cpu className="w-5 h-5" />
          </div>
        </div>

        <div className="p-4 rounded-xl border bg-card shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Суммарный норматив SLA</p>
            <h3 className="text-2xl font-bold text-foreground mt-0.5">
              {Math.round(totalSlaMin / 60)}ч {totalSlaMin % 60}м
            </h3>
            <p className="text-[11px] text-amber-600 mt-1">Норматив регламента</p>
          </div>
          <div className="h-10 w-10 rounded-xl bg-amber-100 dark:bg-amber-950/60 flex items-center justify-center text-amber-600">
            <Clock className="w-5 h-5" />
          </div>
        </div>

        <div className="p-4 rounded-xl border bg-card shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Индекс роботизации (PIX)</p>
            <h3 className="text-2xl font-bold text-purple-600 mt-0.5">{avgAutomation}%</h3>
            <div className="w-24 mt-1.5">
              <Progress value={avgAutomation} className="h-1.5" />
            </div>
          </div>
          <div className="h-10 w-10 rounded-xl bg-purple-100 dark:bg-purple-950/60 flex items-center justify-center text-purple-600">
            <Sparkles className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Filters & Search Toolbar */}
      <div className="p-3.5 rounded-xl border bg-card shadow-sm flex flex-col md:flex-row items-center justify-between gap-3">
        <div className="relative w-full md:w-80">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Поиск по названию, коду, системе или роли..."
            className="pl-9 h-9 text-xs"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-2 w-full md:w-auto overflow-x-auto">
          {/* Category Filter */}
          <select
            className="h-9 px-3 text-xs rounded-md border bg-background text-foreground"
            value={selectedCategory}
            onChange={(e) => setSelectedCategory(e.target.value)}
          >
            <option value="all">Все типы операций</option>
            <option value="rpa_bot">PIX RPA Роботы</option>
            <option value="manual">Ручные задачи</option>
            <option value="approval">Согласования</option>
            <option value="validation">Проверки & Скоринг</option>
            <option value="api_service">API АБС</option>
          </select>

          {/* Lane Filter */}
          <select
            className="h-9 px-3 text-xs rounded-md border bg-background text-foreground"
            value={selectedLane}
            onChange={(e) => setSelectedLane(e.target.value)}
          >
            <option value="all">Все подразделения / дорожки</option>
            {process.lanes.map((lane) => (
              <option key={lane.id} value={lane.id}>
                {lane.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Process Step Regulation Table */}
      <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="bg-muted/70 text-muted-foreground uppercase text-[10px] tracking-wider border-b">
              <tr>
                <th className="px-3.5 py-3 font-semibold">Код</th>
                <th className="px-3.5 py-3 font-semibold">Наименование шага</th>
                <th className="px-3.5 py-3 font-semibold">Тип операции</th>
                <th className="px-3.5 py-3 font-semibold">Подразделение / Роль</th>
                <th className="px-3.5 py-3 font-semibold">ИТ-Система</th>
                <th className="px-3.5 py-3 font-semibold text-center">SLA (мин)</th>
                <th className="px-3.5 py-3 font-semibold">Потенциал PIX RPA</th>
                <th className="px-3.5 py-3 font-semibold text-right">Действие</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filteredNodes.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-center py-8 text-muted-foreground">
                    Шаги по заданным критериям не найдены
                  </td>
                </tr>
              ) : (
                filteredNodes.map((node) => (
                  <tr
                    key={node.id}
                    onClick={() => onSelectNode(node)}
                    className="hover:bg-muted/50 transition-colors cursor-pointer group"
                  >
                    <td className="px-3.5 py-3 font-mono font-bold text-foreground">
                      {node.code || '—'}
                    </td>
                    <td className="px-3.5 py-3">
                      <div className="font-semibold text-foreground group-hover:text-emerald-600 transition-colors">
                        {node.name}
                      </div>
                      {node.description && (
                        <p className="text-[11px] text-muted-foreground line-clamp-1 mt-0.5 max-w-md">
                          {node.description}
                        </p>
                      )}
                    </td>
                    <td className="px-3.5 py-3">{getCategoryBadge(node.category)}</td>
                    <td className="px-3.5 py-3">
                      <div className="font-medium text-foreground">
                        {node.laneName || 'Операционный блок'}
                      </div>
                      <div className="text-[10px] text-muted-foreground">{node.role || 'Исполнитель'}</div>
                    </td>
                    <td className="px-3.5 py-3 font-mono text-slate-600 dark:text-slate-400">
                      <span className="px-1.5 py-0.5 rounded bg-muted border text-[11px]">
                        {node.system || 'АБС ЦФТ'}
                      </span>
                    </td>
                    <td className="px-3.5 py-3 text-center font-semibold">
                      <span
                        className={`px-2 py-0.5 rounded-full text-[11px] ${
                          (node.slaMinutes || 0) >= 120
                            ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                            : 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200'
                        }`}
                      >
                        {node.slaMinutes || 30} мин
                      </span>
                    </td>
                    <td className="px-3.5 py-3">
                      <div className="flex items-center gap-2">
                        <Progress value={node.automationPotential || 0} className="w-16 h-1.5" />
                        <span className="font-semibold text-foreground text-[11px]">
                          {node.automationPotential || 0}%
                        </span>
                      </div>
                    </td>
                    <td className="px-3.5 py-3 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 w-7 p-0 text-muted-foreground group-hover:text-emerald-600"
                        onClick={(e) => {
                          e.stopPropagation()
                          onSelectNode(node)
                        }}
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
