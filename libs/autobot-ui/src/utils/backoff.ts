// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * @autobot/ui — the one exponential-backoff formula for both apps (#15402).
 *
 * Reconnects (WebSocket, live events, terminal) and request retries each
 * re-derived `base * 2^n`, some capped, some not, two with jitter added on
 * different sides of the cap. This is the single definition: `exponent` is
 * the power of two (callers pass `attempt` or `attempt - 1` as their counting
 * starts), jitter is added before the cap, so a delay never exceeds `max`.
 */

export interface BackoffOptions {
  /** Delay for exponent 0, in ms. */
  base: number
  /** Upper bound in ms, jitter included; omit for uncapped. */
  max?: number
  /** Add a uniform random 0..jitterMs-1 ms, so many clients do not retry in step. */
  jitterMs?: number
}

/** Request retries: 1s, 2s, 4s, then 5s -- the shape both apps' ApiClients and async loaders use. */
export const RETRY_BACKOFF: Readonly<Required<Omit<BackoffOptions, 'jitterMs'>>> = Object.freeze({ base: 1000, max: 5000 })

function randomBelow(limit: number): number {
  return crypto.getRandomValues(new Uint32Array(1))[0] % limit
}

const finiteOr = (value: number, fallback: number): number => (Number.isFinite(value) ? value : fallback)

/**
 * `min(base * 2^exponent + jitter, max)`. A negative or NaN exponent counts as 0, a NaN base as 0,
 * and a non-finite `max` as uncapped -- a bad config never yields a NaN delay.
 */
export function backoffDelay(exponent: number, { base, max = Infinity, jitterMs = 0 }: BackoffOptions): number {
  const raw = finiteOr(base, 0) * 2 ** Math.max(0, finiteOr(exponent, 0))
  const jitter = jitterMs > 0 ? randomBelow(jitterMs) : 0
  return Math.min(raw + jitter, Number.isNaN(max) ? Infinity : max)
}
