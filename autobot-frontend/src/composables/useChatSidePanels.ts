// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss
/**
 * Chat right-hand panels (#18243): Session files, Voice chat and Collaboration
 * share one slot, so at most one is open. The state is a single `active` value
 * rather than three booleans kept exclusive by hand, which makes "two open at
 * once" unrepresentable.
 *
 * Focus: a panel's header button gets focus back when its panel closes, but
 * only if focus was stranded on <body> -- never pulled from wherever the user
 * moved on to (switching session closes every panel).
 */
import { computed, nextTick, ref, watch, type Ref, type WritableComputedRef } from 'vue'

export type ChatSidePanel = 'files' | 'voice' | 'collaboration'

export const FILE_PANEL_ID = 'chat-panel-files'

export interface UseChatSidePanels {
  active: Ref<ChatSidePanel | null>
  showFilePanel: WritableComputedRef<boolean>
  showVoicePanel: WritableComputedRef<boolean>
  showCollaborationPanel: WritableComputedRef<boolean>
  fileBtnRef: Ref<HTMLElement | null>
  voiceBtnRef: Ref<HTMLElement | null>
  toggle: (panel: ChatSidePanel) => void
  closeAll: () => void
}

export function useChatSidePanels(): UseChatSidePanels {
  const active = ref<ChatSidePanel | null>(null)
  const fileBtnRef = ref<HTMLElement | null>(null)
  const voiceBtnRef = ref<HTMLElement | null>(null)

  const flag = (panel: ChatSidePanel) =>
    computed({
      get: () => active.value === panel,
      set: (open: boolean) => {
        if (open) active.value = panel
        else if (active.value === panel) active.value = null
      },
    })

  const toggle = (panel: ChatSidePanel): void => {
    active.value = active.value === panel ? null : panel
  }
  const closeAll = (): void => {
    active.value = null
  }

  const restoreFocus = (el: HTMLElement | null): void => {
    const current = document.activeElement
    if (!current || current === document.body) el?.focus()
  }

  watch(active, async (now, before) => {
    await nextTick()
    if (now === 'files') document.getElementById(FILE_PANEL_ID)?.focus()
    else if (now === null && before === 'files') restoreFocus(fileBtnRef.value)
    else if (now === null && before === 'voice') restoreFocus(voiceBtnRef.value)
  })

  return {
    active,
    showFilePanel: flag('files'),
    showVoicePanel: flag('voice'),
    showCollaborationPanel: flag('collaboration'),
    fileBtnRef,
    voiceBtnRef,
    toggle,
    closeAll,
  }
}
