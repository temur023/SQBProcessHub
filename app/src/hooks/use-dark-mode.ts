import { useEffect, useState } from 'react'

/**
 * Включена ли тёмная тема прямо сейчас.
 *
 * Холст карты и графики рисуются не классами Tailwind, а атрибутами SVG и
 * настройками recharts — им нужен не `dark:`-вариант, а конкретный цвет. Поэтому
 * тему приходится знать в JS.
 *
 * Читаем не состояние `next-themes`, а класс на `<html>`: провайдер выставляет
 * его блокирующим скриптом до первой отрисовки, тогда как `resolvedTheme` на
 * первом рендере ещё `undefined` — карта успела бы мигнуть чужой палитрой.
 * MutationObserver ловит и переключение тумблером, и смену системной темы.
 */
export function useIsDark(): boolean {
  const [isDark, setIsDark] = useState(
    () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  )

  useEffect(() => {
    const root = document.documentElement
    const sync = () => setIsDark(root.classList.contains('dark'))
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(root, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  return isDark
}
