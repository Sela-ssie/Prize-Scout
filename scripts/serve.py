#!/usr/bin/env python3
"""Serve the built UI/API; optional ingestion is managed by the application lifespan."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src  # noqa: E402,F401 - load .env before reading configuration


def main():
    import uvicorn
    try:
        port = int(os.getenv('PORT', '8000'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit('PORT must be between 1 and 65535') from None
    uvicorn.run('src.main:app', host=os.getenv('HOST', '127.0.0.1'), port=port, workers=1)


if __name__ == '__main__':
    main()
