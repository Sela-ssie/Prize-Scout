"""Local index API. User requests never consume YouTube quota."""
import os
from functools import lru_cache
from typing import Literal
from fastapi import APIRouter, Query, Request
from sqlalchemy import func
from sqlmodel import Session, select
from . import db
from .categories import Category

router = APIRouter()


@lru_cache(maxsize=1)
def get_engine():
    return db.create_db_and_tables(os.getenv('DATABASE_URL', 'sqlite:///./prizes.db'))


@router.get('/search')
@router.get('/giveaways')
def search(q: str = Query('', max_length=200), page: int = Query(1, ge=1),
           page_size: int = Query(20, ge=1, le=100), min_confidence: float = Query(0, ge=0, le=1),
           availability: Literal['all', 'active', 'unknown', 'expired'] = 'all',
           category: Category = 'all', sort: Literal['confidence', 'newest', 'deadline'] = 'confidence',
           group_campaigns: bool = False, current_only: bool = False):
    return db.list_index(get_engine(), q, page, page_size, min_confidence, availability, category, sort, group_campaigns, current_only)


@router.get('/categories')
def categories(current_only: bool = False):
    return {'categories': db.category_counts(get_engine(), current_only)}


@router.get('/promotions')
def promotions(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return db.list_promotions(get_engine(), page, page_size)


@router.get('/coverage')
def coverage():
    from .coverage import coverage_summary
    return coverage_summary(get_engine())


@router.get('/health')
def health(request: Request):
    with Session(get_engine()) as session:
        videos = session.exec(select(func.count()).select_from(db.Video)).one()
        giveaways = session.exec(select(func.count()).select_from(db.Giveaway)).one()
    return {'status': 'ok', 'indexed_videos': videos, 'giveaways': giveaways,
            'ingestion': {'enabled': getattr(request.app.state, 'ingestion_enabled', False),
                          'running': getattr(request.app.state, 'ingestion_running', False),
                          'last_report': getattr(request.app.state, 'ingestion_last_report', None)}}
