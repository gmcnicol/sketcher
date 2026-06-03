"""FastAPI application for source imports and evolution runs."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

from .workspace import (
    UploadValidationError,
    default_workspace_path,
    ensure_workspace,
    store_source_upload,
)


def configured_cors_origins() -> list[str]:
    configured = os.environ.get("SKETCHER_CORS_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def create_app(workspace: Path | None = None) -> FastAPI:
    resolved_workspace = ensure_workspace(workspace or default_workspace_path())
    app = FastAPI(title="Sketcher Model Builder")
    app.state.workspace = resolved_workspace

    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "workspacePath": str(app.state.workspace),
        }

    @app.post("/sources", status_code=status.HTTP_201_CREATED)
    async def create_source(file: UploadFile = File(...)) -> dict[str, object]:
        data = await file.read()
        try:
            result = store_source_upload(
                app.state.workspace,
                filename=file.filename or "source.svg",
                content_type=file.content_type or "",
                data=data,
            )
        except UploadValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return {
            "source": {
                "id": result.source.id,
                "filename": result.source.filename,
                "sha256": result.source.sha256,
                "byteSize": result.source.byte_size,
                "artifactPath": result.source.artifact_path,
            },
            "run": {
                "id": result.run.id,
                "sourceId": result.run.source_id,
                "status": result.run.status,
            },
        }

    return app
