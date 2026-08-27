import React, { useState, useEffect } from 'react'
import {
  X,
  Check,
  Cpu,
  Sliders,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Slider } from '@/components/ui/slider'
import type { ProcessNode, StepCategory, BusinessProcess } from '@/types/process'

interface NodeDetailDrawerProps {
  node: ProcessNode | null
  process: BusinessProcess
  onClose: () => void
  onSaveNode: (updated: ProcessNode) => void
}

export const NodeDetailDrawer: React.FC<NodeDetailDrawerProps> = ({
  node,
  process,
  onClose,
  onSaveNode,
}) => {
  const [formData, setFormData] = useState<ProcessNode | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)

  useEffect(() => {
    setFormData(node ? { ...node } : null)
    setValidationError(null)
  }, [node])

  // Панель перекрывает половину экрана, но раньше закрывалась только крестиком:
  // привычное Escape не работало, и уйти с клавиатуры было нечем.
  useEffect(() => {
    if (!node) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [node, onClose])

  if (!node || !formData) return null

  const handleSave = () => {
    if ((formData.slaMinutes ?? 0) < 1) {
      setValidationError('SLA должен быть ≥ 1 мин')
      return
    }
    if ((formData.costPerExecution ?? 0) < 0) {
      setValidationError('Себестоимость не может быть отрицательной')
      return
    }
    if (!formData.name.trim()) {
      setValidationError('Наименование операции не может быть пустым')
      return
    }
    setValidationError(null)
    onSaveNode({
      ...formData,
      name: formData.name.trim(),
      slaMinutes: Math.max(1, Math.round(formData.slaMinutes ?? 1)),
      costPerExecution: Math.max(0, Math.round(formData.costPerExecution ?? 0)),
    })
  }

  return (
    <>
      {/* Затемнение: без него панель просто ложилась поверх таблицы, и было
          непонятно, что остальной экран сейчас не в работе. */}
      <div
        className="fixed inset-0 z-40 bg-black/40 animate-in fade-in duration-150"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Свойства шага «${node.name}»`}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-full flex-col justify-between border-l bg-card shadow-2xl animate-in slide-in-from-right duration-200 sm:w-[460px]"
      >
      {/* Drawer Header */}
      <div className="p-4 border-b bg-muted/40 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600">
            <Sliders className="w-4 h-4" />
          </div>
          <div>
            <h3 className="font-bold text-sm text-foreground">
              {formData.code ? `${formData.code}: ` : ''}Свойства шага
            </h3>
            <p className="text-[11px] text-muted-foreground">
              Параметры регламента и конфигурация PIX RPA
            </p>
          </div>
        </div>

        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground"
          onClick={onClose}
        >
          <X className="w-4 h-4" />
        </Button>
      </div>

      {/* Drawer Body Form */}
      <div className="p-5 flex-1 overflow-y-auto space-y-4 text-xs">
        {/* Step Name & Code */}
        <div className="space-y-1.5">
          <Label className="text-xs font-semibold">Наименование операции</Label>
          <Input
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            className="text-xs"
          />
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Код шага</Label>
            <Input
              value={formData.code || ''}
              onChange={(e) => setFormData({ ...formData, code: e.target.value })}
              placeholder="STEP-01"
              className="text-xs font-mono"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Категория</Label>
            <select
              value={formData.category || 'manual'}
              onChange={(e) => {
                const nextCategory = e.target.value as StepCategory
                const lockedTypes = new Set([
                  'startEvent',
                  'endEvent',
                  'exclusiveGateway',
                  'parallelGateway',
                  'inclusiveGateway',
                  'lane',
                ])
                let nextType = formData.type
                if (!lockedTypes.has(formData.type)) {
                  if (nextCategory === 'rpa_bot' || nextCategory === 'api_service') {
                    nextType = 'serviceTask'
                  } else if (formData.type === 'serviceTask') {
                    nextType = 'userTask'
                  }
                }
                setFormData({
                  ...formData,
                  category: nextCategory,
                  type: nextType,
                })
              }}
              className="w-full h-9 px-2 text-xs rounded-md border bg-background"
            >
              <option value="manual">Ручная задача</option>
              <option value="rpa_bot">PIX RPA Робот</option>
              <option value="approval">Согласование / Комитет</option>
              <option value="validation">Проверка / Скоринг</option>
              <option value="api_service">Интеграция АБС</option>
              <option value="notification">Уведомление</option>
            </select>
          </div>
        </div>

        {/* Lane / Department & Role */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Подразделение (Дорожка)</Label>
            <select
              value={formData.laneId || ''}
              onChange={(e) => {
                const lane = process.lanes.find((l) => l.id === e.target.value)
                setFormData({
                  ...formData,
                  laneId: e.target.value,
                  laneName: lane?.name || formData.laneName,
                })
              }}
              className="w-full h-9 px-2 text-xs rounded-md border bg-background"
            >
              <option value="">Не привязано</option>
              {process.lanes.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Роль / Исполнитель</Label>
            <Input
              value={formData.role || ''}
              onChange={(e) => setFormData({ ...formData, role: e.target.value })}
              placeholder="Кредитный эксперт"
              className="text-xs"
            />
          </div>
        </div>

        {/* IT System */}
        <div className="space-y-1.5">
          <Label className="text-xs font-semibold">ИТ-Система</Label>
          <Input
            value={formData.system || ''}
            onChange={(e) => setFormData({ ...formData, system: e.target.value })}
            placeholder="АБС ЦФТ-Банк / ЕПИГУ / PIX RPA"
            className="text-xs"
          />
        </div>

        {/* SLA & Costs */}
        {validationError && (
          <p className="text-xs text-rose-600 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-md px-2.5 py-1.5">
            {validationError}
          </p>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold flex items-center justify-between">
              <span>Норматив SLA</span>
              <span className="text-muted-foreground font-normal">
                {formData.slaMinutes || 30} мин ({Math.round(((formData.slaMinutes || 30) / 60) * 10) / 10}ч)
              </span>
            </Label>
            <Input
              type="number"
              min={1}
              step={1}
              value={formData.slaMinutes || 30}
              onChange={(e) => {
                const v = Math.max(1, Math.round(Number(e.target.value) || 1))
                setFormData({ ...formData, slaMinutes: v })
              }}
              className="text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Себестоимость (UZS)</Label>
            <Input
              type="number"
              min={0}
              step={100}
              value={formData.costPerExecution || 5000}
              onChange={(e) => {
                const v = Math.max(0, Math.round(Number(e.target.value) || 0))
                setFormData({ ...formData, costPerExecution: v })
              }}
              className="text-xs"
            />
          </div>
        </div>

        {/* Automation Potential Slider */}
        <div className="space-y-2 pt-2 border-t">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-semibold flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5 text-emerald-600" />
              Потенциал роботизации (PIX RPA):
            </Label>
            <Badge className="bg-emerald-600 text-white text-xs">
              {formData.automationPotential || 0}%
            </Badge>
          </div>
          <Slider
            value={[formData.automationPotential || 0]}
            max={100}
            step={5}
            onValueChange={(val) =>
              setFormData({ ...formData, automationPotential: val[0] })
            }
          />
          <p className="text-[11px] text-muted-foreground">
            Определяет приоритетность разработки сценария в студии PIX RPA
          </p>
        </div>

        {/* Description / Instructions */}
        <div className="space-y-1.5 pt-2 border-t">
          <Label className="text-xs font-semibold">Регламентное описание операции</Label>
          <Textarea
            rows={3}
            value={formData.description || ''}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            placeholder="Опишите правила выполнения, проверки и требования к шагу..."
            className="text-xs"
          />
        </div>
      </div>

      {/* Drawer Footer */}
      <div className="flex items-center justify-end gap-2 border-t bg-muted/40 p-4">
        <Button variant="outline" size="sm" onClick={onClose} className="flex-1 sm:flex-none">
          Отмена
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          className="flex-1 gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700 sm:flex-none"
        >
          <Check className="h-4 w-4" />
          Сохранить изменения
        </Button>
      </div>
      </div>
    </>
  )
}
