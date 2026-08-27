import React from 'react'
import type { LucideIcon } from 'lucide-react'

/**
 * Плитка метрики — единая для всех разделов.
 *
 * Раньше каждая карточка была собрана на месте своим набором классов: где-то
 * `text-2xl`, где-то `text-xl`, разные отступы и разные оттенки подписи. На
 * одном экране такие плитки читаются как куски разных интерфейсов, поэтому
 * форма зафиксирована здесь, а раздел задаёт только смысл и акцент.
 */
export type StatTone = 'neutral' | 'emerald' | 'amber' | 'purple' | 'sky' | 'rose'

const TONE: Record<StatTone, { value: string; icon: string }> = {
  neutral: { value: 'text-foreground', icon: 'bg-muted text-muted-foreground' },
  emerald: {
    value: 'text-emerald-600 dark:text-emerald-400',
    icon: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  },
  amber: {
    value: 'text-amber-600 dark:text-amber-400',
    icon: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  },
  purple: {
    value: 'text-purple-600 dark:text-purple-400',
    icon: 'bg-purple-500/10 text-purple-600 dark:text-purple-400',
  },
  sky: {
    value: 'text-sky-600 dark:text-sky-400',
    icon: 'bg-sky-500/10 text-sky-600 dark:text-sky-400',
  },
  rose: {
    value: 'text-rose-600 dark:text-rose-400',
    icon: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
  },
}

interface StatTileProps {
  label: string
  value: React.ReactNode
  /** Единица или доля рядом со значением — набирается мельче и не спорит с ним. */
  suffix?: React.ReactNode
  hint?: React.ReactNode
  icon?: LucideIcon
  tone?: StatTone
  /** Строка под значением: прогресс, спарклайн, что угодно. */
  footer?: React.ReactNode
}

export const StatTile: React.FC<StatTileProps> = ({
  label,
  value,
  suffix,
  hint,
  icon: Icon,
  tone = 'neutral',
  footer,
}) => {
  const palette = TONE[tone]
  return (
    <div className="group relative flex items-start justify-between gap-2 rounded-xl border bg-card p-3 shadow-sm transition-colors hover:border-foreground/15 sm:gap-3 sm:p-4">
      <div className="min-w-0 flex-1">
        {/* Подпись переносится, а не обрезается: в двух колонках на телефоне
            «Роботизировано PIX RPA» не помещается в строку, и с `truncate`
            от неё оставалось «Роботизиро…». */}
        <p className="text-[11px] font-medium leading-snug text-muted-foreground sm:text-xs" title={label}>
          {label}
        </p>
        <p className={`metric-value mt-1 text-xl font-semibold leading-none sm:text-2xl ${palette.value}`}>
          {value}
          {suffix ? (
            <span className="ml-1 text-xs font-normal text-muted-foreground">{suffix}</span>
          ) : null}
        </p>
        {hint ? (
          <p className="mt-1.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">{hint}</p>
        ) : null}
        {footer ? <div className="mt-2">{footer}</div> : null}
      </div>
      {/* Значок — украшение при значении: на телефоне он отбирает у плитки
          треть ширины, поэтому ниже `sm` его не показываем. */}
      {Icon ? (
        <div
          aria-hidden
          className={`hidden h-10 w-10 shrink-0 items-center justify-center rounded-xl sm:flex ${palette.icon}`}
        >
          <Icon className="h-5 w-5" />
        </div>
      ) : null}
    </div>
  )
}
