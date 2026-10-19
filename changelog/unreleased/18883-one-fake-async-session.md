---
type: test
scope: backend
issue: 18883
pr: 0000
---
Add `testkit.fake_async_session.FakeAsyncSession`, one fake whose signatures are checked against the installed SQLAlchemy `AsyncSession`, and a ratchet that stops new hand-rolled fake sessions in backend and SLM tests.
