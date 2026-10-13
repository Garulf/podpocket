import asyncio

from podpocket.opml import OpmlImports


async def test_completed_imports_report_uuids_and_failures():
    async def resolve(url):
        if "bad" in url:
            raise ValueError("broken feed")
        return f"uuid-for-{url}"

    batch = await OpmlImports(resolve, wait_seconds=1).start(["good", "bad"])

    assert batch.uuids == ["uuid-for-good"]
    assert batch.poll_uuids == []
    assert batch.failed == 1


async def test_slow_imports_are_returned_as_poll_tokens():
    release = asyncio.Event()

    async def resolve(url):
        await release.wait()
        return "late-uuid"

    imports = OpmlImports(resolve, wait_seconds=0.01)
    first = await imports.start(["slow"])

    assert first.uuids == []
    assert len(first.poll_uuids) == 1

    release.set()
    second = await imports.poll(first.poll_uuids)

    assert second.uuids == ["late-uuid"]
    assert second.poll_uuids == []


async def test_unknown_tokens_count_as_failed():
    async def resolve(url):
        return "x"

    batch = await OpmlImports(resolve, wait_seconds=0.01).poll(["never-issued"])

    assert (batch.uuids, batch.poll_uuids, batch.failed) == ([], [], 1)


async def test_concurrency_is_bounded():
    running = 0
    peak = 0

    async def resolve(url):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return url

    batch = await OpmlImports(resolve, wait_seconds=2, concurrency=3).start(
        [str(n) for n in range(10)]
    )

    assert len(batch.uuids) == 10
    assert peak <= 3


async def test_overlapping_polls_of_the_same_tokens_both_answer():
    release = asyncio.Event()

    async def resolve(url):
        await release.wait()
        return "late-uuid"

    imports = OpmlImports(resolve, wait_seconds=0.1)
    tokens = (await imports.start(["slow"])).poll_uuids

    polls = asyncio.gather(imports.poll(tokens), imports.poll(tokens + tokens))
    await asyncio.sleep(0)
    release.set()
    first, second = await polls

    assert first.uuids == second.uuids == ["late-uuid"]
    assert (first.failed, second.failed) == (0, 0)
