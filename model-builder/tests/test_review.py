import hashlib
import sqlite3
import threading
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from sketcher_model_builder import generations
from sketcher_model_builder import review as review_module
from sketcher_model_builder.api import create_app


VALID_SOURCE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="square" d="M 1 1 H 9 V 9 H 1 Z" />
</svg>
"""


def upload_source(client: TestClient) -> dict:
    response = client.post(
        "/sources",
        files={"file": ("source.svg", VALID_SOURCE_SVG, "image/svg+xml")},
    )
    assert response.status_code == 201
    return response.json()


def create_generation(client: TestClient, run_id: str) -> dict:
    response = client.post(f"/runs/{run_id}/generations")
    assert response.status_code == 201
    return response.json()["generation"]


def fetch_rows(workspace: Path, table: str) -> list[sqlite3.Row]:
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.row_factory = sqlite3.Row
        return db.execute(f"SELECT * FROM {table} ORDER BY created_at, id").fetchall()


def update_candidate(
    workspace: Path,
    candidate_id: str,
    **values: object,
) -> None:
    assignments = ", ".join(f"{key} = ?" for key in values)
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.execute(
            f"UPDATE candidates SET {assignments} WHERE id = ?",
            (*values.values(), candidate_id),
        )


def test_review_state_returns_first_ready_candidate_after_generation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = create_generation(client, run_id)

    response = client.get(f"/runs/{run_id}/review/current")

    assert response.status_code == 200
    review = response.json()["review"]
    first_candidate = generation["candidates"][0]
    assert review["runId"] == run_id
    assert review["generationId"] == generation["id"]
    assert review["generationNumber"] == 1
    assert review["currentIndex"] == 1
    assert review["totalReadyCount"] == 24
    assert review["survivorCount"] == 0
    assert review["rejectedCount"] == 0
    assert review["reviewedCount"] == 0
    assert review["complete"] is False
    assert review["currentCandidate"]["id"] == first_candidate["id"]
    assert review["currentCandidate"]["position"] == first_candidate["position"]
    assert review["currentCandidate"]["originType"] == "preset_mutation"
    assert review["currentCandidate"]["artifactPath"].startswith(
        "artifacts/candidates/"
    )
    assert review["currentCandidate"]["genome"]["schemaVersion"] == 1


def test_survived_decision_persists_and_advances_to_next_candidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = create_generation(client, run_id)
    first_candidate = generation["candidates"][0]
    second_candidate = generation["candidates"][1]

    response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": first_candidate["id"], "decision": "survived"},
    )

    assert response.status_code == 200
    review = response.json()["review"]
    assert review["currentCandidate"]["id"] == second_candidate["id"]
    assert review["currentIndex"] == 2
    assert review["reviewedCount"] == 1
    assert review["survivorCount"] == 1
    assert review["rejectedCount"] == 0

    decisions = fetch_rows(workspace, "candidate_decisions")
    assert len(decisions) == 1
    assert uuid.UUID(decisions[0]["id"]).version == 7
    assert decisions[0]["run_id"] == run_id
    assert decisions[0]["generation_id"] == generation["id"]
    assert decisions[0]["candidate_id"] == first_candidate["id"]
    assert decisions[0]["decision"] == "survived"
    assert decisions[0]["undone_at"] is None


def test_rejected_decision_persists_without_increasing_survivor_count(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = create_generation(client, run_id)
    first_candidate = generation["candidates"][0]

    response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": first_candidate["id"], "decision": "rejected"},
    )

    assert response.status_code == 200
    review = response.json()["review"]
    assert review["reviewedCount"] == 1
    assert review["survivorCount"] == 0
    assert review["rejectedCount"] == 1
    assert fetch_rows(workspace, "candidate_decisions")[0]["decision"] == "rejected"


def test_duplicate_active_decision_returns_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidate_id = create_generation(client, run_id)["candidates"][0]["id"]

    first_response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": candidate_id, "decision": "survived"},
    )
    duplicate_response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": candidate_id, "decision": "rejected"},
    )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 409
    assert "already has an active decision" in duplicate_response.json()["detail"]
    assert len(fetch_rows(workspace, "candidate_decisions")) == 1


def test_undo_marks_latest_active_decision_undone_and_returns_to_that_candidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidates = create_generation(client, run_id)["candidates"]

    client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": candidates[0]["id"], "decision": "survived"},
    )
    client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": candidates[1]["id"], "decision": "rejected"},
    )

    response = client.post(f"/runs/{run_id}/review/undo")

    assert response.status_code == 200
    review = response.json()["review"]
    assert review["currentCandidate"]["id"] == candidates[1]["id"]
    assert review["currentIndex"] == 2
    assert review["reviewedCount"] == 1
    assert review["survivorCount"] == 1
    assert review["rejectedCount"] == 0

    decisions = fetch_rows(workspace, "candidate_decisions")
    assert decisions[0]["candidate_id"] == candidates[0]["id"]
    assert decisions[0]["undone_at"] is None
    assert decisions[1]["candidate_id"] == candidates[1]["id"]
    assert decisions[1]["undone_at"] is not None


def test_undo_without_active_decisions_returns_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    create_generation(client, run_id)

    response = client.post(f"/runs/{run_id}/review/undo")

    assert response.status_code == 409
    assert "no active decision" in response.json()["detail"]


def test_failed_candidates_are_excluded_from_review_and_cannot_be_decided(
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

    generation = create_generation(client, run_id)
    failed_candidate = next(
        candidate
        for candidate in generation["candidates"]
        if candidate["validationStatus"] == "failed"
    )

    review_response = client.get(f"/runs/{run_id}/review/current")
    decision_response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": failed_candidate["id"], "decision": "survived"},
    )

    review = review_response.json()["review"]
    assert review_response.status_code == 200
    assert review["totalReadyCount"] == 24
    assert review["currentIndex"] == 1
    assert review["currentCandidate"]["position"] == 3
    assert decision_response.status_code == 400
    assert "not ready" in decision_response.json()["detail"]


def test_candidate_artifact_endpoint_serves_ready_svg(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidate = create_generation(client, run_id)["candidates"][0]

    response = client.get(f"/candidates/{candidate['id']}/artifact")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["x-content-sha256"] == candidate["sha256"]
    assert hashlib.sha256(response.content).hexdigest() == candidate["sha256"]


def test_candidate_thumbnail_endpoint_serves_cached_png(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidate = create_generation(client, run_id)["candidates"][0]
    render_calls: list[dict] = []

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
  <path d="M 1 1 L 9 9" />
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
    ) -> None:
        assert Path(url) != workspace / candidate["artifactPath"]
        assert Path(url).name.endswith(".tmp.svg")
        assert output_width == 256
        assert output_height == 256
        Path(write_to).write_bytes(b"\x89PNG\r\n\x1a\ncached")

    monkeypatch.setattr(review_module, "render_candidate_svg", fake_render_candidate_svg)
    monkeypatch.setattr(review_module.cairosvg, "svg2png", fake_svg2png)

    first_response = client.get(f"/candidates/{candidate['id']}/thumbnail.png")
    second_response = client.get(f"/candidates/{candidate['id']}/thumbnail.png")

    assert first_response.status_code == 200
    assert first_response.headers["content-type"].startswith("image/png")
    assert first_response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert second_response.content == first_response.content
    assert second_response.headers["x-content-sha256"] == first_response.headers[
        "x-content-sha256"
    ]

    cached_thumbnails = list((workspace / "artifacts" / "thumbnails").glob("*.png"))
    assert len(cached_thumbnails) == 1
    assert len(render_calls) == 1
    assert render_calls[0]["source_path"].parts[-2] == "sources"
    assert render_calls[0]["render_parameters"]["repeats"] == 3
    assert render_calls[0]["render_parameters"]["shade_strokes"] == 0
    assert render_calls[0]["render_parameters"]["full_retrace_interval"] == 0
    assert render_calls[0]["render_parameters"]["flick_probability"] == 0.65


def test_review_thumbnail_prewarm_endpoint_starts_background_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = create_generation(client, run_id)
    job_started = threading.Event()
    job_calls: list[dict] = []

    def fake_run_thumbnail_prewarm_job(
        workspace_arg: Path,
        run_id_arg: str,
        generation_id: str,
        candidate_ids: list[str],
        sizes: tuple[int, ...],
        key: tuple,
    ) -> None:
        job_calls.append(
            {
                "workspace": workspace_arg,
                "run_id": run_id_arg,
                "generation_id": generation_id,
                "candidate_ids": candidate_ids,
                "sizes": sizes,
                "key": key,
            }
        )
        job_started.set()

    monkeypatch.setattr(
        review_module,
        "run_thumbnail_prewarm_job",
        fake_run_thumbnail_prewarm_job,
    )

    response = client.post(f"/runs/{run_id}/review/thumbnails/prewarm")

    assert response.status_code == 202
    body = response.json()["prewarm"]
    assert body["generationId"] == generation["id"]
    assert body["candidateCount"] == 24
    assert body["sizes"] == [256, 1024]
    assert body["status"] == "queued"
    assert job_started.wait(timeout=5)
    assert len(job_calls) == 1
    assert job_calls[0]["workspace"] == workspace
    assert job_calls[0]["run_id"] == run_id
    assert job_calls[0]["generation_id"] == generation["id"]
    assert len(job_calls[0]["candidate_ids"]) == 24
    assert job_calls[0]["candidate_ids"][0] == generation["candidates"][0]["id"]
    assert job_calls[0]["sizes"] == (256, 1024)


def test_candidate_artifact_endpoint_rejects_missing_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidate = create_generation(client, run_id)["candidates"][0]
    (workspace / candidate["artifactPath"]).unlink()

    response = client.get(f"/candidates/{candidate['id']}/artifact")

    assert response.status_code == 409
    assert "missing" in response.json()["detail"]


def test_candidate_artifact_endpoint_rejects_hash_mismatches(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidate = create_generation(client, run_id)["candidates"][0]
    (workspace / candidate["artifactPath"]).write_text("<svg></svg>", encoding="utf-8")

    response = client.get(f"/candidates/{candidate['id']}/artifact")

    assert response.status_code == 409
    assert "hash" in response.json()["detail"]


def test_candidate_artifact_endpoint_rejects_out_of_workspace_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    candidate = create_generation(client, run_id)["candidates"][0]
    update_candidate(
        workspace,
        candidate["id"],
        artifact_path="../outside.svg",
        sha256=hashlib.sha256(b"<svg></svg>").hexdigest(),
    )

    response = client.get(f"/candidates/{candidate['id']}/artifact")

    assert response.status_code == 409
    assert "workspace-relative" in response.json()["detail"]


def test_review_complete_rejects_additional_decisions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = create_generation(client, run_id)

    for candidate in generation["candidates"]:
        decision_response = client.post(
            f"/runs/{run_id}/review/decisions",
            json={"candidateId": candidate["id"], "decision": "rejected"},
        )
        assert decision_response.status_code == 200

    response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": generation["candidates"][0]["id"], "decision": "survived"},
    )

    assert response.status_code == 409
    assert "already complete" in response.json()["detail"]
