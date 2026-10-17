<script setup lang="ts">
// AutoBot - AI-Powered Automation Platform
// Copyright (c) 2025 mrveiss
// Author: mrveiss

/**
 * DevSpeedupView - Developer productivity tools and code generation
 * Issue #902 - Developer Speedup Tools
 * Issue #1359: i18n string extraction
 */

import { ref, computed } from 'vue'
import { useDevSpeedup } from '@/composables/useDevSpeedup'
import { createLogger } from '@/utils/debugUtils'
import Icon from '@/components/ui/Icon.vue'

const logger = createLogger('DevSpeedupView')

const {
  searchResults,
  snippets,
  templates,
  refactorSuggestions,
  generatedTests,
  actionHistory,
  isLoading,
  error,
  quickSearch,
  generateSnippet,
  fetchTemplates,
  suggestRefactor,
  generateTests,
  optimizeCode,
  formatCode,
  lintFix,
} = useDevSpeedup({ autoFetch: true })

const activeTab = ref<'search' | 'snippets' | 'templates' | 'actions'>('templates')
const searchQuery = ref('')
const searchType = ref<'file' | 'code' | 'symbol' | undefined>(undefined)

const snippetDescription = ref('')
const snippetLanguage = ref('python')

const selectedCategory = ref<string | undefined>(undefined)

const codeInput = ref('')
const actionLanguage = ref('python')
const actionType = ref<'refactor' | 'optimize' | 'format' | 'lint' | 'test'>('refactor')

const supportedLanguages = ['python', 'javascript', 'typescript', 'java', 'go', 'rust', 'cpp']

async function handleSearch() {
  if (!searchQuery.value.trim()) return
  await quickSearch(searchQuery.value, searchType.value)
}

async function handleGenerateSnippet() {
  if (!snippetDescription.value.trim()) return
  const snippet = await generateSnippet(snippetDescription.value, snippetLanguage.value)
  if (snippet) {
    logger.debug('Snippet generated:', snippet.id)
    snippetDescription.value = ''
  }
}

async function handleFetchTemplates() {
  await fetchTemplates(selectedCategory.value)
}

async function handleCodeAction() {
  if (!codeInput.value.trim()) return

  switch (actionType.value) {
    case 'refactor':
      await suggestRefactor(codeInput.value, actionLanguage.value)
      break
    case 'optimize':
      const optimized = await optimizeCode(codeInput.value, actionLanguage.value)
      if (optimized) {
        alert(`Optimized code:\n\n${optimized}`)
      }
      break
    case 'format':
      const formatted = await formatCode(codeInput.value, actionLanguage.value)
      if (formatted) {
        codeInput.value = formatted
        logger.debug('Code formatted')
      }
      break
    case 'lint':
      const fixed = await lintFix(codeInput.value, actionLanguage.value)
      if (fixed) {
        codeInput.value = fixed
        logger.debug('Linting fixed')
      }
      break
    case 'test':
      await generateTests(codeInput.value, actionLanguage.value)
      break
  }
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text)
  logger.debug('Copied to clipboard')
}

const templateCategories = computed(() => {
  const cats = new Set<string>()
  templates.value.forEach(tmpl => cats.add(tmpl.category))
  return Array.from(cats)
})
</script>

<template>
  <div class="dev-speedup-view">
    <!-- Page Header -->
    <div class="page-header">
      <div class="page-header-content">
        <h2 class="page-title">{{ $t('views.devSpeedup.title') }}</h2>
        <p class="page-subtitle">{{ $t('views.devSpeedup.subtitle') }}</p>
      </div>
      <div v-if="actionHistory.length > 0" class="actions-summary">
        <div class="actions-label">{{ $t('views.devSpeedup.actionsToday') }}</div>
        <div class="actions-count">{{ actionHistory.length }}</div>
      </div>
    </div>

    <!-- Error Alert -->
    <div v-if="error" class="alert alert-error">
      <Icon name="exclamation-circle" />
      <div class="alert-content">
        <strong>{{ $t('views.devSpeedup.error') }}</strong>
        <p>{{ error }}</p>
      </div>
    </div>

    <!-- Tabs -->
    <nav class="tab-nav">
      <button @click="activeTab = 'templates'" :class="['tab-btn', { active: activeTab === 'templates' }]">
        <Icon name="file-code" /> {{ $t('views.devSpeedup.templatesTab', { count: templates.length }) }}
      </button>
      <!-- Quick search, snippet generation and code actions return with their routes (#18749). -->
    </nav>

    <!-- Tab Content -->
    <div class="tab-content">
      <!-- Templates Tab -->
      <div v-show="activeTab === 'templates'" class="tab-panel">
        <div class="card template-filter">
          <div class="card-body filter-row">
            <select v-model="selectedCategory" class="field-select filter-select">
              <option :value="undefined">{{ $t('views.devSpeedup.allCategories') }}</option>
              <option v-for="cat in templateCategories" :key="cat" :value="cat">{{ cat }}</option>
            </select>
            <button @click="handleFetchTemplates" :disabled="isLoading" class="btn-action-primary">{{ $t('views.devSpeedup.refresh') }}</button>
          </div>
        </div>

        <div v-if="templates.length > 0" class="templates-list">
          <div v-for="template in templates" :key="template.id" class="card">
            <div class="card-body">
              <div class="template-header">
                <div>
                  <h3 class="template-name">{{ template.name }}</h3>
                  <div class="template-tags">
                    <span class="badge badge-info">{{ template.language }}</span>
                    <span class="badge badge-purple">{{ template.category }}</span>
                  </div>
                </div>
                <button @click="copyToClipboard(template.template)" class="btn-copy">{{ $t('views.devSpeedup.copy') }}</button>
              </div>
              <p class="template-desc">{{ template.description }}</p>
              <p v-if="template.variables.length > 0" class="template-vars">
                {{ $t('views.devSpeedup.variables') }}: {{ template.variables.join(', ') }}
              </p>
              <pre class="code-block"><code>{{ template.template }}</code></pre>
            </div>
          </div>
        </div>

        <div v-else class="empty-state">
          <Icon name="file-code" />
          <p>{{ $t('views.devSpeedup.noTemplates') }}</p>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.dev-speedup-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary);
}

.actions-summary {
  text-align: right;
  flex-shrink: 0;
}

.actions-label {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.actions-count {
  font-size: var(--text-2xl);
  font-weight: var(--font-bold);
  color: var(--color-primary);
}

/* Alert */
.alert {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  margin: 0 var(--spacing-5);
  border-radius: var(--radius-md);
}

.alert-error {
  background: var(--color-error-bg);
  border: 1px solid var(--color-error-border);
  color: var(--color-error);
}

.alert-content p {
  margin: var(--spacing-1) 0 0;
  font-size: var(--text-sm);
}

/* Tab content */
.tab-content {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-5);
}

.tab-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-5);
}

/* Search */
.search-row {
  display: flex;
  gap: var(--spacing-3);
}

.search-input { flex: 1; }

/* Results */
.results-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}

.result-item {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  transition: background var(--duration-150) var(--ease-in-out);
}

.result-item:hover { background: var(--bg-secondary); }

.result-content { flex: 1; }

.result-path {
  font-size: var(--text-sm);
  font-weight: var(--font-medium);
  font-family: var(--font-mono);
  color: var(--text-primary);
  margin: var(--spacing-0);
}

.result-line {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: var(--spacing-1) 0 0;
}

.result-text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin: var(--spacing-2) 0 0;
}

.result-context {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  margin: var(--spacing-1) 0 0;
}

.result-score {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* Badge types */
.badge-file { background: var(--color-info-bg); color: var(--color-info); }
.badge-code { background: var(--color-success-bg); color: var(--color-success); }
.badge-symbol { background: rgba(147, 51, 234, 0.1); color: var(--color-purple); }
.badge-purple { background: rgba(147, 51, 234, 0.1); color: var(--color-purple); }

/* Snippets / Templates lists */
.snippets-list,
.templates-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.snippet-header,
.template-header,
.test-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--spacing-3);
}

.snippet-tags,
.template-tags {
  display: flex;
  gap: var(--spacing-2);
  flex-wrap: wrap;
}

.template-tags { margin-top: var(--spacing-1); }

.template-name {
  font-size: var(--text-lg);
  font-weight: var(--font-semibold);
  color: var(--text-primary);
  margin: var(--spacing-0);
}

.snippet-desc,
.template-desc,
.test-desc {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin: 0 0 var(--spacing-3);
}

.template-vars {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: 0 0 var(--spacing-3);
}

.snippet-date {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: var(--spacing-2) 0 0;
}

.btn-copy {
  background: none;
  border: none;
  color: var(--color-primary);
  cursor: pointer;
  font-size: var(--text-sm);
  padding: var(--spacing-1) var(--spacing-2);
  border-radius: var(--radius-sm);
  transition: background var(--duration-150) var(--ease-in-out);
}

.btn-copy:hover { background: var(--color-primary-bg); }

/* Code blocks */
.code-block {
  background: var(--code-bg);
  color: var(--code-text);
  padding: var(--spacing-4);
  border-radius: var(--radius-md);
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  margin: var(--spacing-0);
}

.code-sm { padding: var(--spacing-3); font-size: var(--text-xs); }
.code-textarea { font-family: var(--font-mono); font-size: var(--text-sm); resize: vertical; }

/* Form grid */
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-4);
  margin-bottom: var(--spacing-4);
}

.btn-full { width: 100%; }

/* Filter row */
.filter-row {
  display: flex;
  gap: var(--spacing-4);
  align-items: center;
}

.filter-select { flex: 1; }

/* Suggestions */
.suggestions-list,
.tests-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.suggestion-item,
.test-item {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: var(--spacing-4);
}

.suggestion-meta {
  display: flex;
  gap: var(--spacing-2);
  margin-bottom: var(--spacing-2);
}

.suggestion-desc {
  font-size: var(--text-sm);
  color: var(--text-primary);
  margin: 0 0 var(--spacing-3);
}

.diff-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-4);
}

.diff-label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: 0 0 var(--spacing-1);
}

.test-name {
  font-size: var(--text-sm);
  font-weight: var(--font-semibold);
  color: var(--text-primary);
  margin: var(--spacing-0);
}

/* Empty state */
.empty-state {
  text-align: center;
  padding: var(--spacing-12) var(--spacing-4);
  color: var(--text-secondary);
}

.empty-state svg {
  width: var(--text-3xl);
  height: var(--text-3xl);
  margin: 0 auto var(--spacing-3);
  display: block;
  color: var(--text-muted);
}

.empty-state p { margin: var(--spacing-0); font-size: var(--text-sm); }

@media (max-width: 768px) {
  .search-row { flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
  .diff-grid { grid-template-columns: 1fr; }
  .filter-row { flex-direction: column; }
}
</style>
