import httpx
import os
from typing import List, Optional
from datetime import datetime, timedelta, timezone

class YouTubeClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        self.base = "https://www.googleapis.com/youtube/v3"

    async def search_videos(self, query: str, max_results: int = 5, months_back: int = 3) -> List[dict]:
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY not set")

        # Calculate date for filtering (3 months ago by default)
        published_after = datetime.now(timezone.utc) - timedelta(days=30 * months_back)
        published_after_str = published_after.strftime("%Y-%m-%dT%H:%M:%SZ")

        all_videos = []
        all_ids = []
        page_token = None
        
        async with httpx.AsyncClient() as client:
            # Fetch multiple pages until we have enough results
            while len(all_videos) < max_results:
                # YouTube API max is 50 per request
                per_page = min(50, max_results - len(all_videos))
                
                params = {
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "maxResults": str(per_page),
                    "publishedAfter": published_after_str,  # Only videos from last N months
                    "order": "date",  # Sort by most recent first
                    "key": self.api_key,
                }
                
                if page_token:
                    params["pageToken"] = page_token

                r = await client.get(f"{self.base}/search", params=params, timeout=20.0)
                r.raise_for_status()
                data = r.json()

                items = data.get("items", [])
                if not items:
                    break  # No more results
                
                for item in items:
                    vid = item["id"]["videoId"]
                    title = item["snippet"]["title"]
                    published_at = item["snippet"].get("publishedAt", "")
                    all_videos.append({
                        "id": vid,
                        "channel_id": item["snippet"].get("channelId"),
                        "title": title,
                        "published_at": published_at,
                    })
                    all_ids.append(vid)
                
                # Check if there's a next page
                page_token = data.get("nextPageToken")
                if not page_token:
                    break  # No more pages available

            if not all_ids:
                return []

            # Fetch full descriptions for all videos (in batches of 50)
            id_to_desc = {}
            for i in range(0, len(all_ids), 50):
                batch_ids = all_ids[i:i+50]
                params2 = {"part": "snippet", "id": ",".join(batch_ids), "key": self.api_key}
                r2 = await client.get(f"{self.base}/videos", params=params2, timeout=20.0)
                r2.raise_for_status()
                data2 = r2.json()

                for item in data2.get("items", []):
                    id_to_desc[item["id"]] = item["snippet"].get("description", "")

            for v in all_videos:
                v["description"] = id_to_desc.get(v["id"], "")

            return all_videos
