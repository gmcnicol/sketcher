import hashlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sketcher_model_builder import exports
from sketcher_model_builder.api import create_app
from sketcher_model_builder.workspace import ensure_workspace


def test_survivor_video_export_status_uses_active_survivors_in_decision_order(
    tmp_path: Path,
) -> None:
    workspace = seeded_export_workspace(tmp_path)

    export = exports.get_survivor_video_export(workspace, "run-1")

    assert export.status == "not_started"
    assert export.survivor_count == 2
    assert export.hold_milliseconds == 500
    assert export.transition_milliseconds == 500
    survivors = exports.load_export_survivors(workspace, "run-1")
    assert [survivor.candidate_id for survivor in survivors] == [
        "candidate-2",
        "candidate-1",
    ]


def test_survivor_video_export_file_endpoint_serves_completed_full_video(
    tmp_path: Path,
) -> None:
    workspace = seeded_export_workspace(tmp_path)
    export_directory = exports.export_directory(workspace, "run-1")
    export_directory.mkdir(parents=True)
    video_path = exports.full_video_path(export_directory)
    video_path.write_bytes(b"fake mp4 bytes")
    exports.write_status_file(
        export_directory,
        {
            "status": "complete",
            "runId": "run-1",
            "survivorCount": 2,
            "createdAt": "2026-06-06T00:00:00+00:00",
            "updatedAt": "2026-06-06T00:00:01+00:00",
            "error": None,
        },
    )
    client = TestClient(create_app(workspace))

    response = client.get("/runs/run-1/exports/survivor-video/full.mp4")

    assert response.status_code == 200
    assert response.content == b"fake mp4 bytes"
    assert response.headers["X-Content-SHA256"] == hashlib.sha256(
        b"fake mp4 bytes"
    ).hexdigest()


def test_short_start_times_use_overlapping_sixty_second_cuts() -> None:
    assert exports.short_start_times(30) == [0.0]
    starts = exports.short_start_times(130)
    assert starts == [0.0, 55.0, 70.0]
    assert starts[-1] + exports.SHORT_SECONDS == 130


def seeded_export_workspace(tmp_path: Path) -> Path:
    workspace = ensure_workspace(tmp_path / "workspace")
    artifact_path = Path("artifacts/candidates/run-1/generation-1")
    artifact_directory = workspace / artifact_path
    artifact_directory.mkdir(parents=True)
    candidate_1_path = artifact_path / "001-candidate-1.svg"
    candidate_2_path = artifact_path / "002-candidate-2.svg"
    candidate_3_path = artifact_path / "003-candidate-3.svg"
    candidate_1_sha = write_svg(workspace / candidate_1_path, "candidate-1")
    candidate_2_sha = write_svg(workspace / candidate_2_path, "candidate-2")
    candidate_3_sha = write_svg(workspace / candidate_3_path, "candidate-3")

    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.execute(
            """
            INSERT INTO sources (
                id,
                original_filename,
                content_type,
                byte_size,
                sha256,
                artifact_path,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-1",
                "source.svg",
                "image/svg+xml",
                10,
                "a" * 64,
                "artifacts/sources/source.svg",
                "2026-06-06T00:00:00+00:00",
            ),
        )
        db.execute(
            """
            INSERT INTO runs (id, source_id, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("run-1", "source-1", "active", "2026-06-06T00:00:00+00:00"),
        )
        db.execute(
            """
            INSERT INTO generations (id, run_id, generation_number, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "generation-1",
                "run-1",
                1,
                "ready",
                "2026-06-06T00:00:00+00:00",
            ),
        )
        for position, candidate_id, relative_path, sha in [
            (1, "candidate-1", candidate_1_path, candidate_1_sha),
            (2, "candidate-2", candidate_2_path, candidate_2_sha),
            (3, "candidate-3", candidate_3_path, candidate_3_sha),
        ]:
            db.execute(
                """
                INSERT INTO candidates (
                    id,
                    run_id,
                    generation_id,
                    generation_number,
                    position,
                    origin_type,
                    genome_json,
                    artifact_path,
                    byte_size,
                    sha256,
                    validation_status,
                    validation_message,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    "run-1",
                    "generation-1",
                    1,
                    position,
                    "preset_mutation",
                    "{}",
                    relative_path.as_posix(),
                    (workspace / relative_path).stat().st_size,
                    sha,
                    "ready",
                    "ok",
                    f"2026-06-06T00:00:0{position}+00:00",
                ),
            )
        for decision_id, candidate_id, decision, created_at, undone_at in [
            ("decision-1", "candidate-1", "survived", "2026-06-06T00:00:03+00:00", None),
            ("decision-2", "candidate-2", "survived", "2026-06-06T00:00:02+00:00", None),
            ("decision-3", "candidate-3", "survived", "2026-06-06T00:00:01+00:00", "2026-06-06T00:00:04+00:00"),
        ]:
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
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    "run-1",
                    "generation-1",
                    candidate_id,
                    decision,
                    created_at,
                    undone_at,
                ),
            )
    return workspace


def write_svg(path: Path, candidate_id: str) -> str:
    data = f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="{candidate_id}" d="M 1 1 L 9 9" />
</svg>
""".encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
