import React, { useState } from 'react'
import {
  Plus,
  Search,
  CheckCircle2,
  Clock,
  AlertCircle,
  Download,
  Database,
  Layers,
} from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import type { BusinessProcess, PixRegistryRecord } from '@/types/process'
import { generatePixJson, downloadFile } from '@/lib/processet-export'

interface PixRegistryViewProps {
  process: BusinessProcess
  onUpdateProcess: (updated: BusinessProcess) => void
}

export const PixRegistryView: React.FC<PixRegistryViewProps> = ({
  process,
  onUpdateProcess,
}) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [isAddRecordOpen, setIsAddRecordOpen] = useState(false)
  const [formData, setFormData] = useState<Record<string, any>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const registry = process.registry

  const extraFields = registry.fields.filter(
    (f) => f.code !== 'case_number' && f.code !== 'caseId',
  ).slice(0, 4)

  const filteredRecords = registry.records.filter((rec) => {
    const search = searchQuery.toLowerCase()
    const matchCase = rec.caseId.toLowerCase().includes(search)
    const matchStep = rec.currentStepName.toLowerCase().includes(search)
    const matchAssignee = rec.assignedTo.toLowerCase().includes(search)
    const matchData = Object.values(rec.data).some((val) =>
      String(val).toLowerCase().includes(search),
    )
    return matchCase || matchStep || matchAssignee || matchData
  })

  const handleAddRecord = () => {
    const missing = registry.fields.filter((f) => {
      if (!f.required) return false
      const value = formData[f.code]
      return value === undefined || value === null || String(value).trim() === ''
    })
    if (missing.length > 0) {
      setFormError(`Заполните обязательные поля: ${missing.map((f) => f.name).join(', ')}`)
      return
    }
    if (process.nodes.length === 0) {
      setFormError('В процессе нет шагов — сначала импортируйте диаграмму')
      return
    }

    // Генерация уникального CaseId без коллизий после удалений: max существующего номера +1
    const existingNumbers = registry.records
      .map((r) => {
        const m = r.caseId.match(/(\d+)\s*$/)
        return m ? parseInt(m[1], 10) : 0
      })
      .filter((n) => Number.isFinite(n))
    const nextNum = (existingNumbers.length ? Math.max(...existingNumbers) : 44) + 1
    // Fallback на timestamp если формат не SQB-YYYY-NNNN
    const hasNumericCaseIds = existingNumbers.some((n) => n > 0)
    const newCaseNum = hasNumericCaseIds
      ? `SQB-2026-${String(nextNum).padStart(4, '0')}`
      : `SQB-${Date.now().toString(36).toUpperCase()}-${String(nextNum).padStart(3, '0')}`
    const firstTask = process.nodes.find((n) => n.type === 'userTask' || n.type === 'serviceTask') || process.nodes[0]
    if (!firstTask) {
      setFormError('Не удалось определить первый шаг процесса')
      return
    }
    setFormError(null)

    const newRecord: PixRegistryRecord = {
      id: `rec-${crypto.randomUUID()}`,
      caseId: newCaseNum,
      createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
      status: 'in_progress',
      currentStepId: firstTask.id,
      currentStepName: firstTask.name,
      assignedTo: firstTask.role || 'Кредитный эксперт',
      elapsedMinutes: 5,
      data: {
        ...formData,
      },
    }

    const updated: BusinessProcess = {
      ...process,
      registry: {
        ...registry,
        records: [newRecord, ...registry.records],
      },
    }

    onUpdateProcess(updated)
    setIsAddRecordOpen(false)
    setFormData({})
  }

  const handleExportPix = () => {
    const json = generatePixJson(process)
    downloadFile(json, `${process.passport.code}_PIX_Registry.json`, 'application/json')
  }

  const getStatusBadge = (status: PixRegistryRecord['status']) => {
    switch (status) {
      case 'completed':
        return (
          <Badge className="bg-emerald-600/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20 text-[10px] gap-1">
            <CheckCircle2 className="w-3 h-3" /> Завершено
          </Badge>
        )
      case 'delayed':
        return (
          <Badge className="bg-amber-600/10 text-amber-700 dark:text-amber-400 border-amber-500/20 text-[10px] gap-1">
            <Clock className="w-3 h-3" /> Задержка SLA
          </Badge>
        )
      case 'rejected':
        return (
          <Badge className="bg-rose-600/10 text-rose-700 dark:text-rose-400 border-rose-500/20 text-[10px] gap-1">
            <AlertCircle className="w-3 h-3" /> Отклонено
          </Badge>
        )
      default:
        return (
          <Badge className="bg-blue-600/10 text-blue-700 dark:text-blue-400 border-blue-500/20 text-[10px] gap-1">
            <Clock className="w-3 h-3" /> В работе
          </Badge>
        )
    }
  }

  return (
    <div className="space-y-4">
      {/* Registry Passport Banner */}
      <div className="p-4 rounded-xl border bg-card shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-700 text-white shadow-md">
            <Database className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-bold text-base text-foreground">{registry.name}</h3>
              <Badge variant="outline" className="font-mono text-xs">
                {registry.code}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5 max-w-xl">
              {registry.description} • Структура переменных синхронизирована с формой PIX Реестры
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleExportPix}
            className="text-xs gap-1.5"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Экспорт PIX JSON</span>
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setFormError(null)
              setIsAddRecordOpen(true)
            }}
            className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs gap-1.5"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Создать заявку в реестре</span>
          </Button>
        </div>
      </div>

      {/* Registry Fields Schema Preview */}
      <div className="p-3.5 rounded-xl border bg-muted/40 text-xs">
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-foreground flex items-center gap-1.5">
            <Layers className="w-4 h-4 text-emerald-600" />
            Схема полей реестра PIX ({registry.fields.length} полей):
          </span>
          <span className="text-[11px] text-muted-foreground">
            Поля используются для передачи контекста между задачами в PIX BPM
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {registry.fields.map((f) => (
            <div
              key={f.id}
              className="px-2.5 py-1 rounded-lg bg-background border text-[11px] flex items-center gap-1.5 shadow-2xs"
            >
              <span className="font-medium text-foreground">{f.name}</span>
              <span className="text-muted-foreground font-mono text-[10px]">({f.code})</span>
              <Badge variant="secondary" className="text-[9px] py-0 px-1 font-mono">
                {f.type}
              </Badge>
              {f.required && (
                <span className="text-rose-500 font-bold text-xs" title="Обязательное поле">
                  *
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Records Table Toolbar */}
      <div className="p-3.5 rounded-xl border bg-card shadow-sm flex flex-col md:flex-row items-center justify-between gap-3">
        <div className="relative w-full md:w-80">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Поиск по заявкам реестра (номер, ИНН, компания)..."
            className="pl-9 h-9 text-xs"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="text-xs text-muted-foreground">
          Экземпляров в работе: <strong className="text-foreground">{registry.records.length}</strong>
        </div>
      </div>

      {/* Records Data Table */}
      <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="bg-muted/70 text-muted-foreground uppercase text-[10px] tracking-wider border-b">
              <tr>
                <th className="px-3.5 py-3 font-semibold">№ Заявки (Case ID)</th>
                <th className="px-3.5 py-3 font-semibold">Дата создания</th>
                <th className="px-3.5 py-3 font-semibold">Статус</th>
                <th className="px-3.5 py-3 font-semibold">Текущий шаг процесса</th>
                <th className="px-3.5 py-3 font-semibold">Ответственный</th>
                {extraFields.map((f) => (
                  <th key={f.id} className="px-3.5 py-3 font-semibold">
                    {f.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {filteredRecords.length === 0 ? (
                <tr>
                  <td
                    colSpan={5 + extraFields.length}
                    className="text-center py-8 text-muted-foreground"
                  >
                    Записей в реестре пока нет. Создайте первую заявку кнопкой выше.
                  </td>
                </tr>
              ) : (
                filteredRecords.map((rec) => (
                  <tr key={rec.id} className="hover:bg-muted/50 transition-colors">
                    <td className="px-3.5 py-3 font-mono font-bold text-foreground">
                      {rec.caseId}
                    </td>
                    <td className="px-3.5 py-3 text-muted-foreground">{rec.createdAt}</td>
                    <td className="px-3.5 py-3">{getStatusBadge(rec.status)}</td>
                    <td className="px-3.5 py-3 font-medium text-foreground">
                      {rec.currentStepName}
                    </td>
                    <td className="px-3.5 py-3 text-muted-foreground">{rec.assignedTo}</td>
                    {extraFields.map((f) => (
                      <td key={f.id} className="px-3.5 py-3 font-medium">
                        {typeof rec.data[f.code] === 'number'
                          ? rec.data[f.code].toLocaleString('ru-RU')
                          : String(rec.data[f.code] ?? '—')}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add Record Modal */}
      <Dialog open={isAddRecordOpen} onOpenChange={setIsAddRecordOpen}>
        <DialogContent className="max-w-md sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-bold">Новая заявка в реестре PIX</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {formError && (
              <p className="text-xs text-rose-600 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-md px-2.5 py-1.5">
                {formError}
              </p>
            )}
            {registry.fields.map((field) => (
              <div key={field.id} className="space-y-1">
                <Label className="text-xs font-medium">
                  {field.name} {field.required && <span className="text-rose-500">*</span>}
                </Label>
                {field.type === 'select' ? (
                  <select
                    className="w-full h-8 px-2.5 text-xs rounded-md border bg-background"
                    value={formData[field.code] || ''}
                    onChange={(e) =>
                      setFormData({ ...formData, [field.code]: e.target.value })
                    }
                  >
                    <option value="">Выберите значение</option>
                    {field.options?.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    type={field.type === 'number' ? 'number' : 'text'}
                    placeholder={`Введите ${field.name.toLowerCase()}`}
                    className="h-8 text-xs"
                    value={formData[field.code] || ''}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        [field.code]:
                          field.type === 'number' ? Number(e.target.value) : e.target.value,
                      })
                    }
                  />
                )}
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setIsAddRecordOpen(false)}>
              Отмена
            </Button>
            <Button
              size="sm"
              onClick={handleAddRecord}
              className="bg-emerald-600 hover:bg-emerald-700 text-white"
            >
              Сохранить в реестр
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
