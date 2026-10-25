// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * Global Error Handler Plugin
 * Provides comprehensive error handling for the Vue application
 */

import type { App } from 'vue'
import rumAgent from '../utils/RumAgent'
import { createLogger } from '@/utils/debugUtils'
import { isChunkLoadError } from '@/utils/chunkLoadError'
import { isBenignBrowserNotice } from '@/utils/benignBrowserNotice'
import i18n from '@/i18n'
import { useAppStore } from '@/stores/useAppStore'

// Create scoped logger for errorHandler
const logger = createLogger('errorHandler')

// Telemetry endpoints (RUM) must be excluded from error tracking/notification.
// Otherwise a failing telemetry POST is itself tracked as an error, which POSTs
// again — an infinite feedback loop that floods the console with thousands of
// "/api/rum/event 502" errors (#10956).
const isTelemetryUrl = (url: unknown): boolean =>
  typeof url === 'string' && url.includes('/rum/')

// Connectivity failures (backend restart, brief network drop) reject one fetch
// per in-flight request. Collapse the console error / RUM event / toast to at
// most one per this window so a transient outage can't spam the console.
const NETWORK_ERROR_THROTTLE_MS = 10000

interface ErrorNotification {
  id: string
  message: string
  type: 'error' | 'warning' | 'info'
  timestamp: number
  stack?: string
  dismissible: boolean
}

class GlobalErrorHandler {
  private notifications: ErrorNotification[] = []
  private listeners: Set<(notifications: ErrorNotification[]) => void> = new Set()
  private maxNotifications = 5
  // Timestamp of the last surfaced connectivity failure — see NETWORK_ERROR_THROTTLE_MS.
  private lastNetworkErrorAt = 0

  constructor() {
    this.setupGlobalHandlers()
  }

  private setupGlobalHandlers() {
    // Catch unhandled promise rejections
    window.addEventListener('unhandledrejection', (event) => {
      logger.error('Unhandled promise rejection:', event.reason)
      if (!isBenignBrowserNotice(event.reason)) {
        this.notifyUser(event.reason)
      }

      this.addNotification({
        message: this.getUnhandledRejectionMessage(event.reason),
        type: 'error',
        dismissible: true,
        stack: event.reason?.stack
      })

      // Track with RUM
      rumAgent.trackError('unhandled_promise_rejection', {
        message: event.reason?.message || 'Unknown promise rejection',
        stack: event.reason?.stack,
        source: 'global_error_handler'
      })

      // Prevent the default browser error logging
      event.preventDefault()
    })

    // Catch JavaScript errors
    window.addEventListener('error', (event) => {
      // Benign browser notices (ResizeObserver loop, both wordings) fire during
      // normal responsive layout — never surface them as an Application Error.
      if (isBenignBrowserNotice(event.message, event.error)) {
        event.preventDefault()
        return
      }

      logger.error('Global JavaScript error:', event.error)
      this.notifyUser(event.error || event)

      this.addNotification({
        message: this.getJavaScriptErrorMessage(event.error, event.filename, event.lineno),
        type: 'error',
        dismissible: true,
        stack: event.error?.stack
      })

      // Track with RUM
      rumAgent.trackError('javascript_error', {
        message: event.message,
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
        stack: event.error?.stack,
        source: 'global_error_handler'
      })
    })

    // Catch network errors (fetch failures)
    this.interceptFetch()
  }

  /**
   * The user-visible channel: the app store's system notification (what the
   * notification centre renders). This plugin is the single owner of the window
   * `error` / `unhandledrejection` registration (#19458), so App.vue no longer
   * registers its own listeners and the benign filter above cannot be bypassed.
   */
  private notifyUser(error: unknown) {
    const appStore = useAppStore()
    if (typeof appStore?.addSystemNotification !== 'function') return
    const { t } = i18n.global
    const message = error instanceof Error ? error.message : (error as { message?: string } | null)?.message
    appStore.addSystemNotification({
      severity: 'error',
      title: t('nav.applicationError'),
      message: message || t('nav.unexpectedError')
    })
  }

  private interceptFetch() {
    const originalFetch = window.fetch

    window.fetch = async (...args) => {
      try {
        const response = await originalFetch(...args)

        if (!response.ok) {
          const requestUrl = typeof args[0] === 'string'
            ? args[0]
            : args[0] instanceof URL
              ? args[0].href
              : (args[0] as Request)?.url

          // Never track/log/notify failures of the telemetry endpoint itself —
          // doing so re-POSTs telemetry and forms an infinite loop (#10956).
          if (isTelemetryUrl(requestUrl)) {
            return response
          }

          // Track HTTP errors
          rumAgent.trackError('http_error', {
            status: response.status,
            statusText: response.statusText,
            url: requestUrl,
            source: 'fetch_interceptor'
          })

          // Log HTTP errors to console for debugging (especially 404s)
          if (response.status === 404) {
            logger.error(`[HTTP 404] Resource not found: ${requestUrl}`)
          } else if (response.status >= 400 && response.status < 500) {
            logger.warn(`[HTTP ${response.status}] Client error: ${requestUrl} - ${response.statusText}`)
          } else if (response.status >= 500) {
            logger.error(`[HTTP ${response.status}] Server error: ${requestUrl} - ${response.statusText}`)
          }

          if (response.status >= 500) {
            this.addNotification({
              message: `Server error (${response.status}). Please try again later.`,
              type: 'error',
              dismissible: true
            })
          } else if (response.status === 401) {
            this.addNotification({
              message: 'Authentication required. Please log in again.',
              type: 'warning',
              dismissible: true
            })
          } else if (response.status === 403) {
            this.addNotification({
              message: 'Access denied. You may not have permission for this action.',
              type: 'warning',
              dismissible: true
            })
          } else if (response.status === 404) {
            this.addNotification({
              message: 'Requested resource not found.',
              type: 'warning',
              dismissible: true
            })
          }
        }

        return response
      } catch (error) {
        const err = error as Error
        const requestUrl = typeof args[0] === 'string'
          ? args[0]
          : args[0] instanceof URL
            ? args[0].href
            : (args[0] as Request)?.url

        // AbortError fires legitimately when components abort in-flight requests
        // on unmount / rapid navigation. Fully benign: never log, track, or
        // notify — just re-throw so the caller's own handling runs.
        const isAbortError =
          (error instanceof DOMException && error.name === 'AbortError') ||
          err?.name === 'AbortError'
        // Telemetry failures must not generate more telemetry (#10956 loop).
        if (isAbortError || isTelemetryUrl(requestUrl)) {
          throw error
        }

        const isHealthCheck = typeof requestUrl === 'string' && requestUrl.includes('/health')
        // Collapse repeated connectivity failures (backend restart, network
        // drop) so a transient outage can't spam thousands of console
        // errors / RUM events / toasts (#10956).
        const now = Date.now()
        const throttled = now - this.lastNetworkErrorAt < NETWORK_ERROR_THROTTLE_MS
        this.lastNetworkErrorAt = now

        if (!isHealthCheck && !throttled) {
          logger.error('Fetch error:', error)
        }

        if (!throttled) {
          rumAgent.trackError('network_error', {
            message: err.message,
            stack: err.stack,
            url: requestUrl,
            source: 'fetch_interceptor'
          })
        }

        // Only show notification for non-health-check, non-throttled failures
        if (!isHealthCheck && !throttled) {
          this.addNotification({
            message: 'Network connection failed. Please check your internet connection.',
            type: 'error',
            dismissible: true,
            stack: err.stack
          })
        }

        throw error
      }
    }
  }

  private getUnhandledRejectionMessage(reason: unknown): string {
    const message = (reason as { message?: unknown })?.message
    if (typeof message === 'string') {
      // Check chunk failures first: a stale-deploy dynamic-import error also
      // contains "Failed to fetch", so this must precede that generic branch.
      if (isChunkLoadError(message)) {
        return 'Application update detected. Please reload the page.'
      }
      if (message.includes('Failed to fetch')) {
        return 'Connection failed. Please check your internet connection and try again.'
      }
      if (message.includes('NetworkError')) {
        return 'Network error occurred. Please try again.'
      }
    }

    return 'An unexpected error occurred. Please try refreshing the page.'
  }

  private getJavaScriptErrorMessage(error: Error, _filename?: string, _lineno?: number): string {
    if (error?.message) {
      if (isChunkLoadError(error)) {
        return 'Application files failed to load. Please reload the page.'
      }
      if (isBenignBrowserNotice(error)) {
        return 'Display refresh needed. This is usually harmless.'
      }
      if (error.message.includes('Cannot read properties')) {
        return 'Data loading issue. Please try refreshing the page.'
      }
    }

    return 'A JavaScript error occurred. Please try refreshing the page.'
  }

  private addNotification(notification: Omit<ErrorNotification, 'id' | 'timestamp'>) {
    // Defensive programming: ensure notifications array is initialized
    if (!Array.isArray(this.notifications)) {
      this.notifications = []
    }

    const id = `error_${crypto.randomUUID()}`
    const fullNotification: ErrorNotification = {
      ...notification,
      id,
      timestamp: Date.now()
    }

    // Prevent duplicate notifications
    const isDuplicate = this.notifications.some(n =>
      n.message === notification.message &&
      Date.now() - n.timestamp < 5000 // Within 5 seconds
    )

    if (!isDuplicate) {
      this.notifications.unshift(fullNotification)

      // Keep only the most recent notifications
      if (this.notifications.length > this.maxNotifications) {
        this.notifications = this.notifications.slice(0, this.maxNotifications)
      }

      this.notifyListeners()

      // Auto-dismiss info notifications after 5 seconds
      if (notification.type === 'info') {
        setTimeout(() => {
          this.dismissNotification(id)
        }, 5000)
      }
    }
  }

  public dismissNotification(id: string) {
    this.notifications = this.notifications.filter(n => n.id !== id)
    this.notifyListeners()
  }

  public clearAllNotifications() {
    this.notifications = []
    this.notifyListeners()
  }

  public subscribe(listener: (notifications: ErrorNotification[]) => void) {
    this.listeners.add(listener)
    // Immediately call with current notifications
    listener([...this.notifications])

    return () => {
      this.listeners.delete(listener)
    }
  }

  private notifyListeners() {
    this.listeners.forEach(listener => {
      listener([...this.notifications])
    })
  }

  public getNotifications(): ErrorNotification[] {
    return [...this.notifications]
  }

  public addManualNotification(message: string, type: ErrorNotification['type'] = 'info', dismissible = true) {
    this.addNotification({ message, type, dismissible })
  }
}

const globalErrorHandler = new GlobalErrorHandler()

function recoverFromChunkLoadError(error: Error, componentName: string) {
  logger.warn('Chunk loading error detected, initiating cache clear...')
  import('@/utils/cacheManagement')
    .then(({ handleChunkLoadingError }) => {
      handleChunkLoadingError(error, componentName)
    })
    .catch((cacheError) => {
      logger.error('Failed to handle chunk error:', cacheError)
      // Fallback to hard reload
      window.location.reload()
    })
}

// Plugin installation
export default {
  install(app: App) {
    // The ONE app.config.errorHandler (#19458). It chains: log -> RUM report ->
    // chunk-load recovery -> user notification. rum.ts and main.ts used to assign
    // their own and overwrite each other; neither assigns one any more.
    app.config.errorHandler = (error, instance, info) => {
      const err = error as Error
      logger.error('Vue Error:', { error, info })

      rumAgent.trackError('vue_error', {
        message: err?.message,
        stack: err?.stack,
        componentInfo: info,
        component: instance?.$options?.name || 'unknown'
      })

      if (isChunkLoadError(err)) {
        recoverFromChunkLoadError(err, instance?.$?.type?.name || 'Unknown')
      }

      globalErrorHandler.addManualNotification(
        'Component error occurred. Some features may not work correctly.',
        'error'
      )
    }

    // Add error handler to global properties
    app.config.globalProperties.$errorHandler = globalErrorHandler

    // Provide for composition API
    app.provide('errorHandler', globalErrorHandler)

    // Global Error Handler installed successfully
  }
}

export { globalErrorHandler }
export type { ErrorNotification }
