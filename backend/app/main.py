from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import blog_admin, deals, downloads, institutions, loan_tapes, spv
from app.collections.api import client as collections_client
from app.collections.api import debtor as collections_debtor
from app.collections.api import files as collections_files
from app.collections.api import jobs as collections_jobs
from app.collections.api import ops as collections_ops
from app.collections.api import public as collections_public
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
app.include_router(blog_admin.router)

# Waterline Collections — its own auth (client/ops sessions, debtor token
# links), deliberately not behind the admin Basic Auth used above.
app.include_router(collections_public.router)
app.include_router(collections_client.router)
app.include_router(collections_ops.router)
app.include_router(collections_debtor.router)
app.include_router(collections_files.router)
app.include_router(collections_jobs.router)


@app.get("/health")
def health():
    return {"status": "ok"}
