// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/** Pure dashboard layout transforms (#18242). */
import { describe, it, expect } from 'vitest'
import {
  HEIGHT_MAX,
  HEIGHT_MIN,
  clampHeight,
  fitId,
  fromLegacyDashboards,
  moveWidget,
  spanFromLegacyWidth,
  stepSpan,
  storedLayoutFrom,
  type DashboardWidget,
} from '../dashboardLayoutModel'

const w = (id: string): DashboardWidget => ({ id, type: 'system', title: id, span: 6, height: 320, props: {} })

describe('dashboardLayoutModel', () => {
  it.each([
    [1, 4],
    [2, 8],
    [3, 12],
    [0, 4],
    [9, 12],
  ])('maps a legacy 3-column width %i to span %i', (width, span) => {
    expect(spanFromLegacyWidth(width)).toBe(span)
  })

  it('steps the span through 4/6/8/12 and stops at either end', () => {
    expect(stepSpan(4, 1)).toBe(6)
    expect(stepSpan(8, 1)).toBe(12)
    expect(stepSpan(12, 1)).toBe(12)
    expect(stepSpan(4, -1)).toBe(4)
  })

  it('clamps the height into its bounds', () => {
    expect(clampHeight(10)).toBe(HEIGHT_MIN)
    expect(clampHeight(5000)).toBe(HEIGHT_MAX)
    expect(clampHeight(333.4)).toBe(333)
  })

  it('moves a widget without mutating the input, and clamps at the ends', () => {
    const list = [w('a'), w('b'), w('c')]
    expect(moveWidget(list, 0, 1).map((x) => x.id)).toEqual(['b', 'a', 'c'])
    expect(moveWidget(list, 2, 5).map((x) => x.id)).toEqual(['a', 'b', 'c'])
    expect(moveWidget(list, 2, -5).map((x) => x.id)).toEqual(['c', 'a', 'b'])
    expect(list.map((x) => x.id)).toEqual(['a', 'b', 'c'])
  })

  it('converts legacy localStorage dashboards, ordering widgets by row then column', () => {
    const layout = fromLegacyDashboards([
      {
        id: 'default',
        name: 'Main',
        widgets: [
          { id: 'w3', type: 'agents', y: 1, x: 0, width: 2, height: 400, props: { height: 400 } },
          { id: 'w2', type: 'heatmap', y: 0, x: 1, width: 2, height: 300 },
          { id: 'w1', type: 'system', y: 0, x: 0, width: 1, height: 50 },
        ],
      },
    ])
    const widgets = layout.dashboards[0].widgets
    expect(widgets.map((x) => x.id)).toEqual(['w1', 'w2', 'w3'])
    expect(widgets.map((x) => x.span)).toEqual([4, 8, 8])
    expect(widgets[0].height).toBe(HEIGHT_MIN)
    expect(widgets[2].props).toEqual({ height: 400 })
    expect(layout.activeId).toBe('default')
  })

  it('reads a stored layout from the response body', () => {
    const layout = { dashboards: [{ id: 'main', name: 'Main', widgets: [] }], activeId: 'main' }
    expect(storedLayoutFrom({ success: true, data: { page: 'home', layout } })).toEqual(layout)
  })

  it.each([
    ['no body', undefined],
    ['no data', { success: true }],
    ['dashboards not a list', { data: { layout: { dashboards: 'x' } } }],
    ['a dashboard without widgets', { data: { layout: { dashboards: [{ id: 'main' }] } } }],
  ])('reads %s as nothing stored', (_, body) => {
    expect(storedLayoutFrom(body)).toBeNull()
  })

  it('fits legacy names, titles and ids to the server limits so a load never 422s', () => {
    const long = 'x'.repeat(200)
    const layout = fromLegacyDashboards([
      { id: 'my dash!', name: '   ', widgets: [{ id: 'a b', type: 'system', title: long }, { id: 'a-b', type: 'system' }] },
      { id: 'my dash!', name: long, widgets: [] },
    ])
    const [first, second] = layout.dashboards
    expect(first.name).toBe('views.customDashboard.myDashboard')
    expect(second.name).toHaveLength(120)
    expect(first.widgets[0].title).toHaveLength(120)
    expect(first.widgets.map((w) => w.id)).toEqual(['a-b', 'a-b-2'])
    expect([first.id, second.id]).toEqual(['my-dash-', 'my-dash--2'])
  })

  it('fitId keeps a valid id and falls back when nothing usable is left', () => {
    expect(fitId('widget_1', 'f')).toBe('widget_1')
    expect(fitId('***', 'f')).toBe('f')
    expect(fitId(undefined, 'f')).toBe('f')
  })
})
