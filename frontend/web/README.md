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
- generated output (gitignored): `dist/`, `plugins/`, `boot.json`,
  `manifest.json`.

## Preparing the assets

The dsh checkout must be installed and built first; the assembly consumes its
built client artifacts, never its sources:

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

`javis web` does the same thing automatically when assets are missing, and
`javis web --rebuild-assets` forces a rebuild.

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

## Standing caveat

The combo URL and package-local chunk shapes are mirrored from dsh's own test
harness (`apps/web/tests/assembled-boot.ts`) because they cannot be verified
without a real build. After the first build, load the page once and confirm the
network panel shows only 200s for `/plugins/...`; if a bundle 404s, the fix
belongs in `prepare.mjs` (chunk layout) or in the host's `/plugins` route.
