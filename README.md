# Sketcher

Sketcher is split into two product contexts:

- `webapp/` - Vite React TypeScript browser control surface.
- `model-builder/` - Python/uv model builder for SVG sketch generation and future evolution workflows.

The root is reserved for orchestration, documentation, repo config, and shared project metadata.

## Workspace

Runtime state belongs in `.sketcher/`, which is ignored by git. Future trained or moulded model output should be treated as reusable model artifacts and stored under `.sketcher/models/`, separate from transient candidate SVGs.

## Commands

```bash
make webapp-dev
make webapp-build
make model-builder-test
make sketch model-builder/tests/fixtures/tom2.svg /tmp/tom2.sketch.svg ARGS="--repeats 1 --shade-strokes 1"
docker compose up
```

The webapp dev server is configured for access at `http://nuc:5173/`.
