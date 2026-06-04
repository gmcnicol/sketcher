FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked

EXPOSE 8000
CMD [".venv/bin/uvicorn", "sketcher_model_builder.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
