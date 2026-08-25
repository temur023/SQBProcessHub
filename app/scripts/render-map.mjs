#!/usr/bin/env node
/**
 * Renders the BPMN map to SVG/PNG exactly as ProcessVisualizer.tsx would,
 * using the production parser + geometry + layout engine.
 * QA visual aid: node scripts/render-map.mjs [file.drawio|sample|sample-acc] [zoom]
 */
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const appDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(appDir, '..')
const outDir = path.join(repoRoot, 'qa-render')
mkdirSync(outDir, { recursive: true })

async function makeBundle() {
  const outfile = path.join(appDir, 'node_modules/.cache/qa-bundle.mjs')
  mkdirSync(path.dirname(outfile), { recursive: true })
  await build({
    entryPoints: [path.join(appDir, 'scripts/qa-entry.ts')],
    bundle: true,
    format: 'esm',
    platform: 'node',
    outfile,
    alias: { '@': path.join(appDir, 'src') },
    logLevel: 'silent',
    define: { 'process.env.NODE_ENV': '"test"' },
  })
  return outfile
}

function installDomGlobals() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>')
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.DOMParser = dom.window.DOMParser
  globalThis.XMLSerializer = dom.window.XMLSerializer
  globalThis.HTMLElement = dom.window.HTMLElement
  globalThis.atob = (s) => Buffer.from(s, 'base64').toString('binary')
  globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64')
}

const C = {
  canvas: '#1a1a1a',
  laneLine: 'rgba(255,255,255,0.42)',
  laneHead: '#0f0f0f',
  laneText: '#f3f3f3',
  taskFill: '#141414',
  taskStroke: '#f2f2f2',
  taskText: '#f7f7f7',
  rpaFill: '#101c16',
  rpaStroke: '#3dd68c',
  badFill: '#231c0c',
  badStroke: '#e8b84a',
  edge: '#dedede',
  edgeHi: '#7db7ff',
  labelBg: '#1a1a1a',
  gwStroke: '#e6b422',
  start: '#f3f3f3',
  endOk: '#5ee08a',
  endNo: '#ff6b6b',
}
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif'

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function renderSvg(proc, G, zoom, withGrid) {
  const { rawBox, parseStyleMap, styleNum, edgePath, fitBoxText, fitLaneLabel, laneHeaderWidth, layoutMapTexts, edgeDashArray, isRedStrokeColor, markerIdFor } = G

  const nodeBoxes = new Map()
  for (const n of proc.nodes) nodeBoxes.set(n.id, rawBox(n))
  const laneById = new Map(proc.lanes.map((l) => [l.id, l]))

  const textLayout = layoutMapTexts(proc, {
    labelBg: C.labelBg,
    labelBorder: '#3a3a3a',
    labelText: '#e8e8e8',
    eventOk: C.endOk,
    eventBad: C.endNo,
    sla: '#9a9a9a',
    gwCaption: '#e8e8e8',
  })

  const resolve = (id) => proc.nodes.find((n) => n.id === id) || proc.lanes.find((l) => l.id === id)

  const parts = []
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="1700" height="1060" viewBox="0 0 1700 1060" style="font-family:${esc(FONT)}">`)

  // defs: markers + clips
  parts.push('<defs>')
  parts.push(`<marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="${C.edge}"/></marker>`)
  parts.push(`<marker id="arr-hi" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="${C.edgeHi}"/></marker>`)
  parts.push(`<marker id="arr-dashed" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="#f0f0f0"/></marker>`)
  parts.push(`<marker id="arr-dashed-red" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="#ff6b6b"/></marker>`)
  for (const edge of proc.edges) {
    const col = (edge.strokeColor || '').trim()
    if (!col) continue
    parts.push(`<marker id="${esc(markerIdFor(edge))}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="${esc(col)}"/></marker>`)
  }
  for (const node of proc.nodes) {
    const b = nodeBoxes.get(node.id) || rawBox(node)
    const st = parseStyleMap(node.style)
    const padL = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingleft')
    const padT = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingtop')
    const padR = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingright')
    const padB = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingbottom')
    parts.push(`<clipPath id="clip-${esc(node.id)}"><rect x="${b.x + padL}" y="${b.y + padT}" width="${Math.max(8, b.w - padL - padR)}" height="${Math.max(8, b.h - padT - padB)}" rx="6"/></clipPath>`)
  }
  if (withGrid) {
    parts.push(`<pattern id="g-minor" width="${10 * zoom}" height="${10 * zoom}" patternUnits="userSpaceOnUse"><path d="M ${10 * zoom} 0 L 0 0 0 ${10 * zoom}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="1"/></pattern>`)
    parts.push(`<pattern id="g-major" width="${100 * zoom}" height="${100 * zoom}" patternUnits="userSpaceOnUse"><rect width="${100 * zoom}" height="${100 * zoom}" fill="url(#g-minor)"/><path d="M ${100 * zoom} 0 L 0 0 0 ${100 * zoom}" fill="none" stroke="rgba(255,255,255,0.12)" stroke-width="1"/></pattern>`)
  }
  parts.push('</defs>')

  parts.push(`<rect width="100%" height="100%" fill="${C.canvas}"/>`)
  if (withGrid) parts.push('<rect width="100%" height="100%" fill="url(#g-major)"/>')

  const pan = { x: 24, y: 24 }
  parts.push(`<g transform="translate(${pan.x},${pan.y}) scale(${zoom})">`)

  // lanes
  for (const lane of proc.lanes) {
    parts.push(`<rect x="${lane.geometry.x}" y="${lane.geometry.y}" width="${lane.geometry.width}" height="${lane.geometry.height}" fill="none" stroke="${C.laneLine}" stroke-width="1"/>`)
  }

  // edges
  for (const edge of proc.edges) {
    const src = resolve(edge.sourceId)
    const tgt = resolve(edge.targetId)
    if (!src || !tgt) continue
    const sb = nodeBoxes.get(src.id) || rawBox(src)
    const tb = nodeBoxes.get(tgt.id) || rawBox(tgt)
    const obstacles = []
    for (const n of proc.nodes) {
      if (n.id === src.id || n.id === tgt.id) continue
      obstacles.push(nodeBoxes.get(n.id) || rawBox(n))
    }
    const { d } = edgePath(src, tgt, sb, tb, edge, obstacles)
    const isDashed = Boolean(edge.dashed) || Boolean(edge.dashPattern) || isRedStrokeColor(edge.strokeColor)
    const dashArray = edgeDashArray(edge)
    const baseColor = edge.strokeColor ? edge.strokeColor : (isDashed ? '#e8e8e8' : C.edge)
    const sw = isDashed ? 1.7 : (edge.strokeWidth ? Math.max(1, Math.min(3.5, edge.strokeWidth)) : 1.35)
    let markerId
    if (isDashed && isRedStrokeColor(edge.strokeColor)) markerId = 'url(#arr-dashed-red)'
    else if (isDashed) markerId = edge.strokeColor ? `url(#${markerIdFor(edge)})` : 'url(#arr-dashed)'
    else if (edge.strokeColor) markerId = `url(#${markerIdFor(edge)})`
    else markerId = 'url(#arr)'
    const dashAttr = dashArray ? ` stroke-dasharray="${esc(dashArray)}"` : ''
    parts.push(`<path d="${esc(d)}" fill="none" stroke="${esc(baseColor)}" stroke-width="${sw}" stroke-linejoin="round" stroke-linecap="round"${dashAttr} marker-end="${markerId}"/>`)
  }

  // nodes
  for (const node of proc.nodes) {
    const b = nodeBoxes.get(node.id) || rawBox(node)
    const { x, y, w, h, cx, cy } = b
    const isRpa = node.category === 'rpa_bot'
    const isBad = (node.slaMinutes || 0) >= 120
    const isRej = node.id.toLowerCase().includes('reject') || node.name.toLowerCase().includes('отказ') || node.name.toLowerCase().includes('rad etildi')
    const st = parseStyleMap(node.style)

    if (node.type === 'startEvent') {
      const r = Math.max(8, Math.min(w, h) / 2 - 1)
      parts.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${C.canvas}" stroke="${C.start}" stroke-width="1.8"/>`)
    } else if (node.type === 'endEvent') {
      const r = Math.max(8, Math.min(w, h) / 2 - 1)
      const sc = isRej ? C.endNo : C.endOk
      parts.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${C.canvas}" stroke="${sc}" stroke-width="3.4"/>`)
      parts.push(`<circle cx="${cx}" cy="${cy}" r="${Math.max(4, r - 6)}" fill="none" stroke="${sc}" stroke-width="1.4"/>`)
    } else if (node.type === 'exclusiveGateway' || node.type === 'parallelGateway' || node.type === 'inclusiveGateway') {
      const s = Math.max(12, Math.min(w, h) / 2)
      parts.push(`<polygon points="${cx},${cy - s} ${cx + s},${cy} ${cx},${cy + s} ${cx - s},${cy}" fill="${C.canvas}" stroke="${C.gwStroke}" stroke-width="1.8"/>`)
      parts.push(`<text x="${cx}" y="${cy + 5}" text-anchor="middle" font-size="${Math.max(14, s * 0.7)}" font-weight="700" fill="${C.gwStroke}">${node.type === 'parallelGateway' ? '+' : '×'}</text>`)
    } else {
      const fill = isRpa ? C.rpaFill : isBad ? C.badFill : C.taskFill
      const stroke = isRpa ? C.rpaStroke : isBad ? C.badStroke : C.taskStroke
      const padL = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingleft')
      const padT = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingtop')
      const padR = 6 + styleNum(st, 'spacing') + styleNum(st, 'spacingright')
      const padB = 5 + styleNum(st, 'spacing') + styleNum(st, 'spacingbottom')
      const innerW = Math.max(12, w - padL - padR)
      const innerH = Math.max(12, h - padT - padB)
      const fitted = fitBoxText(node.name, innerW, innerH, 4)
      const align = (st.align || 'center').toLowerCase()
      const valign = (st.verticalalign || 'middle').toLowerCase()
      const textAnchor = align === 'left' ? 'start' : align === 'right' ? 'end' : 'middle'
      const tx = align === 'left' ? x + padL : align === 'right' ? x + w - padR : cx
      const lineH = fitted.fontSize + 3
      const blockH = fitted.lines.length * lineH
      let ty0
      if (valign === 'top') ty0 = y + padT + fitted.fontSize / 2
      else if (valign === 'bottom') ty0 = y + h - padB - blockH + fitted.fontSize / 2
      else ty0 = cy - (fitted.lines.length - 1) * lineH / 2
      parts.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" ry="8" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`)
      parts.push(`<g clip-path="url(#clip-${esc(node.id)})">`)
      fitted.lines.forEach((line, i) => {
        parts.push(`<text x="${tx}" y="${ty0 + i * lineH}" text-anchor="${textAnchor}" dominant-baseline="central" font-size="${fitted.fontSize}" fill="${C.taskText}">${esc(line)}</text>`)
      })
      parts.push('</g>')
    }
  }

  // text layout pieces (same render rules as the component)
  for (const p of textLayout) {
    const fill = p.fill === 'auto' ? '#e8e8e8' : p.fill
    if (p.pill) {
      parts.push(`<rect x="${p.pill.x}" y="${p.pill.y}" width="${p.pill.w}" height="${p.pill.h}" rx="3" fill="${C.labelBg}" stroke="#3a3a3a" stroke-width="0.7"/>`)
    }
    for (const line of p.lines) {
      const db = p.pill ? ' dominant-baseline="middle"' : ''
      const fw = p.bold ? ' font-weight="600"' : ''
      parts.push(`<text x="${line.x}" y="${line.y}" text-anchor="${p.anchor || 'middle'}"${db} font-size="${p.fontSize}"${fw} fill="${esc(fill)}">${esc(line.text)}</text>`)
    }
  }

  // lane headers
  for (const lane of proc.lanes) {
    const label = fitLaneLabel(lane.name, lane.geometry.height)
    const hw = laneHeaderWidth(lane)
    const hx = lane.geometry.x + hw / 2
    const hy = lane.geometry.y + lane.geometry.height / 2
    const lineH = label.fontSize + 3
    parts.push(`<rect x="${lane.geometry.x}" y="${lane.geometry.y}" width="${hw}" height="${lane.geometry.height}" fill="${C.laneHead}" stroke="${C.laneLine}" stroke-width="1"/>`)
    parts.push(`<g transform="rotate(-90, ${hx}, ${hy})">`)
    label.lines.forEach((line, i) => {
      parts.push(`<text x="${hx}" y="${hy - ((label.lines.length - 1) * lineH) / 2 + i * lineH}" text-anchor="middle" dominant-baseline="middle" font-size="${label.fontSize}" font-weight="600" fill="${C.laneText}">${esc(line)}</text>`)
    })
    parts.push('</g>')
  }

  parts.push('</g>')
  parts.push('</svg>')
  return parts.join('\n')
}

installDomGlobals()
const bundlePath = await makeBundle()
const G = await import(bundlePath)

const arg = process.argv[2] || 'process.drawio'
const zoom = Number(process.argv[3] || 0.9)
let proc
let name
if (arg === 'sample') {
  proc = G.sqbCreditProcess
  name = 'sample-sqbCreditProcess'
} else if (arg === 'sample-acc') {
  proc = G.sqbAccountProcess
  name = 'sample-sqbAccountProcess'
} else {
  const file = path.isAbsolute(arg) ? arg : path.join(repoRoot, arg)
  proc = await G.parseDrawio(readFileSync(file, 'utf8'), path.basename(arg))
  name = path.basename(arg).replace(/\.drawio$/, '')
}

for (const grid of [false, true]) {
  const svg = renderSvg(proc, G, zoom, grid)
  const svgFile = path.join(outDir, `${name}${grid ? '-grid' : ''}.svg`)
  writeFileSync(svgFile, svg)
  const pngFile = path.join(outDir, `${name}${grid ? '-grid' : ''}.png`)
  try {
    await sharp(Buffer.from(svg), { density: 144 }).png().toFile(pngFile)
    console.log(`rendered ${pngFile}`)
  } catch (e) {
    console.log(`PNG failed for ${svgFile}: ${e.message}`)
  }
}
console.log('done')
