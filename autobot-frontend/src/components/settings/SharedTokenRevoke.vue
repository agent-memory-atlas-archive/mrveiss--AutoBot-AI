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
import { ref } from 'vue'
import { BaseButton } from '@autobot/ui'
import { useI18n } from 'vue-i18n'
import ApiClient from '@/utils/ApiClient'
import { createLogger } from '@/utils/debugUtils'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import type { ProviderInfo } from '@/utils/providerOAuth'

defineProps<{ providers: ProviderInfo[] }>()

interface RevokeResult {
  provider_revoked?: boolean | null
  provider_error?: string | null
}

const logger = createLogger('SharedTokenRevoke')
const { t } = useI18n()
const { confirm } = useConfirmDialog()
const busy = ref(false)
const message = ref('')
const isWarning = ref(false)

function describe(p: ProviderInfo, result: RevokeResult): void {
  isWarning.value = result.provider_revoked === false
  message.value = isWarning.value
    ? t('settings.providerAuth.providerRevokeFailed', { reason: result.provider_error ?? '' })
    : t('settings.providerAuth.shared.done', { provider: p.label })
}

async function revokeShared(p: ProviderInfo): Promise<void> {
  const ok = await confirm({
    title: t('settings.providerAuth.shared.confirmTitle', { provider: p.label }),
    message: t('settings.providerAuth.shared.confirmMessage', { provider: p.label }),
  })
  if (!ok) return
  busy.value = true
  try {
    describe(p, await ApiClient.delete<RevokeResult>(`/api/llm-auth/${p.name}/shared`))
  } catch (err: unknown) {
    logger.error('Shared token revoke failed', err)
    isWarning.value = true
    message.value = (err as Error)?.message || t('settings.providerAuth.disconnectFailed')
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.shared-token-revoke { border-top: 1px solid var(--border-default); padding-top: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem; }
.shared-heading { font-size: 0.85rem; font-weight: 600; color: var(--text-primary); }
.shared-desc { font-size: 0.8rem; color: var(--text-muted); }
.shared-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.shared-result { font-size: 0.8rem; color: var(--text-secondary); }
.shared-result.warning { color: var(--color-error); }
</style>
