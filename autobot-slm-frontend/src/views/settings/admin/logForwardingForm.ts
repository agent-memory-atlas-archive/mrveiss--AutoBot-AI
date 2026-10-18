// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

/**
 * Form state and request bodies for the Log Forwarding page (#18734).
 * The list returns api_key/password MASKED, so credentials are never prefilled
 * and an update carries one only when the user typed a new value.
 */
import type {
  LogForwardingDestination,
  LogForwardingDestinationCreate,
  LogForwardingDestinationUpdate,
} from '@/composables/autobotAdminModels'

export function defaultForm() {
  return {
    name: '',
    type: 'syslog' as LogForwardingDestination['type'],
    enabled: true,
    url: '',
    syslog_protocol: 'udp',
    file_path: '',
    api_key: '',
    username: '',
    password: '',
    index: 'autobot-logs',
    min_level: 'Information',
    batch_size: 10,
    batch_timeout: 5,
  }
}

export type LogForwardingForm = ReturnType<typeof defaultForm>

/** Fields shared by create and update. Credentials are added by the callers. */
function buildCommonFields(form: LogForwardingForm) {
  return {
    enabled: form.enabled,
    url: form.url || null,
    username: form.username || null,
    index: form.index || null,
    file_path: form.file_path || null,
    syslog_protocol: form.syslog_protocol,
    min_level: form.min_level,
    batch_size: form.batch_size,
    batch_timeout: form.batch_timeout,
  }
}

export function buildCreateBody(form: LogForwardingForm): LogForwardingDestinationCreate {
  return {
    ...buildCommonFields(form),
    name: form.name,
    type: form.type,
    api_key: form.api_key || null,
    password: form.password || null,
    retry_count: 3,
    retry_delay: 1,
    scope: 'global',
  }
}

/** A credential is sent only when newly typed; a masked value must never round-trip. */
export function buildUpdateBody(form: LogForwardingForm): LogForwardingDestinationUpdate {
  const body: LogForwardingDestinationUpdate = buildCommonFields(form)
  if (form.api_key) body.api_key = form.api_key
  if (form.password) body.password = form.password
  return body
}
