# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Ciphertext-only backup of the backend's secrets store (#18281).

Owner decision (2026-10-12): the artifact is a consistent SQLite online-backup
snapshot of the *encrypted* ``secrets.db``. The key (``secrets.key`` /
``AUTOBOT_SECRETS_KEY``) is never part of it, so a restore only works on a host
holding the same key. Key escrow is a separate decision (#16406).

This is a ``BackupServiceType`` run by the existing :class:`BackupService`
over its existing SSH path, not a parallel mechanism. It only READS the store:
the snapshot is made into a temp file next to nothing the backend uses, copied
to SLM storage, checksummed, and the temp file removed. No crypto lives here.

Where the store is: the backend role writes ``AUTOBOT_DATA_DIR`` from the
ansible variable ``backend_data_dir`` (roles/backend/defaults/main.yml,
``/var/lib/autobot``), and ``SecretsService`` puts ``secrets.db`` directly in
that directory. The SLM has no other registry of the backend's data path, so
``SLM_BACKEND_DATA_DIR`` carries it here with the same default (pinned to the
ansible default by a test). The node is the one the backup row names, and it
must carry a backend role.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Tuple

from models.database import BackupStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from models.database import Backup, Node
    from services.backup import BackupService

logger = logging.getLogger(__name__)

#: Directory of the backend's secrets store on the backend node (``backend_data_dir``).
BACKEND_DATA_DIR = os.getenv("SLM_BACKEND_DATA_DIR", "/var/lib/autobot")
#: File name ``SecretsService`` uses inside the data directory.
SECRETS_DB_NAME = "secrets.db"  # pragma: allowlist secret
#: Where the snapshot is staged on the node before the copy.
STAGING_DIR = os.getenv("SLM_SECRETS_BACKUP_STAGING_DIR", "/tmp")
#: Roles that mean "this node runs the backend" (same pair inventory_builder treats as backend).
BACKEND_ROLES = frozenset({"backend", "main"})
STORED_SUFFIX = ".secrets.db"
SNAPSHOT_TIMEOUT_S = int(os.getenv("SLM_SECRETS_BACKUP_TIMEOUT_S", "300"))
REMOVE_TIMEOUT_S = int(os.getenv("SLM_SECRETS_BACKUP_REMOVE_TIMEOUT_S", "30"))
CHECKSUM_TIMEOUT_S = int(os.getenv("SLM_SECRETS_BACKUP_CHECKSUM_TIMEOUT_S", "60"))

NO_BACKEND_NODE = "no registered backend node: the secrets store lives on a node with the backend role"

# Run with: python3 -c <script> <source> <destination>. sqlite3's online backup API
# gives a transactionally consistent copy while the backend keeps writing; the source
# is opened but only read, and a missing source is an error, never an empty new store.
_SNAPSHOT_SCRIPT = """\
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
if not os.path.isfile(src):
    sys.exit('secrets store not found')
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
s.backup(d)
ok = d.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
d.close()
s.close()
sys.exit(0 if ok else 'snapshot failed integrity_check')
"""


def remote_store_path() -> str:
    """The secrets database path on the backend node, derived from the data directory."""
    return str(Path(BACKEND_DATA_DIR) / SECRETS_DB_NAME)


def staged_path(backup_id: str) -> str:
    """The temp path the snapshot is written to on the node."""
    return str(Path(STAGING_DIR) / f"slm-secrets-{backup_id}.db")


def snapshot_command(source: str, destination: str) -> str:
    """The remote shell command that makes the online-backup snapshot (owner-only mode)."""
    return f"umask 077 && python3 -c {shlex.quote(_SNAPSHOT_SCRIPT)} {shlex.quote(source)} {shlex.quote(destination)}"


def is_backend_node(node: "Node") -> bool:
    """Whether *node* is registered with a backend role."""
    return bool(BACKEND_ROLES & set(node.roles or []))


async def _remove_staged(service: "BackupService", node: "Node", path: str) -> None:
    """Best-effort removal of the staged snapshot (a copy of the store must not linger in /tmp)."""
    try:
        cmd = service._build_ssh_command(
            node.ip_address, node.ssh_user or "autobot", node.ssh_port or 22, f"rm -f -- {shlex.quote(path)}"
        )
        await service._run_command(cmd, timeout=REMOVE_TIMEOUT_S)
    except Exception as exc:
        logger.warning("Could not remove staged secrets snapshot on node %s: %s", node.node_id, exc)


async def _remote_sha256(service: "BackupService", node: "Node", path: str) -> str | None:
    """sha256 of the staged snapshot as the node sees it."""
    command = f"sha256sum -- {shlex.quote(path)} | cut -d' ' -f1"
    cmd = service._build_ssh_command(node.ip_address, node.ssh_user or "autobot", node.ssh_port or 22, command)
    ok, out = await service._run_command(cmd, timeout=CHECKSUM_TIMEOUT_S)
    digest = out.strip() if ok else ""
    return digest or None


async def _fetch_verified(service: "BackupService", node: "Node", backup_id: str, remote: str) -> Tuple[Path, str]:
    """Copy the staged snapshot to SLM storage; return (path, sha256), raising on any mismatch."""
    host, user, port = node.ip_address, node.ssh_user or "autobot", node.ssh_port or 22
    remote_sum = await _remote_sha256(service, node, remote)
    ok, path, err = await service._copy_backup_to_storage(host, user, port, remote, backup_id, suffix=STORED_SUFFIX)
    if not ok:
        raise RuntimeError(f"copy to SLM storage failed: {err}")
    local_sum = await service._calculate_checksum(path)
    if not local_sum or local_sum != remote_sum:
        raise RuntimeError("checksum of the copied snapshot does not match the node's")
    path.chmod(0o600)
    return path, local_sum


async def _snapshot(service: "BackupService", node: "Node", remote: str) -> None:
    """Run the online-backup snapshot on the node, raising when it fails."""
    cmd = service._build_ssh_command(
        node.ip_address,
        node.ssh_user or "autobot",
        node.ssh_port or 22,
        snapshot_command(remote_store_path(), remote),
    )
    ok, out = await service._run_command(cmd, timeout=SNAPSHOT_TIMEOUT_S)
    if not ok:
        raise RuntimeError(f"snapshot failed: {out.strip()[:200]}")


async def execute_secrets_backup(
    service: "BackupService", db: "AsyncSession", backup_id: str, node: "Node"
) -> Tuple[bool, str]:
    """Back up the secrets store of *node*; the row ends COMPLETED or FAILED with a message."""
    backup = await service._mark_backup_in_progress(db, backup_id)
    if not backup:
        return False, "Backup not found"
    if not is_backend_node(node):
        return await service._fail_backup(db, backup, NO_BACKEND_NODE)
    remote = staged_path(backup_id)
    try:
        path, checksum = await _snapshot_and_fetch(service, node, backup_id, remote)
    except asyncio.TimeoutError:
        return await service._fail_backup(db, backup, "Secrets backup timed out")
    except Exception as exc:
        logger.error("Secrets backup %s failed: %s", backup_id, exc)
        return await service._fail_backup(db, backup, f"Secrets backup failed: {exc}")
    finally:
        await _remove_staged(service, node, remote)
    return await _record_completed(db, backup, path, checksum)


async def _snapshot_and_fetch(service: "BackupService", node: "Node", backup_id: str, remote: str) -> Tuple[Path, str]:
    """Snapshot on the node, then copy it home and verify it."""
    await _snapshot(service, node, remote)
    return await _fetch_verified(service, node, backup_id, remote)


async def _record_completed(db: "AsyncSession", backup: "Backup", path: Path, checksum: str) -> Tuple[bool, str]:
    """Fill the row for a verified snapshot."""
    backup.status = BackupStatus.COMPLETED.value
    backup.backup_path = str(path)
    backup.size_bytes = path.stat().st_size
    backup.checksum = checksum
    backup.error = None
    backup.completed_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Secrets backup %s completed: %s bytes", backup.backup_id, backup.size_bytes)
    return True, "Secrets backup completed successfully"


def secrets_runner(service: "BackupService"):
    """The dispatch entry for ``BackupServiceType.SECRETS``: ``await runner(db, backup_id, node)``."""
    return functools.partial(execute_secrets_backup, service)
