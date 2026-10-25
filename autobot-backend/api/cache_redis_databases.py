# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The Redis databases the cache API reports on and clears, and where it keeps its own settings.

Numbers come from the one name -> number table at call time (#19232). Aliases are not offered:
an alias shares its target's number, so clearing ``locks`` flushed every session (#19497).
"""

import json
from typing import Dict

from fastapi import HTTPException

from autobot_shared.logging_manager import get_logger
from autobot_shared.redis_client import DATABASE_MAPPING, get_redis_client
from autobot_shared.redis_management.database_names import alias_target

logger = get_logger(__name__)

#: Canonical names only -- no alias, and only databases a cache clear may flush (not audit, celery,
#: logs/vectors). A name the YAML later drops is left out at call time, never offered as a None.
_CACHE_DATABASE_NAMES = ("main", "knowledge", "prompts", "agents", "sessions", "cache", "analytics")

#: The cache API's settings: one namespaced key in the declared cache DB, not a DB of their own (#19497).
CACHE_CONFIG_DATABASE = "cache"
CACHE_CONFIG_KEY = "cache:config"


def redis_databases() -> Dict[str, int]:
    """``{name: number}`` for the declared databases this API covers, read from the one table now."""
    return {name: DATABASE_MAPPING[name] for name in _CACHE_DATABASE_NAMES if name in DATABASE_MAPPING}


def distinct_redis_databases() -> Dict[str, int]:
    """:func:`redis_databases` with one name per number, so ``clear/all`` flushes each DB once."""
    seen: Dict[int, str] = {}
    for name, number in redis_databases().items():
        seen.setdefault(number, name)
    return {name: number for number, name in seen.items()}


def clearable_database(database: str) -> int:
    """The number of *database* if this API may clear it; a 400 naming why otherwise."""
    target = alias_target(database)
    databases = redis_databases()
    if target is not None:
        fate = f"clear '{target}' to flush it" if target in databases else "which this API does not clear"
        detail = f"'{database}' is an alias of '{target}', not a database of its own; {fate}"
        raise HTTPException(status_code=400, detail=detail)
    if database not in databases:
        raise HTTPException(status_code=400, detail=f"Unknown database '{database}'. Available: {list(databases)}")
    return databases[database]


def get_cache_redis_connection(database: str = "main"):
    """Get Redis connection for specified database using canonical client."""
    try:
        client = get_redis_client(async_client=False, database=database)
        if client is None:
            raise ConnectionError(f"Failed to get Redis client for database '{database}'")
        return client
    except Exception as e:
        logger.error("Failed to connect to Redis database '%s': %s", database, str(e))
        raise


def save_config(config_data: dict) -> None:
    """Persist the cache settings; a failure is a 503, never a reported success (#19497)."""
    try:
        get_cache_redis_connection(CACHE_CONFIG_DATABASE).set(CACHE_CONFIG_KEY, json.dumps(config_data))
    except Exception as e:
        logger.error("Could not save cache config to Redis: %s", e)
        raise HTTPException(status_code=503, detail="Cache configuration was not saved: Redis unavailable")


def load_config() -> dict | None:
    """The saved cache settings, or ``None`` when none are saved or Redis cannot be read."""
    try:
        raw = get_cache_redis_connection(CACHE_CONFIG_DATABASE).get(CACHE_CONFIG_KEY)
    except Exception as e:
        logger.warning("Could not load cache config from Redis: %s", e)
        return None
    return json.loads(raw) if raw else None


def clear_single_redis_database(db_name: str, db_number: int) -> dict:
    """Flush one database and report what it held; an error is reported, not raised."""
    try:
        redis_conn = get_cache_redis_connection(db_name)
        keys_before = redis_conn.dbsize()
        redis_conn.flushdb()
        logger.info("Cleared Redis database %s (%s) - %s keys removed", db_name, db_number, keys_before)
        return {"name": db_name, "database": db_number, "keys_cleared": keys_before}
    except Exception:
        logger.error("Failed to clear Redis database %s (%s): %s", db_name, db_number, "Internal server error")
        return {"name": db_name, "database": db_number, "error": "Internal server error", "keys_cleared": 0}
