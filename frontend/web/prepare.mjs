#!/usr/bin/env node
/**
 * Assemble the javis web assets from a *built* deepseek-harness checkout.
 *
 * The browser client javis serves is dsh's own web shell, so this script does
 * what `dsh web` does at runtime: compose the Loader rows from the dsh bundle
 * patches, keep the browser-facing ones, order them by the client module graph,
 * and mint the combo scripts plus the index-injection table the host injects.
 *
 * Nothing in the dsh checkout is written. Output lands next to this file:
 * dist/, plugins/, boot.json, manifest.json (all gitignored).
 *
 *   npm run prepare -- --dsh-root /path/to/deepseek-harness
 */

import { createHash } from 'node:crypto'
import {
  cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, extname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const OVERLAY_FILE = join(HERE, '.overlay.generated.yml')

/** Row ids dsh itself ships disabled; they are never part of the javis graph. */
const ALWAYS_DISABLED = new Set(['ui-schedule'])

function parseArgs(argv) {
  let dshRoot = process.env.DSH_ROOT ?? ''
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === '--dsh-root') dshRoot = argv[index + 1] ?? ''
    else if (arg.startsWith('--dsh-root=')) dshRoot = arg.slice('--dsh-root='.length)
  }
  return { dshRoot: dshRoot === '' ? '' : resolve(dshRoot) }
}

function fail(message) {
  console.error(`javis web prepare: ${message}`)
  process.exit(1)
}

function shortHash(input) {
  return createHash('sha1').update(input).digest('hex').slice(0, 12)
}

const { dshRoot } = parseArgs(process.argv.slice(2))
if (dshRoot === '') fail('missing --dsh-root <deepseek-harness checkout>')
if (!existsSync(join(dshRoot, 'package.json'))) fail(`${dshRoot} is not a dsh checkout`)
if (!existsSync(join(dshRoot, 'apps/web/dist/index.html'))) {
  fail(
    `${dshRoot} has no built web frontend (apps/web/dist/index.html).\n` +
      '  Run `pnpm install && pnpm run build` in the dsh checkout first.',
  )
}

/**
 * Manifests to resolve dsh packages through, in order.
 *
 * pnpm links a workspace package's dependencies only under the package that
 * declares them, so the repository root cannot resolve `@deepseek-ai/dsh-*`
 * helpers. dsh's own browser tests resolve them from the web-app bundle, which
 * is why that manifest comes first.
 */
const RESOLVER_MANIFESTS = [
  'packages/bundle/web-app/package.json',
  'apps/web/package.json',
  'package.json',
]

function resolveDshModule(specifier) {
  const tried = []
  for (const relative of RESOLVER_MANIFESTS) {
    const manifest = join(dshRoot, relative)
    if (!existsSync(manifest)) continue
    tried.push(relative)
    try {
      return createRequire(manifest).resolve(specifier)
    } catch {
      /* keep looking through the remaining manifests */
    }
  }
  fail(
    `cannot resolve ${specifier} from ${dshRoot} (tried: ${tried.join(', ')}).\n` +
      '  Run `pnpm install && pnpm run build` in the dsh checkout first.',
  )
}

async function importDshModule(specifier) {
  return import(pathToFileURL(resolveDshModule(specifier)).href)
}

const appBoot = await importDshModule('@deepseek-ai/dsh-app-boot')
const clientModules = await importDshModule('@deepseek-ai/dsh-client-modules')

const BUNDLE_LAYERS = [
  {
    manifest: join(dshRoot, 'packages/bundle/base/package.json'),
    patch: join(dshRoot, 'packages/bundle/base/cordis.patch.yml'),
  },
  {
    manifest: join(dshRoot, 'packages/bundle/web-app/package.json'),
    patch: join(dshRoot, 'packages/bundle/web-app/cordis.patch.yml'),
  },
]

for (const layer of BUNDLE_LAYERS) {
  if (!existsSync(layer.patch)) fail(`missing dsh bundle patch ${layer.patch}`)
}

const composition = JSON.parse(readFileSync(join(HERE, 'composition.json'), 'utf8'))
const excludeRows = new Set(composition.excludeRows ?? [])
const insertRows = composition.insertRows ?? []

/** Collect `package.json` paths up to `depth` levels below a root. */
function findManifests(root, depth) {
  const found = []
  const walk = (dir, level) => {
    if (level > depth) return
    for (const name of readdirSync(dir)) {
      if (name === 'node_modules' || name.startsWith('.')) continue
      const path = join(dir, name)
      if (!statSync(path).isDirectory()) continue
      if (name === 'lib' || name === 'src' || name === 'tests') continue
      const manifest = join(path, 'package.json')
      if (existsSync(manifest)) found.push(manifest)
      walk(path, level + 1)
    }
  }
  if (existsSync(root)) walk(root, 1)
  return found
}

/** Every workspace package manifest, keyed by package name. */
const packageManifests = new Map()
for (const path of [...findManifests(join(dshRoot, 'packages'), 3), ...findManifests(join(dshRoot, 'apps'), 2)]) {
  let pkg
  try {
    pkg = JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    continue
  }
  if (typeof pkg.name === 'string') packageManifests.set(pkg.name, { path, pkg })
}

/** Resolve one dsh Loader row into a browser plugin, or undefined for host-only rows. */
function browserPlugin(entry) {
  if (entry.disabled === true || typeof entry.name !== 'string') return undefined
  const found = packageManifests.get(entry.name)
  if (found === undefined) return undefined
  const declaration = found.pkg.dsh?.client
  if (declaration?.platform !== 'web') return undefined
  const exported = found.pkg.exports?.['./client']
  const relative = typeof exported === 'string' ? exported : exported?.default
  if (relative === undefined) {
    fail(`${entry.name} declares dsh.client without a ./client export`)
  }
  return { rowId: entry.name, id: entry.name, bundlePath: resolve(dirname(found.path), relative), declaration }
}

/** Apply the dsh layers, then the javis pruning overlay, and return browser rows. */
function composePluginRoster(overlayYaml) {
  const layers = BUNDLE_LAYERS.map((layer) => appBoot.loadOverlayPatches('javis web prepare', layer.patch))
  if (overlayYaml !== undefined) {
    writeFileSync(OVERLAY_FILE, overlayYaml)
    layers.push(appBoot.loadOverlayPatches('javis web prepare', OVERLAY_FILE))
  }
  const entries = appBoot.composeEntries(layers)
  return entries
}

const baseEntries = composePluginRoster(undefined)
const rowIdByPackage = new Map()
for (const entry of baseEntries) {
  if (typeof entry.name !== 'string') continue
  rowIdByPackage.set(entry.name, typeof entry.id === 'string' ? entry.id : entry.name)
}
const rosterRowIds = new Set(rowIdByPackage.values())

// composition.json is javis-owned and version-locked to the dsh build recorded
// in manifest.json, so an id the roster does not define is a typo we must not
// swallow: it would silently keep a row the composition asked to drop.
const unknownExcludes = [...excludeRows].filter((row) => !rosterRowIds.has(row))
if (unknownExcludes.length > 0) {
  fail(
    `composition.json lists rows the dsh roster does not define: ${unknownExcludes.join(', ')}\n` +
      '  Check the row ids in packages/bundle/*/cordis.patch.yml.',
  )
}

const basePlugins = new Map()
for (const entry of baseEntries) {
  const plugin = browserPlugin(entry)
  if (plugin !== undefined) basePlugins.set(plugin.id, plugin)
}

const kept = new Set(
  [...basePlugins.keys()].filter((id) => {
    const rowId = rowIdByPackage.get(id) ?? id
    return !excludeRows.has(rowId) && !ALWAYS_DISABLED.has(rowId)
  }),
)

// A silent mismatch between composition.json row ids and the dsh roster would
// keep rows the composition asked to drop, so refuse to proceed when nothing
// was pruned. `ui-schedule` alone always prunes, so require more than that.
const pruned = [...basePlugins.keys()].filter(
  (id) => excludeRows.has(rowIdByPackage.get(id) ?? id),
)
if (excludeRows.size > 0 && pruned.length === 0) {
  fail(
    'composition.json excluded no rows: its row ids do not match the dsh roster.\n' +
      `  Roster row ids: ${[...new Set([...rowIdByPackage.values()])].sort().slice(0, 12).join(', ')} …`,
  )
}

const autoAdded = new Set()
let grew = true
while (grew) {
  grew = false
  for (const id of [...kept]) {
    const plugin = basePlugins.get(id)
    for (const dependency of plugin?.declaration?.inject ?? []) {
      if (!basePlugins.has(dependency) || kept.has(dependency)) continue
      kept.add(dependency)
      autoAdded.add(dependency)
      grew = true
    }
  }
}

const disabledRows = [...basePlugins.keys()].filter((id) => !kept.has(id))
const insertYaml =
  insertRows.length === 0
    ? ''
    : `- insert:\n${insertRows
        .map((row) => `    - id: '${row.id}'\n      name: '${row.name}'\n`)
        .join('')}`
const overlayYaml =
  insertYaml +
  (disabledRows
    .map((id) => `- id: ${rowIdByPackage.get(id) ?? id}\n  disabled: true\n`)
    .join('') || '- id: javis-web-prepare-noop\n  disabled: true\n')

const finalEntries = composePluginRoster(overlayYaml)
const plugins = []
const insertedPackages = new Set(insertRows.map((row) => row.name))
for (const entry of finalEntries) {
  const plugin = browserPlugin(entry)
  if (plugin === undefined) continue
  if (!kept.has(plugin.id) && !insertedPackages.has(plugin.id)) continue
  const code = readFileSync(plugin.bundlePath)
  const rev = shortHash(code)
  plugins.push({
    ...plugin,
    rev,
    code,
    inject: plugin.declaration.inject,
    external: plugin.declaration.external,
    immediately: plugin.declaration.immediately,
  })
}

if (plugins.length === 0) fail('composition produced no browser plugins; check composition.json')

const ordered = clientModules.orderByModuleGraph(
  plugins.map(({ id, inject, external, immediately }) => ({
    id,
    ...(inject === undefined ? {} : { inject }),
    ...(external === undefined ? {} : { external }),
    ...(immediately === true ? { immediately: true } : {}),
  })),
)
const orderedIds = ordered.map((row) => row.id)
const pluginById = new Map(plugins.map((plugin) => [plugin.id, plugin]))

const entryRows = orderedIds.map((id) => {
  const plugin = pluginById.get(id)
  return {
    id,
    // A per-package URL, not a combo: the bundle's own `require.async`
    // chunks (`./client.<name>.js`) resolve relative to this URL, so they must
    // land inside the package's own served directory.
    url: `/plugins/${id}/client.js?rev=${plugin.rev}`,
    rev: plugin.rev,
    ...(plugin.inject === undefined ? {} : { inject: plugin.inject }),
    ...(plugin.external === undefined ? {} : { external: plugin.external }),
    ...(plugin.immediately === true ? { immediately: true } : {}),
  }
})

const BOOTSTRAP = new Set(['@deepseek-ai/dsh-client-modules'])
const bootstrapIds = orderedIds.filter((id) => BOOTSTRAP.has(id))
const applicationIds = orderedIds.filter((id) => !BOOTSTRAP.has(id))

const outputDir = HERE
const distDir = join(outputDir, 'dist')
const pluginsDir = join(outputDir, 'plugins')
rmSync(distDir, { recursive: true, force: true })
rmSync(pluginsDir, { recursive: true, force: true })
mkdirSync(pluginsDir, { recursive: true })

/**
 * Copy the built shell, dropping what the browser never fetches: source maps
 * (~13 MB here) and the WebWorker preview bundle (javis serves the normal page).
 */
cpSync(join(dshRoot, 'apps/web/dist'), distDir, {
  recursive: true,
  filter: (src) => {
    const rel = relative(join(dshRoot, 'apps/web/dist'), src)
    if (rel === 'preview' || rel.startsWith(`preview${sep}`) || rel.startsWith('preview.')) {
      return false
    }
    return extname(src) !== '.map'
  },
})

/** URL → file (relative to pluginsDir) for every script the page may request. */
const bundles = {}

function writeBundle(url, fileName, body) {
  const target = join(pluginsDir, fileName)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, body)
  bundles[url] = fileName
}

for (const plugin of plugins) {
  // Mirror the package's lib/ directory under its own /plugins prefix, so the
  // entry bundle and every dynamic chunk next to it are served from one base.
  const libDir = dirname(plugin.bundlePath)
  const packageDir = join(pluginsDir, plugin.id)
  // Only the emitted JavaScript (plus any CSS) reaches a browser: `.d.ts`,
  // `.map` and `.tsbuildinfo` in lib/ are build metadata.
  if (existsSync(libDir)) {
    cpSync(libDir, packageDir, {
      recursive: true,
      filter: (src) => {
        const ext = extname(src)
        return ext === '' || ext === '.js' || ext === '.css'
      },
    })
  }
  writeBundle(
    `/plugins/${plugin.id}/client.js?rev=${plugin.rev}`,
    join(plugin.id, 'client.js'),
    plugin.code,
  )
}

const batches = []
for (const id of orderedIds) {
  const plugin = pluginById.get(id)
  const phase = bootstrapIds.includes(id) ? 'bootstrap' : 'application'
  batches.push({
    phase,
    url: `/plugins/${id}/client.js?rev=${plugin.rev}`,
    rev: plugin.rev,
    entries: [id],
  })
}

const graph = { rev: shortHash(entryRows.map((row) => row.rev).join(':')), entries: entryRows, batches }
const injections = clientModules.bootInjections(graph)

const dshPackage = JSON.parse(readFileSync(join(dshRoot, 'package.json'), 'utf8'))
let dshCommit = 'unknown'
try {
  const { execFileSync } = await import('node:child_process')
  dshCommit = execFileSync('git', ['-C', dshRoot, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim()
} catch {
  /* not a git checkout — version stamp alone is enough */
}

writeFileSync(
  join(outputDir, 'boot.json'),
  `${JSON.stringify(
    {
      graph,
      injections,
      bundles,
      assets: { distRoot: 'dist', distIndex: 'dist/index.html' },
    },
    null,
    2,
  )}\n`,
)
writeFileSync(
  join(outputDir, 'manifest.json'),
  `${JSON.stringify(
    {
      generatedAt: new Date().toISOString(),
      dshVersion: dshPackage.version ?? 'unknown',
      dshCommit,
      entryCount: entryRows.length,
      bundles: Object.keys(bundles).length,
      excludedRows: [...excludeRows].filter((row) => rosterRowIds.has(row)),
      autoAddedPackages: [...autoAdded].sort(),
    },
    null,
    2,
  )}\n`,
)
rmSync(OVERLAY_FILE, { force: true })

console.log(
  `javis web prepare: ${entryRows.length} plugins, ${Object.keys(bundles).length} bundles ` +
    `(dsh ${dshPackage.version ?? 'unknown'} ${dshCommit})`,
)
if (autoAdded.size > 0) {
  console.log(`javis web prepare: auto-added ${autoAdded.size} injected dependencies`)
}
