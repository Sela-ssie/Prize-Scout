"""Bounded YouTube requests. Every attempt is charged before network I/O."""
import asyncio
import os
import re
import httpx


class YouTubeRequestError(RuntimeError):
    """Sanitized error: never includes the API-key-bearing request URL."""

    def __init__(self, message, *, invalid_page_token=False):
        super().__init__(message)
        self.invalid_page_token = invalid_page_token


class YouTubeClient:
    def __init__(self, api_key=None, guard=None, client=None, max_attempts=3, sleep=asyncio.sleep):
        self.api_key = api_key or os.getenv('YOUTUBE_API_KEY')
        self.guard = guard
        self.client = client
        self.max_attempts = min(max(int(max_attempts), 1), 3)
        self.sleep = sleep
        self.base = 'https://www.googleapis.com/youtube/v3'

    async def _request(self, endpoint, params):
        if not self.api_key:
            raise YouTubeRequestError('YOUTUBE_API_KEY is not configured')
        if self.guard is None:
            raise YouTubeRequestError('A quota guard is required')
        for attempt in range(self.max_attempts):
            self.guard.charge(100 if endpoint == 'search' else 1)
            status = None
            invalid_page_token = False
            try:
                if self.client is None:
                    async with httpx.AsyncClient() as client:
                        response = await client.get(f'{self.base}/{endpoint}', params={**params, 'key': self.api_key}, timeout=20)
                else:
                    response = await self.client.get(f'{self.base}/{endpoint}', params={**params, 'key': self.api_key}, timeout=20)
                status = response.status_code
                if status < 400:
                    data = response.json()
                    # The API omits items for a valid empty result. Require its
                    # envelope before accepting this, so malformed responses can
                    # never masquerade as deleted/private cached videos.
                    kinds = {'channels': 'youtube#channelListResponse', 'videos': 'youtube#videoListResponse',
                             'playlistItems': 'youtube#playlistItemListResponse'}
                    if (isinstance(data, dict) and 'items' not in data
                            and data.get('kind') == kinds.get(endpoint)
                            and isinstance(data.get('pageInfo'), dict)
                            and data['pageInfo'].get('totalResults') == 0):
                        data['items'] = []
                    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                        raise YouTubeRequestError('YouTube returned invalid response data')
                    if any(not isinstance(item, dict) for item in data['items']):
                        raise YouTubeRequestError('YouTube returned invalid response data')
                    return data
                retryable = status == 429 or status >= 500
                if status == 400:
                    try:
                        errors = response.json().get('error', {}).get('errors', [])
                        invalid_page_token = any(e.get('reason') == 'invalidPageToken' for e in errors)
                    except (ValueError, AttributeError, TypeError):
                        pass
            except httpx.TransportError:
                retryable = True
            except (ValueError, KeyError):
                raise YouTubeRequestError('YouTube returned invalid response data') from None
            if not retryable or attempt + 1 == self.max_attempts:
                raise YouTubeRequestError(f'YouTube request failed ({status or "network error"})', invalid_page_token=invalid_page_token) from None
            await self.sleep(2 ** attempt)
        raise YouTubeRequestError('YouTube request failed')

    @staticmethod
    def _uploads(item):
        try:
            playlist = item['contentDetails']['relatedPlaylists']['uploads']
        except (KeyError, TypeError):
            raise YouTubeRequestError('Channel uploads playlist unavailable') from None
        if not isinstance(playlist, str) or not re.fullmatch(r'UU[A-Za-z0-9_-]+', playlist):
            raise YouTubeRequestError('Channel uploads playlist unavailable')
        return playlist

    async def uploads_playlist(self, channel_id):
        data = await self._request('channels', {'part': 'contentDetails', 'id': channel_id})
        if not data['items']:
            raise YouTubeRequestError('Channel not found or unavailable')
        return self._uploads(data['items'][0])

    async def resolve_channel(self, channel):
        """Resolve one validated @handle or canonical channel ID for one unit."""
        if not isinstance(channel, str) or not (
                re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel)
                or (channel.startswith('@') and 1 < len(channel) <= 101
                    and not any(c.isspace() or c in '/?&#' for c in channel))):
            raise ValueError('Channel must be a YouTube @handle or full UC channel ID')
        param = 'forHandle' if channel.startswith('@') else 'id'
        data = await self._request('channels', {'part': 'snippet,contentDetails', param: channel})
        if len(data['items']) != 1:
            raise YouTubeRequestError('Channel not found or unavailable')
        item = data['items'][0]
        channel_id = item.get('id')
        playlist = self._uploads(item)
        snippet = item.get('snippet')
        if (not isinstance(channel_id, str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id)
                or not re.fullmatch(r'UU[A-Za-z0-9_-]{22}', playlist)
                or not isinstance(snippet, dict) or not isinstance(snippet.get('title'), str)):
            raise YouTubeRequestError('YouTube returned invalid channel metadata')
        return {'channel_id': channel_id, 'uploads_playlist_id': playlist,
                'title': snippet['title']}

    async def playlist_page(self, playlist_id, page_token=None):
        params = {'part': 'contentDetails', 'playlistId': playlist_id, 'maxResults': 50}
        if page_token:
            params['pageToken'] = page_token
        data = await self._request('playlistItems', params)
        if any(not isinstance(item.get('contentDetails'), dict) or not isinstance(item['contentDetails'].get('videoId'), str) for item in data['items']):
            raise YouTubeRequestError('YouTube returned invalid playlist data')
        if data.get('nextPageToken') is not None and not isinstance(data['nextPageToken'], str):
            raise YouTubeRequestError('YouTube returned invalid pagination data')
        ids = [item['contentDetails']['videoId'] for item in data.get('items', []) if item.get('contentDetails', {}).get('videoId')]
        return ids, data.get('nextPageToken')

    async def videos(self, ids):
        if not ids:
            return []
        if len(ids) > 50:
            raise ValueError('videos batch exceeds 50 IDs')
        data = await self._request('videos', {'part': 'snippet', 'id': ','.join(ids)})
        rows = []
        for item in data.get('items', []):
            if not isinstance(item.get('id'), str) or item['id'] not in ids or not isinstance(item.get('snippet'), dict):
                raise YouTubeRequestError('YouTube returned invalid video data')
            snippet = item['snippet']
            if any(not isinstance(snippet.get(field, ''), str) for field in ('title', 'description', 'channelId', 'channelTitle', 'publishedAt')):
                raise YouTubeRequestError('YouTube returned invalid video metadata')
            rows.append({'id': item['id'], 'channel_id': snippet.get('channelId'),
                         'channel_title': snippet.get('channelTitle', ''),
                         'title': snippet.get('title', ''), 'description': snippet.get('description', ''),
                         'published_at': snippet.get('publishedAt')})
        return rows
