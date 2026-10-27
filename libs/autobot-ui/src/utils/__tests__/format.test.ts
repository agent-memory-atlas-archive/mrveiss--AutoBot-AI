// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest'
import { formatDuration, formatFileSize } from '../format'

describe('formatDuration msCompound (#19680)', () => {
  it('walks seconds, minutes and hours from milliseconds', () => {
    expect(formatDuration(0, { style: 'msCompound' })).toBe('0s')
    expect(formatDuration(59999, { style: 'msCompound' })).toBe('59s')
    expect(formatDuration(61000, { style: 'msCompound' })).toBe('1m 1s')
    expect(formatDuration(3_660_000, { style: 'msCompound' })).toBe('1h 1m')
  })

  it('returns the null text for a missing value', () => {
    expect(formatDuration(null, { style: 'msCompound', nullText: '-' })).toBe('-')
  })
})

describe('formatFileSize with a short unit ladder (#19680)', () => {
  const short = { decimals: 1, units: ['B', 'KB', 'MB', 'GB'], nullText: '0 B' } as const
  it('keeps the file-browser text', () => {
    expect(formatFileSize(0, short)).toBe('0 B')
    expect(formatFileSize(1536, short)).toBe('1.5 KB')
    expect(formatFileSize(null, short)).toBe('0 B')
  })
})
