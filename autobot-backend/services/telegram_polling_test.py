# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Tests for the Telegram polling ingest (#19010).

Covers the three properties the issue asks for: the offset survives a restart
(so nothing is answered twice), the mode switch leaves exactly one ingest live,
and polled updates go through the same ``process_telegram_update`` as the
webhook.
"""

import asyncio
from types import SimpleNamespace

import pytest

from services import telegram_polling as tp


class FakeRedis:
    """Minimal async Redis: a dict that outlives any one poller, like the real store."""

    def __init__(self) -> None:
        self.data: dict = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = str(value).encode("utf-8")


def _update(update_id: int) -> dict:
    return {"update_id": update_id, "message": {"message_id": update_id, "chat": {"id": 1}, "text": "hi"}}


@pytest.fixture
def harness(monkeypatch):
    redis = FakeRedis()
    state = SimpleNamespace(offsets=[], routed=[], batches=[])

    async def fake_redis():
        return redis

    async def fake_from_redis():
        return SimpleNamespace(bot_token="t", base_url="https://api.telegram.org/bott")

    async def fake_fetch(service, offset):
        state.offsets.append(offset)
        return state.batches.pop(0) if state.batches else []

    async def fake_process(app, update):
        state.routed.append(update["update_id"])

    monkeypatch.setattr(tp, "get_redis_client", fake_redis)
    monkeypatch.setattr(tp.TelegramBotService, "from_redis", staticmethod(fake_from_redis))
    monkeypatch.setattr(tp, "fetch_updates", fake_fetch)
    monkeypatch.setattr("api.telegram_bot.process_telegram_update", fake_process)
    state.redis = redis
    return state


async def _drain(app) -> None:
    await asyncio.gather(*list(app.state.telegram_update_tasks))


class TestOffsetPersistence:
    @pytest.mark.asyncio
    async def test_restart_resumes_after_last_dispatched_update(self, harness):
        await tp.save_ingest_mode(tp.INGEST_MODE_POLLING)
        harness.batches = [[_update(7), _update(8)], []]

        first_app = SimpleNamespace(state=SimpleNamespace())
        assert await tp.poll_once(first_app) == 2
        await _drain(first_app)

        # A fresh app is a restarted backend; only Redis carries over.
        assert await tp.poll_once(SimpleNamespace(state=SimpleNamespace())) == 0
        assert harness.offsets == [0, 9]
        assert harness.routed == [7, 8]


class TestModeSwitch:
    @pytest.mark.asyncio
    async def test_webhook_mode_never_polls(self, harness):
        await tp.save_ingest_mode(tp.INGEST_MODE_WEBHOOK)
        harness.batches = [[_update(1)]]

        assert await tp.poll_once(SimpleNamespace(state=SimpleNamespace())) == 0
        assert harness.offsets == []

    @pytest.mark.asyncio
    async def test_unset_mode_defaults_to_webhook(self, harness):
        assert await tp.get_ingest_mode() == tp.INGEST_MODE_WEBHOOK

    @pytest.mark.asyncio
    async def test_unknown_mode_is_rejected(self, harness):
        with pytest.raises(ValueError):
            await tp.save_ingest_mode("both")

    @pytest.mark.asyncio
    async def test_selecting_polling_deletes_webhook(self, harness, monkeypatch):
        from api import telegram_bot as tg
        from api.schemas_telegram import TelegramBotConfigRequest

        calls: list = []

        async def fake_delete(service):
            calls.append("deleteWebhook")

        async def fake_set(url, secret):
            calls.append("setWebhook")

        monkeypatch.setattr(tg, "delete_webhook", fake_delete)
        service = SimpleNamespace(set_webhook=fake_set)
        req = TelegramBotConfigRequest(bot_token="t", ingest_mode="polling")

        assert await tg._apply_ingest_mode(service, req) is None
        assert calls == ["deleteWebhook"]
        assert await tp.get_ingest_mode() == tp.INGEST_MODE_POLLING

    def test_polling_request_with_webhook_url_is_invalid(self):
        from pydantic import ValidationError

        from api.schemas_telegram import TelegramBotConfigRequest

        with pytest.raises(ValidationError):
            TelegramBotConfigRequest(bot_token="t", ingest_mode="polling", webhook_url="https://x.example")


class TestSharedRoutingPath:
    @pytest.mark.asyncio
    async def test_webhook_and_poller_share_process_telegram_update(self, monkeypatch):
        """The webhook delegates to the function the poller dispatches -- one path."""
        from api import telegram_bot as tg

        seen: list = []

        async def fake_process(app, update):
            seen.append((app, update))

        monkeypatch.setattr(tg, "process_telegram_update", fake_process)
        app = SimpleNamespace(state=SimpleNamespace())
        request = SimpleNamespace(app=app)

        await tg.telegram_webhook(request=request, update=_update(3))
        tp._dispatch(app, _update(4))
        await _drain(app)

        assert [u["update_id"] for _, u in seen] == [3, 4]
        assert all(a is app for a, _ in seen)


class TestTokenNeverLogged:
    @pytest.mark.asyncio
    async def test_transport_error_drops_the_tokenised_url(self, monkeypatch):
        """aiohttp errors embed the URL, which embeds the token -- it must not propagate."""
        import contextlib

        import aiohttp

        token = "123:SECRET"

        @contextlib.asynccontextmanager
        async def boom(method, url, **kwargs):
            raise aiohttp.InvalidURL(url)
            yield  # pragma: no cover

        monkeypatch.setattr(tp, "get_http_client", lambda: SimpleNamespace(tracked_request=boom))
        service = tp.TelegramBotService(bot_token=token)

        with pytest.raises(RuntimeError) as err:
            await tp.fetch_updates(service, 0)
        assert token not in str(err.value)
        assert err.value.__cause__ is None
