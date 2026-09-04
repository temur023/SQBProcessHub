import React, { useEffect, useState } from 'react'
import {
  Cpu,
  Download,
  FileSpreadsheet,
  Layers,
  Server,
  Sparkles,
  TrendingUp,
  UploadCloud,
  type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ThemeToggle } from '@/components/ThemeToggle'
import type { BusinessProcess } from '@/types/process'
import { isTaskNode } from '@/types/process'
import { checkBackendHealth } from '@/lib/api'

interface HeaderProps {
  currentProcess: BusinessProcess
  activeTab: string
  setActiveTab: (tab: string) => void
  onOpenImport: () => void
  onOpenExport: () => void
  onExportExcel: () => void
}

interface TabDef {
  id: string
  icon: LucideIcon
  label: string
  /** Подпись для узкого экрана: полная не помещается и рвёт ленту. */
  short: string
  count?: number
  dot?: boolean
}

interface ActionDef {
  id: string
  icon: LucideIcon
  label: string
  title: string
  onClick: () => void
  primary?: boolean
  iconClass?: string
}

const HEALTH_POLL_MS = 15000

/**
 * Шапка платформы.
 *
 * Собрана в две строки с жёстким разделением ролей: сверху — что за процесс
 * открыт и что с ним можно сделать, снизу — навигация и метрики. Раньше всё
 * лежало в одном ряду с `flex-wrap`, и на ноутбуке кнопки уезжали под логотип,
 * а на телефоне шапка занимала пол-экрана.
 *
 * Действия ниже `lg` схлопываются в иконки: подпись переезжает в `title` и
 * `aria-label`, поэтому ряд остаётся проходимым и с клавиатуры, и наощупь.
 */
export const Header: React.FC<HeaderProps> = ({
  currentProcess,
  activeTab,
  setActiveTab,
  onOpenImport,
  onOpenExport,
  onExportExcel,
}) => {
  const [isBackendOnline, setIsBackendOnline] = useState<boolean | null>(null)

  useEffect(() => {
    let alive = true
    const ping = () => {
      void checkBackendHealth().then((status) => {
        if (alive) setIsBackendOnline(status)
      })
    }
    ping()
    const timer = setInterval(ping, HEALTH_POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const rpaCount = currentProcess.nodes.filter((n) => n.category === 'rpa_bot').length
  const totalTasks = currentProcess.nodes.filter((n) => isTaskNode(n.type)).length

  const tabs: TabDef[] = [
    {
      id: 'visualizer',
      icon: Layers,
      label: 'Карта процесса',
      short: 'Карта',
      count: currentProcess.nodes.length,
    },
  ]

  const actions: ActionDef[] = [
    {
      id: 'import',
      icon: UploadCloud,
      label: 'Импорт',
      title: 'Загрузить карту процесса из draw.io',
      onClick: onOpenImport,
      iconClass: 'text-emerald-600 dark:text-emerald-400',
    },
    {
      id: 'excel',
      icon: FileSpreadsheet,
      label: 'Регламент',
      title: 'Скачать таблицу регламента в формате Excel/CSV',
      onClick: onExportExcel,
      iconClass: 'text-green-600 dark:text-green-400',
    },
    {
      id: 'export',
      icon: Download,
      label: 'Экспорт',
      title: 'Центр экспорта: BPMN 2.0, .pmm, Processet, Excel',
      onClick: onOpenExport,
      primary: true,
    },
  ]

  return (
    <header className="sticky top-0 z-40 border-b bg-card/85 backdrop-blur supports-[backdrop-filter]:bg-card/70">
      <div className="mx-auto w-full max-w-[1600px] px-3 sm:px-4">
        {/* ── Строка 1: чей процесс открыт и что с ним делать ────────────── */}
        <div className="flex h-14 items-center gap-2 sm:gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-600 via-teal-700 to-cyan-800 font-bold text-white shadow-sm shadow-emerald-700/20">
            <span className="text-[11px] tracking-tighter">SQB</span>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="hidden shrink-0 text-sm font-semibold tracking-tight sm:inline">
                SQB Process Hub
              </span>
              <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                {currentProcess.passport.code}
              </Badge>
              <Badge variant="outline" className="hidden shrink-0 text-[10px] md:inline-flex">
                v{currentProcess.passport.version}
              </Badge>
            </div>
            <p
              className="truncate text-[11px] text-muted-foreground"
              title={`${currentProcess.passport.name} • ${currentProcess.passport.owner}`}
            >
              {currentProcess.passport.name}
            </p>
          </div>

          {/* Состояние бэкенда: от него зависит, соберётся ли .pmm, поэтому
              значок живёт в шапке, а не прячется в настройках. */}
          <Badge
            variant="outline"
            className={`hidden shrink-0 items-center gap-1.5 font-mono text-[10px] lg:inline-flex ${
              isBackendOnline
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                : 'text-muted-foreground'
            }`}
            title={
              isBackendOnline
                ? 'FastAPI отвечает: доступны экспорт .pmm и серверный разбор карты'
                : 'Бэкенд недоступен: карта разбирается в браузере, экспорт .pmm недоступен'
            }
          >
            {isBackendOnline ? (
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            ) : (
              <Server className="h-2.5 w-2.5" />
            )}
            {isBackendOnline ? 'FastAPI' : 'Локально'}
          </Badge>

          {/* На узком экране ряд действий и так забит, поэтому переключатель
              темы переезжает во вторую строку — ровно один из двух виден. */}
          <div className="hidden md:block">
            <ThemeToggle />
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            {actions.map(({ id, icon: Icon, label, title, onClick, primary, iconClass }) => (
              <Button
                key={id}
                size="sm"
                variant={primary ? 'default' : 'outline'}
                onClick={onClick}
                title={title}
                aria-label={title}
                className={`h-9 gap-1.5 px-2.5 text-xs font-medium lg:px-3 ${
                  primary
                    ? 'bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-sm hover:from-emerald-700 hover:to-teal-700'
                    : ''
                }`}
              >
                <Icon className={`h-4 w-4 shrink-0 ${primary ? '' : iconClass ?? ''}`} />
                <span className="hidden lg:inline">{label}</span>
              </Button>
            ))}
          </div>
        </div>

        {/* ── Строка 2: навигация и метрики процесса ─────────────────────── */}
        <div className="flex items-center gap-3 border-t pt-1.5 pb-1.5">
          <nav
            aria-label="Разделы платформы"
            className="no-scrollbar snap-strip -mx-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto px-1"
          >
            {tabs.map(({ id, icon: Icon, label, short, count, dot }) => {
              const active = activeTab === id
              return (
                <button
                  key={id}
                  type="button"
                  aria-current={active ? 'page' : undefined}
                  onClick={() => setActiveTab(id)}
                  className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    active
                      ? 'bg-primary text-primary-foreground shadow-sm'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="hidden sm:inline">{label}</span>
                  <span className="sm:hidden">{short}</span>
                  {count != null && (
                    <span
                      className={`rounded px-1.5 py-px text-[10px] tabular-nums ${
                        active ? 'bg-primary-foreground/20' : 'bg-muted-foreground/15'
                      }`}
                    >
                      {count}
                    </span>
                  )}
                  {dot && (
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-purple-400 opacity-75" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-purple-500" />
                    </span>
                  )}
                </button>
              )
            })}
          </nav>

          <div className="shrink-0 md:hidden">
            <ThemeToggle />
          </div>

          <div className="hidden shrink-0 items-center gap-3 text-[11px] text-muted-foreground xl:flex">
            <span className="flex items-center gap-1.5" title="Суммарный норматив по шагам карты">
              <Sparkles className="h-3.5 w-3.5 text-amber-500" />
              SLA <strong className="tabular-nums text-foreground">{currentProcess.passport.targetSlaHours} ч</strong>
            </span>
            <span className="h-3 w-px bg-border" />
            <span className="flex items-center gap-1.5" title="Шагов, закрытых роботом PIX RPA">
              <Cpu className="h-3.5 w-3.5 text-emerald-500" />
              RPA{' '}
              <strong className="tabular-nums text-emerald-600 dark:text-emerald-400">
                {rpaCount}/{totalTasks}
              </strong>
            </span>
            <span className="h-3 w-px bg-border" />
            <span className="flex items-center gap-1.5" title="Соответствие факта эталонной карте по данным Processet">
              <TrendingUp className="h-3.5 w-3.5 text-sky-500" />
              Сверка{' '}
              <strong className="tabular-nums text-foreground">
                {currentProcess.miningMetrics.conformanceRate}%
              </strong>
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}
