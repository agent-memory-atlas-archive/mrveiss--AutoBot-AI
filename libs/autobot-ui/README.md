# @autobot/ui

Shared, **theme-agnostic** UI component kit for AutoBot. One implementation of each
element — consumed by **both** frontends so structure, behavior, and accessibility
stay consistent, while **each app keeps its own color identity**.

- `autobot-frontend` (main user GUI) — its own palette
- `autobot-slm-frontend` (control plane) — its own, distinct palette

This is umbrella [#10860](https://github.com/mrveiss/AutoBot-AI/issues/10860), **Task A**
(scaffold + token contract). Component build-out and per-app migration are Tasks B–D.

## The one rule

**Shared components never hardcode a color, radius, shadow, or font.** They style
exclusively off the semantic tokens in [`src/tokens/contract.css`](./src/tokens/contract.css)
(all prefixed `--aui-`). That indirection is what lets the *same* component wear each
app's identity.

Components also:

- use **scoped CSS** (no dependency on the consuming app's Tailwind build);
- are accessible by default (real semantics, `focus-visible` rings, `aria-*`);
- honor `prefers-reduced-motion` via the token motion values.

`BaseButton.vue` is the canonical reference for all of the above.

## Consuming it (Tasks C/D — not done in Task A)

Each frontend adds a `file:` dependency (mirrors `@autobot/vnc`):

```jsonc
// autobot-frontend/package.json  and  autobot-slm-frontend/package.json
"dependencies": {
  "@autobot/ui": "file:../libs/autobot-ui"
}
```

> Lockfile regeneration for that wiring must run on a machine with npm and lands with
> the first-consume PR (Task C/D), not here — this scaffold PR does not touch either
> app's `package.json`/lockfile, so it cannot break `npm ci`.

Then import the contract once at app startup and use components anywhere:

```ts
import '@autobot/ui/tokens'          // semantic token names + neutral fallbacks
import '@autobot/ui/adapter'         // maps the contract onto the shared semantic names (below)
```

```vue
<script setup lang="ts">
import { BaseButton } from '@autobot/ui'
</script>
<template>
  <BaseButton variant="primary" @click="save">Save</BaseButton>
</template>
```

## Implementing the contract (each app supplies its own values)

The kit ships ONE adapter, [`src/tokens/adapter.css`](./src/tokens/adapter.css), that maps
the `--aui-*` contract onto the shared **semantic names** (`--bg-surface`, `--text-primary`,
`--border-default`, `--color-primary`, `--color-error`, `--radius-md`, ...). It holds no
value. Each app defines those names with its own values, so **same names, different
values → different skins**:

- `autobot-frontend`: `src/assets/css/design-tokens.css` and `themes/`
- `autobot-slm-frontend`: `src/assets/styles/slm-scheme.css` (teal scheme) and `main.css`

An app that leaves one of the adapter's names undefined gets an invalid `--aui-*` token,
so define the name in the app; do not add a fallback to the adapter. (Before this, each
app kept its own copy of the adapter and mapped the contract onto names only it defined.)

Because the kit is Tailwind-v4-independent (scoped CSS + tokens), an app only needs to
provide these values — no Tailwind config wiring for the package is required.

## Token contract

See [`src/tokens/contract.css`](./src/tokens/contract.css) for the authoritative list.
Groups: brand/interactive color, neutrals/chrome, status colors, focus, radius,
elevation, typography, spacing, motion. The values there are **neutral fallbacks** so
the kit renders unthemed in isolation (Storybook/tests) — apps override them.

## Build

```bash
npm run build   # Vite library build → ES + CJS (vue kept external)
```
