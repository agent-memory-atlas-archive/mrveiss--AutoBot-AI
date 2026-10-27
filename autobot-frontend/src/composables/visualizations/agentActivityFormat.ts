// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

/**
 * Display formatting for the Agent Activity Monitor (#17514): moved out of
 * AgentActivityVisualization.vue, which is at its size ceiling. Every visible word comes
 * from i18n; an unmeasured figure reads "not reported", never 0.
 */

import { formatUptime } from '@/utils/formatHelpers'
import type { Agent } from '@/composables/visualizations/useAgentActivityData'

type Translate = (key: string, params?: Record<string, unknown>) => string

const AGENT_ICONS: Record<string, string> = {
  orchestrator: '🎭',
  worker: '⚙️',
  monitor: '👁️',
  analyzer: '🔍',
  executor: '🚀',
}

export function agentActivityFormat(t: Translate) {
  const k = (key: string) => `visualizations.agentActivity.${key}`

  function successText(agent: Agent): string {
    if (agent.tasksCompleted == null || agent.successRate == null) return t(k('notReported'))
    return agent.tasksCompleted === 0 ? t(k('notApplicable')) : `${agent.successRate}%`
  }

  const getAgentIcon = (type: string): string => AGENT_ICONS[type] || '🤖'
  const formatAgentType = (type: string): string => t(k(`types.${type}`))

  /** Sub-minute uptimes keep the translated seconds text; the rest is the kit's. */
  function uptimeText(seconds: number): string {
    return seconds < 60 ? t(k('seconds'), { n: seconds }) : formatUptime(seconds)
  }

  function formatTimeAgo(timestamp: number): string {
    const seconds = Math.floor((Date.now() - timestamp) / 1000)
    if (seconds < 60) return t(k('secondsAgo'), { n: seconds })
    if (seconds < 3600) return t(k('minutesAgo'), { n: Math.floor(seconds / 60) })
    return t(k('hoursAgo'), { n: Math.floor(seconds / 3600) })
  }

  function truncate(text: string, maxLength: number): string {
    return text.length <= maxLength ? text : text.slice(0, maxLength - 1) + '…'
  }

  return { successText, getAgentIcon, formatAgentType, formatUptime: uptimeText, formatTimeAgo, truncate }
}
