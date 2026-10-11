# Audit and event-record systems (#17225)

Re-verified against `main` when D2/D3/D5/D6 landed. Counts are call sites in non-test Python, found
by grepping the **behaviour** (a store write onto an audit-shaped key, an audit ORM row, an
`INSERT INTO ...audit`, an append to an audit path), not only the symbol names. The guard
`repo_tests/audit_writer_single_source_17225_test.py` applies the same detector to the tree and
fails on a writer outside the pinned set.

The first inventory (eight stores plus one non-store writer) was incomplete: a behaviour sweep found
five more audit stores it had missed. They are rows 9-13 below and are **not** covered by the owner
decision of 2026-10-10.

## The canonical service

`autobot-backend/services/audit/`:

| Module | Role |
|---|---|
| `audit.py` | The one write path (`record`, `emit`, `emit_security`/`emit_compliance`/`emit_knowledge`, `audit_record`) and read path (`query`, `query_filtered`, `query_events`, `count_since`), plus `apply_retention`. Every producer reaches `record`, which also feeds threat detection (#13562). |
| `taxonomy.py` | `AuditEvent`, `AuditCategory`, `AuditFilter`, and the three legacy action vocabularies (`AuditAction`, `EventType`, `AuditEventType`). |
| `projection.py` | The only module that writes an `audit:` key: `audit:unified` plus `audit:category:<cat>`. Declares `system_of_record("audit_trail")`. |
| `retention.py` | The one retention policy table (D5). |
| `purge.py` | The one purge path, used by the nightly task and the admin cleanup endpoint. |
| `knowledge_view.py` | `KnowledgeAuditLog`: the knowledge audit API's views over KNOWLEDGE events. |

"Everything recorded for user U in window W" is one call: `query(actor_id=U, from_ts=..., to_ts=...)`.
`query_filtered` raises `AuditStoreUnavailable` when the store cannot be read, so an unreadable
trail is never reported as an empty one (#13258).

## Inventory

| # | Module | Store | Status | Writers | Retention | Tamper protection |
|---|---|---|---|---|---|---|
| 1 | `services/audit/` (canonical) | Redis `audit:unified` + `audit:category:<cat>` | **Canonical.** The system of record until D1 lands (declared as an inadequacy in `store_authority.py`). | All of rows 2, 3, 4, 7, 8 below, plus direct `emit`/`record` producers | `retention.py` table: category-key TTL and nightly purge | None: a Redis sorted set. D1 is the fix. |
| 2 | `services/audit/audit_log.py` | Redis `audit_log:<user>` / `audit_log:global` | **Retired (D2), module deleted.** `AuditAction` moved to `taxonomy.py`; `audit_record` is the canonical helper. | 0. Callers already used the canonical bridge: 14 `audit_record` sites in `api/chat_sessions.py` (3), `api/knowledge.py` (5), `api/secrets.py` (2), `services/device_jwt.py` (1), `services/run_jwt.py` (3) | Old keys expire by their 90-day TTL | n/a |
| 3 | `services/audit_logger.py` | Redis `audit` database (db 10): daily `audit:log:<date>` and five index zsets, JSONL fallback | **Adapter (D3).** Stores nothing; `AuditEntry` is recorded as a SECURITY `AuditEvent` and read back from #1. | `audit_log()`: 18 sites in 11 modules (`api/feature_flags.py` 2, `api/knowledge_source_liveness.py` 1, `api/mcp_external_servers.py` 1, `api/memory_privacy.py` 6, `api/scheduler_toggles.py` 2, `services/approval_execution.py` 1, `services/mcp_external_bridge.py` 1, `services/orphan_repair.py` 1, `services/orphan_storage_cleanup_action.py` 1, `services/realtime_mcp_bridge.py` 1, `scripts/migrate_shared_facts_index.py` 1); `get_audit_logger().log`: 4 (`middleware/audit_middleware.py` 3, `services/gateway/egress_governor.py` 1). Reader: `api/audit.py` (6 endpoints). | db-10 keys expire by the TTL they were written with | n/a |
| 4 | `security_layer.py` `SecurityLayer.audit_log` | JSONL file at `config.audit_log_file` | **Routed through #1 (D6).** One tamper mechanism; no second hash chain. A record that cannot be scheduled is counted (`autobot_audit_write_failures_total`, `NotScheduled`). | 64 call sites in 15 modules (`api/agent.py`, `auth_middleware.py`, `auth_rbac.py`, `worker_node.py`, ...). Readers `get_command_history` and `get_audit_entries` (`api/security.py`). | Table (SECURITY) | via #1 |
| 5 | `models/workflow_audit.py` + `services/workflow_permission_service.py` | Postgres `workflow_audit_log` | Undecided. | 1 | None | Convention only |
| 6 | `middleware/audit_middleware.py` | Not a store: feeds #3 | via #3 | 3 | via #1 | via #1 |
| 7 | `services/event_log.py` | Redis `autobot:event_log` | **Retired (D2), module deleted.** `EventType` moved to `taxonomy.py`. | 0. `api/voice_bundle_admin.py` (1, the only direct writer, whose records were invisible to the admin events API) and `api/auth.py` (2) now call `emit_compliance`. | Old key expires by its 90-day TTL | n/a |
| 8 | `knowledge/audit_log.py` | Redis `audit:user:<id>`, `audit:fact:<id>`, `audit:org:<id>` + `setex` bodies | **Retired (D2), module deleted.** `knowledge_view.KnowledgeAuditLog` records and reads KNOWLEDGE events in #1; the double write is gone. | 0 (no production caller ever called `log_event`). Reader: `api/knowledge_audit.py` (5 endpoints). | Old keys expire by their TTL | n/a |
| 9 | `autobot_shared/user_management/models/audit.py` (`AuditLog`) | Postgres `audit_logs` (org, user UUID FK, action, resource, outcome, details JSONB, ip, user agent) | **Missed by the first inventory.** Undecided, and bears on D1 (see below). | `rbac_middleware.py` (denial audit, #14750), `user_management/services/user_service.py`, `organization_service.py`, `team_service.py` | None | Convention only |
| 10 | `services/secrets_audit_store.py` | SQLite `secrets_audit` | Missed. Undecided. | 1 module | None | None |
| 11 | `a2a/trust_score.py` | SQLite `trust_audit` | Missed. Undecided. | 1 module | None | None |
| 12 | `security/enterprise/compliance_manager.py` | Encrypted/plain JSONL under `audit_base_path` | Missed. Undecided. `ComplianceManager` has no production constructor call (exported only). | 1 module | None | Fernet encryption for sensitive events |
| 13 | SLM backend (`autobot-slm-backend/api/security.py`, its `user_service.py`) | SLM's own `AuditLog` table | Missed. A separate service. | 2 modules | n/a | n/a |

Not stores, recorded for completeness: `workers/audit_tasks.py` keeps code-audit **daemon state**
under `audit:testgaps:*`, `audit:dead_code:*`, `audit:claims:*`, `audit:deferred_findings`,
`audit:filing_status` (D4, below). `services/terminal_websocket/audit.py` and `api/secrets.py`'s
`audit_log()` write only to the application log. `autobot-infrastructure/shared/scripts/secrets_manager.py`
keeps an `audit_log.json` outside the guard's roots.

## Decisions (owner, 2026-10-10) and status

| # | Decision | Status |
|---|---|---|
| D1 | Postgres `audit_event` is the system of record; Redis a projection | **Not implemented: needs a decision.** The existing Postgres `audit_logs` table (row 9) already holds this concept with nearly the canonical schema. A new `audit_event` table beside it would be a second Postgres audit table. Options: (a) extend `audit_logs` (add `category`, `actor` text, `session_id`, `event_id`; its `user_id` is a UUID FK, the canonical actor is a string) and point `record` at it; (b) a new `audit_event` table as decided, with row 9's writers moved onto it later. Recommendation: (a). Until then `store_authority.py` declares `audit_trail` with a Redis system of record **as an inadequacy**, so the gap is visible to the registry. |
| D2 | Retire #2, #7, #8 by repointing callers; old keys expire by TTL | **Done.** Modules deleted, callers repointed, guard forbids their import. No stored data was touched. |
| D3 | `audit_log()` becomes an adapter onto #1 | **Done.** |
| D4 | Daemon keys get their own prefix | **Not implemented: needs a data move.** `audit:deferred_findings` is a dead-letter queue with no TTL; renaming the key in code orphans every queued finding, and `audit:dead_code:last_inventory` is the diff baseline (losing it re-files every finding). The move needs a `RENAME` of the existing keys at the same moment as the code change, which is a #17038 action. The harm the shared prefix caused (the retention task trimming daemon zsets) is gone with D5: the purge no longer scans `audit:*`. |
| D5 | One retention policy table | **Done.** `retention.py` (`AUTOBOT_AUDIT_<CATEGORY>_RETENTION_DAYS`, defaults 90/90/365/365) drives the projection TTLs and the one purge path. `AUTOBOT_AUDIT_RETENTION_DAYS` stays the nightly purge's on-switch (0 = off) and only ever shortens a period. The old task trimmed every `audit:*` zset at one period, cutting 365-day records at 90. |
| D6 | Route `security_layer.py` through #1 | **Done.** |

## Behaviour changes a reviewer should know

* `audit.emit(AuditEvent(...))` records the event it is given. It used to be shadowed by an
  `EventType` bridge, so 7 producers (`services/task_claim.py`, `api/voice_bundle_user.py` x2,
  the four retention tasks) recorded a COMPLIANCE event whose action was the event's repr.
* A producer on a thread with no running loop (a sync FastAPI handler, a worker thread) hands its
  record to the serving loop instead of dropping it.
* Reads scan newest-first (`zrevrangebyscore`), so a trail longer than the scan cap shows its most
  recent records, not its oldest.
* The operation-audit dashboard (`api/audit.py`) and the security-layer views now show records from
  every producer for the same filters, and records written to the retired stores before the
  upgrade are not shown (they stay in Redis until their TTL).
* `GET /api/security/audit-log` returns newest-first.

## Data-move proposal (for the #17038 approval queue; not implemented)

1. Optional backfill for visibility: copy db-10 `audit:log:<date>` entries, `audit_log:global`,
   `autobot:event_log` and the knowledge `audit:*:<id>` bodies into `audit:unified`/
   `audit:category:*` in the canonical shape, so records from before the upgrade remain visible
   until they would have expired. Without it they are retained but unreadable for up to 90/365 days.
2. D4: `RENAME audit:testgaps:last_run`, `audit:dead_code:last_inventory`, `audit:claims:last_run*`,
   `audit:deferred_findings`, `audit:filing_status` to a `daemon:audit:` prefix, deployed together
   with the matching constant change in `workers/audit_tasks.py`.
3. D1, once the table is chosen: backfill the Redis projection into Postgres so the system of record
   starts complete.
