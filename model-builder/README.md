# Model Builder

The model-builder is the Python/uv context for Sketcher generation code,
persistence, and the FastAPI service used by the webapp. It imports source SVGs,
stores runtime state in a workspace, generates candidate SVG artifacts, records
review decisions, and creates the next generation from survivor choices.

## Commands

```bash
uv sync
uv run sketch tests/fixtures/tom2.svg /tmp/tom2.sketch.svg --repeats 1 --shade-strokes 1
uv run pytest
```

From the repo root:

```bash
make model-builder-test
make sketch model-builder/tests/fixtures/tom2.svg /tmp/tom2.sketch.svg ARGS="--repeats 1 --shade-strokes 1"
```

Docker Compose starts the API internally and exposes it only through the webapp
proxy. Use `http://nuc:5174/api/health` to verify the API through that proxy.

For host-only API development, run:

```bash
SKETCHER_CORS_ORIGINS=http://nuc:5173 uv run uvicorn sketcher_model_builder.api:create_app --factory --host 0.0.0.0 --port 8000
```

The host API is then reachable at `http://nuc:8000/`. Prefer the Docker Compose
workflow for integrated UI testing because it keeps this API internal and
accesses it through the webapp proxy.

## Workspace

The workspace path comes from `SKETCHER_WORKSPACE`. If unset, host commands
default to the repo root `.sketcher/` directory. Docker Compose sets
`SKETCHER_WORKSPACE=/workspace/.sketcher` and mounts the root `.sketcher/`
directory there.

Workspace contents:

- `sketcher.sqlite3` - SQLite database.
- `artifacts/sources/` - cleaned source SVG artifacts.
- `artifacts/candidates/` - generated candidate SVG artifacts.
- `models/` - reserved model artifact directory, configured in Compose with
  `SKETCHER_MODEL_ARTIFACTS=/workspace/.sketcher/models`.

Core SQLite tables:

- `sources` - uploaded SVG metadata, SHA-256, byte size, and artifact path.
- `runs` - active evolution run for a source.
- `generations` - generation number and status.
- `candidates` - candidate metadata, genome JSON, artifact path, validation
  status, SHA-256, and byte size.
- `candidate_parents` - queryable lineage links for bred generations.
- `candidate_decisions` - survived/rejected decisions, including undone
  decisions.

Artifact paths stored in SQLite are workspace-relative. Candidate artifact
serving rejects missing, hash-mismatched, and out-of-workspace paths.

## Source Import

`POST /sources` accepts `image/svg+xml` uploads with `.svg` filenames. The
source importer validates SVG XML, cleans editor metadata, stores a source
artifact, creates a `source` row, and creates an active `run` row.

Long traced strokes may be split into substrokes before storage. The stored
source artifact is the normalized input used for candidate generation.

## Generation

`POST /runs/{runId}/generations` creates the first generation for a run. The MVP
targets 24 ready candidates. Failed render or validation attempts are persisted
as failed candidate rows and excluded from review.

Each candidate stores:

- UUID7 candidate ID.
- Run and generation IDs.
- Generation number and position.
- Origin type such as `preset_mutation`, `survivor_mutation`,
  `survivor_carryover`, or `random_immigrant`.
- Genome JSON with render strategy, parameters, seed, and lineage fields.
- Workspace-relative artifact path, byte size, and SHA-256 for ready artifacts.
- Validation status and message.

Generation status is `running` while artifacts are being produced, `ready` when
the target ready count is met, and `partial_failed` if attempts are exhausted.

## Review Decisions

`GET /runs/{runId}/review/current` returns the next ready candidate without an
active decision. Review order follows candidate position.

`POST /runs/{runId}/review/decisions` records one active decision per ready
candidate:

- `survived` keeps the candidate eligible as a parent.
- `rejected` records the choice but does not feed breeding in the MVP.

`POST /runs/{runId}/review/undo` marks the latest active decision with
`undone_at` and returns review state to that candidate. Undone rows remain in
SQLite for history but do not count toward active review totals.

Review must be complete for all ready candidates before the next generation can
be created.

## Next Generation Rules

`POST /runs/{runId}/generations/next` accepts `mode` values:

- `breed` - requires at least one active survivor in the completed generation.
- `reroll` - allowed only when the completed generation has zero survivors.

Breeding creates a mix of survivor mutations, survivor carryovers, and fresh
random immigrants. If survivor diversity is low, meaning one or two survivors,
the next generation includes extra random immigrants. Survivor carryovers are
capped so one generation cannot be dominated by unchanged parents.

Reroll creates a fresh generation of random immigrants with no parent links.

## MVP Boundaries

- Static SVG only.
- Genome data is an internal mixed parameter/strategy representation.
- Rejected candidates are stored but are not used as negative breeding examples.
- Survivor choices affect only the next generation in the current run; there is
  no global taste/profile update yet.
- No run dashboard, bundle export/import, sketched animation, or technicolour
  generation yet.

## Future Directions

The reusable output of future training or moulding work should live as model
artifacts under `models/`, separate from transient generated SVG candidates.
Future work includes a global taste/profile model, operator genome refinement,
sketched animation, technicolour exploration, bundle export/import, and a run
dashboard.
