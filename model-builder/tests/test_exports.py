import hashlib
import json
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


def test_survivor_frame_uses_candidate_preview_render_method(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = seeded_export_workspace(tmp_path)
    survivor = exports.load_export_survivors(workspace, "run-1")[0]
    directory = exports.export_directory(workspace, "run-1")
    render_calls: list[dict] = []

    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.execute(
            """
            UPDATE candidates
            SET genome_json = ?
            WHERE id = ?
            """,
            (
                json.dumps(
                    {
                        "renderParameters": {
                            "repeats": 200,
                            "shade_strokes": 99,
                            "full_retrace_interval": 8,
                            "flick_probability": 0.65,
                        }
                    }
                ),
                survivor.candidate_id,
            ),
        )
    survivor = exports.load_export_survivors(workspace, "run-1")[0]

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        render_calls.append(
            {
                "source_path": source_path,
                "artifact_path": artifact_path,
                "render_parameters": render_parameters,
            }
        )
        artifact_path.write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="preview-render" d="M 1 1 L 9 9" />
</svg>
""",
            encoding="utf-8",
        )

    def fake_svg2png(
        *,
        url: str,
        write_to: str,
        output_width: int,
        output_height: int,
        background_color: str,
    ) -> None:
        assert Path(url) != workspace / survivor.artifact_path
        assert Path(url).name.endswith(".tmp.svg")
        assert "preview-render" in Path(url).read_text(encoding="utf-8")
        assert output_width == exports.FULL_VIDEO_WIDTH
        assert output_height == exports.FULL_VIDEO_HEIGHT
        assert background_color == "white"
        Path(write_to).write_bytes(b"\x89PNG\r\n\x1a\nframe")

    monkeypatch.setattr(exports, "render_candidate_svg", fake_render_candidate_svg)
    monkeypatch.setattr(exports.cairosvg, "svg2png", fake_svg2png)

    frame = exports.render_survivor_frame(workspace, directory, survivor)

    assert frame.exists()
    assert frame.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert len(render_calls) == 1
    assert render_calls[0]["source_path"].parts[-2] == "sources"
    assert render_calls[0]["artifact_path"].name.endswith(".tmp.svg")
    assert render_calls[0]["render_parameters"]["repeats"] == 3
    assert render_calls[0]["render_parameters"]["shade_strokes"] == 0
    assert render_calls[0]["render_parameters"]["full_retrace_interval"] == 0
    assert render_calls[0]["render_parameters"]["flick_probability"] == 0.65
    assert not render_calls[0]["artifact_path"].exists()


def seeded_export_workspace(tmp_path: Path) -> Path:
    workspace = ensure_workspace(tmp_path / "workspace")
    source_path = Path("artifacts/sources/source.svg")
    source_artifact = workspace / source_path
    source_artifact.parent.mkdir(parents=True, exist_ok=True)
    source_artifact.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="source" d="M 1 1 L 9 9" />
</svg>
""",
        encoding="utf-8",
    )
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
                source_artifact.stat().st_size,
                hashlib.sha256(source_artifact.read_bytes()).hexdigest(),
                source_path.as_posix(),
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
