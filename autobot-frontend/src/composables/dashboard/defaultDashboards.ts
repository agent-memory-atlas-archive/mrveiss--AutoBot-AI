// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/** Home dashboard defaults and new-dashboard templates (#18242). Pure data, no Vue. */
import {
  spanFromLegacyWidth,
  type DashboardLayout,
  type DashboardWidget,
} from './dashboardLayoutModel'

export const DEFAULT_DASHBOARD_ID = 'default'

function widget(
  id: string,
  type: string,
  title: string,
  legacyWidth: number,
  height: number,
  props: Record<string, unknown> = {},
): DashboardWidget {
  return { id, type, title, span: spanFromLegacyWidth(legacyWidth), height, props }
}

export function defaultHomeWidgets(): DashboardWidget[] {
  return [
    widget('widget-1', 'system', 'System Monitor', 1, 350),
    widget('widget-2', 'heatmap', 'Resource Heatmap', 2, 300, { height: 300 }),
    widget('widget-3', 'agents', 'Agent Activity', 2, 400, { height: 400 }),
  ]
}

export function defaultHomeLayout(): DashboardLayout {
  const now = Date.now()
  return {
    dashboards: [
      { id: DEFAULT_DASHBOARD_ID, name: 'Main Dashboard', widgets: defaultHomeWidgets(), createdAt: now, updatedAt: now },
    ],
    activeId: DEFAULT_DASHBOARD_ID,
  }
}

export function templateWidgets(template: string): DashboardWidget[] {
  switch (template) {
    case 'monitoring':
      return [
        widget('w1', 'system', 'System Monitor', 1, 350),
        widget('w2', 'heatmap', 'CPU Heatmap', 2, 300, { height: 300, metric: 'cpu' }),
      ]
    case 'agents':
      return [widget('w1', 'agents', 'Agent Activity', 3, 450, { height: 450 })]
    case 'workflows':
      return [widget('w1', 'workflow', 'Workflow View', 3, 500, { height: 500 })]
    default:
      return []
  }
}
