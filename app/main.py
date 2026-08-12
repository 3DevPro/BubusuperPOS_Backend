from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings

app = FastAPI(title="Loyverse Clone API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Must exist before StaticFiles mounts it — a fresh checkout has no media/
# directory until the first upload, or here on startup.
Path("media/products").mkdir(parents=True, exist_ok=True)
app.mount("/api/v1/media", StaticFiles(directory="media"), name="media")
