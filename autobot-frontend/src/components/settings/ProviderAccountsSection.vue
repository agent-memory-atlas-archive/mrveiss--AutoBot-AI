<template>
  <div class="provider-accounts">
    <p v-if="loadError" class="error-msg" role="alert">{{ loadError }}</p>
    <template v-else>
      <div v-if="isAdmin" class="provider-scope-section" data-testid="provider-section-system">
        <h4 class="provider-scope-heading">{{ t('settings.providerAuth.systemHeading') }}</h4>
        <p class="provider-scope-desc">{{ t('settings.providerAuth.systemDesc') }}</p>
        <div class="provider-auth-list">
          <ProviderOAuthConnect
            v-for="p in providers"
            :key="`system-${p.name}`"
            scope="system"
            :provider-name="p.name"
            :provider-label="p.label"
            :flows="p.flows"
            :configured="p.configured"
            :missing-settings="p.missing_settings"
          >
            <template #apikey>
              <p class="apikey-hint">{{ t('settings.providerAuth.apiKeyHint') }}</p>
            </template>
          </ProviderOAuthConnect>
        </div>
        <SharedTokenRevoke v-if="isSuperadmin" :providers="providers" />
      </div>

      <div class="provider-scope-section" data-testid="provider-section-user">
        <h4 class="provider-scope-heading">{{ t('settings.providerAuth.userHeading') }}</h4>
        <p class="provider-scope-desc">{{ t('settings.providerAuth.userDesc') }}</p>
        <div class="provider-auth-list">
          <ProviderOAuthConnect
            v-for="p in providers"
            :key="`user-${p.name}`"
            scope="user"
            :provider-name="p.name"
            :provider-label="p.label"
            :flows="p.flows"
            :configured="p.configured"
            :missing-settings="p.missing_settings"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import ApiClient from '@/utils/ApiClient'
import { createLogger } from '@/utils/debugUtils'
import ProviderOAuthConnect from '@/components/settings/ProviderOAuthConnect.vue'
import SharedTokenRevoke from '@/components/settings/SharedTokenRevoke.vue'
import { isSuperadminRole } from '@/constants/roles'
import { useUserStore } from '@/stores/useUserStore'
import type { ProviderInfo } from '@/utils/providerOAuth'

defineProps<{ isAdmin: boolean }>()

const logger = createLogger('ProviderAccountsSection')
const { t } = useI18n()
const providers = ref<ProviderInfo[]>([])
// #19241: the shared-token revoke is superadmin-only (backend: require_role(Role.SUPERADMIN)).
const userStore = useUserStore()
const isSuperadmin = computed(() => isSuperadminRole(userStore.currentUser?.role))
const loadError = ref('')

onMounted(async () => {
  try {
    providers.value = await ApiClient.get<ProviderInfo[]>('/api/llm-auth/providers')
  } catch (err: unknown) {
    logger.error('Failed to load provider list', err)
    loadError.value = t('settings.providerAuth.loadFailed')
  }
})
</script>

<style scoped>
.provider-accounts { display: flex; flex-direction: column; gap: 1.25rem; }
.provider-scope-heading { font-size: 0.95rem; font-weight: 600; color: var(--text-primary); }
.provider-scope-desc { font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; }
.provider-auth-list { display: flex; flex-direction: column; gap: 1rem; }
.error-msg { color: var(--color-error); font-size: 0.85rem; }
</style>
