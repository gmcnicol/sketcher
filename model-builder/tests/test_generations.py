import hashlib
import json
import random
import sqlite3
import uuid
from pathlib import Path

import pytest
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


def install_fast_renderer(monkeypatch) -> None:
    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        seed = render_parameters["seed"]
        artifact_path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path d="M 1 1 L 9 9" style="fill:none;stroke:#111;stroke-width:1;stroke-opacity:1" data-sketcher-pass="{seed}" />
</svg>
""",
            encoding="utf-8",
        )

    monkeypatch.setattr(generations, "render_candidate_svg", fake_render_candidate_svg)


def review_generation(
    client: TestClient,
    run_id: str,
    candidates: list[dict],
    *,
    survivor_count: int,
) -> None:
    for index, candidate in enumerate(candidates):
        decision = "survived" if index < survivor_count else "rejected"
        response = client.post(
            f"/runs/{run_id}/review/decisions",
            json={"candidateId": candidate["id"], "decision": decision},
        )
        assert response.status_code == 200
    assert client.get(f"/runs/{run_id}/review/current").json()["review"]["complete"]


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


def test_survivor_stroke_count_mutations_can_reach_thousands() -> None:
    parent_parameters = {"repeats": 28, "shade_strokes": 58}

    repeat_mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(5),
    )
    shade_mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(1),
    )

    assert repeat_mutation["repeats"] > 1000
    assert repeat_mutation["repeats"] <= generations.REPEATS_MAX
    assert shade_mutation["shade_strokes"] > 1000
    assert shade_mutation["shade_strokes"] <= generations.SHADE_STROKES_MAX


def test_next_generation_requires_completed_review(tmp_path: Path, monkeypatch) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    client.post(f"/runs/{run_id}/generations")

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 409
    assert "review must be complete" in response.json()["detail"]


def test_zero_survivors_blocks_breed_and_allows_reroll(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=0)

    breed_response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )
    reroll_response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "reroll"},
    )

    assert breed_response.status_code == 409
    assert "Reroll this generation instead" in breed_response.json()["detail"]
    assert reroll_response.status_code == 201
    next_generation = reroll_response.json()["generation"]
    assert next_generation["generationNumber"] == 2
    assert next_generation["readyCount"] == 24
    assert {candidate["originType"] for candidate in next_generation["candidates"]} == {
        "random_immigrant"
    }
    assert all(
        candidate["genome"]["parentCandidateIds"] == []
        for candidate in next_generation["candidates"]
    )


@pytest.mark.parametrize("survivor_count", [1, 2])
def test_low_diversity_breed_creates_four_immigrants(
    tmp_path: Path,
    monkeypatch,
    survivor_count: int,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=survivor_count)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 201
    candidates = response.json()["generation"]["candidates"]
    ready_candidates = [
        candidate for candidate in candidates if candidate["validationStatus"] == "ready"
    ]
    assert len([c for c in ready_candidates if c["originType"] == "random_immigrant"]) == 4
    assert (
        len([c for c in ready_candidates if c["originType"] == "survivor_carryover"])
        == survivor_count
    )
    assert (
        len([c for c in ready_candidates if c["originType"] == "survivor_mutation"])
        == 24 - 4 - survivor_count
    )


def test_normal_breed_creates_two_immigrants(tmp_path: Path, monkeypatch) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=5)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 201
    ready_candidates = [
        candidate
        for candidate in response.json()["generation"]["candidates"]
        if candidate["validationStatus"] == "ready"
    ]
    assert len([c for c in ready_candidates if c["originType"] == "random_immigrant"]) == 2
    assert len([c for c in ready_candidates if c["originType"] == "survivor_carryover"]) == 5
    assert len([c for c in ready_candidates if c["originType"] == "survivor_mutation"]) == 17


def test_survivor_carryovers_are_appended_last_and_capped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=12)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 201
    candidates = [
        candidate
        for candidate in response.json()["generation"]["candidates"]
        if candidate["validationStatus"] == "ready"
    ]
    carryovers = [c for c in candidates if c["originType"] == "survivor_carryover"]
    assert len(carryovers) == 8
    assert [candidate["originType"] for candidate in candidates[-8:]] == [
        "survivor_carryover"
    ] * 8
    assert [c["genome"]["parentCandidateIds"][0] for c in carryovers] == [
        candidate["id"] for candidate in generation["candidates"][:8]
    ]


def test_carryovers_must_be_reviewed_again_in_new_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=3)

    next_generation = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    ).json()["generation"]
    review = client.get(f"/runs/{run_id}/review/current").json()["review"]

    assert review["generationId"] == next_generation["id"]
    assert review["reviewedCount"] == 0
    assert review["survivorCount"] == 0
    assert review["totalReadyCount"] == 24
    assert len(fetch_rows(workspace, "candidate_decisions")) == 24


def test_undone_survived_decisions_do_not_count_as_active_survivors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]

    survived_response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": generation["candidates"][0]["id"], "decision": "survived"},
    )
    undo_response = client.post(f"/runs/{run_id}/review/undo")
    assert survived_response.status_code == 200
    assert undo_response.status_code == 200
    review_generation(client, run_id, generation["candidates"], survivor_count=0)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 409
    assert "Reroll this generation instead" in response.json()["detail"]


def test_next_generation_artifacts_are_workspace_relative_and_hash_checked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=1)

    next_generation = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    ).json()["generation"]
    carryover = next(
        candidate
        for candidate in next_generation["candidates"]
        if candidate["originType"] == "survivor_carryover"
    )
    artifact_path = Path(carryover["artifactPath"])
    artifact_bytes = (workspace / artifact_path).read_bytes()

    assert not artifact_path.is_absolute()
    assert artifact_path.parts[:2] == ("artifacts", "candidates")
    assert carryover["byteSize"] == len(artifact_bytes)
    assert carryover["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert carryover["sha256"] == generation["candidates"][0]["sha256"]
    artifact_response = client.get(f"/candidates/{carryover['id']}/artifact")
    assert artifact_response.status_code == 200
    assert artifact_response.headers["x-content-sha256"] == carryover["sha256"]
