// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * AutoBot - AI-Powered Automation Platform
 * Author: mrveiss
 *
 * themePresets.ts - named theme + accent combinations
 * Issue #8988: User-Selectable Theme System - Custom Theme Presets
 *
 * Split out of useTheme.ts (#18223) to keep that file under its size ceiling.
 * Imports types only from useTheme, so there is no runtime cycle.
 */

import type { Theme, AccentColor } from './useTheme'

/** Theme preset options - named combinations of theme + accent + optional density */
export type ThemePreset =
  | 'auto' // System preference
  | 'catppuccin-mocha' // Dark + Purple
  | 'catppuccin-latte' // Light + Purple
  | 'solarized-dark' // Dark + Teal
  | 'solarized-light' // Light + Teal
  | 'high-contrast-dark' // Dark + Blue (high contrast)
  | 'high-contrast-light' // Light + Blue (high contrast)
  | 'brand-dark' // Dark + AutoBot brand color (teal)
  | 'brand-light' // Light + AutoBot brand color (teal)
  | 'midnight' // Dark + Indigo
  | 'sunset' // Light + Orange
  | 'forest' // Dark + Green
  | 'rose' // Light + Pink

/** Configuration for each theme preset */
export interface ThemePresetConfig {
  name: string
  description: string
  theme: Theme
  accentColor: AccentColor
  highContrast?: boolean
}

/** Preset configurations */
export const THEME_PRESETS: Record<ThemePreset, ThemePresetConfig> = {
  auto: {
    name: 'Auto',
    description: 'Follows system preference',
    theme: 'system',
    accentColor: 'theme',
  },
  'catppuccin-mocha': {
    name: 'Catppuccin Mocha',
    description: 'Warm dark theme with purple accents',
    theme: 'dark',
    accentColor: 'purple',
  },
  'catppuccin-latte': {
    name: 'Catppuccin Latte',
    description: 'Soft light theme with purple accents',
    theme: 'light',
    accentColor: 'purple',
  },
  'solarized-dark': {
    name: 'Solarized Dark',
    description: 'Classic dark theme with cyan accents',
    theme: 'dark',
    accentColor: 'teal',
  },
  'solarized-light': {
    name: 'Solarized Light',
    description: 'Classic light theme with cyan accents',
    theme: 'light',
    accentColor: 'teal',
  },
  'high-contrast-dark': {
    name: 'High Contrast Dark',
    description: 'Maximum readability dark theme',
    theme: 'dark',
    accentColor: 'blue',
    highContrast: true,
  },
  'high-contrast-light': {
    name: 'High Contrast Light',
    description: 'Maximum readability light theme',
    theme: 'light',
    accentColor: 'blue',
    highContrast: true,
  },
  'brand-dark': {
    name: 'AutoBot Dark',
    description: 'Official AutoBot dark theme',
    theme: 'dark',
    accentColor: 'teal',
  },
  'brand-light': {
    name: 'AutoBot Light',
    description: 'Official AutoBot light theme',
    theme: 'light',
    accentColor: 'teal',
  },
  midnight: {
    name: 'Midnight',
    description: 'Deep blue night theme',
    theme: 'dark',
    accentColor: 'indigo',
  },
  sunset: {
    name: 'Sunset',
    description: 'Warm orange light theme',
    theme: 'light',
    accentColor: 'orange',
  },
  forest: {
    name: 'Forest',
    description: 'Natural green dark theme',
    theme: 'dark',
    accentColor: 'green',
  },
  rose: {
    name: 'Rose',
    description: 'Elegant pink light theme',
    theme: 'light',
    accentColor: 'pink',
  },
}
