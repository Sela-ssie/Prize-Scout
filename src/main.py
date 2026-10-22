from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .api import router as api_router
from .worker import lifespan

# Environment variables are loaded in src/__init__.py

app = FastAPI(title="PrizeScout", lifespan=lifespan)

app.include_router(api_router, prefix="/api")


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(request, exc):
    # SQLAlchemy exceptions may embed SQL parameters or cached source text.
    logging.getLogger(__name__).error('Database unavailable during %s', request.method)
    return JSONResponse(status_code=503, content={'detail': 'The giveaway index is temporarily unavailable. Please try again.'},
                        headers={'Retry-After': '30'})


frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    # Mount after API routes so a production build runs under one origin.
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:
    @app.get("/")
    def root():
        return {"message": "PrizeScout API. Build frontend/ or use the Vite dev server."}
