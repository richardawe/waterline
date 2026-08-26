from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import deals, institutions, loan_tapes, spv
from app.config import get_settings

settings = get_settings()

app = FastAPI(title="Waterline API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(institutions.router)
app.include_router(deals.router)
app.include_router(loan_tapes.router)
app.include_router(loan_tapes.private_router)
app.include_router(spv.router)


@app.get("/health")
def health():
    return {"status": "ok"}
