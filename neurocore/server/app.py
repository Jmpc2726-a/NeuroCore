import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from neurocore.config import config
from neurocore.server.routes import api_router
from neurocore.server.security import SecurityMiddleware
from neurocore.tools.chart_tools import warm_up as warm_up_charts

logger = logging.getLogger("neurocore.server")

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
STATIC_DIR = os.path.join(WEB_DIR, "static")
INDEX_PATH = os.path.join(WEB_DIR, "templates", "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_directories()
    # Carrega o matplotlib em segundo plano para o primeiro gráfico não demorar.
    asyncio.get_running_loop().run_in_executor(None, warm_up_charts)
    if config.PUBLIC_MODE and not config.ACCESS_TOKEN:
        logger.warning(
            "NeuroCore em modo público sem NEUROCORE_ACCESS_TOKEN: qualquer visitante pode usar "
            "as chaves de API configuradas no servidor."
        )
    yield


def create_app() -> FastAPI:
    show_docs = not config.PUBLIC_MODE
    app = FastAPI(
        title="NeuroCore Code Agent API",
        description="Autonomous AI Coding & Daily Productivity Assistant REST & SSE API",
        version="1.2.0",
        lifespan=lifespan,
        docs_url="/docs" if show_docs else None,
        redoc_url="/redoc" if show_docs else None,
        openapi_url="/openapi.json" if show_docs else None,
    )

    if config.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.CORS_ORIGINS,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "Authorization", "X-NeuroCore-Token", "X-Client-Id", "X-Filename"],
        )
    app.add_middleware(SecurityMiddleware)

    if os.path.exists(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        if os.path.exists(INDEX_PATH):
            return FileResponse(INDEX_PATH, media_type="text/html", headers={"Cache-Control": "no-cache"})
        fallback_html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>NeuroCore</title>
    <style>
        body { background-color: #0d1210; color: #d7e4dc; font-family: system-ui, sans-serif; padding: 2rem; }
        h1 { color: #4ade80; }
    </style>
</head>
<body>
    <h1>NeuroCore Code Agent</h1>
    <p>API online em <code>/api/v1</code>. A interface web não foi encontrada.</p>
</body>
</html>"""
        return HTMLResponse(content=fallback_html, status_code=200)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        # O detalhe vai para o log do servidor, nunca para o cliente.
        logger.exception("Erro não tratado em %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "Erro interno do servidor.", "status": "error", "path": str(request.url.path)},
        )

    if config.PUBLIC_MODE:
        @app.middleware("http")
        async def hsts(request: Request, call_next):
            response = await call_next(request)
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            return response

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
