"""Regression: ZenML's finalizer and log shutdown may close the same session."""

import asyncio
from types import SimpleNamespace

from aiobotocore.httpsession import AIOHTTPSession
from zenml.integrations.s3.artifact_stores.s3_artifact_store import ZenMLS3Filesystem


def test_concurrent_zenml_cleanup_does_not_raise_after_successful_work():
    async def scenario():
        # Exercise the real HTTP session cleanup, without making AWS requests.
        session = AIOHTTPSession()
        await session.__aenter__()
        closing = asyncio.Event()
        release = asyncio.Event()

        async def slow_resource_close():
            closing.set()
            await release.wait()

        session._exit_stack.push_async_callback(slow_resource_close)
        creator = SimpleNamespace(__aexit__=session.__aexit__)
        close = ZenMLS3Filesystem._safe_aexit_s3_creator
        first = asyncio.create_task(close(creator))
        await asyncio.wait_for(closing.wait(), timeout=2)
        second = asyncio.create_task(close(creator))
        await asyncio.sleep(0)  # Both callers entered before the first finishes.
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert results == [None, None], results
        assert session._sessions is None
        await close(creator)  # ZenML also tolerates a later sequential close.

    asyncio.run(scenario())
