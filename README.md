# Sketcher

Sketcher is an MVP for evolving hand-sketched SVG variants. It has two runtime
contexts:

- `webapp/` - Vite React control surface for uploading a source SVG, generating
  candidates, and reviewing them.
- `model-builder/` - Python/uv FastAPI service and CLI for SVG import,
  candidate generation, review persistence, and breeding rules.

The root owns orchestration, documentation, repo config, and shared project
metadata.

## Docker Compose Development

Use Docker Compose for the normal integrated workflow:

```bash
docker compose up --build
```

The UI is exposed at `http://nuc:5174/` by default. Set `WEBAPP_PORT` to expose
the UI on another host port:

```bash
WEBAPP_PORT=5180 docker compose up --build
```

The model-builder API is not exposed directly to the host. The webapp proxies
browser requests from `/api` to the internal Compose service alias `api:8000`.
For example, the health endpoint is available through the webapp proxy at
`http://nuc:5174/api/health`.

Run the repeatable Compose smoke check with:

```bash
make compose-smoke
```

That target builds and starts the stack, retries the proxied health endpoint,
prints `docker compose ps`, and leaves the stack running for manual inspection.

## Host Development

Install dependencies in each context before running host commands:

```bash
cd webapp && npm install
cd ../model-builder && uv sync
```

Common commands:

```bash
make webapp-dev
make webapp-build
make model-builder-test
make sketch model-builder/tests/fixtures/tom2.svg /tmp/tom2.sketch.svg ARGS="--repeats 1 --shade-strokes 1"
make compose-config
```

To run both services directly on the host, start the API first:

```bash
cd model-builder
SKETCHER_CORS_ORIGINS=http://nuc:5173 uv run uvicorn sketcher_model_builder.api:create_app --factory --host 0.0.0.0 --port 8000
```

Then start the webapp with an absolute API URL:

```bash
cd webapp
VITE_MODEL_BUILDER_URL=http://nuc:8000 npm run dev
```

Use `http://nuc:5173/` for that host-only UI. Docker Compose remains the
preferred integrated workflow because it keeps the API internal and routes API
traffic through the webapp proxy.

## Workspace

Runtime state belongs in `.sketcher/`, which is ignored by git. Docker Compose
mounts the root `.sketcher/` directory into the model-builder container at
`/workspace/.sketcher` and sets:

- `SKETCHER_WORKSPACE=/workspace/.sketcher`
- `SKETCHER_MODEL_ARTIFACTS=/workspace/.sketcher/models`

On the host, model-builder defaults to the root `.sketcher/` directory unless
`SKETCHER_WORKSPACE` is set.

Important paths:

- `.sketcher/sketcher.sqlite3` - SQLite database for sources, runs,
  generations, candidates, candidate parent links, and review decisions.
- `.sketcher/artifacts/sources/` - cleaned uploaded source SVGs.
- `.sketcher/artifacts/candidates/` - generated candidate SVG artifacts.
- `.sketcher/models/` - reserved for reusable model artifacts from future
  training or moulding work.

It is safe to delete `.sketcher/` when you want a fresh local workspace.

## MVP Workflow

1. Open the UI at `http://nuc:5174/` when running through Docker Compose.
2. Upload a static `.svg` file. The upload creates a `source` row, stores a
   cleaned source artifact, and creates an active `run`.
3. Click `Generate candidates`. The first generation targets 24 ready candidate
   SVGs. The UI shows progress while the model-builder persists candidate rows
   and artifacts.
4. Review candidates after their image has loaded:
   - `j` marks the current candidate as survived.
   - `k` marks the current candidate as rejected.
   - `u` undoes the latest active decision.
5. Finish reviewing all ready candidates in the generation.
6. If at least one survivor remains, manually breed the next generation. When
   only one or two survivors remain, the UI warns about low survivor diversity;
   the next bred generation includes extra fresh candidates.
7. If zero survivors remain, reroll instead. Reroll creates a fresh generation
   without survivor parents.

Review decisions are persisted in SQLite. Undone decisions remain in the
database with `undone_at` set, but only active decisions count toward current
review totals and breeding eligibility.

## MVP Boundaries

The MVP intentionally keeps the evolution loop narrow:

- Static SVG only. There is no animated source import or animated candidate
  playback yet.
- The genome is a mixed parameter/strategy representation for SVG rendering,
  not a stable public model format.
- Rejected candidates are recorded for audit/history but are not used as
  negative breeding signal.
- There is no global taste/profile model update yet; survivor choices affect
  only the next generation for the current run.
- There is no run dashboard, bundle export/import, or long-term library view.

## Future Directions

Planned directions beyond the MVP include:

- Global taste/profile model that learns across runs.
- Operator genome refinement for more expressive mutation and breeding
  operations.
- Sketched animation support.
- Technicolour/color exploration.
- Bundle export/import for runs, artifacts, and model state.
- Run dashboard for browsing history, status, lineage, and reusable outputs.

## Validation

Use these checks before publishing changes:

```bash
cd webapp && npm test
cd webapp && npm run lint
cd webapp && npm run build
cd model-builder && uv run pytest
make compose-smoke
```
