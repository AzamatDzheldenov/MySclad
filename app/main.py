from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.routes import router as warehouse_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers

FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_TITLE,
        version="1.0.0",
        description="API управления складскими запасами и заказами с защитой от Race Condition",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(warehouse_router)

    @app.get("/", include_in_schema=False)
    async def serve_crm_frontend() -> FileResponse:
        return FileResponse(FRONTEND_INDEX)

    @app.get("/health", tags=["System"])
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
