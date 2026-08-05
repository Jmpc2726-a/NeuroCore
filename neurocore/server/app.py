import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from neurocore.config import config
from neurocore.server.routes import api_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_directories()
    yield

def create_app() -> FastAPI:
    app = FastAPI(
        title="NeuroCore Code Agent API",
        description="Autonomous AI Coding & Daily Productivity Assistant REST & SSE API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = os.path.join(os.path.dirname(__file__), "..", "web", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates", "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        # Default Cyberpunk Web Studio fallback html
        fallback_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>NeuroCore Web Studio</title>
    <style>
        body { background-color: #090D16; color: #00F0FF; font-family: monospace; padding: 2rem; }
        h1 { color: #8B5CF6; }
    </style>
</head>
<body>
    <h1>NeuroCore Code Agent — Futuristic Web Studio</h1>
    <p>Backend API & SSE activity streams online. API Root: <code>/api/v1</code></p>
</body>
</html>"""
        return HTMLResponse(content=fallback_html, status_code=200)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "status": "error", "path": str(request.url.path)}
        )

    app.include_router(api_router, prefix="/api/v1")

    return app

app = create_app()
