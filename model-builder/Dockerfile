FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN uv sync --dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "sketcher_model_builder.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
