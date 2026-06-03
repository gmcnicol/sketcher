import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from sketcher_model_builder import generations
from sketcher_model_builder.api import create_app


VALID_SOURCE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="square" d="M 1 1 H 9 V 9 H 1 Z" />
</svg>
"""

EMPTY_SOURCE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'


def upload_source(client: TestClient, svg: bytes = VALID_SOURCE_SVG) -> dict:
    response = client.post(
        "/sources",
        files={"file": ("source.svg", svg, "image/svg+xml")},
    )
    assert response.status_code == 201
    return response.json()


def fetch_rows(workspace: Path, table: str) -> list[sqlite3.Row]:
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.row_factory = sqlite3.Row
        return db.execute(f"SELECT * FROM {table} ORDER BY created_at, id").fetchall()


def test_generation_endpoint_creates_first_generation_with_24_ready_candidates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client)
    run_id = upload["run"]["id"]

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 201
    generation = response.json()["generation"]
    assert uuid.UUID(generation["id"]).version == 7
    assert generation["runId"] == run_id
    assert generation["generationNumber"] == 1
    assert generation["status"] == "ready"
    assert generation["totalCandidateCount"] == 24
    assert generation["readyCount"] == 24
    assert generation["failedCount"] == 0
    assert len(generation["candidates"]) == 24

    current_response = client.get(f"/runs/{run_id}/generations/current")
    assert current_response.status_code == 200
    assert current_response.json()["generation"]["id"] == generation["id"]

    rows = fetch_rows(workspace, "candidates")
    assert len(rows) == 24
    strategy_families = {
        json.loads(row["genome_json"])["strategyFamily"] for row in rows
    }
    assert strategy_families == {
        "outline_retrace",
        "directional_fill",
        "vertical_flow",
    }

    for row in rows:
        assert uuid.UUID(row["id"]).version == 7
        artifact_path = Path(row["artifact_path"])
        assert not artifact_path.is_absolute()
        assert artifact_path.parts[:2] == ("artifacts", "candidates")
        artifact_bytes = (workspace / artifact_path).read_bytes()
        assert row["byte_size"] == len(artifact_bytes)
        assert row["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()

        genome = json.loads(row["genome_json"])
        assert genome["strategyFamily"]
        assert isinstance(genome["seed"], int)
        assert genome["renderParameters"]["seed"] == genome["seed"]


def test_duplicate_generation_creation_returns_conflict_without_extra_candidates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]

    first_response = client.post(f"/runs/{run_id}/generations")
    duplicate_response = client.post(f"/runs/{run_id}/generations")

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert len(fetch_rows(workspace, "generations")) == 1
    assert len(fetch_rows(workspace, "candidates")) == 24


def test_unknown_run_generation_returns_not_found(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "workspace"))

    response = client.post(f"/runs/{uuid.uuid4()}/generations")

    assert response.status_code == 404


def test_invalid_source_artifact_returns_bad_request_without_partial_generation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client, EMPTY_SOURCE_SVG)["run"]["id"]

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 400
    assert fetch_rows(workspace, "generations") == []
    assert fetch_rows(workspace, "candidates") == []


def test_missing_source_artifact_returns_bad_request_without_partial_generation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client)
    run_id = upload["run"]["id"]
    (workspace / upload["source"]["artifactPath"]).unlink()

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 400
    assert fetch_rows(workspace, "generations") == []
    assert fetch_rows(workspace, "candidates") == []


def test_failed_render_attempts_are_persisted_and_excluded_from_ready_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    calls = 0

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("forced render failure")
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path d="M 1 1 L 9 9" style="fill:none;stroke:#111;stroke-width:1;stroke-opacity:1" data-sketcher-pass="1" />
</svg>
""",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        generations,
        "render_candidate_svg",
        fake_render_candidate_svg,
    )

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 201
    generation = response.json()["generation"]
    assert generation["status"] == "ready"
    assert generation["totalCandidateCount"] == 26
    assert generation["readyCount"] == 24
    assert generation["failedCount"] == 2

    rows = fetch_rows(workspace, "candidates")
    failed = [row for row in rows if row["validation_status"] == "failed"]
    ready = [row for row in rows if row["validation_status"] == "ready"]
    assert len(failed) == 2
    assert len(ready) == 24
    assert {row["validation_message"] for row in failed} == {"forced render failure"}
