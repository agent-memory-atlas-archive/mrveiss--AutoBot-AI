---
tags: [type/architecture, status/current]
date: 2026-10-11
issue: 18223
---

# Refined UI tokens: name mapping

**Issues:** #18223 (Refined UI, build-order step 1), #14785 (one token SSOT) · **Source:** the Refined UI redesign handoff and its `tokens.css`.

The Refined handoff names its colours `--bg`, `--surface`, `--text`, and so on. The application already ships
`--bg-*`, `--text-*`, `--border-*`, `--color-*` and the card/table/input fills, and every view uses them. Step 1
folds the Refined values into the existing system instead of forking it:

| Layer | File | Holds |
|---|---|---|
| Light values, all sizes | `autobot-frontend/src/assets/css/design-tokens.css` | the one base declaration of every Refined name (light is the Refined default) |
| Dark | `src/assets/css/themes/dark.css` | Refined colour names for `data-theme="dark"` |
| High contrast, monochrome | `src/assets/css/themes/contrast.css`, `mono.css` | Refined colour names for `data-theme="contrast"` and `"mono"` |
| Bridge | `src/assets/css/themes/refined-map.css` | the existing names, as `var()` of the Refined name of the active theme (no values) |
| Tailwind | `src/assets/tailwind.css` `@theme` | `--color-autobot-*` aliases for the Refined names with no existing utility |
| Catalogue | `src/design-system/tokens.ts` | `REFINED_COLOR_TOKENS`, `REFINED_SIZE_TOKENS`, `BUILTIN_THEMES` |

Because the bridge is `var()` only, one value per theme feeds both vocabularies: change `--surface` in
`dark.css` and `--bg-card`, `--card-bg` and `--bg-input` follow.

## Themes

`data-theme` on `<html>`: `light` (Refined default), `dark`, `contrast` (high contrast, 2px borders), `mono`
(monochrome). `useTheme` also accepts `system`, which resolves to `light` or `dark`. `contrast` and `mono` are
light-based: `themes/light.css` supplies their non-colour-name tokens (charts, code syntax, badges, scrollbars).

`contrast` and `mono` also set `--color-primary*` to the Refined ink at accent specificity, so an accent preset
(`data-accent`) cannot put a hue on a theme whose point is having none.

## Colour names

"existing" means an existing name now resolves to this Refined name through `refined-map.css`. "new" means the
Refined name has no existing equivalent and is added. The Tailwind column is the `--color-autobot-<name>` alias
added to `@theme`; existing names keep their existing `autobot-*` utilities.

| Refined | Existing name(s) it feeds | Status | Tailwind | Note |
|---|---|---|---|---|
| `--bg` | `--bg-primary` | existing | existing `autobot-*` | page background |
| `--surface` | `--bg-secondary`, `--bg-surface`, `--bg-elevated`, `--bg-card`, `--bg-input`, `--card-bg`, `--table-row-bg`, `--input-bg` | existing | existing `autobot-*` | panel, card and field fill |
| `--surface2` | `--bg-tertiary`, `--table-header-bg` | existing | `color-autobot-surface2` | the quiet second surface |
| `--border` | `--border-default`, `--card-border`, `--table-border`, `--input-border` | existing | existing `autobot-*` | default border |
| `--line` | `--border-subtle` | existing | `color-autobot-line` | hairline divider |
| `--bw` | - | new | - | border width; 2px in contrast |
| `--text` | `--text-primary` | existing | existing `autobot-*` | body text |
| `--text2` | `--text-secondary` | existing | existing `autobot-*` |  |
| `--muted` | `--text-tertiary`, `--text-muted` | existing | existing `autobot-*` |  |
| `--link` | `--text-link` | existing | existing `autobot-*` | Refined links are ink-coloured; see "Not folded in step 1" for the underline decision |
| `--link-h` | `--text-link-hover` | existing | existing `autobot-*` |  |
| `--primary` | - | new | `color-autobot-ink` | ink colour (tooltips, selected edges, primary buttons). NOT `--color-primary`: see below |
| `--primary-h` | - | new | `color-autobot-ink-hover` | hover of `--primary` |
| `--on-primary` | `--text-inverse` | existing | `color-autobot-on-ink` | text on `--primary` |
| `--accent` | `--color-accent` | existing | `color-autobot-accent` | was set by the ember variant only; now every built-in theme |
| `--focus` | `--color-focus`, `--color-focus-ring` | existing | `color-autobot-focus` | focus outline colour (was an alias of `--color-primary`) |
| `--brand-bg` | - | new | `color-autobot-brand-bg` |  |
| `--brand-fg` | - | new | `color-autobot-brand-fg` |  |
| `--nav-on-bw` | - | new | - |  |
| `--tile` | - | new | `color-autobot-tile` |  |
| `--tile-b` | - | new | `color-autobot-tile-border` |  |
| `--stroke` | - | new | `color-autobot-stroke` |  |
| `--i1` | - | new | `color-autobot-illus-1` |  |
| `--i2` | - | new | `color-autobot-illus-2` |  |
| `--i3` | - | new | `color-autobot-illus-3` |  |
| `--i1t` | - | new | `color-autobot-illus-1-tint` |  |
| `--i2t` | - | new | `color-autobot-illus-2-tint` |  |
| `--i3t` | - | new | `color-autobot-illus-3-tint` |  |
| `--bar` | - | new | `color-autobot-bar` |  |
| `--bar-alert` | - | new | `color-autobot-bar-alert` |  |
| `--bar-track` | - | new | `color-autobot-bar-track` |  |
| `--alert-text` | - | new | `color-autobot-alert-text` |  |
| `--ok-bg` | - | new | `color-autobot-pill-ok-bg` |  |
| `--ok-fg` | - | new | `color-autobot-pill-ok-fg` |  |
| `--warn-bg` | - | new | `color-autobot-pill-warn-bg` |  |
| `--warn-fg` | - | new | `color-autobot-pill-warn-fg` |  |
| `--bad-bg` | - | new | `color-autobot-pill-bad-bg` |  |
| `--bad-fg` | - | new | `color-autobot-pill-bad-fg` |  |
| `--idle-bg` | - | new | `color-autobot-pill-idle-bg` |  |
| `--idle-fg` | - | new | `color-autobot-pill-idle-fg` |  |
| `--info-bg` | - | new | `color-autobot-pill-info-bg` |  |
| `--info-fg` | - | new | `color-autobot-pill-info-fg` |  |
| `--pill-b` | - | new | - |  |
| `--dot-ok` | - | new | `color-autobot-dot-ok` |  |
| `--dot-idle` | - | new | `color-autobot-dot-idle` |  |
| `--dot-warn` | - | new | `color-autobot-dot-warn` |  |
| `--dot-bad` | - | new | `color-autobot-dot-bad` |  |
| `--danger-tile` | - | new | `color-autobot-danger-tile` |  |
| `--danger-tile-b` | - | new | `color-autobot-danger-tile-border` |  |

New colour names: 36 of 49. Every one is declared in the four theme sets;
`--pill-b` and `--nav-on-bw` are unset (so inherit the base value) in themes that do not change them.

## Sizes (theme independent)

All declared once in `design-tokens.css`; none has an existing equivalent except the two aliased below.

`--bp-sm`, `--bp-md`, `--bp-lg`, `--bp-xl`, `--bp-2xl`, `--grid-cols`, `--grid-gutter`, `--page-margin`, `--topbar-h`, `--subnav-w`, `--subnav-rail`, `--panel-w`, `--content-max`, `--reading-max`, `--space-section`, `--stack`, `--widget-s`, `--widget-m`, `--widget-l`, `--widget-full`, `--kpi-span`, `--tbl-font-size`, `--tbl-row-h`, `--tbl-cell-px`, `--tbl-cell-py`, `--tbl-head-h`, `--tbl-head-bg`, `--tbl-head-fg`, `--tbl-head-font`, `--tbl-head-case`, `--tbl-head-ls`, `--tbl-head-border`, `--tbl-border`, `--tbl-row-hover`, `--tbl-row-selected`, `--tbl-selected-edge`, `--tbl-zebra`, `--tbl-zebra-bg`, `--tbl-check-col-w`, `--tbl-actions-col-w`, `--tbl-min-col`, `--tbl-cell-max`, `--tbl-num`, `--tbl-sticky-top`, `--field-h-sm`, `--field-h-md`, `--field-h-lg`, `--field-px`, `--field-radius`, `--field-font-size`, `--field-border`, `--field-border-hover`, `--field-bg`, `--field-bg-disabled`, `--field-fg`, `--field-focus-ring`, `--field-focus-offset`, `--label-size`, `--label-weight`, `--label-gap`, `--help-size`, `--help-fg`, `--error-fg`, `--error-border`, `--error-bw`, `--field-gap`, `--inline-gap`, `--form-section-gap`, `--form-label-col`, `--form-max`, `--action-gap`, `--check-gap`, `--control-size`, `--switch-w`, `--switch-h`, `--switch-knob`, `--tooltip-delay`, `--tooltip-hide-delay`, `--tooltip-offset`, `--tooltip-max`

| Refined size | Resolves to | Note |
|---|---|---|
| `--topbar-h` | `--header-height` | same value (56px); one source |
| `--field-radius` | `--radius-md` | same value (0.375rem = 6px); one source |
| `--grid-cols`, `--grid-gutter`, `--space-section`, `--stack` | own values | step down at 1024px and 768px behind `@media` |
| `--content-max` | own value (1440px) | differs from `--content-max-width` (1280px); both stay until the shell lands (step 2) |
| `--subnav-w`, `--subnav-rail`, `--panel-w` | own values | new layout; `--sidebar-width` (240px) is a different thing |
| `--tbl-*` density | `[data-layout-density="compact"]` | the mockups' `data-density="compact"` is folded into the one existing density attribute; `[data-zebra="on"]` is kept as is |

## Not folded in step 1

- **`[data-ui="minimal"]` / `.adv` / `.only-min` rules.** They are behaviour, not tokens; they belong to step 2 (shell, UI-mode toggle).
- **The CSS-only `[data-tip]` tooltip.** The handoff says the app uses the shared Tooltip primitive (#14779) with the `--tooltip-*` tokens, which are folded.
- **Fonts (IBM Plex Sans / Mono).** Not loaded in step 1: that changes layout metrics, which this step must not.
- **`--color-primary` is not `--primary`.** `--color-primary` is owned by the accent presets (`data-accent`, default `blue`), which override whatever a theme sets, so mapping the Refined navy onto it would have no effect on `light`/`dark`. `--primary` is added as its own name and used by Refined components from step 3. Making the Refined ink the default primary means a default accent of "theme" (no `data-accent` override): an owner decision, not made here.
- **Status colours (`--color-success`, `--color-warning`, `--color-error`, `--color-info`).** These are solid fills used with `--text-on-*`; the Refined `--ok-*`/`--warn-*`/`--bad-*`/`--info-*` are background/foreground pill pairs. Different roles, so both exist.
- **`--text-link` follows the Refined ink.** The Refined link colour equals the text colour, so a link needs a non-colour cue. Existing views keep whatever underline they have; adding one is a per-section change (step 5).
- **Shadows.** Refined has none. The existing `--shadow-*` are untouched until the primitives (step 3).
- **`prefers-color-scheme` fallbacks in `dark.css` / `light.css`** keep their literal legacy palette: they apply only before `useTheme` sets `data-theme` (first paint), and now match `:root:not([data-theme])` so they cannot override `contrast`/`mono`.

## Known limits

- `[data-theme-variant="ember"]` is more specific than `refined-map.css`, so it still overrides the surface, text and border names under any theme, including `contrast` and `mono`. Ember is an opt-in variant (it was the default for every user before #18161).
