// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

/**
 * useBackendHealthNotice — one system notification while the backend is degraded or
 * critical, carrying its status details; removed when it recovers (#16253).
 *
 * The details panel in SystemStatusNotification (status, last check, consecutive failures)
 * had no producer: nothing ever raised a notification with `statusDetails`, so it rendered
 * only in Storybook. The health monitor already measures those fields, so its health changes
 * now drive the notice.
 */

import type { HealthData } from '@/utils/HealthMonitor'
import type { SystemSeverity, SystemStatusDetails } from '@/types/system'

type BackendState = HealthData['status']['backend']

interface NoticeStore {
  addSystemNotification(n: {
    severity: SystemSeverity
    title: string
    message: string
    statusDetails?: SystemStatusDetails
  }): string
  removeSystemNotification(id: string): void
  systemNotifications: Array<{ id: string; statusDetails?: SystemStatusDetails }>
}

const NOTICE_SEVERITY: Partial<Record<BackendState, SystemSeverity>> = { degraded: 'warning', critical: 'error' }

export function useBackendHealthNotice(
  store: NoticeStore,
  t: (key: string) => string,
  consecutiveFailures: () => number,
) {
  let notice: { id: string; state: BackendState } | null = null

  function details(health: HealthData): SystemStatusDetails {
    const now = Date.now()
    return {
      status: health.status.backend,
      lastCheck: health.monitoring.lastCheck,
      consecutiveFailures: consecutiveFailures(),
      timestamp: now,
    }
  }

  function clear(): void {
    if (notice) store.removeSystemNotification(notice.id)
    notice = null
  }

  /** Apply one health change: raise, refresh, replace or clear the backend notice. */
  function onHealthChange(health: HealthData): void {
    const state = health.status.backend
    const severity = NOTICE_SEVERITY[state]
    if (!severity) {
      if (state === 'healthy') clear() // 'unknown' says nothing new: keep what is shown
      return
    }
    if (notice?.state === state) {
      const shown = store.systemNotifications.find((n) => n.id === notice?.id)
      if (shown) shown.statusDetails = details(health)
      return
    }
    clear()
    const id = store.addSystemNotification({
      severity,
      title: t(`nav.backendHealth.${state}Title`),
      message: t(`nav.backendHealth.${state}Message`),
      statusDetails: details(health),
    })
    notice = { id, state }
  }

  return { onHealthChange, clear }
}
