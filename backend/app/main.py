from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import deals, downloads, institutions, loan_tapes, spv
from app.config import get_settings

settings = get_settings()
is_production = settings.environment.lower() == "production"

app = FastAPI(
    title="Waterline API",
    version="0.1.0",
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_api_caching(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

app.include_router(institutions.router)
app.include_router(deals.router)
app.include_router(loan_tapes.router)
app.include_router(loan_tapes.private_router)
app.include_router(spv.router)
app.include_router(downloads.router)


@app.get("/health")
def health():
    return {"status": "ok"}
