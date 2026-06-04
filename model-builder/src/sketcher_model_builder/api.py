"""FastAPI application for source imports and evolution runs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .generations import (
    CandidateArtifactLineageError,
    DuplicateGenerationError,
    MissingGenerationError,
    NoSurvivorsError,
    RerollNotAllowedError,
    ReviewNotCompleteError,
    SourceArtifactError,
    UnknownRunError,
    create_first_generation,
    create_next_generation,
    generation_summary_to_api,
    get_current_generation,
)
from .workspace import (
    UploadValidationError,
    default_workspace_path,
    ensure_workspace,
    store_source_upload,
)
from .review import (
    CandidateArtifactError,
    CandidateNotReadyError,
    CandidateScopeError,
    DuplicateDecisionError,
    NothingToUndoError,
    ReviewCompleteError,
    ReviewError,
    UnknownCandidateError,
    get_current_review_state,
    load_candidate_artifact,
    record_candidate_decision,
    review_state_to_api,
    undo_latest_decision,
)


class ReviewDecisionRequest(BaseModel):
    candidateId: str
    decision: Literal["survived", "rejected"]


class NextGenerationRequest(BaseModel):
    mode: Literal["breed", "reroll"]


def configured_cors_origins() -> list[str]:
    configured = os.environ.get("SKETCHER_CORS_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
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

    @app.post("/runs/{run_id}/generations", status_code=status.HTTP_201_CREATED)
    def create_run_generation(run_id: str) -> dict[str, object]:
        try:
            summary = create_first_generation(app.state.workspace, run_id)
        except UnknownRunError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except DuplicateGenerationError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except SourceArtifactError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return {"generation": generation_summary_to_api(summary)}

    @app.post(
        "/runs/{run_id}/generations/next",
        status_code=status.HTTP_201_CREATED,
    )
    def create_run_next_generation(
        run_id: str,
        request: NextGenerationRequest,
    ) -> dict[str, object]:
        try:
            summary = create_next_generation(
                app.state.workspace,
                run_id,
                mode=request.mode,
            )
        except (UnknownRunError, MissingGenerationError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except (
            ReviewNotCompleteError,
            NoSurvivorsError,
            RerollNotAllowedError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except (SourceArtifactError, CandidateArtifactLineageError) as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return {"generation": generation_summary_to_api(summary)}

    @app.get("/runs/{run_id}/generations/current")
    def get_run_generation(run_id: str) -> dict[str, object]:
        try:
            summary = get_current_generation(app.state.workspace, run_id)
        except (UnknownRunError, MissingGenerationError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error

        return {"generation": generation_summary_to_api(summary)}

    @app.get("/runs/{run_id}/review/current")
    def get_run_review(run_id: str) -> dict[str, object]:
        try:
            state = get_current_review_state(app.state.workspace, run_id)
        except (UnknownRunError, MissingGenerationError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error

        return {"review": review_state_to_api(state)}

    @app.post("/runs/{run_id}/review/decisions")
    def create_review_decision(
        run_id: str,
        request: ReviewDecisionRequest,
    ) -> dict[str, object]:
        try:
            state = record_candidate_decision(
                app.state.workspace,
                run_id,
                candidate_id=request.candidateId,
                decision=request.decision,
            )
        except (UnknownRunError, MissingGenerationError, UnknownCandidateError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except (DuplicateDecisionError, ReviewCompleteError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except (CandidateScopeError, CandidateNotReadyError, ReviewError) as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return {"review": review_state_to_api(state)}

    @app.post("/runs/{run_id}/review/undo")
    def undo_review_decision(run_id: str) -> dict[str, object]:
        try:
            state = undo_latest_decision(app.state.workspace, run_id)
        except (UnknownRunError, MissingGenerationError) as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except NothingToUndoError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

        return {"review": review_state_to_api(state)}

    @app.get("/candidates/{candidate_id}/artifact")
    def get_candidate_artifact(candidate_id: str) -> FileResponse:
        try:
            artifact = load_candidate_artifact(app.state.workspace, candidate_id)
        except UnknownCandidateError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except CandidateArtifactError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

        return FileResponse(
            artifact.path,
            media_type="image/svg+xml",
            headers={"X-Content-SHA256": artifact.sha256},
        )

    return app
