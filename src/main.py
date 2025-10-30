from fastapi import FastAPI
import os
from .api import router as api_router

# Environment variables are loaded in src/__init__.py

# Debug: Check if API key is loaded
api_key = os.environ.get("YOUTUBE_API_KEY")
print(f"API Key loaded: {'Yes' if api_key else 'No'}")
if api_key:
    print(f"API Key starts with: {api_key[:10]}...")

app = FastAPI(title="Prize Scout")

app.include_router(api_router, prefix="/api")

@app.get("/")
def root():
    return {"message": "Prize Scout API. Use /api/search"}
