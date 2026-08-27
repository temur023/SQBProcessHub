import React, { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Info, XCircle } from 'lucide-react'
import type { BusinessProcess, ProcessNode, ProcessValidation } from '@/types/process'

interface ImportReportProps {
  process: BusinessProcess
  /** Клик по замечанию открывает карточку шага, к которому оно относится. */
  onSelectNode?: (node: ProcessNode) => void
}

/** «1 ошибка», «2 ошибки», «5 ошибок» — иначе заголовок читается как машинный. */
function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = count % 100
  const mod10 = count % 10
  if (mod100 >= 11 && mod100 <= 14) return `${count} ${many}`
  if (mod10 === 1) return `${count} ${one}`
  if (mod10 >= 2 && mod10 <= 4) return `${count} ${few}`
  return `${count} ${many}`
}

const LEVEL_ORDER: Record<ProcessValidation['level'], number> = {
  error: 0,
  warning: 1,
  info: 2,
}

const LEVEL_TITLE: Record<ProcessValidation['level'], string> = {
  error: 'Ошибки',
  warning: 'Предупреждения',
  info: 'Что платформа поправила',
}

const LEVEL_STYLE: Record<ProcessValidation['level'], { text: string; chip: string; icon: React.ReactNode }> = {
  error: {
    text: 'text-red-600 dark:text-red-400',
    chip: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30',
    icon: <XCircle className="w-3.5 h-3.5 shrink-0" />,
  },
  warning: {
    text: 'text-amber-600 dark:text-amber-400',
    chip: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30',
    icon: <AlertTriangle className="w-3.5 h-3.5 shrink-0" />,
  },
  info: {
    text: 'text-sky-600 dark:text-sky-400',
    chip: 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30',
    icon: <Info className="w-3.5 h-3.5 shrink-0" />,
  },
}

/**
 * Отчёт о качестве импортированной карты.
 *
 * Импорт draw.io всегда что-то домысливает — подписи, ветки шлюзов, время шага,
 * привязку линии к фигуре. Сотрудник должен увидеть это сразу после загрузки,
 * а не обнаружить в выгрузке для PIX: замечание без адресата бесполезно,
 * поэтому каждая строка ведёт к конкретной фигуре на карте.
 */
export const ImportReport: React.FC<ImportReportProps> = ({ process, onSelectNode }) => {
  const [expanded, setExpanded] = useState(false)

  const issues = useMemo(
    () =>
      [...(process.validation ?? [])].sort(
        (a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level],
      ),
    [process.validation],
  )
  const counts = useMemo(() => {
    const acc: Record<ProcessValidation['level'], number> = { error: 0, warning: 0, info: 0 }
    for (const issue of issues) acc[issue.level] += 1
    return acc
  }, [issues])

  const nodeById = useMemo(
    () => new Map(process.nodes.map((n) => [n.id, n])),
    [process.nodes],
  )

  if (!issues.length) {
    return (
      <div className="px-4 py-1.5 border-b border-border bg-emerald-500/5 flex items-center gap-2">
        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
        <span className="text-xs text-emerald-700 dark:text-emerald-400">
          Карта «{process.fileName}» импортирована без замечаний.
        </span>
      </div>
    )
  }

  const headline = counts.error
    ? `${plural(counts.error, 'ошибка', 'ошибки', 'ошибок')} в карте — выгрузка будет неполной`
    : counts.warning
    ? `${plural(counts.warning, 'предупреждение', 'предупреждения', 'предупреждений')} по карте`
    : 'Карта импортирована, есть уточнения'

  return (
    <div className="border-b border-border bg-muted/40">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-4 py-2 flex items-center gap-2 text-left hover:bg-muted/70 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0" />
        )}
        <span className="text-xs font-semibold">Проверка импорта:</span>
        <span
          className={`text-xs ${
            counts.error ? LEVEL_STYLE.error.text : counts.warning ? LEVEL_STYLE.warning.text : LEVEL_STYLE.info.text
          }`}
        >
          {headline}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {(['error', 'warning', 'info'] as const)
            .filter((level) => counts[level] > 0)
            .map((level) => (
              <span
                key={level}
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-medium ${LEVEL_STYLE[level].chip}`}
              >
                {LEVEL_STYLE[level].icon}
                {counts[level]}
              </span>
            ))}
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-3 max-h-[32vh] overflow-auto space-y-3">
          {(['error', 'warning', 'info'] as const)
            .filter((level) => counts[level] > 0)
            .map((level) => (
              <div key={level}>
                <div className={`text-[10px] uppercase tracking-wide font-semibold mb-1 ${LEVEL_STYLE[level].text}`}>
                  {LEVEL_TITLE[level]} · {counts[level]}
                </div>
                <ul className="space-y-1">
                  {issues
                    .filter((issue) => issue.level === level)
                    .map((issue, index) => {
                      const node = issue.nodeId ? nodeById.get(issue.nodeId) : undefined
                      const clickable = Boolean(node && onSelectNode)
                      return (
                        <li
                          key={`${issue.code ?? level}-${index}`}
                          className={`rounded border border-border/60 bg-background/60 px-2.5 py-1.5 ${
                            clickable ? 'cursor-pointer hover:border-border' : ''
                          }`}
                          onClick={() => node && onSelectNode?.(node)}
                        >
                          <div className="flex items-start gap-2">
                            <span className={`mt-0.5 ${LEVEL_STYLE[level].text}`}>
                              {LEVEL_STYLE[level].icon}
                            </span>
                            <div className="min-w-0">
                              <div className="text-xs leading-snug">{issue.message}</div>
                              {issue.hint && (
                                <div className="text-[11px] text-muted-foreground leading-snug mt-0.5">
                                  {issue.hint}
                                </div>
                              )}
                              {clickable && (
                                <div className="text-[10px] text-muted-foreground/80 mt-0.5">
                                  Открыть шаг на карте →
                                </div>
                              )}
                            </div>
                          </div>
                        </li>
                      )
                    })}
                </ul>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}
