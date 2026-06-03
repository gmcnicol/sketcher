# Model Builder

Python/uv context for Sketcher generation code, persistence, and future evolution workflows.

The reusable output of future training or moulding work should live as model
artifacts, separate from transient generated SVG candidates. By default, Docker
Compose reserves `.sketcher/models/` for those artifacts through the
`SKETCHER_MODEL_ARTIFACTS` environment variable.

## Commands

```bash
uv run sketch tests/fixtures/tom2.svg /tmp/tom2.sketch.svg --repeats 1 --shade-strokes 1
uv run pytest
```
