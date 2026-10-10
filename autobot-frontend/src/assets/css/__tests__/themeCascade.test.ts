// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * themeCascade.test.ts - the cascade rules #18161 and #17564 rest on.
 *
 * Reads the stylesheets as text: the property being pinned is which selector
 * declares which token, which a CSS parser in jsdom cannot report (it does not
 * compute custom-property inheritance across files).
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'

const assets = resolve(__dirname, '../../..')
const read = (rel: string): string =>
  readFileSync(resolve(assets, rel), 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '')

/** selector prelude -> declared custom-property names, for top-level rules. */
function rules(css: string): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].replace(/\s+/g, ' ').trim()
    const names = [...m[2].matchAll(/(--[\w-]+)\s*:/g)].map((d) => d[1])
    out.set(selector, new Set([...(out.get(selector) ?? []), ...names]))
  }
  return out
}

describe('density scope (#18161, #17564)', () => {
  const prefs = rules(read('assets/css/themes/preferences.css'))
  const scopes = ['compact', 'spacious', 'comfortable'].map((name) => {
    const key = `[data-layout-density="${name}"], .density-${name}`
    return { name, key, names: prefs.get(key) }
  })

  it.each(scopes)('$name is entered by the attribute and by a class', ({ key, names }) => {
    expect(names, `no rule for ${key}`).toBeDefined()
  })

  it('every scope sets the same tokens, so switching it moves all of them', () => {
    const [first, ...rest] = scopes.map((s) => [...(s.names ?? [])].sort())
    for (const other of rest) expect(other).toEqual(first)
  })

  it('every scope sets the four #17564 tokens and the spacing scale', () => {
    const required = [
      '--table-row-height',
      '--table-cell-py',
      '--card-padding',
      '--spacing-unit',
      ...['xs', 'sm', 'md', 'lg', 'xl', '2xl'].map((s) => `--spacing-${s}`),
    ]
    for (const { names } of scopes) for (const name of required) expect(names).toContain(name)
  })

  it('design-tokens.css declares the four tokens once, as the comfortable base', () => {
    const base = read('assets/css/design-tokens.css')
    for (const name of ['--table-row-height', '--table-cell-py', '--card-padding', '--spacing-unit']) {
      expect([...base.matchAll(new RegExp(`^\\s*${name}\\s*:`, 'gm'))].length).toBe(1)
    }
  })
})

describe('ember palette scope (#18161)', () => {
  it('the base --red-500 and --orange-500 are the default palette', () => {
    const base = read('assets/css/design-tokens.css')
    expect(base).toMatch(/--red-500:\s*#ef4444/i)
    expect(base).toMatch(/--orange-500:\s*#ff7849/i)
  })

  it('ember.css declares them only behind the ember selector', () => {
    const ember = rules(read('assets/css/themes/ember.css'))
    for (const [selector, names] of ember) {
      const scoped = selector.includes('data-theme-variant="ember"')
      if (names.has('--red-500') || names.has('--orange-500')) expect(scoped, selector).toBe(true)
    }
    const hits = [...ember].filter(([, n]) => n.has('--red-500') && n.has('--orange-500'))
    expect(hits.length).toBe(1)
  })
})
