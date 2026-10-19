<!--
  Copyright 2025-2026 mrveiss
  SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
/**
 * 12-column dashboard grid (#18242, spec §4.4).
 *
 * Widgets take a span of 4/6/8/12 columns (`--widget-s/m/l/full`) and a height in px.
 * In edit mode a widget can be moved by pointer drag or by keyboard grab mode, and
 * resized with its width and height buttons. Every change goes out through
 * `update:widgets`. The parent persists it (useDashboardLayout); this component holds
 * no storage. Styled with token utility classes only: no component-local style block (#12730).
 */
import { computed, ref, toRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { BaseButton } from '@autobot/ui'
import {
  HEIGHT_STEP,
  WIDGET_SPANS,
  clampHeight,
  moveWidget,
  stepSpan,
  type DashboardWidget,
} from '@/composables/dashboard/dashboardLayoutModel'
import { useDashboardGrabMode } from '@/composables/dashboard/useDashboardGrabMode'

const props = defineProps<{
  widgets: DashboardWidget[]
  editing: boolean
}>()

const emit = defineEmits<{ 'update:widgets': [widgets: DashboardWidget[]] }>()
const { t } = useI18n()

const model = computed({
  get: () => props.widgets,
  set: (next: DashboardWidget[]) => emit('update:widgets', next),
})
const { grabbedId, announcement, onKeydown } = useDashboardGrabMode(model)
const editing = toRef(props, 'editing')
const draggingId = ref<string | null>(null)

const SPAN_TOKEN: Record<number, string> = { 4: '--widget-s', 6: '--widget-m', 8: '--widget-l', 12: '--widget-full' }

function itemStyle(w: DashboardWidget): Record<string, string> {
  return { '--item-span': `var(${SPAN_TOKEN[w.span]})`, '--item-height': `${w.height}px` }
}

function handleKeydown(id: string, e: KeyboardEvent): void {
  // Only the widget itself: Space/Enter on a resize button inside it must press that button.
  if (!editing.value || e.target !== e.currentTarget) return
  if (onKeydown(id, e)) e.preventDefault()
}

function patch(id: string, change: Partial<DashboardWidget>): void {
  model.value = props.widgets.map((w) => (w.id === id ? { ...w, ...change } : w))
}

function resizeWidth(w: DashboardWidget, dir: 1 | -1): void {
  patch(w.id, { span: stepSpan(w.span, dir) })
}

function resizeHeight(w: DashboardWidget, dir: 1 | -1): void {
  patch(w.id, { height: clampHeight(w.height + dir * HEIGHT_STEP) })
}

function onDragStart(id: string, e: DragEvent): void {
  draggingId.value = id
  e.dataTransfer?.setData('text/plain', id)
}

function onDrop(targetId: string): void {
  const from = props.widgets.findIndex((w) => w.id === draggingId.value)
  const to = props.widgets.findIndex((w) => w.id === targetId)
  draggingId.value = null
  if (from < 0 || to < 0 || from === to) return
  model.value = moveWidget(props.widgets, from, to - from)
}

const atMinSpan = (w: DashboardWidget) => w.span === WIDGET_SPANS[0]
const atMaxSpan = (w: DashboardWidget) => w.span === WIDGET_SPANS[WIDGET_SPANS.length - 1]
</script>

<template>
  <div class="grid grid-cols-12 gap-(--stack)">
    <section
      v-for="w in widgets"
      :key="w.id"
      class="relative col-span-(--item-span) h-(--item-height) overflow-auto rounded-lg border bg-autobot-bg-card border-autobot-border max-md:col-span-12 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-autobot-focus"
      :class="{
        'cursor-grab border-dashed': editing,
        'outline-2 outline-offset-2 outline-autobot-focus': grabbedId === w.id,
        'opacity-50': draggingId === w.id,
      }"
      :style="itemStyle(w)"
      :tabindex="editing ? 0 : undefined"
      :aria-roledescription="editing ? t('dashboardGrid.movableWidget') : undefined"
      :aria-label="editing ? t('dashboardGrid.widgetLabel', { name: w.title || w.type }) : undefined"
      :aria-describedby="editing ? 'dashboard-grid-help' : undefined"
      :draggable="editing"
      @keydown="handleKeydown(w.id, $event)"
      @dragstart="onDragStart(w.id, $event)"
      @dragover.prevent
      @drop.prevent="onDrop(w.id)"
      @dragend="draggingId = null"
    >
      <div v-if="editing" class="absolute top-1 right-1 z-dropdown flex gap-0.5 rounded-md bg-autobot-bg-elevated">
        <BaseButton size="xs" variant="ghost" :aria-label="t('dashboardGrid.narrower')" :disabled="atMinSpan(w)" @click="resizeWidth(w, -1)">−</BaseButton>
        <BaseButton size="xs" variant="ghost" :aria-label="t('dashboardGrid.wider')" :disabled="atMaxSpan(w)" @click="resizeWidth(w, 1)">+</BaseButton>
        <BaseButton size="xs" variant="ghost" :aria-label="t('dashboardGrid.shorter')" @click="resizeHeight(w, -1)">↑</BaseButton>
        <BaseButton size="xs" variant="ghost" :aria-label="t('dashboardGrid.taller')" @click="resizeHeight(w, 1)">↓</BaseButton>
      </div>
      <slot name="widget" :widget="w" />
    </section>
    <p id="dashboard-grid-help" class="sr-only">{{ t('dashboardGrid.keyboardHelp') }}</p>
    <div class="sr-only" aria-live="polite" aria-atomic="true">{{ announcement }}</div>
  </div>
</template>
