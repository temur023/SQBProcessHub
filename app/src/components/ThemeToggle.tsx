import React, { useEffect, useState } from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'

const MODES = [
  { value: 'light', label: 'Светлая тема', Icon: Sun },
  { value: 'dark', label: 'Тёмная тема', Icon: Moon },
  { value: 'system', label: 'Как в системе', Icon: Monitor },
] as const

/**
 * Переключатель темы — сегментами, а не одной кнопкой-циклом.
 *
 * Кнопка, перебирающая три режима по кругу, не показывает текущее состояние:
 * сотрудник не понимает, стоит ли у него «тёмная» или «как в системе». Здесь
 * активный режим виден всегда.
 *
 * До монтирования next-themes не знает разрешённую тему, поэтому подсветку
 * рисуем только на клиенте — иначе на первом кадре подсвечивался бы неверный
 * сегмент.
 */
export const ThemeToggle: React.FC = () => {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  return (
    <div
      role="radiogroup"
      aria-label="Тема оформления"
      className="inline-flex items-center rounded-lg border bg-muted/50 p-0.5"
    >
      {MODES.map(({ value, label, Icon }) => {
        const active = mounted && theme === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => setTheme(value)}
            className={`flex h-7 w-7 items-center justify-center rounded-md transition-colors ${
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
          </button>
        )
      })}
    </div>
  )
}
