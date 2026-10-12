// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss
/** One right-hand chat panel at a time, with focus handed in and back (#18243). */
import { describe, it, expect, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { useChatSidePanels, FILE_PANEL_ID } from '../useChatSidePanels'

afterEach(() => {
  document.body.innerHTML = ''
})

describe('useChatSidePanels', () => {
  it('starts with every panel closed', () => {
    const p = useChatSidePanels()
    expect([p.showFilePanel.value, p.showVoicePanel.value, p.showCollaborationPanel.value]).toEqual([false, false, false])
  })

  it('opening a panel closes the one that was open', () => {
    const p = useChatSidePanels()
    p.toggle('files')
    p.toggle('voice')
    expect([p.showFilePanel.value, p.showVoicePanel.value, p.showCollaborationPanel.value]).toEqual([false, true, false])
    p.showCollaborationPanel.value = true
    expect(p.active.value).toBe('collaboration')
    expect(p.showVoicePanel.value).toBe(false)
  })

  it('toggling the open panel closes it', () => {
    const p = useChatSidePanels()
    p.toggle('voice')
    p.toggle('voice')
    expect(p.active.value).toBeNull()
  })

  it('closing a panel that is not the open one leaves the open one alone', () => {
    const p = useChatSidePanels()
    p.toggle('voice')
    p.showFilePanel.value = false
    expect(p.showVoicePanel.value).toBe(true)
  })

  it('closeAll closes whichever panel is open', () => {
    const p = useChatSidePanels()
    p.toggle('files')
    p.closeAll()
    expect(p.active.value).toBeNull()
  })

  it('focuses the files panel on open and its button on close', async () => {
    const p = useChatSidePanels()
    const panel = document.createElement('div')
    panel.id = FILE_PANEL_ID
    panel.tabIndex = -1
    const btn = document.createElement('button')
    document.body.append(panel, btn)
    p.fileBtnRef.value = btn

    p.toggle('files')
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(panel)

    panel.remove() // the panel unmounts and focus falls to <body>
    p.toggle('files')
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(btn)
  })

  it('does not steal focus the user already moved elsewhere', async () => {
    const p = useChatSidePanels()
    const btn = document.createElement('button')
    const other = document.createElement('input')
    document.body.append(btn, other)
    p.voiceBtnRef.value = btn
    p.toggle('voice')
    await nextTick()
    other.focus()
    p.toggle('voice')
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(other)
  })
})
