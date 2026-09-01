import type { BusinessProcess } from '@/types/process'
import { parseDrawio } from './drawio'
import { generateBpmn2Xml } from './bpmn-export'
import { generateProcessetEventLogCsv, generateProcessRegulationCsv, generatePixJson } from './processet-export'

const API_BASE = '/api/v1'

/** Экспорт невозможен без бэкенда — вызывающий код обязан показать это пользователю. */
export class ExportUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ExportUnavailableError'
  }
}

/** Load process from backend store */
export async function loadProcessFromBackend(processId: string): Promise<BusinessProcess | null> {
  try {
    const res = await fetch(`${API_BASE}/processes/${encodeURIComponent(processId)}`)
    if (res.ok) {
      return await res.json()
    }
  } catch (err) {
    console.warn('Failed to load process from backend:', err)
  }
  return null
}

/** List all processes from backend */
export async function listProcessesFromBackend(): Promise<Array<{id: string, name: string}>> {
  try {
    const res = await fetch(`${API_BASE}/processes/`)
    if (res.ok) {
      const data = await res.json()
      return data.map((p: any) => ({ id: p.id, name: p.name }))
    }
  } catch (err) {
    console.warn('Failed to list processes from backend:', err)
  }
  return []
}

/** Check if Python FastAPI server is live on :8000 */
export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch('/api/health', { method: 'GET', cache: 'no-cache' })
    if (res.ok) {
      const data = await res.json()
      return data.status === 'ok'
    }
    return false
  } catch {
    return false
  }
}

/**
 * Платформа отказалась принять карту: в исходнике дефект, из-за которого
 * PIX не откроет выгрузку. Это не сбой связи, а осознанный ответ бэкенда —
 * его текст нужно показать сотруднику целиком, вместе с адресом фигуры.
 */
export class SourceRejectedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SourceRejectedError'
  }
}

/**
 * Отличает «бэкенд отверг файл» от «бэкенда нет».
 *
 * Раньше любой не-OK ответ уводил импорт в клиентский разбор: сотрудник
 * получал карту, собранную в обход проверки, и ни слова о том, что схему
 * забракавали. Отказ по существу (4xx) обязан долететь до экрана, а на
 * недоступность бэкенда (5xx, обрыв связи) по-прежнему работает запасной
 * разбор в браузере — иначе импорт встанет из-за упавшего сервиса.
 */
async function rejectionFrom(res: Response): Promise<SourceRejectedError | null> {
  if (res.ok || res.status >= 500) return null
  let detail = ''
  try {
    const body = await res.json()
    detail = typeof body?.detail === 'string' ? body.detail : ''
  } catch {
    detail = ''
  }
  return new SourceRejectedError(
    detail || `Платформа не приняла файл (код ${res.status}).`,
  )
}

/** Import .drawio, .xml, .bpmn file using Python FastAPI or local fallback */
export async function importDrawioFileApi(file: File): Promise<{ process: BusinessProcess; source: 'fastapi' | 'local' }> {
  try {
    const formData = new FormData()
    formData.append('file', file)

    const res = await fetch(`${API_BASE}/import/file`, {
      method: 'POST',
      body: formData,
    })

    const rejected = await rejectionFrom(res)
    if (rejected) throw rejected

    if (res.ok) {
      const process: BusinessProcess = await res.json()
      if ((process.nodes?.length ?? 0) > 0) {
        return { process, source: 'fastapi' }
      }
    }
  } catch (err) {
    if (err instanceof SourceRejectedError) throw err
    console.warn('FastAPI import endpoint unreachable, using client parser fallback:', err)
  }

  // Local fallback (also used when backend returned an empty graph)
  const text = await file.text()
  const process = await parseDrawio(text, file.name)
  await saveProcessToBackend(process)
  return { process, source: 'local' }
}

/** Import raw XML using Python FastAPI or local fallback */
export async function importDrawioXmlApi(xml: string, fileName: string = 'Pasted_Process.drawio'): Promise<{ process: BusinessProcess; source: 'fastapi' | 'local' }> {
  try {
    const res = await fetch(`${API_BASE}/import/xml`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ xml, fileName }),
    })

    const rejected = await rejectionFrom(res)
    if (rejected) throw rejected

    if (res.ok) {
      const process: BusinessProcess = await res.json()
      if ((process.nodes?.length ?? 0) > 0) {
        return { process, source: 'fastapi' }
      }
    }
  } catch (err) {
    if (err instanceof SourceRejectedError) throw err
    console.warn('FastAPI XML import endpoint unreachable, using client parser fallback:', err)
  }

  // Local fallback (also used when backend returned an empty graph)
  const process = await parseDrawio(xml, fileName)
  await saveProcessToBackend(process)
  return { process, source: 'local' }
}

/** Save process to FastAPI server */
export async function saveProcessToBackend(process: BusinessProcess): Promise<void> {
  try {
    await fetch(`${API_BASE}/processes/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(process),
    })
  } catch {
    // ignore
  }
}

/** Одно замечание к файлу выгрузки — тем же языком, каким о нём скажет PIX. */
export interface ExportProblem {
  level: 'error' | 'warning'
  code: string
  message: string
  /** Идентификатор фигуры или связи в самом файле. */
  where?: string | null
}

export interface ExportFormatCheck {
  format: 'bpmn' | 'pmm'
  ok: boolean
  errors: number
  warnings: number
  problems: ExportProblem[]
}

export interface ExportCheckReport {
  processId: string
  ok: boolean
  summary: string
  formats: ExportFormatCheck[]
}

/**
 * Проверка выгрузок правилами PIX — до того, как файл уедет в студию.
 *
 * Студия отвергает пакет целиком из-за одного дефекта и называет только код
 * ошибки; здесь те же проверки делаются заранее и с указанием фигуры. Без
 * бэкенда проверка недоступна: пакет .pmm собирает только он.
 */
export async function fetchExportCheck(processId: string): Promise<ExportCheckReport | null> {
  try {
    const res = await fetch(`${API_BASE}/import/${encodeURIComponent(processId)}/export/check`)
    if (res.ok) return await res.json()
  } catch {
    // офлайн-режим: вызывающий код покажет, что проверка недоступна
  }
  return null
}

/**
 * BPMN в том виде, в каком его отдаст скачивание.
 * Клиентский генератор — двойник бэкендового, но источником истины остаётся
 * бэкенд, поэтому предпросмотр берём оттуда, если он доступен.
 */
export async function fetchBpmnXml(processId: string): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/import/${encodeURIComponent(processId)}/export/bpmn`)
    if (res.ok) return await res.text()
  } catch {
    // офлайн-режим: вызывающий код покажет результат клиентского генератора
  }
  return null
}

/**
 * Нативный пакет .pmm в том виде, в каком его отдаст скачивание.
 *
 * Клиентского сборщика .pmm нет — пакет собирает только бэкенд, — поэтому
 * просмотр этой выгрузки без запущенного FastAPI невозможен, и вызывающий код
 * обязан сказать об этом сотруднику, а не показать пустое окно.
 */
export async function fetchPmmPackage(processId: string): Promise<ArrayBuffer> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/import/${encodeURIComponent(processId)}/export/pmm`)
  } catch {
    throw new ExportUnavailableError(
      'Просмотр .pmm требует запущенного бэкенда FastAPI. Запустите backend/start.sh и повторите.',
    )
  }
  if (!res.ok) {
    throw new ExportUnavailableError(
      `Сервер вернул ${res.status}. Нативный пакет .pmm собирается только на бэкенде.`,
    )
  }
  return await res.arrayBuffer()
}

/** Trigger direct backend file download with fallback */
export async function triggerExportDownload(
  process: BusinessProcess,
  type: 'bpmn' | 'pmm' | 'xpdl' | 'event-log' | 'regulation' | 'pix-json'
): Promise<void> {
  let endpoint = ''
  let defaultFilename = ''

  if (type === 'bpmn') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/bpmn`
    defaultFilename = `${process.passport.code}_PIX_Map.bpmn`
  } else if (type === 'pmm') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/pmm`
    defaultFilename = `${process.passport.code}_PIX_Map.pmm`
  } else if (type === 'xpdl') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/xpdl`
    defaultFilename = `${process.passport.code}_Process.xpdl`
  } else if (type === 'event-log') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/event-log`
    defaultFilename = `${process.passport.code}_EventLogs.csv`
  } else if (type === 'regulation') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/regulation`
    defaultFilename = `${process.passport.code}_Regulation.csv`
  } else if (type === 'pix-json') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/pix-json`
    defaultFilename = `${process.passport.code}_PIX_Registry.json`
  }

  try {
    const res = await fetch(endpoint)
    if (res.ok) {
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = defaultFilename
      a.click()
      URL.revokeObjectURL(url)
      return
    }
    if (type === 'pmm') {
      throw new ExportUnavailableError(
        `Сервер вернул ${res.status}. Нативный пакет .pmm собирается только на бэкенде.`,
      )
    }
    console.warn(`Backend download failed with ${res.status}, generating client-side file`)
  } catch (err) {
    if (err instanceof ExportUnavailableError) throw err
    if (type === 'pmm') {
      // .pmm — это ZIP из XML-частей PIX; клиентского генератора нет, и молча
      // «ничего не скачать» пользователю нельзя.
      throw new ExportUnavailableError(
        'Экспорт .pmm требует запущенного бэкенда FastAPI. Запустите backend/start.sh и повторите.',
      )
    }
    console.warn('Backend download failed, generating client-side file:', err)
  }

  // Fallback client-side download
  let content = ''
  let mimeType = 'text/plain;charset=utf-8'

  if (type === 'bpmn') {
    content = generateBpmn2Xml(process)
    mimeType = 'application/xml;charset=utf-8'
  } else if (type === 'event-log') {
    content = generateProcessetEventLogCsv(process)
    mimeType = 'text/csv;charset=utf-8'
  } else if (type === 'regulation') {
    content = generateProcessRegulationCsv(process)
    mimeType = 'text/csv;charset=utf-8'
  } else if (type === 'pix-json') {
    content = generatePixJson(process)
    mimeType = 'application/json;charset=utf-8'
  }

  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = defaultFilename
  a.click()
  URL.revokeObjectURL(url)
}
