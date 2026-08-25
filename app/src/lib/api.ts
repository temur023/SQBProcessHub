import type { BusinessProcess, PixRegistryRecord } from '@/types/process'
import { parseDrawio } from './drawio'
import { generateBpmn2Xml } from './bpmn-export'
import { generateProcessetEventLogCsv, generateProcessRegulationCsv, generatePixJson } from './processet-export'

const API_BASE = '/api/v1'

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

/** Import .drawio, .xml, .bpmn file using Python FastAPI or local fallback */
export async function importDrawioFileApi(file: File): Promise<{ process: BusinessProcess; source: 'fastapi' | 'local' }> {
  try {
    const formData = new FormData()
    formData.append('file', file)

    const res = await fetch(`${API_BASE}/import/file`, {
      method: 'POST',
      body: formData,
    })

    if (res.ok) {
      const process: BusinessProcess = await res.json()
      if ((process.nodes?.length ?? 0) > 0) {
        return { process, source: 'fastapi' }
      }
    }
  } catch (err) {
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

    if (res.ok) {
      const process: BusinessProcess = await res.json()
      if ((process.nodes?.length ?? 0) > 0) {
        return { process, source: 'fastapi' }
      }
    }
  } catch (err) {
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

/** Create a new case in PIX Registry on backend */
export async function createRegistryCaseApi(
  processId: string,
  caseId: string,
  assignedTo: string,
  data: Record<string, any>
): Promise<PixRegistryRecord | null> {
  try {
    const res = await fetch(`${API_BASE}/processes/${encodeURIComponent(processId)}/registry/cases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caseId, assignedTo, data }),
    })

    if (res.ok) {
      return await res.json()
    }
  } catch (err) {
    console.warn('FastAPI create case failed:', err)
  }
  return null
}

/** Trigger direct backend file download with fallback */
export async function triggerExportDownload(
  process: BusinessProcess,
  type: 'bpmn' | 'event-log' | 'regulation' | 'pix-json'
): Promise<void> {
  let endpoint = ''
  let defaultFilename = ''

  if (type === 'bpmn') {
    endpoint = `${API_BASE}/import/${encodeURIComponent(process.id)}/export/bpmn`
    defaultFilename = `${process.passport.code}_Processet.bpmn20.xml`
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
  } catch (err) {
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
