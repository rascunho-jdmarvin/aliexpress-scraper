import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.products import router as products_router
from app.api.routes.clients import router as clients_router
from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AliExpress Scraper API starting up")
    yield
    logger.info("AliExpress Scraper API shut down")


app = FastAPI(
    title="AliExpress Scraper API",
    description="Scrapes AliExpress product pages and stores structured data in Supabase.",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products_router)
app.include_router(clients_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
