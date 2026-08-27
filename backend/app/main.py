"""
SQB Process Hub — FastAPI Backend
Main application entry point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import processes, import_export, analytics

app = FastAPI(
    title="SQB Process Hub API",
    description=(
        "Backend API для платформы автоматизации бизнес-процессов "
        "АКБ «Узпромстройбанк» (SQB). "
        "Обеспечивает парсинг draw.io диаграмм, управление PIX-реестрами "
        "и интеграцию с Infomaximum Processet."
    ),
    version="1.0.0",
    contact={
        "name": "SQB Digital Banking Dept.",
        "url": "https://sqb.uz",
    },
    license_info={
        "name": "Proprietary — АКБ «Узпромстройбанк»",
    },
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ──────────────── CORS ────────────────
# Allow local dev (React Vite :5173) and production domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "https://sqb-process-hub.vercel.app",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://.+\.(e2b\.app|vercel\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────── Routers ────────────────
app.include_router(processes.router,      prefix="/api/v1")
app.include_router(import_export.router,  prefix="/api/v1")
app.include_router(analytics.router,      prefix="/api/v1")


# ──────────────── Health / Root ────────────────
@app.get("/", tags=["health"], summary="Root health check")
def root():
    return JSONResponse({
        "service": "SQB Process Hub API",
        "version": "1.0.0",
        "status": "online",
        "docs": "/api/docs"
    })


@app.get("/api/health", tags=["health"], summary="Health check endpoint")
def health():
    return {"status": "ok", "version": "1.0.0"}
