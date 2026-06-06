"""FastAPI application for source imports and evolution runs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, status
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
    list_run_history,
    run_history_to_api,
)
from .exports import (
    ExportNotReadyError,
    ExportToolError,
    NoExportSurvivorsError,
    SurvivorVideoExport,
    SurvivorVideoExportError,
    get_survivor_video_export,
    load_survivor_video_export_file,
    start_survivor_video_export,
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
    GenerationRunningError,
    NothingToUndoError,
    ReviewCompleteError,
    ReviewError,
    UnknownCandidateError,
    get_current_review_state,
    load_candidate_artifact,
    load_candidate_thumbnail,
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

    @app.get("/runs")
    def list_runs() -> dict[str, object]:
        return {"runs": run_history_to_api(list_run_history(app.state.workspace))}

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
        except GenerationRunningError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

        return {"review": review_state_to_api(state)}

    @app.get("/runs/{run_id}/exports/survivor-video")
    def get_run_survivor_video_export(run_id: str) -> dict[str, object]:
        try:
            export = get_survivor_video_export(app.state.workspace, run_id)
        except UnknownRunError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except NoExportSurvivorsError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error

        return {"export": survivor_video_export_to_api(export)}

    @app.post(
        "/runs/{run_id}/exports/survivor-video",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_run_survivor_video_export(run_id: str) -> dict[str, object]:
        try:
            export = start_survivor_video_export(app.state.workspace, run_id)
        except UnknownRunError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except NoExportSurvivorsError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except ExportToolError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        except SurvivorVideoExportError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return {"export": survivor_video_export_to_api(export)}

    @app.get("/runs/{run_id}/exports/survivor-video/full.mp4")
    def get_run_survivor_video_file(run_id: str) -> FileResponse:
        try:
            export_file = load_survivor_video_export_file(
                app.state.workspace,
                run_id,
                "full",
            )
        except UnknownRunError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except (NoExportSurvivorsError, ExportNotReadyError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except SurvivorVideoExportError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return FileResponse(
            export_file.path,
            media_type="video/mp4",
            filename=export_file.filename,
            headers={"X-Content-SHA256": export_file.sha256},
        )

    @app.get("/runs/{run_id}/exports/survivor-video/shorts/{short_index}.mp4")
    def get_run_survivor_video_short(run_id: str, short_index: int) -> FileResponse:
        try:
            export_file = load_survivor_video_export_file(
                app.state.workspace,
                run_id,
                "short",
                short_index=short_index,
            )
        except UnknownRunError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except (NoExportSurvivorsError, ExportNotReadyError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(error),
            ) from error
        except SurvivorVideoExportError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        return FileResponse(
            export_file.path,
            media_type="video/mp4",
            filename=export_file.filename,
            headers={"X-Content-SHA256": export_file.sha256},
        )

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
        except GenerationRunningError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
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
        except GenerationRunningError as error:
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

    @app.get("/candidates/{candidate_id}/thumbnail.png")
    def get_candidate_thumbnail(
        candidate_id: str,
        size: int = Query(default=256, ge=64, le=1024),
    ) -> FileResponse:
        try:
            thumbnail = load_candidate_thumbnail(
                app.state.workspace,
                candidate_id,
                size=size,
            )
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
            thumbnail.path,
            media_type="image/png",
            headers={"X-Content-SHA256": thumbnail.sha256},
        )

    return app


def survivor_video_export_to_api(export: SurvivorVideoExport) -> dict[str, object]:
    full_video_url = (
        f"/runs/{export.run_id}/exports/survivor-video/full.mp4"
        if export.full_video_path
        else None
    )
    return {
        "runId": export.run_id,
        "status": export.status,
        "survivorCount": export.survivor_count,
        "holdMilliseconds": export.hold_milliseconds,
        "transitionMilliseconds": export.transition_milliseconds,
        "fps": export.fps,
        "fullVideo": {
            "path": export.full_video_path,
            "url": full_video_url,
            "byteSize": export.full_video_byte_size,
            "sha256": export.full_video_sha256,
        }
        if export.full_video_path
        else None,
        "shorts": [
            {
                "index": short.index,
                "startSeconds": short.start_seconds,
                "endSeconds": short.end_seconds,
                "path": short.path,
                "url": f"/runs/{export.run_id}/exports/survivor-video/shorts/{short.index}.mp4"
                if short.path
                else None,
                "byteSize": short.byte_size,
                "sha256": short.sha256,
            }
            for short in export.shorts
        ],
        "error": export.error,
        "createdAt": export.created_at,
        "updatedAt": export.updated_at,
    }
