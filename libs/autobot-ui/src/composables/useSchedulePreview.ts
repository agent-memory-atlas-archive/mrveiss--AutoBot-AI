// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
//
// Next-run preview for the schedule picker (#18432). Shows the advisory
// client computation immediately, then REPLACES it with the server's answer
// (the same code the scheduler runs). Server absent (null / rejected) keeps
// the advisory runs and says so; a server verdict of "invalid" wins.

import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { nextRuns } from '../schedule/nextRuns'
import type { SchedulePreviewFn, ScheduleSpec } from '../schedule/types'

export type PreviewSource = 'server' | 'advisory' | 'none'
export interface PreviewError {
  code: string
  params: Record<string, unknown>
}

export function useSchedulePreview(
  spec: Ref<ScheduleSpec | null>,
  preview: () => SchedulePreviewFn | undefined,
  debounceMs: () => number,
  count = 3,
) {
  const runs = ref<Date[]>([])
  const source = ref<PreviewSource>('none')
  const error = ref<PreviewError | null>(null)
  const loading = ref(false)
  let seq = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  function showAdvisory(s: ScheduleSpec): void {
    const local = nextRuns(s, count)
    runs.value = local ?? []
    source.value = local ? 'advisory' : 'none'
    error.value = null
  }

  async function askServer(s: ScheduleSpec, fn: SchedulePreviewFn, mine: number): Promise<void> {
    loading.value = true
    let result = null
    try {
      result = await fn(s, count)
    } catch {
      result = null // endpoint down or rejected: the advisory preview stays
    }
    if (mine !== seq) return
    loading.value = false
    if (!result) return
    if (!result.valid) {
      runs.value = []
      source.value = 'none'
      error.value = { code: result.error_code ?? '', params: result.error_params ?? {} }
      return
    }
    runs.value = result.next_runs.map((iso) => new Date(iso))
    source.value = 'server'
  }

  function refresh(): void {
    const mine = ++seq
    clearTimeout(timer)
    const s = spec.value
    if (!s) {
      runs.value = []
      source.value = 'none'
      error.value = null
      return
    }
    showAdvisory(s)
    const fn = preview()
    if (!fn) return
    const wait = debounceMs()
    if (wait <= 0) void askServer(s, fn, mine)
    else timer = setTimeout(() => void askServer(s, fn, mine), wait)
  }

  watch(spec, refresh, { immediate: true, deep: true })
  onBeforeUnmount(() => clearTimeout(timer))
  return { runs, source, error, loading, refresh }
}
