import hashlib
import sqlite3
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from sketcher_model_builder.api import create_app
from sketcher_model_builder.workspace import ensure_workspace


VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'


def row_count(workspace: Path, table: str) -> int:
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_workspace_initialization_creates_directories_and_database(tmp_path: Path) -> None:
    workspace = ensure_workspace(tmp_path / "workspace")

    assert (workspace / "sketcher.sqlite3").exists()
    assert (workspace / "artifacts" / "sources").is_dir()
    assert (workspace / "artifacts" / "candidates").is_dir()
    assert (workspace / "models").is_dir()

    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        schema_version = db.execute(
            "SELECT value FROM workspace_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert schema_version == "3"


def test_valid_svg_upload_creates_source_run_and_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", VALID_SVG, "image/svg+xml")},
    )

    assert response.status_code == 201
    body = response.json()
    source = body["source"]
    run = body["run"]

    assert uuid.UUID(source["id"]).version == 7
    assert uuid.UUID(run["id"]).version == 7
    assert run == {
        "id": run["id"],
        "sourceId": source["id"],
        "status": "active",
    }
    assert source["filename"] == "example.svg"
    assert source["byteSize"] == len(VALID_SVG)
    assert source["sha256"] == hashlib.sha256(VALID_SVG).hexdigest()
    assert source["artifactPath"].startswith("artifacts/sources/")
    assert not Path(source["artifactPath"]).is_absolute()

    artifact = workspace / source["artifactPath"]
    assert artifact.read_bytes() == VALID_SVG

    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.row_factory = sqlite3.Row
        source_row = db.execute("SELECT * FROM sources").fetchone()
        run_row = db.execute("SELECT * FROM runs").fetchone()

    assert source_row["id"] == source["id"]
    assert source_row["sha256"] == source["sha256"]
    assert source_row["artifact_path"] == source["artifactPath"]
    assert run_row["source_id"] == source["id"]
    assert run_row["status"] == "active"


def test_invalid_content_type_creates_no_records_or_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", VALID_SVG, "text/plain")},
    )

    assert response.status_code == 400
    assert row_count(workspace, "sources") == 0
    assert row_count(workspace, "runs") == 0
    assert list((workspace / "artifacts" / "sources").iterdir()) == []


def test_non_svg_body_creates_no_records_or_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", b"not svg", "image/svg+xml")},
    )

    assert response.status_code == 400
    assert row_count(workspace, "sources") == 0
    assert row_count(workspace, "runs") == 0
    assert list((workspace / "artifacts" / "sources").iterdir()) == []


def test_health_returns_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "workspacePath": str(workspace.resolve()),
    }
