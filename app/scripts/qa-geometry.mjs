#!/usr/bin/env node
/**
 * QA geometry harness for the BPMN visualizer.
 *
 * Parses the real .drawio files with the PRODUCTION parser (src/lib/drawio.ts)
 * and validates the PRODUCTION layout engine (src/lib/visual-geometry.ts):
 *   - edge paths: NaN, zero-length segments, degenerate paths,
 *     segments crossing non-endpoint node interiors (lines hidden under boxes)
 *   - dashed edges: dash array validity (dashed lines must actually render)
 *   - texts: layoutMapTexts output must not overlap nodes or each other
 *   - lane header overflow
 *
 * Usage: node scripts/qa-geometry.mjs [--json]
 */
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const appDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(appDir, '..')

// ── 1. Bundle production sources for Node ─────────────────────────────────
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

// ── 2. jsdom globals needed by the parser ─────────────────────────────────
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

// ── 3. Checks ─────────────────────────────────────────────────────────────
function segHitsBox(a, b, box, minLen = 2) {
  const dx = b.x - a.x
  const dy = b.y - a.y
  if (Math.hypot(dx, dy) < 1) return false
  let t0 = 0
  let t1 = 1
  const p = [-dx, dx, -dy, dy]
  const q = [a.x - box.x, box.x + box.w - a.x, a.y - box.y, box.y + box.h - a.y]
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return false
    } else {
      const r = q[i] / p[i]
      if (p[i] < 0) {
        if (r > t1) return false
        if (r > t0) t0 = r
      } else {
        if (r < t0) return false
        if (r < t1) t1 = r
      }
    }
  }
  return Math.hypot(dx, dy) * (t1 - t0) > minLen
}

function bboxOverlapFrac(a, b) {
  const w = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x, b.x))
  const h = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y, b.y))
  const area = w * h
  const minArea = Math.min(Math.max(0, a.x2 - a.x) * Math.max(0, a.y2 - a.y), Math.max(0, b.x2 - b.x) * Math.max(0, b.y2 - b.y))
  return minArea > 0 ? area / minArea : 0
}

function analyze(proc, label) {
  const issues = []
  const { rawBox, edgePath, layoutMapTexts, edgeDashArray, isRedStrokeColor, isOrthogonalEdge } = G

  const nodeById = new Map(proc.nodes.map((n) => [n.id, n]))
  const laneById = new Map(proc.lanes.map((l) => [l.id, l]))
  const resolve = (id) => nodeById.get(id) || laneById.get(id)
  const boxes = new Map()
  for (const n of [...proc.nodes, ...proc.lanes]) boxes.set(n.id, rawBox(n))

  // ── Edges (production routing with obstacles, as the component does) ──
  const edgeGeom = new Map()
  for (const edge of proc.edges) {
    const src = resolve(edge.sourceId)
    const tgt = resolve(edge.targetId)
    if (!src || !tgt) {
      issues.push({ kind: 'edge-dangling', id: edge.id, msg: `edge references missing node (${edge.sourceId}→${edge.targetId})` })
      continue
    }
    const sb = boxes.get(src.id) || rawBox(src)
    const tb = boxes.get(tgt.id) || rawBox(tgt)
    const obstacles = []
    for (const n of proc.nodes) {
      if (n.id === src.id || n.id === tgt.id) continue
      obstacles.push(boxes.get(n.id) || rawBox(n))
    }
    const r = edgePath(src, tgt, sb, tb, edge, obstacles)
    edgeGeom.set(edge.id, { ...r, src, tgt, sb, tb })
    if (r.pts.some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y))) {
      issues.push({ kind: 'edge-nan', id: edge.id, msg: `NaN coordinates: ${r.d}` })
    }
    let total = 0
    for (let i = 0; i < r.pts.length - 1; i++) {
      const len = Math.hypot(r.pts[i + 1].x - r.pts[i].x, r.pts[i + 1].y - r.pts[i].y)
      if (len < 0.5) issues.push({ kind: 'edge-zero-seg', id: edge.id, msg: `zero-length segment at ${i}` })
      total += len
    }
    if (r.pts.length >= 2 && total < 4) {
      issues.push({ kind: 'edge-degenerate', id: edge.id, msg: `path too short (${total.toFixed(1)}px): ${r.d}` })
    }
    // dashed → dash array must be valid, else the dashes are invisible
    const isDashed = Boolean(edge.dashed) || Boolean(edge.dashPattern) || isRedStrokeColor(edge.strokeColor)
    if (isDashed) {
      const da = edgeDashArray(edge)
      if (!da || !/^[\d.\s]+$/.test(da.trim()) || da.trim().split(/\s+/).filter(Boolean).length < 2) {
        issues.push({ kind: 'dash-invalid', id: edge.id, msg: `invalid dash pattern "${da}"` })
      }
    }
  }

  // edge segments crossing non-endpoint node interiors
  for (const [id, g] of edgeGeom) {
    for (const n of proc.nodes) {
      if (n.id === g.src.id || n.id === g.tgt.id) continue
      if (n.type === 'lane') continue
      const nb = boxes.get(n.id) || rawBox(n)
      for (let i = 0; i < g.pts.length - 1; i++) {
        if (segHitsBox(g.pts[i], g.pts[i + 1], nb)) {
          issues.push({ kind: 'edge-through-node', id, msg: `passes through box of "${n.name || n.id}"` })
          break
        }
      }
    }
  }

  // duplicate parallel edges
  const seen = new Map()
  for (const [id, g] of edgeGeom) {
    const key = `${g.src.id}|${g.tgt.id}`
    if (seen.has(key)) {
      issues.push({ kind: 'edge-duplicate', id, msg: `duplicate edge parallel to ${seen.get(key)} (${g.src.id}→${g.tgt.id})` })
    }
    seen.set(key, id)
  }

  // ── Texts: production layout engine output ──
  let pieces
  try {
    pieces = layoutMapTexts(proc)
  } catch (e) {
    issues.push({ kind: 'layout-error', id: 'layout', msg: `layoutMapTexts threw: ${e.message}` })
    pieces = []
  }

  const nodeBBoxes = proc.nodes.map((n) => {
    const b = boxes.get(n.id) || rawBox(n)
    return { x: b.x, y: b.y, x2: b.x + b.w, y2: b.y + b.h, id: n.id, name: n.name }
  })

  // аппроксимация bbox для текстов без плашки
  const pieceBBox = (p) => {
    if (p.pill) return p.pill
    const w = Math.max(...p.lines.map((l) => l.text.length)) * p.fontSize * 0.56
    const first = p.lines[0]
    const last = p.lines[p.lines.length - 1]
    return {
      x: first.x - w / 2,
      y: first.y - p.fontSize * 0.8,
      x2: first.x + w / 2,
      y2: last.y + p.fontSize * 0.25,
    }
  }

  for (let i = 0; i < pieces.length; i++) {
    const p = pieces[i]
    const pb = pieceBBox(p)
    for (let j = i + 1; j < pieces.length; j++) {
      const q = pieces[j]
      const qb = pieceBBox(q)
      const frac = bboxOverlapFrac(pb, qb)
      if (frac > 0.06) {
        issues.push({ kind: 'text-stuck', id: p.id, msg: `«${p.lines.map((l) => l.text).join(' ')}» overlaps «${q.lines.map((l) => l.text).join(' ')}» by ${(frac * 100).toFixed(0)}%` })
      }
    }
    for (const nb of nodeBBoxes) {
      const frac = bboxOverlapFrac(pb, nb)
      if (frac > 0.12) {
        issues.push({ kind: 'text-on-node', id: p.id, msg: `«${p.lines.map((l) => l.text).join(' ')}» sits on node «${nb.name}» (${(frac * 100).toFixed(0)}%)` })
      }
    }
    // заголовки дорожек непрозрачны — текст не должен прятаться под ними
    for (const l of proc.lanes) {
      const hw = G.laneHeaderWidth(l)
      const hdr = { x: l.geometry.x, y: l.geometry.y, x2: l.geometry.x + hw, y2: l.geometry.y + l.geometry.height }
      const frac = bboxOverlapFrac(pb, hdr)
      if (frac > 0.08) {
        issues.push({ kind: 'text-under-header', id: p.id, msg: `«${p.lines.map((l) => l.text).join(' ')}» partially hidden under lane header «${l.name}» (${(frac * 100).toFixed(0)}%)` })
      }
    }
  }

  // потерянные слова: каждая подпись должна содержать все слова исходника
  const srcText = (id) => {
    const nodeId = id.split('#')[0]
    const n = proc.nodes.find((x) => x.id === nodeId)
    if (n) return n.name
    const e = proc.edges.find((x) => x.id === nodeId)
    return e ? e.name : ''
  }
  for (const p of pieces) {
    if (p.kind !== 'edge-label' && p.kind !== 'event-caption') continue
    const src = (srcText(p.id) || '').replace(/\s+/g, ' ').trim()
    if (!src) continue
    const rendered = p.lines.map((l) => l.text).join(' ')
    for (const word of src.split(' ')) {
      const clean = word.replace(/[.,;:!?()]/g, '')
      if (clean.length > 1 && !rendered.includes(clean)) {
        issues.push({ kind: 'text-lost-words', id: p.id, msg: `«${src}» rendered as «${rendered}» — потеряно слово «${word}»` })
        break
      }
    }
  }

  // каждое событие с названием должно получить подпись
  for (const n of proc.nodes) {
    if ((n.type === 'startEvent' || n.type === 'endEvent') && n.name && n.name !== 'Старт' && n.name !== 'Завершение') {
      const has = pieces.some((p) => p.kind === 'event-caption' && p.id.startsWith(`${n.id}#`))
      if (!has) {
        issues.push({ kind: 'caption-missing', id: n.id, msg: `event «${n.name}» has no caption in layout` })
      }
    }
    // шлюз не должен называться просто символом — вопрос потерялся при парсинге
    if ((n.type === 'exclusiveGateway' || n.type === 'parallelGateway' || n.type === 'inclusiveGateway')) {
      if (/^[×+?✕✗✓*]$/u.test((n.name || '').trim())) {
        issues.push({ kind: 'gw-name-symbol', id: n.id, msg: `gateway has symbol-only name «${n.name}» — question label lost` })
      }
    }
    // текст внутри бокса не должен обрезаться по словам
    if (n.type === 'userTask' || n.type === 'task' || n.type === 'serviceTask') {
      const b = boxes.get(n.id) || rawBox(n)
      const fitted = G.fitBoxText(n.name, Math.max(12, b.w - 12), Math.max(12, b.h - 10), 4)
      const joined = fitted.lines.join(' ')
      if (joined.length < n.name.replace(/\s+/g, ' ').trim().length) {
        issues.push({
          kind: 'node-text-truncated',
          id: n.id,
          msg: `«${n.name}» truncated to «${joined}» in box ${Math.round(b.w)}x${Math.round(b.h)}`,
        })
      }
    }
  }

  // ── Lane header fit ──
  for (const l of proc.lanes) {
    const hw = G.laneHeaderWidth(l)
    if (hw > l.geometry.width) {
      issues.push({ kind: 'lane-head-overflow', id: l.id, msg: `lane header ${hw}px > lane width ${l.geometry.width}px` })
    }
  }

  return issues
}

// ── 4. Run ────────────────────────────────────────────────────────────────
installDomGlobals()
const bundlePath = await makeBundle()
const G = await import(bundlePath)

const targets = [
  { file: path.join(repoRoot, 'process.drawio'), name: 'process.drawio' },
  { file: path.join(repoRoot, 'sqb_credit_process.drawio'), name: 'sqb_credit_process.drawio' },
  { file: path.join(appDir, 'public/sqb_credit_process.drawio'), name: 'public/sqb_credit_process.drawio' },
  { proc: G.sqbCreditProcess, name: 'sample: sqbCreditProcess' },
  { proc: G.sqbAccountProcess, name: 'sample: sqbAccountProcess' },
]

let exitCode = 0
const jsonOut = process.argv.includes('--json')

for (const t of targets) {
  let proc = t.proc
  if (!proc) {
    if (!existsSync(t.file)) continue
    const text = readFileSync(t.file, 'utf8')
    try {
      proc = await G.parseDrawio(text, path.basename(t.file))
    } catch (e) {
      console.log(`✗ ${t.name}: PARSE ERROR: ${e.message}`)
      exitCode = 1
      continue
    }
  }
  const issues = analyze(proc, t.name)
  const summary = {
    file: t.name,
    nodes: proc.nodes.length,
    lanes: proc.lanes.length,
    edges: proc.edges.length,
    nodeNames: proc.nodes.map((n) => `${n.id}=${n.name}`),
    issues,
  }
  if (jsonOut) {
    writeFileSync(path.join(repoRoot, `qa-${t.name.replace(/[^A-Za-z0-9]+/g, '_')}.json`), JSON.stringify(summary, null, 2))
    console.log(`json: qa-${t.name}.json (${issues.length} issues)`)
  } else {
    console.log(`\n===== ${t.name} — ${proc.nodes.length} nodes, ${proc.lanes.length} lanes, ${proc.edges.length} edges`)
    if (issues.length === 0) {
      console.log(`✓ no geometry issues`)
    } else {
      exitCode = 1
      const byKind = new Map()
      for (const i of issues) byKind.set(i.kind, (byKind.get(i.kind) || 0) + 1)
      console.log(`✗ ${issues.length} issues: ${[...byKind.entries()].map(([k, c]) => `${k}×${c}`).join(', ')}`)
      for (const i of issues) console.log(`   [${i.kind}] ${i.id}: ${i.msg}`)
    }
  }
}

process.exit(exitCode)
