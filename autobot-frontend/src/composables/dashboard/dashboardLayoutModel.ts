// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * Dashboard layout model and pure transforms (#18242). No Vue, no I/O, so it is unit-testable.
 */

export const WIDGET_SPANS = [4, 6, 8, 12] as const
export type WidgetSpan = (typeof WIDGET_SPANS)[number]
export const HEIGHT_MIN = 160
export const HEIGHT_MAX = 960
export const HEIGHT_STEP = 80

export interface DashboardWidget {
  id: string
  type: string
  title: string
  span: WidgetSpan
  height: number
  props: Record<string, unknown>
  refreshInterval?: number | null
}

export interface Dashboard {
  id: string
  name: string
  widgets: DashboardWidget[]
  createdAt: number
  updatedAt: number
}

export interface DashboardLayout {
  dashboards: Dashboard[]
  activeId: string | null
}

/** Old 3-column `width` (1..3) → 12-column span. */
export function spanFromLegacyWidth(width: number): WidgetSpan {
  if (width >= 3) return 12
  if (width === 2) return 8
  return 4
}

export function clampHeight(px: number): number {
  return Math.min(HEIGHT_MAX, Math.max(HEIGHT_MIN, Math.round(px)))
}

/** Next/previous allowed span; stays put at either end. */
export function stepSpan(span: WidgetSpan, dir: 1 | -1): WidgetSpan {
  const i = WIDGET_SPANS.indexOf(span) + dir
  return WIDGET_SPANS[Math.min(WIDGET_SPANS.length - 1, Math.max(0, i))]
}

/** Move the widget at `from` by `delta` slots, returning a new array. */
export function moveWidget<T>(widgets: T[], from: number, delta: number): T[] {
  const to = Math.min(widgets.length - 1, Math.max(0, from + delta))
  if (to === from) return widgets
  const next = [...widgets]
  const [w] = next.splice(from, 1)
  next.splice(to, 0, w)
  return next
}

interface LegacyWidget {
  id: string
  type: string
  title?: string
  width?: number
  height?: number
  props?: Record<string, unknown>
  refreshInterval?: number
  y?: number
  x?: number
}

interface LegacyDashboard {
  id: string
  name: string
  widgets?: LegacyWidget[]
  createdAt?: number
  updatedAt?: number
}

function fromLegacyWidget(w: LegacyWidget): DashboardWidget {
  return {
    id: w.id,
    type: w.type,
    title: w.title ?? '',
    span: spanFromLegacyWidth(w.width ?? 1),
    height: clampHeight(w.height ?? 320),
    props: w.props ?? {},
    refreshInterval: w.refreshInterval ?? null,
  }
}

/** Legacy localStorage dashboards → server layout, widgets ordered by (y, x). */
export function fromLegacyDashboards(raw: LegacyDashboard[]): DashboardLayout {
  const dashboards = raw.map((d) => ({
    id: d.id,
    name: d.name,
    widgets: [...(d.widgets ?? [])]
      .sort((a, b) => (a.y ?? 0) - (b.y ?? 0) || (a.x ?? 0) - (b.x ?? 0))
      .map(fromLegacyWidget),
    createdAt: d.createdAt ?? 0,
    updatedAt: d.updatedAt ?? 0,
  }))
  return { dashboards, activeId: dashboards[0]?.id ?? null }
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

/**
 * The layout inside a GET /users/me/dashboard-layouts/{page} body (`{ data: { layout } }`),
 * checked at runtime: anything not shaped like a layout reads as "nothing stored", so a
 * changed or broken response falls back to the defaults instead of rendering garbage.
 */
export function storedLayoutFrom(body: unknown): DashboardLayout | null {
  const layout = isRecord(body) && isRecord(body.data) ? body.data.layout : undefined
  if (!isRecord(layout) || !Array.isArray(layout.dashboards)) return null
  const ok = layout.dashboards.every((d) => isRecord(d) && typeof d.id === 'string' && Array.isArray(d.widgets))
  return ok ? (layout as unknown as DashboardLayout) : null
}
