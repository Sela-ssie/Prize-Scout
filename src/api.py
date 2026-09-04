from fastapi import APIRouter, Query
from .youtube_client import YouTubeClient
from .extractor import extract_giveaways_from_description
from typing import List
import json
import os
from . import db
from concurrent.futures import ThreadPoolExecutor
import asyncio

router = APIRouter()

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
yt = YouTubeClient(api_key=YOUTUBE_API_KEY)

# optional sync DB engine. We will run DB writes in a thread to avoid blocking
DATABASE_URL = os.environ.get("DATABASE_URL")
# Default to SQLite file in project root if DATABASE_URL not provided
if not DATABASE_URL:
    DATABASE_URL = 'sqlite:///./prizes.db'

sync_engine = db.create_db_and_tables(DATABASE_URL)
db_executor = ThreadPoolExecutor(max_workers=2)


@router.get("/search")
async def search(q: str = Query(..., min_length=1), max_results: int = 50):
    """Search YouTube for videos matching query and extract giveaway details from descriptions."""
    
    # Search for maximum videos allowed by YouTube API
    # Since we filter aggressively, we need to search many to get enough results
    search_count = 150  # With pagination, we can fetch multiple pages of 50 each
    videos = await yt.search_videos(q, max_results=search_count)

    results = []
    for v in videos:
        # Stop if we already have enough results
        if len(results) >= max_results:
            break
            
        desc = v.get("description", "")
        extracted = extract_giveaways_from_description(desc, published_at=v.get("published_at"))
        if extracted:
            # attach video link and title
            extracted["video_title"] = v.get("title")
            extracted["video_id"] = v.get("id")
            extracted["video_url"] = f"https://www.youtube.com/watch?v={v.get('id')}"
            extracted["published_at"] = v.get("published_at")  # Add published date
            
            results.append(extracted)

            # persist if DB available (run in thread)
            if sync_engine is not None:
                try:
                    video_row = {
                        "id": v.get("id"),
                        "channel_id": v.get("channel_id"),
                        "title": v.get("title"),
                        "url": extracted["video_url"],
                        "published_at": v.get("published_at"),
                        # Always persist the raw text. See CLAUDE.md.
                        "description": desc,
                    }

                    loop = asyncio.get_running_loop()
                    # schedule sync save to run in threadpool
                    await loop.run_in_executor(db_executor, db.save_giveaway_sync, sync_engine, video_row, extracted)
                except Exception:
                    # don't fail request on DB errors; log in real app
                    pass

    return {"query": q, "results": results, "searched": len(videos), "found": len(results)}


@router.get('/giveaways')
def list_giveaways():
    """Return saved giveaways from the DB."""
    if sync_engine is None:
        return {"results": []}

    from sqlmodel import select
    from .db import Giveaway

    with db.get_sync_session(sync_engine) as session:
        stmt = select(Giveaway).order_by(Giveaway.confidence.desc())
        rows = session.exec(stmt).all()
        results = []
        for r in rows:
            results.append({
                "id": r.id,
                "video_id": r.video_id,
                "prizes": r.prizes,
                "prizes_structured": json.loads(r.prizes_json) if r.prizes_json else [],
                "deadline": r.deadline,
                "howto": r.howto,
                "confidence": r.confidence,
                "extractor_version": r.extractor_version,
            })
        return {"results": results}
