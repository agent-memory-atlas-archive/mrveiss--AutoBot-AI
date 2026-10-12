// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
//
// Picker state for SchedulePicker (#18432): the editable PickerRule, the
// zone, and the compiled spec. A spec the picker cannot show (parse → null)
// is kept verbatim and surfaces as "Custom schedule" until the user clicks a
// repeat. External model changes re-sync WITHOUT echoing back an emit.

import { nextTick, onMounted, ref, watch } from 'vue'
import { compilePickerRule } from '../schedule/compile'
import { defaultRule, ruleTime } from '../schedule/defaults'
import { parseSpecToRule } from '../schedule/parse'
import type { PickerRule, RepeatKind, ScheduleSpec } from '../schedule/types'

export function useScheduleState(
  model: () => ScheduleSpec | null,
  defaultZone: () => string,
  emitSpec: (spec: ScheduleSpec) => void,
) {
  const start = model()
  const timezone = ref(start?.timezone ?? defaultZone())
  const rule = ref<PickerRule | null>(start ? parseSpecToRule(start) : defaultRule('daily', undefined, timezone.value))
  const spec = ref<ScheduleSpec | null>(start)
  let silent = false
  let lastKey = start ? JSON.stringify(start) : ''

  function publish(next: ScheduleSpec): void {
    spec.value = next
    lastKey = JSON.stringify(next)
    emitSpec(next)
  }

  function adopt(next: ScheduleSpec): void {
    silent = true
    timezone.value = next.timezone
    rule.value = parseSpecToRule(next)
    spec.value = next
    lastKey = JSON.stringify(next)
    void nextTick(() => (silent = false))
  }

  /** A spec accepted from elsewhere (expert cron): show it AND tell the parent. */
  function accept(next: ScheduleSpec): void {
    adopt(next)
    emitSpec(next)
  }

  function choose(repeat: RepeatKind): void {
    rule.value = defaultRule(repeat, ruleTime(rule.value), timezone.value)
  }

  watch([rule, timezone], () => {
    if (silent) return
    if (rule.value) publish(compilePickerRule(rule.value, timezone.value))
    else if (spec.value) publish({ ...spec.value, timezone: timezone.value })
  }, { deep: true })

  watch(model, (next) => {
    if (next && JSON.stringify(next) !== lastKey) adopt(next)
  }, { deep: true })

  onMounted(() => {
    if (!start && rule.value) publish(compilePickerRule(rule.value, timezone.value))
  })

  return { rule, timezone, spec, choose, adopt, accept }
}
