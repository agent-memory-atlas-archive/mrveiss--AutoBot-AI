// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss
/**
 * Both CodeCells read ONE syntax palette (#14853): every `--codecell-syntax-*`
 * either component references is defined in design-tokens.css, and neither
 * `<style>` block carries a literal colour.
 */
import { describe, it, expect } from 'vitest'
import artifact from './CodeCell.vue?raw'
import canvas from '../canvas/CodeCell.vue?raw'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Read from disk: vitest's css handling does not give `?raw` the real text.
const tokens = readFileSync(resolve(__dirname, '../../assets/css/design-tokens.css'), 'utf-8')
const styleOf = (src: string) => src.slice(src.indexOf('<style')).replace(/\/\*[\s\S]*?\*\//g, '')
const used = (src: string) =>
  new Set(styleOf(src).match(/--codecell-syntax-[a-z]+-(?:dark|light)/g) ?? [])
const defined = new Set(
  [...tokens.matchAll(/^\s*(--codecell-syntax-[a-z]+-(?:dark|light)):/gm)].map((m) => m[1]),
)

describe.each([
  ['artifact-cells/CodeCell.vue', artifact],
  ['canvas/CodeCell.vue', canvas],
])('%s syntax palette', (_name, src) => {
  it('uses at least one shared syntax token', () => {
    expect(used(src).size).toBeGreaterThan(0)
  })
  it('references only tokens design-tokens.css defines', () => {
    for (const t of used(src)) expect(defined.has(t), t).toBe(true)
  })
  it('carries no literal hex in its style block', () => {
    expect(styleOf(src)).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })
})
