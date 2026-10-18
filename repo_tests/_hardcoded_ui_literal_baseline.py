# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
r"""Hard-coded English UI text the frontends write instead of translating (#12732).

**The boundary.** An entry is a `.vue` file, mapped to how many hard-coded
English literals it contains, where:

* Files: `.vue` under `autobot-frontend/src` and `autobot-slm-frontend/src`,
  excluding `*.stories.*`, `__tests__/`, `*.spec.*`, `*.test.*`. Enumerated from
  the filesystem under the repo root.
* A literal is one of three kinds:
  * template-text: in the `<template>` section with HTML comments removed, text
    between tags matching `>\s*([A-Za-z][A-Za-z ,.!?'\u2019()-]{2,}?)\s*<`, kept
    if its stripped length is >= 3 and it contains two consecutive lowercase
    letters;
  * attribute: unbound `(title|placeholder|aria-label|label|alt)="([A-Za-z][^"{}]{2,})"`
    not preceded by `:`, `@`, a word character or `-`;
  * script: in `<script>`:
    ``(?:\.value\s*=|message:|title:|confirm\(|alert\(|Error\()\s*[`'"]([A-Z][a-z][^`'"]{3,})[`'"]``.

**Not seen:** text built by concatenation or interpolation, single-word
lowercase tokens, text containing characters outside the template-text class
(digits, `:`, `/`, `&`), `v-bind` string literals, `.ts` files, props defaults,
and any language other than English.

**Pinned per file, not as a total**, so a literal removed in one file cannot
hide a new one in another. Counts are exact: a removed literal leaves its entry
stale until it is lowered in the commit that did the work, and a file that
reaches 0 leaves the dict.

**Shrink-only.** Replace a literal with `t('...')` and an English key in
`en.json`; do not delete text to get under the check. English-only i18n is the
current ruling, so this counts literals not yet moved to `t()`.
"""

from __future__ import annotations

#: ``repo-relative .vue path -> hard-coded English literals not yet moved to t()``.
HARDCODED_UI_LITERALS: dict[str, int] = {
    "autobot-frontend/src/components/CommandPalette.vue": 1,
    "autobot-frontend/src/components/admin/NpuWorkerPairConfirmDialog.vue": 7,
    "autobot-frontend/src/components/agents/AgentTaskDetail.vue": 7,
    "autobot-frontend/src/components/agents/HeartbeatPanel.vue": 30,
    "autobot-frontend/src/components/analytics/AddSourceModal.vue": 2,
    "autobot-frontend/src/components/analytics/CodeEvolutionTimeline.vue": 2,
    "autobot-frontend/src/components/analytics/CodeGenerationDashboard.vue": 12,
    "autobot-frontend/src/components/analytics/CodeQualityDashboard.vue": 1,
    "autobot-frontend/src/components/analytics/CodebaseAnalytics.vue": 1,
    "autobot-frontend/src/components/analytics/ShareSourceModal.vue": 2,
    "autobot-frontend/src/components/analytics/UnwiredTrackerTile.vue": 2,
    "autobot-frontend/src/components/analytics/panels/CodebaseIntelligenceScoresPanel.vue": 5,
    "autobot-frontend/src/components/analytics/panels/CodebaseOwnershipPanel.vue": 2,
    "autobot-frontend/src/components/artifact-cells/ChartCell.vue": 4,
    "autobot-frontend/src/components/artifact-cells/CodeCell.vue": 2,
    "autobot-frontend/src/components/async/AsyncErrorFallback.vue": 4,
    "autobot-frontend/src/components/auth/LoginForm.vue": 1,
    "autobot-frontend/src/components/autoresearch/ApprovalCard.vue": 3,
    "autobot-frontend/src/components/autoresearch/ExperimentTimeline.vue": 1,
    "autobot-frontend/src/components/autoresearch/InsightsPanel.vue": 4,
    "autobot-frontend/src/components/autoresearch/PromptOptimizerPanel.vue": 8,
    "autobot-frontend/src/components/browser/BrowserSessionManager.vue": 1,
    "autobot-frontend/src/components/browser/InteractiveScreenshot.vue": 3,
    "autobot-frontend/src/components/canvas/CanvasCell.vue": 13,
    "autobot-frontend/src/components/canvas/CanvasCellToolbar.vue": 5,
    "autobot-frontend/src/components/canvas/CanvasConflictBanner.vue": 5,
    "autobot-frontend/src/components/canvas/CanvasEdgeStates.vue": 13,
    "autobot-frontend/src/components/canvas/CanvasExportSheet.vue": 8,
    "autobot-frontend/src/components/canvas/CanvasPanel.vue": 1,
    "autobot-frontend/src/components/canvas/CanvasSplitLayout.vue": 1,
    "autobot-frontend/src/components/canvas/CanvasToolPalette.vue": 9,
    "autobot-frontend/src/components/canvas/CodeCell.vue": 4,
    "autobot-frontend/src/components/charts/FunctionCallGraph.vue": 3,
    "autobot-frontend/src/components/chat/ChatBrowser.vue": 1,
    "autobot-frontend/src/components/chat/ChatInput.vue": 7,
    "autobot-frontend/src/components/chat/ChatInterface.vue": 3,
    "autobot-frontend/src/components/chat/ChatMessages.vue": 1,
    "autobot-frontend/src/components/chat/DeleteConversationDialog.vue": 2,
    "autobot-frontend/src/components/chat/MCPPromptTemplatePicker.vue": 5,
    "autobot-frontend/src/components/chat/McpPromptPicker.vue": 13,
    "autobot-frontend/src/components/chat/MessageItem.vue": 2,
    "autobot-frontend/src/components/chat/SlashCommandDropdown.vue": 1,
    "autobot-frontend/src/components/desktop/DesktopInterface.vue": 13,
    "autobot-frontend/src/components/desktop/PopoutChromiumBrowser.vue": 3,
    "autobot-frontend/src/components/documents/AIDocumentEditor.vue": 9,
    "autobot-frontend/src/components/examples/ThemingPatternExample.vue": 33,
    "autobot-frontend/src/components/file-browser/SandboxFileBrowser.vue": 8,
    "autobot-frontend/src/components/knowledge/ChromaDBExplorer.vue": 17,
    "autobot-frontend/src/components/knowledge/DeduplicationManager.vue": 7,
    "autobot-frontend/src/components/knowledge/KBSearchResultPanel.vue": 10,
    "autobot-frontend/src/components/knowledge/KnowledgeGraph.vue": 1,
    "autobot-frontend/src/components/knowledge/KnowledgeHealth.vue": 1,
    "autobot-frontend/src/components/knowledge/KnowledgeSystemDocs.vue": 1,
    "autobot-frontend/src/components/knowledge/KnowledgeUpload.vue": 3,
    "autobot-frontend/src/components/knowledge/McpResourceBrowser.vue": 7,
    "autobot-frontend/src/components/knowledge/SystemKnowledgeManager.vue": 19,
    "autobot-frontend/src/components/knowledge/WebResearchSettings.vue": 45,
    "autobot-frontend/src/components/knowledge/connectors/ConnectorConfigModal.vue": 11,
    "autobot-frontend/src/components/knowledge/connectors/ConnectorHistoryPanel.vue": 4,
    "autobot-frontend/src/components/knowledge/web-research/CrawlTab.vue": 1,
    "autobot-frontend/src/components/knowledge/web-research/ExtractTab.vue": 1,
    "autobot-frontend/src/components/knowledge/web-research/ScrapeTab.vue": 2,
    "autobot-frontend/src/components/knowledge/web-research/SiteMapTab.vue": 1,
    "autobot-frontend/src/components/layout/AppHeader.vue": 1,
    "autobot-frontend/src/components/llc/LlcBreadcrumb.vue": 1,
    "autobot-frontend/src/components/llc/TemplateBrowser.vue": 4,
    "autobot-frontend/src/components/manpage/ManPageManager.vue": 3,
    "autobot-frontend/src/components/plugins/CapabilityApprovalDialog.vue": 3,
    "autobot-frontend/src/components/plugins/MarketplaceSourcesModal.vue": 1,
    "autobot-frontend/src/components/plugins/PluginInstallModal.vue": 2,
    "autobot-frontend/src/components/settings/PushNotificationSettingsPanel.vue": 6,
    "autobot-frontend/src/components/settings/SettingsPanel.vue": 2,
    "autobot-frontend/src/components/settings/TelegramSettingsPanel.vue": 9,
    "autobot-frontend/src/components/settings/ThemePresetPicker.vue": 5,
    "autobot-frontend/src/components/terminal/SSHTerminal.vue": 3,
    "autobot-frontend/src/components/terminal/TerminalOutput.vue": 1,
    "autobot-frontend/src/components/terminal/TerminalWindow.vue": 2,
    "autobot-frontend/src/components/theme/EmberThemeToggle.vue": 3,
    "autobot-frontend/src/components/transcriber/AiAnalysisPanel.vue": 7,
    "autobot-frontend/src/components/transcriber/AsrProviderSelector.vue": 4,
    "autobot-frontend/src/components/transcriber/UploadModal.vue": 2,
    "autobot-frontend/src/components/ui/ConfirmDialog.vue": 2,
    "autobot-frontend/src/components/ui/PreferencesPanel.vue": 1,
    "autobot-frontend/src/components/visualizations/ServiceMessageTimeline.vue": 2,
    "autobot-frontend/src/components/workflow/NotificationConfigModal.vue": 4,
    "autobot-frontend/src/components/workflow/PhaseProgressionIndicator.vue": 1,
    "autobot-frontend/src/components/workflow/WorkflowNotificationConfig.vue": 2,
    "autobot-frontend/src/components/workflow/WorkflowTemplateGallery.vue": 1,
    "autobot-frontend/src/views/AdminMcpServersView.vue": 2,
    "autobot-frontend/src/views/AdminSandboxView.vue": 2,
    "autobot-frontend/src/views/AgentActivity.vue": 5,
    "autobot-frontend/src/views/BrowserAutomationView.vue": 2,
    "autobot-frontend/src/views/ComponentShowcaseView.vue": 2,
    "autobot-frontend/src/views/CustomDashboard.vue": 6,
    "autobot-frontend/src/views/DevSpeedupView.vue": 1,
    "autobot-frontend/src/views/ExperimentDashboard.vue": 6,
    "autobot-frontend/src/views/OnboardingWizard.vue": 22,
    "autobot-frontend/src/views/SettingsView.vue": 6,
    "autobot-frontend/src/views/SharedChatView.vue": 1,
    "autobot-frontend/src/views/UsageView.vue": 49,
    "autobot-frontend/src/views/slm/ThemeManagerView.vue": 3,
    "autobot-frontend/src/views/transcriber/ProjectDetailView.vue": 8,
    "autobot-frontend/src/views/transcriber/ProjectsView.vue": 13,
    "autobot-frontend/src/views/transcriber/TranscriptView.vue": 2,
    "autobot-frontend/src/views/voice/VoiceBundleInfo.vue": 2,
    "autobot-slm-frontend/src/components/DeploymentLogViewer.vue": 4,
    "autobot-slm-frontend/src/components/RoleManagementModal.vue": 1,
    "autobot-slm-frontend/src/components/agents/ProcessMonitorTab.vue": 1,
    "autobot-slm-frontend/src/components/fleet/AssignNPURoleModal.vue": 1,
    "autobot-slm-frontend/src/components/fleet/DecommissionModal.vue": 1,
    "autobot-slm-frontend/src/components/fleet/NPUDetailsPanel.vue": 2,
    "autobot-slm-frontend/src/components/fleet/NodeServicesPanel.vue": 1,
    "autobot-slm-frontend/src/components/monitoring/GrafanaDashboard.vue": 2,
    "autobot-slm-frontend/src/components/orchestration/RestartConfirmDialog.vue": 1,
    "autobot-slm-frontend/src/views/CodeSyncView.vue": 9,
    "autobot-slm-frontend/src/views/DeploymentsView.vue": 21,
    "autobot-slm-frontend/src/views/ExternalAgentsView.vue": 1,
    "autobot-slm-frontend/src/views/MaintenanceView.vue": 9,
    "autobot-slm-frontend/src/views/OrchestrationView.vue": 2,
    "autobot-slm-frontend/src/views/ReplicationView.vue": 2,
    "autobot-slm-frontend/src/views/SecurityView.vue": 22,
    "autobot-slm-frontend/src/views/SetupWizardView.vue": 3,
    "autobot-slm-frontend/src/views/SystemUpdatesTab.vue": 5,
    "autobot-slm-frontend/src/views/monitoring/AlertsMonitor.vue": 1,
    "autobot-slm-frontend/src/views/monitoring/ErrorMonitor.vue": 1,
    "autobot-slm-frontend/src/views/settings/APISettings.vue": 1,
    "autobot-slm-frontend/src/views/settings/BackendSettings.vue": 2,
    "autobot-slm-frontend/src/views/settings/DisposalPolicySettings.vue": 1,
    "autobot-slm-frontend/src/views/settings/GeneralSettings.vue": 3,
    "autobot-slm-frontend/src/views/settings/MonitoringSettings.vue": 1,
    "autobot-slm-frontend/src/views/settings/NotificationsSettings.vue": 2,
    "autobot-slm-frontend/src/views/settings/SecuritySettings.vue": 6,
    "autobot-slm-frontend/src/views/settings/admin/CacheSettings.vue": 5,
    "autobot-slm-frontend/src/views/settings/admin/ConfigDefaultsSettings.vue": 4,
    "autobot-slm-frontend/src/views/settings/admin/DataHygieneView.vue": 1,
    "autobot-slm-frontend/src/views/settings/admin/LLMSettings.vue": 2,
    "autobot-slm-frontend/src/views/settings/admin/LogForwardingSettings.vue": 4,
    "autobot-slm-frontend/src/views/settings/admin/NPUWorkersSettings.vue": 4,
    "autobot-slm-frontend/src/views/settings/admin/PromptsSettings.vue": 4,
    "autobot-slm-frontend/src/views/settings/admin/SSOSettings.vue": 2,
    "autobot-slm-frontend/src/views/settings/admin/UserManagementSettings.vue": 6,
    "autobot-slm-frontend/src/views/tools/admin/BatchTool.vue": 3,
    "autobot-slm-frontend/src/views/tools/admin/MCPTool.vue": 1,
    "autobot-slm-frontend/src/views/tools/admin/NoVNCTool.vue": 1,
    "autobot-slm-frontend/src/views/tools/admin/VisionTool.vue": 1,
    "autobot-slm-frontend/src/views/tools/admin/VoiceTool.vue": 4,
}
