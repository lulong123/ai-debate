from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import session, sse
from app.storage.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="AI Roundtable",
    description="AI-powered multi-perspective roundtable discussion platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(session.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(sse.router, prefix="/api/sessions", tags=["sse"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
