"""Candidate review state, decisions, undo, and artifact loading."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .generations import MissingGenerationError, UnknownRunError
from .workspace import connect, ensure_workspace, new_uuid7, utc_now


Decision = Literal["survived", "rejected"]


class ReviewError(ValueError):
    """Raised when candidate review cannot proceed."""


class UnknownCandidateError(ReviewError):
    """Raised when a candidate ID is not present in the workspace."""


class CandidateScopeError(ReviewError):
    """Raised when a candidate does not belong to the current review deck."""


class CandidateNotReadyError(ReviewError):
    """Raised when a candidate is not ready for review."""


class DuplicateDecisionError(ReviewError):
    """Raised when a candidate already has an active decision."""


class ReviewCompleteError(ReviewError):
    """Raised when a decision is submitted after all ready candidates are reviewed."""


class NothingToUndoError(ReviewError):
    """Raised when there is no active review decision to undo."""


class CandidateArtifactError(ReviewError):
    """Raised when a candidate artifact cannot be safely served."""


@dataclass(frozen=True)
class ReviewCandidate:
    id: str
    run_id: str
    generation_id: str
    generation_number: int
    position: int
    origin_type: str
    genome: dict[str, Any]
    artifact_path: str
    byte_size: int
    sha256: str
    validation_status: str
    validation_message: str | None
    created_at: str


@dataclass(frozen=True)
class ReviewState:
    run_id: str
    generation_id: str
    generation_number: int
    current_candidate: ReviewCandidate | None
    current_index: int
    total_ready_count: int
    survivor_count: int
    rejected_count: int
    reviewed_count: int
    complete: bool


@dataclass(frozen=True)
class CandidateArtifact:
    path: Path
    sha256: str
    byte_size: int


def get_current_review_state(workspace: Path, run_id: str) -> ReviewState:
    workspace = ensure_workspace(workspace)
    with connect(workspace) as db:
        generation = load_current_generation_row(db, run_id)
        return review_state_from_db(db, run_id, generation)


def record_candidate_decision(
    workspace: Path,
    run_id: str,
    *,
    candidate_id: str,
    decision: Decision,
) -> ReviewState:
    workspace = ensure_workspace(workspace)
    if decision not in ("survived", "rejected"):
        raise ReviewError("Decision must be survived or rejected.")

    with connect(workspace) as db:
        generation = load_current_generation_row(db, run_id)
        candidate = load_candidate_row(db, candidate_id)
        validate_candidate_in_current_generation(candidate, run_id, generation)

        state = review_state_from_db(db, run_id, generation)
        if state.complete:
            raise ReviewCompleteError("Review is already complete.")

        active_decision = db.execute(
            """
            SELECT 1 FROM candidate_decisions
            WHERE generation_id = ?
              AND candidate_id = ?
              AND undone_at IS NULL
            LIMIT 1
            """,
            (generation["id"], candidate_id),
        ).fetchone()
        if active_decision is not None:
            raise DuplicateDecisionError(
                f"Candidate {candidate_id} already has an active decision."
            )

        db.execute(
            """
            INSERT INTO candidate_decisions (
                id,
                run_id,
                generation_id,
                candidate_id,
                decision,
                created_at,
                undone_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                new_uuid7(),
                run_id,
                generation["id"],
                candidate_id,
                decision,
                utc_now(),
            ),
        )

        return review_state_from_db(db, run_id, generation)


def undo_latest_decision(workspace: Path, run_id: str) -> ReviewState:
    workspace = ensure_workspace(workspace)
    with connect(workspace) as db:
        generation = load_current_generation_row(db, run_id)
        row = db.execute(
            """
            SELECT id FROM candidate_decisions
            WHERE run_id = ?
              AND generation_id = ?
              AND undone_at IS NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (run_id, generation["id"]),
        ).fetchone()
        if row is None:
            raise NothingToUndoError("There is no active decision to undo.")

        db.execute(
            "UPDATE candidate_decisions SET undone_at = ? WHERE id = ?",
            (utc_now(), row["id"]),
        )
        return review_state_from_db(db, run_id, generation)


def load_candidate_artifact(workspace: Path, candidate_id: str) -> CandidateArtifact:
    workspace = ensure_workspace(workspace)
    with connect(workspace) as db:
        candidate = load_candidate_row(db, candidate_id)
        if candidate["validation_status"] != "ready":
            raise CandidateArtifactError("Candidate artifact is not available.")
        if not candidate["artifact_path"] or not candidate["sha256"]:
            raise CandidateArtifactError("Candidate artifact metadata is incomplete.")

        relative_path = Path(candidate["artifact_path"])
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.parts[:2] != ("artifacts", "candidates")
        ):
            raise CandidateArtifactError(
                "Candidate artifact path is not a valid workspace-relative candidate path."
            )

        candidate_root = (workspace / "artifacts" / "candidates").resolve()
        artifact_path = (workspace / relative_path).resolve()
        try:
            artifact_path.relative_to(candidate_root)
        except ValueError as error:
            raise CandidateArtifactError(
                "Candidate artifact path points outside the candidate artifact directory."
            ) from error

        if not artifact_path.exists() or not artifact_path.is_file():
            raise CandidateArtifactError("Candidate artifact file is missing.")

        data = artifact_path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != candidate["sha256"]:
            raise CandidateArtifactError("Candidate artifact hash does not match metadata.")

        return CandidateArtifact(
            path=artifact_path,
            sha256=sha256,
            byte_size=len(data),
        )


def load_current_generation_row(
    db: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row:
    run = db.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise UnknownRunError(f"Run {run_id} was not found.")

    generation = db.execute(
        """
        SELECT * FROM generations
        WHERE run_id = ?
        ORDER BY generation_number DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if generation is None:
        raise MissingGenerationError(f"Run {run_id} does not have a generation yet.")

    return generation


def load_candidate_row(db: sqlite3.Connection, candidate_id: str) -> sqlite3.Row:
    candidate = db.execute(
        "SELECT * FROM candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        raise UnknownCandidateError(f"Candidate {candidate_id} was not found.")
    return candidate


def validate_candidate_in_current_generation(
    candidate: sqlite3.Row,
    run_id: str,
    generation: sqlite3.Row,
) -> None:
    if candidate["run_id"] != run_id:
        raise CandidateScopeError(
            f"Candidate {candidate['id']} does not belong to run {run_id}."
        )
    if candidate["generation_id"] != generation["id"]:
        raise CandidateScopeError(
            f"Candidate {candidate['id']} does not belong to the current generation."
        )
    if candidate["validation_status"] != "ready":
        raise CandidateNotReadyError(
            f"Candidate {candidate['id']} is not ready for review."
        )


def review_state_from_db(
    db: sqlite3.Connection,
    run_id: str,
    generation: sqlite3.Row,
) -> ReviewState:
    candidate_rows = db.execute(
        """
        SELECT * FROM candidates
        WHERE generation_id = ?
          AND validation_status = 'ready'
        ORDER BY position
        """,
        (generation["id"],),
    ).fetchall()
    candidates = [review_candidate_from_row(row) for row in candidate_rows]

    active_rows = db.execute(
        """
        SELECT * FROM candidate_decisions
        WHERE generation_id = ?
          AND undone_at IS NULL
        ORDER BY created_at, id
        """,
        (generation["id"],),
    ).fetchall()
    active_decisions = {
        row["candidate_id"]: row["decision"]
        for row in active_rows
    }

    current_candidate: ReviewCandidate | None = None
    current_index = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        if candidate.id not in active_decisions:
            current_candidate = candidate
            current_index = index
            break

    survivor_count = sum(
        1 for decision in active_decisions.values() if decision == "survived"
    )
    rejected_count = sum(
        1 for decision in active_decisions.values() if decision == "rejected"
    )
    reviewed_count = survivor_count + rejected_count

    return ReviewState(
        run_id=run_id,
        generation_id=generation["id"],
        generation_number=generation["generation_number"],
        current_candidate=current_candidate,
        current_index=current_index if candidates else 0,
        total_ready_count=len(candidates),
        survivor_count=survivor_count,
        rejected_count=rejected_count,
        reviewed_count=reviewed_count,
        complete=bool(candidates) and reviewed_count >= len(candidates),
    )


def review_candidate_from_row(row: sqlite3.Row) -> ReviewCandidate:
    return ReviewCandidate(
        id=row["id"],
        run_id=row["run_id"],
        generation_id=row["generation_id"],
        generation_number=row["generation_number"],
        position=row["position"],
        origin_type=row["origin_type"],
        genome=json.loads(row["genome_json"]),
        artifact_path=row["artifact_path"],
        byte_size=row["byte_size"],
        sha256=row["sha256"],
        validation_status=row["validation_status"],
        validation_message=row["validation_message"],
        created_at=row["created_at"],
    )


def review_state_to_api(state: ReviewState) -> dict[str, Any]:
    return {
        "runId": state.run_id,
        "generationId": state.generation_id,
        "generationNumber": state.generation_number,
        "currentCandidate": review_candidate_to_api(state.current_candidate)
        if state.current_candidate
        else None,
        "currentIndex": state.current_index,
        "totalReadyCount": state.total_ready_count,
        "survivorCount": state.survivor_count,
        "rejectedCount": state.rejected_count,
        "reviewedCount": state.reviewed_count,
        "complete": state.complete,
    }


def review_candidate_to_api(candidate: ReviewCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "runId": candidate.run_id,
        "generationId": candidate.generation_id,
        "generationNumber": candidate.generation_number,
        "position": candidate.position,
        "originType": candidate.origin_type,
        "genome": candidate.genome,
        "artifactPath": candidate.artifact_path,
        "byteSize": candidate.byte_size,
        "sha256": candidate.sha256,
        "validationStatus": candidate.validation_status,
        "validationMessage": candidate.validation_message,
        "createdAt": candidate.created_at,
    }
