// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * Server-persisted dashboard layout per user and page (#18242).
 * GET/PUT/DELETE /users/me/dashboard-layouts/{page}. A failed save keeps the local
 * state and sets `saveError`; the grid shows it, so nothing fails silently.
 */
import { ref } from 'vue'
import apiClient from '@/utils/ApiClient'
import { getApiBase } from '@/config/ssot-config'
import { createLogger } from '@/utils/debugUtils'
import { fromLegacyDashboards, storedLayoutFrom, type DashboardLayout } from './dashboardLayoutModel'

export type DashboardPage = 'home' | 'company-os'

const LEGACY_KEY = 'autobot_dashboards'
const logger = createLogger('useDashboardLayout')

export function useDashboardLayout(page: DashboardPage, defaults: () => DashboardLayout) {
  const layout = ref<DashboardLayout>(defaults())
  const loaded = ref(false)
  const saveError = ref<string | null>(null)
  const url = `${getApiBase()}/users/me/dashboard-layouts/${page}`

  async function put(next: DashboardLayout): Promise<boolean> {
    try {
      await apiClient.put(url, next)
      saveError.value = null
      return true
    } catch (e) {
      saveError.value = e instanceof Error ? e.message : String(e)
      logger.error('Saving dashboard layout failed:', e)
      return false
    }
  }

  function readLegacy(): DashboardLayout | null {
    if (page !== 'home') return null
    const raw = localStorage.getItem(LEGACY_KEY)
    if (!raw) return null
    try {
      return fromLegacyDashboards(JSON.parse(raw))
    } catch (e) {
      logger.error('Legacy dashboards unreadable; left in place:', e)
      return null
    }
  }

  async function migrateLegacy(): Promise<void> {
    const legacy = readLegacy()
    if (!legacy?.dashboards.length) return
    layout.value = legacy
    if (await put(legacy)) localStorage.removeItem(LEGACY_KEY)
  }

  async function load(): Promise<void> {
    const stored = storedLayoutFrom(await apiClient.get(url))
    if (stored?.dashboards.length) layout.value = stored
    else await migrateLegacy()
    loaded.value = true
  }

  /** Saves explicitly (the view's Save button); resolves false and sets saveError on failure. */
  async function save(next: DashboardLayout): Promise<boolean> {
    layout.value = next
    return put(next)
  }

  async function reset(): Promise<void> {
    await apiClient.delete(url)
    layout.value = defaults()
  }

  return { layout, loaded, saveError, load, save, reset }
}
