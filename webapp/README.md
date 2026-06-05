# Sketcher Webapp

The webapp is the browser control surface for the MVP evolution loop. It uploads
source SVGs to the model-builder API, starts generation, displays candidate
artifacts, records review decisions, and starts the next generation when review
is complete.

## Commands

```bash
npm install
npm run dev
npm test
npm run lint
npm run build
```

When running the integrated Docker Compose workflow, use the root command:

```bash
docker compose up --build
```

The Compose UI is available at `http://nuc:5174/` by default. The host Vite dev
server from `npm run dev` is configured for `http://nuc:5173/`.

## API Configuration

The app sends API requests to `VITE_MODEL_BUILDER_URL`, defaulting to `/api`.
Docker Compose sets `VITE_MODEL_BUILDER_URL=/api` and configures Vite to proxy
that path to the internal model-builder service. The browser should not call the
model-builder container directly.

For host-only development against a host API, start model-builder with CORS for
the Vite origin and run:

```bash
VITE_MODEL_BUILDER_URL=http://nuc:8000 npm run dev
```

Main API calls used by the UI:

- `POST /api/sources` uploads the source SVG and creates a run.
- `POST /api/runs/{runId}/generations` starts the first generation.
- `GET /api/runs/{runId}/generations/current` polls generation progress.
- `GET /api/runs/{runId}/review/current` loads the current review state.
- `GET /api/candidates/{candidateId}/thumbnail.png` loads raster candidate
  previews for review and history.
- `GET /api/candidates/{candidateId}/artifact` remains the source SVG artifact.
- `POST /api/runs/{runId}/review/decisions` records survive/reject decisions.
- `POST /api/runs/{runId}/review/undo` undoes the latest active decision.
- `POST /api/runs/{runId}/generations/next` breeds or rerolls the next
  generation.

## User Workflow

1. Choose a static `.svg` file and start the run.
2. Open the debug metadata panel when you need source ID, run ID, SHA-256, or
   stored artifact path.
3. Click `Generate candidates`. The first generation targets 24 ready
   candidates. Progress shows ready count, failed attempts, total attempts,
   elapsed time, and last update time.
4. Review the current candidate only after the preview image loads. Buttons and
   keyboard shortcuts are disabled until then.
5. Use keyboard shortcuts while reviewing:
   - `j` marks the loaded candidate as survived.
   - `k` marks the loaded candidate as rejected.
   - `u` undoes the latest active decision.
6. Finish the generation review. If survivors remain, click `Breed next
   generation`; the `b` shortcut also breeds from a complete review with
   survivors. If no survivors remain, click `Reroll generation`.

Editable inputs ignore review shortcuts, so typing in form controls does not
submit review decisions.

## Completed Review States

The completed-review panel summarizes reviewed, survived, and rejected counts.

If one or two survivors remain, the panel shows a low-diversity note. Breeding
is still allowed, and the model-builder adds extra fresh candidates to the next
generation.

If zero survivors remain, breeding is not available. Reroll creates a fresh next
generation with no survivor parents.

## MVP Boundaries

- The webapp accepts static SVG uploads only.
- It displays generated candidate artifacts as cached PNG previews; it does not
  edit candidates.
- Review decisions affect the current run's next generation only.
- Debug metadata is local operating information, not a run dashboard.
- Global taste/profile updates, animation, technicolour, bundle export/import,
  and historical run browsing are future capabilities.
