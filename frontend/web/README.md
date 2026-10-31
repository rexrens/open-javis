# javis web frontend (dsh web assembly)

This directory does **not** contain a standalone web application. The browser UI
javis serves is DeepSeek Harness' own web client: the host injects a boot
manifest (`window.__DSH_BOOT__`) and serves the plugin bundles the client
fetches from `/plugins/...`. Nothing here can be started with a plain
`vite dev` — the same rule dsh applies to `apps/web`, which refuses to serve
itself standalone.

What lives here is the **assembly layer** javis owns:

- `composition.json` — which dsh browser rows to disable for the javis v1
  composition, plus the Remote endpoints javis implements.
- `prepare.mjs` — one-shot assembly against a built dsh checkout.
- generated output, **committed to this repo**: `dist/` (the built shell),
  `plugins/` (one bundle per kept dsh row), `boot.json` (the boot manifest) and
  `manifest.json` (which dsh build they came from).
- `LICENSE.deepseek-harness` — the MIT license the copied build output carries.

## Running without a dsh checkout

The committed assets are pruned to what a browser actually fetches (no source
maps, no `.d.ts`, no WebWorker preview bundle: ~13 MB total), so a fresh clone
can run the UI directly:

```sh
uv run javis web
```

Only a dsh upgrade requires regenerating them (see below).

## Preparing the assets

`prepare.mjs` consumes a **built** dsh checkout, never its sources. Install and
build dsh first — `prepare.mjs` fails with "cannot resolve
@deepseek-ai/dsh-app-boot" if that step was skipped:

```sh
cd /path/to/deepseek-harness
pnpm install
pnpm run build          # emits apps/web/dist and each package's lib/client.js
```

Then assemble the javis-side assets:

```sh
cd frontend/web
npm run prepare -- --dsh-root /path/to/deepseek-harness
```

`javis web --rebuild-assets --dsh-root <checkout>` does the same thing from the
CLI. Rebuild, then commit the refreshed `dist/`, `plugins/`, `boot.json` and
`manifest.json` together with any contract changes they imply: the launcher
refuses to start when `manifest.json` records a dsh version other than the one
the Python host implements.

## Output contract

`boot.json` is the only file the Python host reads:

```
{
  "graph":       { rev, entries[], batches[] },   // served as window.__DSH_BOOT__
  "injections":  [{ kind, ... }],                 // rendered into index.html
  "bundles":     { "<url>": "<file>" },           // /plugins/* URL → file in plugins/
  "assets":      { "distRoot": "dist", "distIndex": "dist/index.html" }
}
```

`manifest.json` records the dsh version and commit the assets came from. The
Python launcher refuses to start when the manifest is missing or records a
different dsh version than the one javis was verified against.

`composition.json` row ids are checked against the dsh roster: an id that no
layer defines aborts the assembly instead of silently keeping the row it was
meant to drop.

## Standing caveat

The combo URL and package-local chunk shapes are mirrored from dsh's own test
harness (`apps/web/tests/assembled-boot.ts`) because they cannot be derived from
the dsh sources alone. The committed assembly has been loaded in a real browser
(all `/plugins/...` requests 200, one full chat turn, no console errors), but a
dsh upgrade must repeat that check: if a bundle 404s, the fix belongs in
`prepare.mjs` (chunk layout) or in the host's `/plugins` route.
