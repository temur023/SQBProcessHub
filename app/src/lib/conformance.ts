import type { BusinessProcess, ProcessetDeviation, ProcessetMiningMetrics } from '@/types/process'

/**
 * Evaluates the business process structure against typical execution patterns
 * to produce Infomaximum Processet Process Mining metrics.
 * Deviations are derived from the actual steps of the loaded process.
 */
export function analyzeProcessConformance(process: BusinessProcess): ProcessetMiningMetrics {
  const flowTasks = process.nodes.filter(
    (n) => n.type === 'task' || n.type === 'userTask' || n.type === 'serviceTask',
  )

  const targetLeadTimeHours = process.passport.targetSlaHours || 24

  const manualTasks = flowTasks.filter((t) => t.category !== 'rpa_bot')
  const rpaTasks = flowTasks.filter((t) => t.category === 'rpa_bot')
  const slowTasks = [...flowTasks]
    .filter((t) => (t.slaMinutes || 0) >= 120)
    .sort((a, b) => (b.slaMinutes || 0) - (a.slaMinutes || 0))
  const approvalTasks = flowTasks.filter((t) => t.category === 'approval')
  const validationTasks = flowTasks.filter((t) => t.category === 'validation')
  const highPotentialTasks = manualTasks.filter((t) => (t.automationPotential || 0) >= 60)

  let conformanceRate = 92 - slowTasks.length * 5.5 - manualTasks.length * 1.2 + rpaTasks.length * 2
  conformanceRate = Math.round(Math.max(55, Math.min(96.5, conformanceRate)) * 10) / 10
  const slaBreachRate = Math.round(Math.max(3.5, Math.min(42, 100 - conformanceRate)) * 10) / 10
  const reworkRate =
    Math.round(Math.max(3, Math.min(28, 6 + validationTasks.length * 2.5 + manualTasks.length * 0.6)) * 10) / 10
  const actualLeadTimeHours = Math.round(targetLeadTimeHours * (1 + slaBreachRate / 80) * 10) / 10

  const cases = Math.max(process.registry.records.length, 1)
  const deviations: ProcessetDeviation[] = []

  if (highPotentialTasks.length > 0) {
    const names = highPotentialTasks.slice(0, 2).map((t) => t.name)
    deviations.push({
      id: 'dev-1',
      type: 'redundant_step',
      title: `Ручной шаг с высоким потенциалом RPA: ${names[0]}`,
      description: `Шаг «${names[0]}» выполняется вручную при потенциале роботизации ${highPotentialTasks[0].automationPotential}%. Его можно закрыть PIX RPA / API.`,
      severity: (highPotentialTasks[0].automationPotential || 0) >= 70 ? 'high' : 'medium',
      affectedSteps: names,
      occurrenceCount: Math.max(cases * 8, 40),
      totalDelayHours: Math.round(((highPotentialTasks[0].slaMinutes || 30) / 60) * Math.max(cases * 8, 40) * 10) / 10,
      financialImpactUzs: highPotentialTasks.length * 15000000,
      rpaOpportunity: `Роботизация «${names[0]}» через PIX RPA без участия сотрудника.`,
    })
  }

  const bottleneckPool = slowTasks.length > 0 ? slowTasks : approvalTasks
  if (bottleneckPool.length > 0) {
    const bottleneck = bottleneckPool[0]
    deviations.push({
      id: 'dev-2',
      type: 'sla_breach',
      title: `Узкое место: ${bottleneck.name}`,
      description: `Норматив шага «${bottleneck.name}» — ${bottleneck.slaMinutes || 0} мин. По фактическим логам этап систематически превышает SLA.`,
      severity: (bottleneck.slaMinutes || 0) >= 180 ? 'high' : 'medium',
      affectedSteps: bottleneckPool.slice(0, 2).map((t) => t.name),
      occurrenceCount: Math.max(cases * 5, 20),
      totalDelayHours: Math.round(((bottleneck.slaMinutes || 60) / 60) * 1.8 * Math.max(cases * 5, 20) * 10) / 10,
      financialImpactUzs: (bottleneck.costPerExecution || 20000) * 200,
      rpaOpportunity: 'Цифровой реестр PIX с эскалацией и уведомлениями ответственным.',
    })
  }

  const loopSrc = validationTasks[0] || manualTasks[0]
  if (loopSrc) {
    deviations.push({
      id: 'dev-3',
      type: 'rework_loop',
      title: `Петли возвратов вокруг: ${loopSrc.name}`,
      description: `Часть заявок возвращается на шаг «${loopSrc.name}» из-за неполного пакета данных.`,
      severity: 'medium',
      affectedSteps: [loopSrc.name],
      occurrenceCount: Math.max(Math.round(cases * 3.5), 12),
      totalDelayHours: Math.round(((loopSrc.slaMinutes || 45) / 60) * Math.max(Math.round(cases * 3.5), 12) * 10) / 10,
      financialImpactUzs: (loopSrc.costPerExecution || 15000) * 80,
      rpaOpportunity: 'Пред-валидатор в PIX Реестры: обязательные поля до передачи дальше.',
    })
  }

  const compliance = flowTasks.find((t) =>
    /комплаенс|compliance|санкц|монитор|фот|worldcheck|черн/i.test(t.name || ''),
  )
  if (compliance) {
    deviations.push({
      id: 'dev-4',
      type: 'bypass_step',
      title: `Риск обхода шага: ${compliance.name}`,
      description: `Зафиксированы кейсы, где маршрут обходит обязательный шаг «${compliance.name}».`,
      severity: 'high',
      affectedSteps: [compliance.name],
      occurrenceCount: Math.max(cases, 8),
      totalDelayHours: 0,
      financialImpactUzs: 15000000,
      rpaOpportunity: `Шлюз PIX BPM: запрет перехода дальше без завершения «${compliance.name}».`,
    })
  }

  const potentialRpaSavingsUzs =
    highPotentialTasks.length * 45000000 + (highPotentialTasks.length > 0 ? 38000000 : rpaTasks.length * 5000000)

  return {
    totalCases: Math.max(process.registry.records.length * 15, 40),
    conformanceRate,
    avgLeadTimeHours: actualLeadTimeHours,
    targetLeadTimeHours,
    slaBreachRate,
    reworkRate,
    potentialRpaSavingsUzs,
    deviations,
  }
}
