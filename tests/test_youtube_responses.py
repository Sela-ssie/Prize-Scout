import asyncio

import httpx
import pytest

from src import db
from src.ingestion import QuotaGuard
from src.youtube_client import YouTubeClient, YouTubeRequestError


@pytest.mark.parametrize('payload, message', [
    ({'kind': 'youtube#channelListResponse', 'pageInfo': {'totalResults': 0}}, 'Channel not found'),
    ({}, 'invalid response data'),
    ({'kind': 'youtube#channelListResponse', 'pageInfo': {'totalResults': 1}}, 'invalid response data'),
])
def test_empty_api_results_are_distinguished_from_malformed_data(tmp_path, payload, message):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as http:
            client = YouTubeClient('test-key', QuotaGuard(engine), http)
            with pytest.raises(YouTubeRequestError, match=message):
                await client.resolve_channel('@missing')
    asyncio.run(run())
