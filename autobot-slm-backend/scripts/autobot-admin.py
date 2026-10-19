#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
autobot-admin - server-side management CLI for AutoBot SLM.

Usage:
    autobot-admin reset-password <username> <new_password>

Reads DB connection from /etc/autobot/slm-secrets.env or
/opt/autobot/autobot-slm-backend/.env (later file wins per key).
Works without the SLM backend running (direct PostgreSQL access).
"""

import argparse
import logging
import os
import re
import sys
import time

logger = logging.getLogger("autobot-admin")


def load_env_file(path: str) -> dict:
    """Load key=value pairs from an env file, ignoring comments and blanks."""
    env: dict = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def get_db_url() -> str:
    """Build a PostgreSQL DSN from secrets files. Later file wins per key."""
    env: dict = {}
    for path in (
        "/etc/autobot/slm-secrets.env",
        "/opt/autobot/autobot-slm-backend/.env",
    ):
        env.update(load_env_file(path))

    host = env.get("SLM_DB_HOST", "127.0.0.1")
    port = env.get("SLM_DB_PORT", "5432")
    name = env.get("SLM_DB_NAME", "autobot_slm")
    user = env.get("SLM_DB_USER", "autobot")
    password = env.get("SLM_DB_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


#: A reset records the subject's password epoch in the SLM's own store (#18663), so every token
#: issued before it is refused (#12924). GREATEST keeps a later epoch from going backwards.
_UPSERT_EPOCH_SQL = (
    "INSERT INTO slm_password_epochs (subject, epoch) VALUES (%s, %s) "
    "ON CONFLICT (subject) DO UPDATE SET epoch = GREATEST(slm_password_epochs.epoch, EXCLUDED.epoch)"
)


def cmd_reset_password(args: argparse.Namespace) -> None:
    """Bcrypt-hash new_password and write it to slm_users, then sync secrets."""
    try:
        import bcrypt  # noqa: PLC0415
        import psycopg2  # noqa: PLC0415
    except ImportError as exc:
        sys.exit(f"Missing dependency: {exc}. " "Install with: pip install bcrypt psycopg2-binary")

    password_hash = bcrypt.hashpw(args.new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

    db_url = get_db_url()
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE slm_users SET password_hash = %s WHERE username = %s RETURNING id",
                (password_hash, args.username),
            )
            if cur.rowcount == 0:
                sys.exit(f"Error: user '{args.username}' not found.")
            # Same transaction: tokens issued before this reset stop working (#18672, #12924).
            cur.execute(_UPSERT_EPOCH_SQL, (args.username, int(time.time())))
        conn.commit()
    finally:
        conn.close()

    logger.info("Password for '%s' updated in database.", args.username)

    # Sync SLM_ADMIN_PASSWORD in secrets file for the admin user so the next
    # SLM service restart picks up the new plaintext value used by seed plays.
    secrets_path = "/etc/autobot/slm-secrets.env"
    if args.username == "admin" and os.path.exists(secrets_path):
        with open(secrets_path, encoding="utf-8") as fh:
            content = fh.read()
        updated = re.sub(
            r"^SLM_ADMIN_PASSWORD=.*$",
            f"SLM_ADMIN_PASSWORD={args.new_password}",
            content,
            flags=re.MULTILINE,
        )
        if "SLM_ADMIN_PASSWORD=" not in updated:
            updated += f"\nSLM_ADMIN_PASSWORD={args.new_password}\n"
        with open(secrets_path, "w", encoding="utf-8") as fh:
            fh.write(updated)
        logger.info("Updated SLM_ADMIN_PASSWORD in %s", secrets_path)

    logger.info("Done.")


def main() -> None:
    # Operator-facing output: the message alone, on stdout, as before (#1082 logging standard).
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(
        prog="autobot-admin",
        description="AutoBot server administration tool",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    rp = subparsers.add_parser("reset-password", help="Reset a user password")
    rp.add_argument("username", help="Username to reset")
    rp.add_argument("new_password", help="New plaintext password (will be bcrypt-hashed)")
    rp.set_defaults(func=cmd_reset_password)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
