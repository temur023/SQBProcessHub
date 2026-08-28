import React, { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Crosshair, Info, XCircle } from 'lucide-react'
import type { BusinessProcess, ProcessValidation } from '@/types/process'
import { issueKey, issueNodeIds } from '@/lib/diagnostics'

interface ImportReportProps {
  process: BusinessProcess
  /**
   * Клик по замечанию наводит карту на его фигуры и подсвечивает их.
   *
   * Отдаёт и само замечание (его текст показывается подсказкой над холстом),
   * и список фигур, и ключ строки. Ключ считает отчёт, а не вызывающий код:
   * строки отсортированы по важности, и порядковый номер в исходном списке
   * замечаний уже другой.
   */
  onFocusIssue?: (issue: ProcessValidation, nodeIds: string[], key: string) => void
  /** Замечание, подсвеченное на карте прямо сейчас. */
  activeIssueKey?: string
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
export const ImportReport: React.FC<ImportReportProps> = ({
  process,
  onFocusIssue,
  activeIssueKey,
}) => {
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

  const nodeIdSet = useMemo(() => new Set(process.nodes.map((n) => n.id)), [process.nodes])

  /** Фигуры замечания, которые действительно остались на карте. */
  const shapesOf = (issue: ProcessValidation): string[] =>
    issueNodeIds(issue).filter((id) => nodeIdSet.has(id))

  if (!issues.length) {
    return (
      <div className="border-b bg-emerald-500/5">
        {/* Та же сетка, что у шапки и основной области: без общего контейнера
            полоса начиналась от края окна, а всё остальное — от логотипа. */}
        <div className="mx-auto flex w-full max-w-[1600px] items-center gap-2 px-3 py-1.5 sm:px-4">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <span className="truncate text-xs text-emerald-700 dark:text-emerald-400">
            Карта «{process.fileName}» импортирована без замечаний.
          </span>
        </div>
      </div>
    )
  }

  const headline = counts.error
    ? `${plural(counts.error, 'ошибка', 'ошибки', 'ошибок')} в карте — выгрузка будет неполной`
    : counts.warning
    ? `${plural(counts.warning, 'предупреждение', 'предупреждения', 'предупреждений')} по карте`
    : 'Карта импортирована, есть уточнения'

  return (
    <div className="border-b bg-muted/40">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left transition-colors hover:bg-muted/70"
      >
        <span className="mx-auto flex w-full max-w-[1600px] items-center gap-2 px-3 py-2 sm:px-4">
          {expanded ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <span className="shrink-0 text-xs font-semibold">Проверка импорта:</span>
          {/* Заголовок обрезается, а счётчики — нет: на телефоне уводить
              цифры за край нельзя, ради них панель и открывают. */}
          <span
            className={`min-w-0 flex-1 truncate text-xs ${
              counts.error ? LEVEL_STYLE.error.text : counts.warning ? LEVEL_STYLE.warning.text : LEVEL_STYLE.info.text
            }`}
          >
            {headline}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            {(['error', 'warning', 'info'] as const)
              .filter((level) => counts[level] > 0)
              .map((level) => (
                <span
                  key={level}
                  className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${LEVEL_STYLE[level].chip}`}
                >
                  {LEVEL_STYLE[level].icon}
                  {counts[level]}
                </span>
              ))}
          </span>
        </span>
      </button>

      {expanded && (
        <div className="mx-auto w-full max-w-[1600px] max-h-[32vh] space-y-3 overflow-auto px-3 pb-3 sm:px-4">
          {(['error', 'warning', 'info'] as const)
            .filter((level) => counts[level] > 0)
            .map((level) => (
              <div key={level}>
                <div className={`text-[10px] uppercase tracking-wide font-semibold mb-1 ${LEVEL_STYLE[level].text}`}>
                  {LEVEL_TITLE[level]} · {counts[level]}
                </div>
                <ul className="space-y-1">
                  {issues
                    .map((issue, index) => ({ issue, key: issueKey(issue, index) }))
                    .filter(({ issue }) => issue.level === level)
                    .map(({ issue, key }) => {
                      const shapes = shapesOf(issue)
                      const clickable = Boolean(shapes.length && onFocusIssue)
                      const active = clickable && activeIssueKey === key
                      return (
                        <li key={key}>
                          {/* Кнопка, а не кликабельный <li>: строку отчёта надо
                              уметь выбрать с клавиатуры — иначе замечание
                              недоступно тем, кто не работает мышью. */}
                          <button
                            type="button"
                            disabled={!clickable}
                            aria-pressed={active}
                            onClick={() => {
                              // Панель сворачивается: развёрнутый список
                              // занимает треть экрана, и подсвеченный шаг
                              // оставался за нижним краем карты. Текст
                              // замечания сотрудник тут же видит на самой
                              // карте — подсказкой рядом с фигурой.
                              setExpanded(false)
                              onFocusIssue?.(issue, shapes, key)
                            }}
                            className={`w-full rounded border px-2.5 py-1.5 text-left transition-colors ${
                              active
                                ? 'border-orange-500/70 bg-orange-500/10'
                                : 'border-border/60 bg-background/60'
                            } ${clickable ? 'cursor-pointer hover:border-border' : 'cursor-default'}`}
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
                                  <div className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground/80">
                                    <Crosshair className="h-3 w-3 shrink-0" />
                                    {active
                                      ? 'Подсвечено на карте'
                                      : shapes.length > 1
                                      ? `Показать на карте (${shapes.length} фигур)`
                                      : 'Показать на карте'}
                                  </div>
                                )}
                              </div>
                            </div>
                          </button>
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
