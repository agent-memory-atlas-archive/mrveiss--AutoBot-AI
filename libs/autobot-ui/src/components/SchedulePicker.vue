<!--
  Copyright 2025-2026 mrveiss
  SPDX-License-Identifier: Apache-2.0

  SchedulePicker — the ONE human, clickable schedule control (#18432).
  Edits a PickerRule and emits a ScheduleSpec (cron / calendar / once, picked
  automatically); lists show the derived summary, never cron. The kit holds no
  strings: the app passes `translate` (keys are schedule.*, see
  schedule/summary.ts) and `locale` (weekday/date names come from Intl).
  `preview` is the app's server call (source of truth); without it, or when it
  resolves null, the picker falls back to its advisory client preview.
  Token-only styling.
-->
<script setup lang="ts">
import { computed, useId, watch } from 'vue'
import { summaryText, weekdayName } from '../schedule/summary'
import { listZones } from '../schedule/zone'
import { joinTime, splitTime } from '../schedule/time'
import { MINUTE_STEPS, WEEKDAYS, type PickerRule, type RepeatKind, type SchedulePreviewFn, type ScheduleSpec, type ScheduleTranslate, type Weekday } from '../schedule/types'
import { useScheduleExpert } from '../composables/useScheduleExpert'
import { useSchedulePreview, type PreviewError } from '../composables/useSchedulePreview'
import { useScheduleState } from '../composables/useScheduleState'
import ScheduleTimeField from './ScheduleTimeField.vue'

const props = withDefaults(
  defineProps<{
    modelValue: ScheduleSpec | null
    /** App translator — the kit owns keys, never strings. */
    translate: ScheduleTranslate
    locale?: string
    /** Zone preselected for a new schedule. */
    defaultTimezone?: string
    /** The company's zone, when known: labelled "company time" in summaries. */
    companyTimezone?: string
    preview?: SchedulePreviewFn
    debounceMs?: number
  }>(),
  { locale: 'en', defaultTimezone: 'UTC', debounceMs: 250 },
)
const emit = defineEmits<{
  'update:modelValue': [spec: ScheduleSpec]
  'update:valid': [valid: boolean]
}>()

const uid = useId()
const t = (key: string, params?: Record<string, unknown>) => props.translate(key, params)
const { rule, timezone, spec, choose, accept } = useScheduleState(
  () => props.modelValue, () => props.defaultTimezone, (s) => emit('update:modelValue', s),
)
const pv = useSchedulePreview(spec, () => props.preview, () => props.debounceMs)
const expert = useScheduleExpert(() => timezone.value, () => props.preview, accept)

const REPEATS: RepeatKind[] = ['minutes', 'hourly', 'daily', 'weekly', 'monthly', 'once']
const NTHS = [1, 2, 3, 4, -1] as const
const HOURS = Array.from({ length: 24 }, (_, i) => i)
const DAYS_OF_MONTH = Array.from({ length: 31 }, (_, i) => i + 1)
const zones = computed(() => listZones(props.defaultTimezone, props.companyTimezone ?? '', timezone.value))
const pick = <R extends RepeatKind>(r: R) => computed(() => (rule.value?.repeat === r ? (rule.value as Extract<PickerRule, { repeat: R }>) : null))
const minutes = pick('minutes')
const hourly = pick('hourly')
const daily = pick('daily')
const weekly = pick('weekly')
const monthly = pick('monthly')
const once = pick('once')
const nthKey = (n: number) => (n === -1 ? 'last' : String(n))

const zoneLabel = computed(() =>
  timezone.value === props.companyTimezone ? t('schedule.zone.company') : timezone.value)
const summary = computed(() => summaryText(rule.value, zoneLabel.value, t, props.locale))
const savedText = computed(() => {
  const s = spec.value
  return s ? (s.kind === 'cron' ? s.cron : s.kind === 'calendar' ? s.rrule : s.at) : ''
})
const error = computed<PreviewError | null>(() => expert.error.value ?? pv.error.value)
const valid = computed(() => spec.value !== null && error.value === null)
watch(valid, (v) => emit('update:valid', v), { immediate: true })

const KNOWN_CODES = ['schedule.cron_extension_unsupported', 'schedule.invalid_timezone', 'schedule.once_in_past', 'schedule.invalid_rrule', 'schedule.invalid_cron']
const errorText = computed(() => {
  const e = error.value
  if (!e) return ''
  return t(KNOWN_CODES.includes(e.code) ? e.code : 'schedule.invalid_generic', e.params)
})

const dayName = (d: Weekday, style: 'long' | 'short') => weekdayName(d, props.locale, style)
const fmtRun = (d: Date) =>
  new Intl.DateTimeFormat(props.locale, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: timezone.value }).format(d)

const windowOn = computed({
  get: () => !!minutes.value?.window,
  set: (on: boolean) => { if (minutes.value) minutes.value.window = on ? { from: '08:00', to: '18:00' } : undefined },
})
function hourOf(key: 'from' | 'to'): number {
  const w = minutes.value?.window
  return w ? ((splitTime(w[key]) ?? [0])[0] || (key === 'to' ? 24 : 0)) : 0
}
function setHour(key: 'from' | 'to', hour: number): void {
  const w = minutes.value?.window
  if (w) w[key] = joinTime(hour, 0)
}
function toggleDay(list: Weekday[] | undefined, day: Weekday, keepOne: boolean): Weekday[] {
  const has = (list ?? []).includes(day)
  if (has && keepOne && (list ?? []).length === 1) return list ?? []
  return has ? (list ?? []).filter((d) => d !== day) : [...(list ?? []), day]
}
function onMinuteDay(day: Weekday): void {
  if (minutes.value) minutes.value.days = toggleDay(minutes.value.days, day, false)
}
function onWeekDay(day: Weekday): void {
  if (weekly.value) weekly.value.days = toggleDay(weekly.value.days, day, true)
}
type MonthlyMode = 'day' | 'lastDay' | 'nth'
const monthlyMode = computed<MonthlyMode>({
  get: () => (!monthly.value ? 'day' : 'lastDay' in monthly.value.on ? 'lastDay' : 'nth' in monthly.value.on ? 'nth' : 'day'),
  set: (mode) => {
    if (!monthly.value) return
    monthly.value.on = mode === 'day' ? { day: 1 } : mode === 'lastDay' ? { lastDay: true } : { nth: 1, weekday: 'MO' }
  },
})
const onDay = computed(() => (monthly.value && 'day' in monthly.value.on ? monthly.value.on : null))
const onNth = computed(() => (monthly.value && 'nth' in monthly.value.on ? monthly.value.on : null))

const previewNote = computed(() => {
  if (pv.loading.value) return t('schedule.previewLoading')
  if (pv.source.value === 'advisory') return t('schedule.previewApprox')
  if (pv.runs.value.length === 0 && pv.source.value === 'none' && !error.value) return t('schedule.previewUnavailable')
  return ''
})
</script>

<template>
  <fieldset class="aui-sched">
    <legend class="aui-sched__legend">{{ t('schedule.title') }}</legend>

    <div class="aui-sched__row">
      <span :id="`${uid}-rep`" class="aui-sched__label">{{ t('schedule.repeats') }}</span>
      <div class="aui-seg" role="radiogroup" :aria-labelledby="`${uid}-rep`">
        <label v-for="r in REPEATS" :key="r" class="aui-seg__item" :class="{ 'is-on': rule?.repeat === r }">
          <input class="aui-seg__input" type="radio" :name="`${uid}-repeat`" :value="r" :checked="rule?.repeat === r" @change="choose(r)" />
          <span>{{ t(`schedule.repeat.${r}`) }}</span>
        </label>
      </div>
    </div>

    <p v-if="!rule" class="aui-sched__note" role="status">
      <strong>{{ t('schedule.summary.custom') }}</strong> {{ t('schedule.customNote') }}
      <button type="button" class="aui-link" @click="expert.startEditing(savedText)">{{ t('schedule.editCron') }}</button>
    </p>

    <template v-if="minutes">
      <div class="aui-sched__row">
        <label :for="`${uid}-every`">{{ t('schedule.every') }}</label>
        <select :id="`${uid}-every`" v-model.number="minutes.every" class="aui-field">
          <option v-for="n in MINUTE_STEPS" :key="n" :value="n">{{ n }}</option>
        </select>
        <span>{{ t('schedule.minutesUnit') }}</span>
      </div>
      <div class="aui-sched__row">
        <label class="aui-check"><input v-model="windowOn" type="checkbox" />{{ t('schedule.onlyBetween') }}</label>
        <template v-if="minutes.window">
          <label class="aui-sr-only" :for="`${uid}-from`">{{ t('schedule.windowFrom') }}</label>
          <select :id="`${uid}-from`" class="aui-field" :value="hourOf('from')" @change="setHour('from', Number(($event.target as HTMLSelectElement).value))">
            <option v-for="h in HOURS" :key="h" :value="h">{{ joinTime(h, 0) }}</option>
          </select>
          <span>{{ t('schedule.and') }}</span>
          <label class="aui-sr-only" :for="`${uid}-to`">{{ t('schedule.windowTo') }}</label>
          <select :id="`${uid}-to`" class="aui-field" :value="hourOf('to')" @change="setHour('to', Number(($event.target as HTMLSelectElement).value))">
            <option v-for="h in HOURS.slice(1).concat(24)" :key="h" :value="h">{{ joinTime(h % 24, 0) }}</option>
          </select>
        </template>
      </div>
      <fieldset class="aui-sched__group">
        <legend class="aui-sched__label">{{ t('schedule.onDays') }}</legend>
        <button v-for="d in WEEKDAYS" :key="d" type="button" class="aui-chip" :class="{ 'is-on': minutes.days?.includes(d) }"
          :aria-pressed="!!minutes.days?.includes(d)" :aria-label="dayName(d, 'long')" @click="onMinuteDay(d)">{{ dayName(d, 'short') }}</button>
        <span class="aui-sched__note">{{ t('schedule.daysHintAny') }}</span>
      </fieldset>
    </template>

    <div v-if="hourly" class="aui-sched__row">
      <label :for="`${uid}-hmin`">{{ t('schedule.atMinute') }}</label>
      <select :id="`${uid}-hmin`" v-model.number="hourly.minute" class="aui-field">
        <option v-for="m in 60" :key="m" :value="m - 1">{{ joinTime(0, m - 1).slice(3) }}</option>
      </select>
    </div>

    <div v-if="daily" class="aui-sched__row">
      <span class="aui-sched__label">{{ t('schedule.at') }}</span>
      <ScheduleTimeField v-model="daily.time" :id-prefix="`${uid}-d`" :hour-label="t('schedule.hour')" :minute-label="t('schedule.minute')" />
      <label class="aui-check"><input v-model="daily.weekdaysOnly" type="checkbox" />{{ t('schedule.weekdaysOnly') }}</label>
    </div>

    <template v-if="weekly">
      <fieldset class="aui-sched__group">
        <legend class="aui-sched__label">{{ t('schedule.onDaysRequired') }}</legend>
        <button v-for="d in WEEKDAYS" :key="d" type="button" class="aui-chip" :class="{ 'is-on': weekly.days.includes(d) }"
          :aria-pressed="weekly.days.includes(d)" :aria-label="dayName(d, 'long')" @click="onWeekDay(d)">{{ dayName(d, 'short') }}</button>
      </fieldset>
      <div class="aui-sched__row">
        <span class="aui-sched__label">{{ t('schedule.at') }}</span>
        <ScheduleTimeField v-model="weekly.time" :id-prefix="`${uid}-w`" :hour-label="t('schedule.hour')" :minute-label="t('schedule.minute')" />
      </div>
    </template>

    <template v-if="monthly">
      <div class="aui-sched__row">
        <span :id="`${uid}-mm`" class="aui-sched__label">{{ t('schedule.monthly.which') }}</span>
        <div class="aui-seg" role="radiogroup" :aria-labelledby="`${uid}-mm`">
          <label v-for="m in (['day', 'lastDay', 'nth'] as const)" :key="m" class="aui-seg__item" :class="{ 'is-on': monthlyMode === m }">
            <input class="aui-seg__input" type="radio" :name="`${uid}-mmode`" :value="m" :checked="monthlyMode === m" @change="monthlyMode = m" />
            <span>{{ t(`schedule.monthly.${m}`) }}</span>
          </label>
        </div>
      </div>
      <div v-if="onDay" class="aui-sched__row">
        <label :for="`${uid}-mday`">{{ t('schedule.monthly.onDay') }}</label>
        <select :id="`${uid}-mday`" v-model.number="onDay.day" class="aui-field">
          <option v-for="d in DAYS_OF_MONTH" :key="d" :value="d">{{ d }}</option>
        </select>
        <span v-if="onDay.day > 28" class="aui-sched__note">{{ t('schedule.shortMonths') }}</span>
      </div>
      <div v-if="onNth" class="aui-sched__row">
        <label :for="`${uid}-nth`">{{ t('schedule.monthly.onThe') }}</label>
        <select :id="`${uid}-nth`" v-model.number="onNth.nth" class="aui-field">
          <option v-for="n in NTHS" :key="n" :value="n">{{ t(`schedule.nth.${nthKey(n)}`) }}</option>
        </select>
        <label class="aui-sr-only" :for="`${uid}-nthday`">{{ t('schedule.weekday') }}</label>
        <select :id="`${uid}-nthday`" v-model="onNth.weekday" class="aui-field">
          <option v-for="d in WEEKDAYS" :key="d" :value="d">{{ dayName(d, 'long') }}</option>
        </select>
      </div>
      <div class="aui-sched__row">
        <span class="aui-sched__label">{{ t('schedule.at') }}</span>
        <ScheduleTimeField v-model="monthly.time" :id-prefix="`${uid}-m`" :hour-label="t('schedule.hour')" :minute-label="t('schedule.minute')" />
      </div>
    </template>

    <div v-if="once" class="aui-sched__row">
      <label :for="`${uid}-date`">{{ t('schedule.date') }}</label>
      <input :id="`${uid}-date`" v-model="once.date" type="date" class="aui-field" />
      <span class="aui-sched__label">{{ t('schedule.at') }}</span>
      <ScheduleTimeField v-model="once.time" :id-prefix="`${uid}-o`" :hour-label="t('schedule.hour')" :minute-label="t('schedule.minute')" />
    </div>

    <div class="aui-sched__row">
      <label :for="`${uid}-zone`">{{ t('schedule.timezone') }}</label>
      <select :id="`${uid}-zone`" v-model="timezone" class="aui-field">
        <option v-for="z in zones" :key="z" :value="z">{{ z }}</option>
      </select>
      <span v-if="timezone === companyTimezone" class="aui-sched__note">{{ t('schedule.zone.usesCompany') }}</span>
    </div>

    <p class="aui-sched__summary" role="status" aria-live="polite">{{ summary }}</p>

    <div class="aui-sched__runs">
      <span :id="`${uid}-runs`" class="aui-sched__label">{{ t('schedule.nextRuns') }}</span>
      <ol v-if="pv.runs.value.length" :aria-labelledby="`${uid}-runs`" class="aui-sched__list">
        <li v-for="r in pv.runs.value" :key="r.getTime()">{{ fmtRun(r) }}</li>
      </ol>
      <p v-else-if="spec && !error && pv.source.value !== 'none'" class="aui-sched__note">{{ t('schedule.noRuns') }}</p>
      <p v-if="previewNote" class="aui-sched__note">{{ previewNote }}</p>
    </div>

    <p v-if="error && !expert.open.value" class="aui-sched__error" role="alert">{{ errorText }}</p>

    <div class="aui-sched__adv">
      <button type="button" class="aui-link" :aria-expanded="expert.open.value" :aria-controls="`${uid}-adv`" @click="expert.open.value = !expert.open.value">{{ t('schedule.advanced') }}</button>
      <div v-if="expert.open.value" :id="`${uid}-adv`">
        <p v-if="spec" class="aui-sched__row"><span class="aui-tag">{{ t(`schedule.saved.${spec.kind}`) }}</span></p>
        <template v-if="expert.editing.value">
          <label class="aui-sr-only" :for="`${uid}-cron`">{{ t('schedule.cronLabel') }}</label>
          <input :id="`${uid}-cron`" class="aui-field aui-mono" type="text" autocomplete="off" spellcheck="false"
            :value="expert.text.value" :aria-invalid="!!expert.error.value" @input="expert.onInput(($event.target as HTMLInputElement).value)" />
          <p class="aui-sched__note">{{ t('schedule.cronHint') }}</p>
        </template>
        <input v-else class="aui-field aui-mono" type="text" readonly :value="savedText" :aria-label="t('schedule.savedAs')" />
        <p v-if="spec?.kind === 'once'" class="aui-sched__note">{{ t('schedule.savedNote.once') }}</p>
        <p v-if="error && expert.open.value" class="aui-sched__error" role="alert">{{ errorText }}</p>
        <button type="button" class="aui-link" @click="expert.editing.value ? expert.stopEditing() : expert.startEditing(spec?.kind === 'cron' ? spec.cron : '')">
          {{ expert.editing.value ? t('schedule.doneEditing') : t('schedule.editCron') }}
        </button>
      </div>
    </div>
  </fieldset>
</template>

<style scoped>
.aui-sched {
  display: flex;
  flex-direction: column;
  gap: var(--aui-space-3);
  margin: 0;
  padding: var(--aui-space-4);
  border: 1px solid var(--aui-color-border);
  border-radius: var(--aui-radius-lg);
  background: var(--aui-color-surface);
  color: var(--aui-color-text);
  font-family: var(--aui-font-sans);
  font-size: var(--aui-text-sm);
}
.aui-sched__legend,
.aui-sched__label {
  font-weight: var(--aui-font-weight-semibold);
}
.aui-sched__row,
.aui-sched__group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--aui-space-2);
  margin: 0;
  padding: 0;
  border: 0;
}
.aui-sched__note {
  margin: 0;
  color: var(--aui-color-text-muted);
  font-size: var(--aui-text-xs);
}
.aui-sched__error {
  margin: 0;
  color: var(--aui-color-error);
}
.aui-sched__summary {
  margin: 0;
  padding: var(--aui-space-3);
  border-radius: var(--aui-radius-md);
  background: var(--aui-color-surface-sunken);
  font-weight: var(--aui-font-weight-medium);
}
.aui-sched__list {
  margin: 0;
  padding-left: var(--aui-space-5);
  font-family: var(--aui-font-mono);
}
.aui-seg {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--aui-space-1);
}
.aui-seg__item {
  position: relative;
  padding: var(--aui-space-1) var(--aui-space-3);
  border: 1px solid var(--aui-color-border-strong);
  border-radius: var(--aui-radius-md);
  background: var(--aui-color-surface);
  cursor: pointer;
}
.aui-seg__item.is-on,
.aui-chip.is-on {
  border-color: var(--aui-color-primary);
  background: var(--aui-color-primary);
  color: var(--aui-color-primary-contrast);
}
.aui-seg__input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.aui-seg__item:focus-within,
.aui-chip:focus-visible,
.aui-field:focus-visible,
.aui-link:focus-visible {
  outline: var(--aui-focus-ring-width) solid var(--aui-color-focus-ring);
  outline-offset: 2px;
}
.aui-chip {
  min-width: 2.75rem;
  height: 2rem;
  padding: 0 var(--aui-space-2);
  border: 1px solid var(--aui-color-border-strong);
  border-radius: var(--aui-radius-full);
  background: var(--aui-color-surface);
  color: var(--aui-color-text);
  font: inherit;
  cursor: pointer;
}
.aui-field {
  padding: var(--aui-space-1) var(--aui-space-2);
  border: 1px solid var(--aui-color-border-strong);
  border-radius: var(--aui-radius-md);
  background: var(--aui-color-surface);
  color: var(--aui-color-text);
  font-family: var(--aui-font-sans);
  font-size: var(--aui-text-sm);
}
.aui-mono {
  font-family: var(--aui-font-mono);
}
.aui-check {
  display: inline-flex;
  align-items: center;
  gap: var(--aui-space-1);
}
.aui-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--aui-color-primary);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.aui-tag {
  padding: var(--aui-space-1) var(--aui-space-2);
  border-radius: var(--aui-radius-sm);
  background: var(--aui-color-surface-raised);
  border: 1px solid var(--aui-color-border);
  font-size: var(--aui-text-xs);
}
.aui-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}
</style>
