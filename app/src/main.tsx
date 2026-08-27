import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { ThemeProvider } from 'next-themes'
import './index.css'
import App from './App.tsx'

// Тёмная тема была описана в токенах и в десятках компонентов (`dark:`), но
// включить её было нечем: класс `dark` никто не выставлял. Провайдер закрывает
// это и заодно уважает системную настройку — карта процесса рисуется на тёмном
// холсте, и светлый интерфейс вокруг неё бил по глазам.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
)
