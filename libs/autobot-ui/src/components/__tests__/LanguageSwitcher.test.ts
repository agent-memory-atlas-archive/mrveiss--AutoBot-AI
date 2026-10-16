// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
//
// LanguageSwitcher (#18354): the one language picker for both apps. Pins the
// select and menu variants' contract -- an accessible name, endonym labels,
// update:modelValue only on a real change, and the menu closing on a choice,
// Escape or an outside click.

import { describe, it, expect, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import LanguageSwitcher from '../LanguageSwitcher.vue'
import { LOCALE_ENDONYMS, localeOptions } from '../../utils/locales'

const options = localeOptions(['en', 'de', 'he'])
let wrapper: VueWrapper | null = null

function mountSwitcher(variant: 'select' | 'menu' = 'select') {
  wrapper = mount(LanguageSwitcher, {
    props: { modelValue: 'en', options, label: 'Language', variant },
    attachTo: document.body,
  })
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('localeOptions', () => {
  it('labels each code by its endonym, and an unknown code by itself', () => {
    expect(localeOptions(['de', 'xx'])).toEqual([
      { code: 'de', label: 'Deutsch' },
      { code: 'xx', label: 'xx' },
    ])
    expect(Object.keys(LOCALE_ENDONYMS)).toHaveLength(11)
  })
})

describe('LanguageSwitcher select', () => {
  it('is a labelled select whose options carry endonyms and their own lang', () => {
    const w = mountSwitcher()
    const select = w.get('select')
    expect(w.get(`label[for="${select.attributes('id')}"]`).text()).toBe('Language')
    expect(w.findAll('option').map((o) => [o.text(), o.attributes('lang')])).toEqual([
      ['English', 'en'],
      ['Deutsch', 'de'],
      ['עברית', 'he'],
    ])
  })

  it('emits only when the language actually changes', async () => {
    const w = mountSwitcher()
    await w.get('select').setValue('en')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    await w.get('select').setValue('de')
    expect(w.emitted('update:modelValue')).toEqual([['de']])
  })
})

describe('LanguageSwitcher menu', () => {
  it('opens a listbox from a named button and closes it on a choice', async () => {
    const w = mountSwitcher('menu')
    const button = w.get('button')
    expect(button.attributes('aria-label')).toBe('Language')
    expect(button.attributes('aria-expanded')).toBe('false')

    await button.trigger('click')
    expect(button.attributes('aria-expanded')).toBe('true')
    const selected = w.findAll('[role="option"]').filter((o) => o.attributes('aria-selected') === 'true')
    expect(selected.map((o) => o.text())).toEqual(['English'])

    await w.findAll('[role="option"]')[1].trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([['de']])
    expect(w.find('[role="listbox"]').exists()).toBe(false)
  })

  it('closes on Escape and on a click outside', async () => {
    const w = mountSwitcher('menu')
    await w.get('button').trigger('click')
    await w.get('.aui-lang').trigger('keydown', { key: 'Escape' })
    expect(w.find('[role="listbox"]').exists()).toBe(false)

    await w.get('button').trigger('click')
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await w.vm.$nextTick()
    expect(w.find('[role="listbox"]').exists()).toBe(false)
  })
})
