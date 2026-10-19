// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * Keyboard grab mode for dashboard widgets (owner ruling on #18242).
 * Space/Enter grabs and drops, Arrows move, Shift+Left/Right changes the span,
 * Shift+Up/Down changes the height, and Esc restores the pre-grab state.
 * Every change is announced through `announcement`.
 */
import { ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  HEIGHT_STEP,
  clampHeight,
  moveWidget,
  stepSpan,
  type DashboardWidget,
} from './dashboardLayoutModel'

export function useDashboardGrabMode(widgets: Ref<DashboardWidget[]>) {
  const { t } = useI18n()
  const grabbedId = ref<string | null>(null)
  const announcement = ref('')
  let snapshot: DashboardWidget[] | null = null

  const indexOf = (id: string) => widgets.value.findIndex((w) => w.id === id)
  const label = (w: DashboardWidget) => w.title || w.type

  function announcePosition(id: string, key: string) {
    const i = indexOf(id)
    const w = widgets.value[i]
    announcement.value = t(key, { name: label(w), position: i + 1, total: widgets.value.length, span: w.span, height: w.height })
  }

  function toggleGrab(id: string) {
    if (grabbedId.value === id) {
      grabbedId.value = null
      snapshot = null
      announcePosition(id, 'dashboardGrid.announce.dropped')
      return
    }
    snapshot = widgets.value.map((w) => ({ ...w }))
    grabbedId.value = id
    announcePosition(id, 'dashboardGrid.announce.grabbed')
  }

  function cancel() {
    const id = grabbedId.value
    if (!id || !snapshot) return
    widgets.value = snapshot
    snapshot = null
    grabbedId.value = null
    announcePosition(id, 'dashboardGrid.announce.cancelled')
  }

  function resize(id: string, key: string) {
    const i = indexOf(id)
    const w = { ...widgets.value[i] }
    if (key === 'ArrowLeft' || key === 'ArrowRight') w.span = stepSpan(w.span, key === 'ArrowRight' ? 1 : -1)
    else w.height = clampHeight(w.height + (key === 'ArrowDown' ? HEIGHT_STEP : -HEIGHT_STEP))
    widgets.value = widgets.value.map((x, j) => (j === i ? w : x))
    announcePosition(id, 'dashboardGrid.announce.resized')
  }

  // Up/Down jump by the number of same-span widgets that fit in one 12-column row.
  function move(id: string, key: string) {
    const rowStep = Math.max(1, Math.round(12 / (widgets.value[indexOf(id)].span || 12)))
    const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -rowStep, ArrowDown: rowStep }[key] ?? 0
    widgets.value = moveWidget(widgets.value, indexOf(id), delta)
    announcePosition(id, 'dashboardGrid.announce.moved')
  }

  /** Returns true when the key was handled (caller then prevents default). */
  function onKeydown(id: string, e: KeyboardEvent): boolean {
    if (e.key === ' ' || e.key === 'Enter') return toggleGrab(id), true
    if (grabbedId.value !== id) return false
    if (e.key === 'Escape') return cancel(), true
    if (!e.key.startsWith('Arrow')) return false
    if (e.shiftKey) resize(id, e.key)
    else move(id, e.key)
    return true
  }

  return { grabbedId, announcement, onKeydown, cancel }
}
