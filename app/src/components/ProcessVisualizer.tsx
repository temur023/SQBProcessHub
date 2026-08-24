import React, { useState, useMemo } from 'react'
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  Cpu,
  Clock,
  AlertTriangle,
  Layers,
  Filter,
  Info,
  Server,
  Play,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { BusinessProcess, ProcessNode, ProcessEdge } from '@/types/process'

interface ProcessVisualizerProps {
  process: BusinessProcess
  onSelectNode: (node: ProcessNode) => void
  selectedNodeId?: string
}

export const ProcessVisualizer: React.FC<ProcessVisualizerProps> = ({
  process,
  onSelectNode,
  selectedNodeId,
}) => {
  const [zoom, setZoom] = useState(1.0)
  const [activeFilter, setActiveFilter] = useState<'all' | 'rpa' | 'bottlenecks' | 'manual'>('all')

  // Calculate canvas bounding box
  const bounds = useMemo(() => {
    let maxX = 1300
    let maxY = 700
    for (const lane of process.lanes) {
      maxX = Math.max(maxX, lane.geometry.x + lane.geometry.width + 80)
      maxY = Math.max(maxY, lane.geometry.y + lane.geometry.height + 80)
    }
    for (const node of process.nodes) {
      maxX = Math.max(maxX, node.geometry.x + node.geometry.width + 120)
      maxY = Math.max(maxY, node.geometry.y + node.geometry.height + 120)
    }
    return { width: Math.max(maxX, 1400), height: Math.max(maxY, 850) }
  }, [process])

  const filteredNodes = useMemo(() => {
    if (activeFilter === 'all') return process.nodes
    if (activeFilter === 'rpa') return process.nodes.filter((n) => n.category === 'rpa_bot')
    if (activeFilter === 'manual') return process.nodes.filter((n) => n.category === 'manual')
    if (activeFilter === 'bottlenecks') return process.nodes.filter((n) => (n.slaMinutes || 0) >= 120)
    return process.nodes
  }, [process.nodes, activeFilter])

  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((n) => n.id)), [filteredNodes])

  // Helper to calculate exact orthogonal arrow routes
  const calculateEdgePath = (edge: ProcessEdge) => {
    const src = process.nodes.find((n) => n.id === edge.sourceId)
    const tgt = process.nodes.find((n) => n.id === edge.targetId)
    if (!src || !tgt) return { path: '', labelX: 0, labelY: 0 }

    if (edge.points && edge.points.length >= 2) {
      const path = edge.points.reduce((acc, p, idx) => {
        return `${acc} ${idx === 0 ? 'M' : 'L'} ${p.x} ${p.y}`
      }, '')
      const midIdx = Math.floor(edge.points.length / 2)
      return {
        path,
        labelX: edge.points[midIdx].x,
        labelY: edge.points[midIdx].y - 8,
      }
    }

    const srcW = src.geometry.width || 120
    const srcH = src.geometry.height || 60
    const tgtW = tgt.geometry.width || 120
    const tgtH = tgt.geometry.height || 60

    // Decide ports based on relative positions
    let srcX = src.geometry.x + srcW
    let srcY = src.geometry.y + srcH / 2
    let tgtX = tgt.geometry.x
    let tgtY = tgt.geometry.y + tgtH / 2

    // If target is directly below source (e.g. Reject branch or downward gateway flow)
    const isTargetBelow = tgt.geometry.y > src.geometry.y + srcH - 10 && Math.abs(tgt.geometry.x - src.geometry.x) < 80
    if (isTargetBelow) {
      srcX = src.geometry.x + srcW / 2
      srcY = src.geometry.y + srcH
      tgtX = tgt.geometry.x + tgtW / 2
      tgtY = tgt.geometry.y
      return {
        path: `M ${srcX} ${srcY} L ${srcX} ${tgtY}`,
        labelX: srcX + 10,
        labelY: (srcY + tgtY) / 2,
      }
    }

    // Horizontal / Orthogonal routing
    let path = ''
    const midX = (srcX + tgtX) / 2

    if (Math.abs(srcY - tgtY) < 6) {
      // Straight horizontal line
      path = `M ${srcX} ${srcY} L ${tgtX} ${tgtY}`
    } else {
      // Step orthogonal line
      path = `M ${srcX} ${srcY} L ${midX} ${srcY} L ${midX} ${tgtY} L ${tgtX} ${tgtY}`
    }

    return {
      path,
      labelX: midX,
      labelY: (srcY + tgtY) / 2 - 10,
    }
  }

  return (
    <div className="flex flex-col h-full bg-card rounded-xl border shadow-sm overflow-hidden">
      {/* Visualizer Toolbar */}
      <div className="p-3 border-b bg-muted/40 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-emerald-600" />
          <span className="text-xs font-bold text-foreground uppercase tracking-wider">
            Интерактивная карта BPMN (Draw.io → PIX)
          </span>
          <Badge variant="outline" className="text-[11px]">
            Масштаб: {Math.round(zoom * 100)}%
          </Badge>
        </div>

        {/* Filter buttons */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-muted-foreground mr-1 flex items-center gap-1">
            <Filter className="w-3 h-3" /> Фильтр:
          </span>
          <button
            onClick={() => setActiveFilter('all')}
            className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
              activeFilter === 'all'
                ? 'bg-primary text-primary-foreground font-medium'
                : 'bg-background hover:bg-muted text-muted-foreground border'
            }`}
          >
            Все элементы ({process.nodes.length})
          </button>
          <button
            onClick={() => setActiveFilter('rpa')}
            className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors ${
              activeFilter === 'rpa'
                ? 'bg-emerald-600 text-white font-medium'
                : 'bg-background hover:bg-muted text-muted-foreground border'
            }`}
          >
            <Cpu className="w-3 h-3 text-emerald-500" />
            PIX RPA ({process.nodes.filter((n) => n.category === 'rpa_bot').length})
          </button>
          <button
            onClick={() => setActiveFilter('bottlenecks')}
            className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors ${
              activeFilter === 'bottlenecks'
                ? 'bg-amber-600 text-white font-medium'
                : 'bg-background hover:bg-muted text-muted-foreground border'
            }`}
          >
            <AlertTriangle className="w-3 h-3 text-amber-500" />
            Узкие места SLA
          </button>

          {/* Zoom controls */}
          <div className="flex items-center gap-1 ml-2 border-l pl-2">
            <Button
              variant="outline"
              size="icon"
              className="h-7 w-7"
              onClick={() => setZoom((z) => Math.max(0.4, z - 0.15))}
              title="Уменьшить"
            >
              <ZoomOut className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-7 w-7"
              onClick={() => setZoom(1.0)}
              title="Сбросить масштаб 100%"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-7 w-7"
              onClick={() => setZoom((z) => Math.min(1.8, z + 0.15))}
              title="Увеличить"
            >
              <ZoomIn className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      </div>

      {/* Canvas Area */}
      <div className="relative flex-1 overflow-auto bg-slate-100/60 dark:bg-slate-950 p-6 min-h-[580px]">
        <div
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: 'top left',
            width: `${bounds.width}px`,
            height: `${bounds.height}px`,
            position: 'relative',
          }}
          className="transition-transform duration-100 ease-out"
        >
          {/* 1. Render Swimlanes (Lanes / Departments) */}
          {process.lanes.map((lane, idx) => (
            <div
              key={lane.id}
              style={{
                position: 'absolute',
                left: `${lane.geometry.x}px`,
                top: `${lane.geometry.y}px`,
                width: `${lane.geometry.width}px`,
                height: `${lane.geometry.height}px`,
              }}
              className={`border-b-2 border-slate-300 dark:border-slate-800 ${
                idx % 2 === 0
                  ? 'bg-white/90 dark:bg-slate-900/60'
                  : 'bg-slate-50/90 dark:bg-slate-900/30'
              } overflow-hidden shadow-2xs`}
            >
              {/* Lane Header Banner */}
              <div className="h-full w-10 bg-slate-200/80 dark:bg-slate-800/80 border-r border-slate-300 dark:border-slate-700 flex items-center justify-center float-left select-none">
                <span
                  style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
                  className="text-[11px] font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider px-1 text-center"
                >
                  {lane.name}
                </span>
              </div>
            </div>
          ))}

          {/* 2. Render SVG Arrows & Transitions */}
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none"
            style={{ width: `${bounds.width}px`, height: `${bounds.height}px` }}
          >
            <defs>
              <marker
                id="arrowhead-norm"
                markerWidth="8"
                markerHeight="6"
                refX="7"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" fill="#64748b" />
              </marker>
              <marker
                id="arrowhead-active"
                markerWidth="8"
                markerHeight="6"
                refX="7"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" fill="#059669" />
              </marker>
            </defs>

            {process.edges.map((edge) => {
              const { path, labelX, labelY } = calculateEdgePath(edge)
              if (!path) return null

              const isEdgeHighlighted =
                selectedNodeId &&
                (edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId)

              return (
                <g key={edge.id}>
                  <path
                    d={path}
                    fill="none"
                    stroke={isEdgeHighlighted ? '#059669' : '#64748b'}
                    strokeWidth={isEdgeHighlighted ? 2.5 : 1.5}
                    markerEnd={isEdgeHighlighted ? 'url(#arrowhead-active)' : 'url(#arrowhead-norm)'}
                    className="transition-all duration-200"
                  />
                  {edge.name && (
                    <g transform={`translate(${labelX}, ${labelY})`}>
                      <rect
                        x="-40"
                        y="-8"
                        width="80"
                        height="16"
                        rx="4"
                        fill="white"
                        stroke="#cbd5e1"
                        strokeWidth="1"
                        className="dark:fill-slate-900 dark:stroke-slate-700"
                      />
                      <text
                        x="0"
                        y="3"
                        textAnchor="middle"
                        className="text-[9px] font-semibold fill-slate-700 dark:fill-slate-300 select-none"
                      >
                        {edge.name}
                      </text>
                    </g>
                  )}
                </g>
              )
            })}
          </svg>

          {/* 3. Render BPMN Nodes */}
          {process.nodes.map((node) => {
            const isSelected = selectedNodeId === node.id
            const isFaded = !filteredNodeIds.has(node.id)
            const isRpa = node.category === 'rpa_bot'
            const isBottleneck = (node.slaMinutes || 0) >= 120
            const isEndReject =
              node.id.toLowerCase().includes('reject') ||
              node.name.toLowerCase().includes('отказ')

            // Start Event Node (Green Circle)
            if (node.type === 'startEvent') {
              return (
                <div
                  key={node.id}
                  onClick={() => onSelectNode(node)}
                  style={{
                    position: 'absolute',
                    left: `${node.geometry.x}px`,
                    top: `${node.geometry.y}px`,
                    width: '48px',
                    height: '48px',
                  }}
                  className={`group cursor-pointer ${isFaded ? 'opacity-30' : 'opacity-100'}`}
                >
                  <div
                    className={`h-12 w-12 rounded-full bg-emerald-500 border-4 border-emerald-200 dark:border-emerald-800 shadow-md flex items-center justify-center text-white hover:scale-110 transition-transform ${
                      isSelected ? 'ring-4 ring-emerald-500/60 ring-offset-2' : ''
                    }`}
                  >
                    <Play className="w-4 h-4 fill-white ml-0.5" />
                  </div>
                  <div className="absolute top-13 left-1/2 -translate-x-1/2 text-center whitespace-nowrap">
                    <span className="text-[10px] font-bold text-slate-800 dark:text-slate-200 bg-white/90 dark:bg-slate-900/90 px-1.5 py-0.5 rounded shadow-2xs border border-slate-200 dark:border-slate-800">
                      {node.name || 'Поступление заявки'}
                    </span>
                  </div>
                </div>
              )
            }

            // End Event Node (Red or Green Double-border Circle)
            if (node.type === 'endEvent') {
              const isSuccess = !isEndReject
              return (
                <div
                  key={node.id}
                  onClick={() => onSelectNode(node)}
                  style={{
                    position: 'absolute',
                    left: `${node.geometry.x}px`,
                    top: `${node.geometry.y}px`,
                    width: '48px',
                    height: '48px',
                  }}
                  className={`group cursor-pointer ${isFaded ? 'opacity-30' : 'opacity-100'}`}
                >
                  <div
                    className={`h-12 w-12 rounded-full shadow-md flex items-center justify-center hover:scale-110 transition-transform ${
                      isSuccess
                        ? 'bg-emerald-600 border-4 border-emerald-300 dark:border-emerald-800 text-white'
                        : 'bg-rose-600 border-4 border-rose-300 dark:border-rose-800 text-white'
                    } ${isSelected ? 'ring-4 ring-rose-500/60 ring-offset-2' : ''}`}
                  >
                    <div className="h-4 w-4 rounded-full bg-white" />
                  </div>
                  <div className="absolute top-13 left-1/2 -translate-x-1/2 text-center whitespace-nowrap">
                    <span
                      className={`text-[10px] font-bold px-1.5 py-0.5 rounded shadow-2xs border ${
                        isSuccess
                          ? 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950 border-emerald-200'
                          : 'text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950 border-rose-200'
                      }`}
                    >
                      {node.name || (isSuccess ? 'Кредит выдан' : 'Отказ')}
                    </span>
                  </div>
                </div>
              )
            }

            // Gateway Nodes (XOR / AND Diamond)
            if (
              node.type === 'exclusiveGateway' ||
              node.type === 'parallelGateway' ||
              node.type === 'inclusiveGateway'
            ) {
              const isParallel = node.type === 'parallelGateway'
              return (
                <div
                  key={node.id}
                  onClick={() => onSelectNode(node)}
                  style={{
                    position: 'absolute',
                    left: `${node.geometry.x}px`,
                    top: `${node.geometry.y}px`,
                    width: '46px',
                    height: '46px',
                  }}
                  className={`group cursor-pointer select-none ${isFaded ? 'opacity-30' : 'opacity-100'}`}
                >
                  {/* Diamond Shape */}
                  <div
                    className={`h-[46px] w-[46px] rotate-45 rounded-lg bg-amber-100 dark:bg-amber-950 border-2 border-amber-500 shadow-md flex items-center justify-center hover:scale-110 transition-transform ${
                      isSelected ? 'ring-4 ring-amber-500/60 ring-offset-2' : ''
                    }`}
                  >
                    <span className="-rotate-45 text-amber-900 dark:text-amber-200 font-extrabold text-base leading-none">
                      {isParallel ? '+' : '×'}
                    </span>
                  </div>
                  {/* Gateway condition label */}
                  {node.name && node.name !== 'Условие' && (
                    <div className="absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap text-center">
                      <span className="text-[10px] font-bold text-amber-900 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/90 border border-amber-300 dark:border-amber-800 px-1.5 py-0.5 rounded shadow-2xs">
                        {node.name}
                      </span>
                    </div>
                  )}
                </div>
              )
            }

            // Task Card (UserTask / ServiceTask / PIX RPA)
            return (
              <div
                key={node.id}
                onClick={() => onSelectNode(node)}
                style={{
                  position: 'absolute',
                  left: `${node.geometry.x}px`,
                  top: `${node.geometry.y}px`,
                  width: `${node.geometry.width || 170}px`,
                  minHeight: `${node.geometry.height || 70}px`,
                }}
                className={`p-2.5 rounded-xl border bg-card text-card-foreground shadow-sm hover:shadow-md cursor-pointer transition-all flex flex-col justify-between group ${
                  isSelected
                    ? 'ring-2 ring-emerald-500 border-emerald-500 shadow-emerald-500/20'
                    : isRpa
                    ? 'border-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/30'
                    : isBottleneck
                    ? 'border-amber-400 bg-amber-50/40 dark:bg-amber-950/30'
                    : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900'
                } ${isFaded ? 'opacity-30' : 'opacity-100'}`}
              >
                {/* Header with Code & Automation Tag */}
                <div className="flex items-center justify-between gap-1 mb-1">
                  <Badge variant="outline" className="text-[10px] px-1 py-0 font-mono font-bold">
                    {node.code || 'STEP'}
                  </Badge>
                  {isRpa ? (
                    <Badge className="bg-emerald-600 text-white text-[9px] px-1.5 py-0 flex items-center gap-0.5">
                      <Cpu className="w-2.5 h-2.5" /> PIX RPA
                    </Badge>
                  ) : isBottleneck ? (
                    <Badge className="bg-amber-600 text-white text-[9px] px-1.5 py-0 flex items-center gap-0.5">
                      <Clock className="w-2.5 h-2.5" /> SLA {Math.round(node.slaMinutes! / 60)}ч
                    </Badge>
                  ) : (
                    <span className="text-[10px] text-muted-foreground flex items-center gap-0.5 font-medium">
                      <Clock className="w-2.5 h-2.5" /> {node.slaMinutes || 30}м
                    </span>
                  )}
                </div>

                {/* Task Title */}
                <h4 className="text-xs font-semibold text-foreground line-clamp-2 leading-snug group-hover:text-emerald-600 transition-colors">
                  {node.name}
                </h4>

                {/* Footer with Role / System */}
                <div className="mt-2 pt-1 border-t flex items-center justify-between text-[10px] text-muted-foreground">
                  <span className="truncate max-w-[90px] flex items-center gap-0.5">
                    <Server className="w-2.5 h-2.5 shrink-0 text-slate-400" />
                    {node.system || 'АБС'}
                  </span>
                  {node.automationPotential !== undefined && (
                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">
                      {node.automationPotential}% RPA
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Visualizer Legend Footer */}
      <div className="p-2.5 bg-muted/40 border-t flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-3 rounded-full bg-emerald-500 inline-block" />
            <span>Старт</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-3 rounded-md bg-emerald-100 border border-emerald-500 inline-block" />
            <span className="text-emerald-700 dark:text-emerald-400 font-medium">PIX RPA Робот</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-3 rounded-md bg-white border border-slate-300 inline-block" />
            <span>Ручная операция</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-3 rotate-45 bg-amber-100 border border-amber-500 inline-block" />
            <span>Шлюз условий (BPMN)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-3 rounded-full bg-emerald-600 inline-block" />
            <span>Успех</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-3 w-3 rounded-full bg-rose-600 inline-block" />
            <span>Отказ</span>
          </div>
        </div>

        <div className="flex items-center gap-1 text-[11px]">
          <Info className="w-3.5 h-3.5 text-blue-500" />
          <span>Кликните на любой шаг для редактирования регламента и SLA</span>
        </div>
      </div>
    </div>
  )
}
