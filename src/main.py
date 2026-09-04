from fastapi import FastAPI

from .api import router as api_router

# Environment variables are loaded in src/__init__.py

app = FastAPI(title="Prize Scout")

app.include_router(api_router, prefix="/api")


@app.get("/")
def root():
    return {"message": "Prize Scout API. Use /api/search"}
