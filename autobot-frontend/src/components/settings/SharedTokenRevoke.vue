<template>
  <div class="shared-token-revoke" data-testid="shared-token-revoke">
    <h5 class="shared-heading">{{ t('settings.providerAuth.shared.heading') }}</h5>
    <p class="shared-desc">{{ t('settings.providerAuth.shared.description') }}</p>
    <div class="shared-actions">
      <BaseButton
        v-for="p in providers"
        :key="p.name"
        variant="error"
        size="sm"
        :loading="busy"
        :data-testid="`shared-revoke-${p.name}`"
        @click="revokeShared(p)"
      >
        {{ t('settings.providerAuth.shared.revokeButton', { provider: p.label }) }}
      </BaseButton>
    </div>
    <p v-if="message" :class="['shared-result', { warning: isWarning }]" role="status">{{ message }}</p>
  </div>
</template>

<script setup lang="ts">
// #19241 (owner decision 2026-10-23): superadmin-only revoke of the legacy LLM
// sign-in token every org may share. The backend gates it with
// require_role(Role.SUPERADMIN); this component is only rendered for superadmins.
import { BaseButton } from '@autobot/ui'
import { useI18n } from 'vue-i18n'
import { useSharedTokenRevoke } from '@/composables/useSharedTokenRevoke'
import type { ProviderInfo } from '@/utils/providerOAuth'

defineProps<{ providers: ProviderInfo[] }>()

const { t } = useI18n()
const { busy, message, isWarning, revokeShared } = useSharedTokenRevoke()
</script>

<style scoped>
.shared-token-revoke { border-top: 1px solid var(--border-default); padding-top: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem; }
.shared-heading { font-size: 0.85rem; font-weight: 600; color: var(--text-primary); }
.shared-desc { font-size: 0.8rem; color: var(--text-muted); }
.shared-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.shared-result { font-size: 0.8rem; color: var(--text-secondary); }
.shared-result.warning { color: var(--color-error); }
</style>
