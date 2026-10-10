# Audit and event-record systems (#17225)

Inventory taken on `main` at the commit this file lands in. Counts are call sites in non-test
Python under `autobot-backend/`, `autobot_shared/` and `scripts/`, by grep of the call name; a call
through an alias is not counted. #6475 closed on three systems; the issue counts six. The tree has
**eight stores** plus one non-store writer, because two "deprecated" modules still write on their own.

## Inventory

| # | Module | Store | Schema | Writers (call sites) | Readers | Retention | Tamper protection |
|---|---|---|---|---|---|---|---|
| 1 | `services/audit/audit.py` (canonical) | Redis zset `audit:unified` plus `audit:category:<cat>` | JSON `AuditEvent` (category, action, actor, resource, metadata, `occurred_at`) | `emit`/`record`/`emit_*`: ~15; bridge `audit_record` (security category): 19; `KnowledgeAuditLog` bridge | `query`, `query_events`; `api/admin_event_logs.py` | Per category TTL on the category key (90 d SECURITY/COMPLIANCE, 365 d KNOWLEDGE/GOVERNANCE); `audit:unified` deliberately has none (#8303) | None. Redis zset, mutable. |
| 2 | `services/audit/audit_log.py` (deprecated, live via `services/audit/__init__.py`) | Redis zset `audit_log:<user_id>` plus `audit_log:global` | JSON with `created_at` | `record_event` / `audit_record` shim (re-exported by #1) | `query_audit_log` | 90 d TTL refreshed on every write | None |
| 3 | `services/audit_logger.py` | Redis `audit` database (db 10): daily zsets `audit:log:<date>`, `audit:op:*`, `audit:user:*`, `audit:session:*`, `audit:vm:*`, `audit:result:*`; JSONL fallback `audit_<date>.jsonl` | JSON `AuditEntry` (entry sanitised before write) | `audit_log(...)` 123 call sites; `get_audit_logger().log` 4 (`middleware/audit_middleware.py`, `services/gateway/egress_governor.py`) | `AuditLogger.query` and `api/audit.py` (6 endpoints) | `audit_retention_days` (default 90) as TTL on the daily partitions; `cleanup_old_logs` | None. Entry sanitising only. |
| 4 | `security_layer.py` `SecurityLayer.audit_log` | Plain JSONL file at `config.audit_log_file` | `{timestamp,user,action,outcome,details}` | `auth_rbac.py` 3, `auth_middleware.py` 7, `worker_node.py` 2, and `security_layer.py` itself (8) | `get_command_history` (parses the file) | **None.** No rotation or TTL in this module. | Described as "tamper-resistant (append-only)"; the only mechanism is `open(..., "a")`. File mode and immutability are not enforced here. |
| 5 | `models/workflow_audit.py` + `services/workflow_permission_service.py` | Postgres `workflow_audit_log` | `id, timestamp, user_id, workflow_id, action, details JSONB` | `WorkflowPermissionService` 1 write site | `get_audit_log` -> `api/workflow_permissions.py` | **None** (no purge anywhere) | "Never updated or deleted by the application" by convention; no DB trigger or revoke |
| 6 | `middleware/audit_middleware.py` | Not a store: feeds #3 through `get_audit_logger().log` | n/a | ASGI middleware, `initialization/middleware.py` `configure_audit` | via #3 | via #3 | via #3 |
| 7 | `services/event_log.py` (deprecated) | Redis zset `autobot:event_log` (`main` db) | JSON event | **Direct, not via #1:** `api/voice_bundle_admin.py` imports `emit` from it | `event_log.query_events`. **Not** what `audit.query_events` reads: that bridge queries #1's COMPLIANCE category, so the `voice_bundle_admin` records are invisible to the admin events API (`api/admin_event_logs.py`) | 90 d TTL on the single key | None |
| 8 | `knowledge/audit_log.py` (deprecated) | Redis zsets `audit:user:<id>`, `audit:fact:<id>`, `audit:org:<id>` (index of event ids) plus the event body via `setex` | JSON | `KnowledgeAuditLog.log_event`, subclassed by #1, so every knowledge event is written **twice** (here and `audit:unified`) | `get_user_activity`, `get_fact_access_log`, `get_organization_audit_log`, `generate_compliance_report`; `api/knowledge_audit.py` | `ttl_seconds` per instance (on the index zsets and the `setex` body) | None |

Not an audit store, same prefix: `workers/audit_tasks.py` keeps code-audit daemon state under
`audit:testgaps:*`, `audit:dead_code:*`, `audit:claims:*`, `audit:deferred_findings`,
`audit:filing_status`. It shares the `audit:` namespace with #3 and #8.

Retention sweep: `tasks/audit_log_retention.py` scans `audit:*` zsets with
`zremrangebyscore` through `get_redis_client()` (default database). That reaches #1, #8 and the
daemon keys' zsets (it skips non-zsets). Whether the default database is the one that holds #3
(db 10) was not verified here; it is a host/config question, not answered from the source read.

## Classification

| System | Verdict | Reason |
|---|---|---|
| 1 `audit.py` | Same concept, canonical | Who did what to which resource |
| 2 `audit_log.py` | **Fork of 1** | Same record (security actions by user), already bridged by `audit_record` |
| 3 `audit_logger.py` (+6 middleware) | **Fork of 1** | Operation audit trail with user/session/result; different schema and 6 indexes, same question |
| 4 `security_layer` file | **Fork of 1** | Auth/RBAC/command actions by user; same question, weakest store |
| 5 `workflow_audit_log` | **Fork of 1, but the only one with an RDBMS durability claim.** | A workflow lifecycle audit trail; arguably the only one meeting a durable-record bar |
| 7 `event_log` | **Fork of 1** | Compliance events; #1 `COMPLIANCE` category is its documented successor |
| 8 `knowledge/audit_log` | **Fork of 1** | Per-user/fact/org knowledge access; #1 `KNOWLEDGE` category |
| daemon keys | Distinct | Work-queue/state of a CI-style daemon, not a record of user actions. Rename of prefix is a data move; see D4 |

## What this change does and does not do

Done: this inventory, the decisions below, and a guard
(`repo_tests/audit_writer_single_source_17225_test.py`) that pins the set of modules allowed to
write an audit store. A ninth writer fails the test with the fix named.

Not done, on purpose. Every candidate merge below changes a stored record's location, schema or
retention, which the no-agent-rewrite rule (#17038) reserves for a human decision:

* Moving a caller from #7 or #8 to #1 changes where its record lands.
* Moving the 123 `audit_log()` callers to #1 changes the store, schema and index set.
* A "facade" that writes the same record to several stores changes what each store holds.

## Decisions (owner)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | One system of record | (a) #1 Redis; (b) #5-style Postgres table as the durable record, Redis as a projection (`store_authority.system_of_record`); (c) keep several by purpose | **(b)**. Redis zsets with TTL are not a compliance record. Add one Postgres `audit_event` table; #1 `record()` writes it. Needs a migration: human approval. |
| D2 | Retire #2, #7, #8 | (a) backfill their keys into #1 then stop writing; (b) stop writing, let TTL expire (<=90 d lose nothing new); (c) leave | **(b)** after pointing `voice_bundle_admin.py` and the knowledge callers at #1. Each is a location change for new records, no rewrite of old ones. The #8 double write goes away. |
| D3 | #3 (`audit_logger`, 123 callers) | (a) make `audit_log()` an adapter that writes #1 and keep its queries; (b) migrate callers; (c) keep as the operation trail | **(a)**: callers unchanged, store changes. Schema mapping (entry fields into `AuditEvent.metadata`) must be signed off. |
| D4 | `audit:` namespace | (a) rename daemon keys to `daemon:audit:*`; (b) leave | **(a)** when daemon state is next touched; it is a key move. |
| D5 | Retention | one policy table keyed by category, applied by `tasks/audit_log_retention.py`; remove per-writer TTLs | Depends on D1. Interacts with #16846. #4 and #5 have none today: that is a compliance gap in its own right. |
| D6 | #4 "tamper-resistant" claim | (a) hash-chain or write via #1; (b) fix the docstring | **(a)**, with D1. Until then the docstring overstates. |

Open acceptance criteria of #17225 and their status: one system of record (needs D1/D2/D3); single
"everything for user U in window W" query (needs D1; the stores key users differently: `audit:user:<id>:<date>`,
`audit_log:<id>`, `audit:user:<id>`, a JSONL scan, a Postgres column); one retention path (D5); CONTROL_PLANE_MAP row
(left for the PR that does D1, since this change updates no status). Writers enumerated: see the table.
