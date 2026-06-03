FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN uv sync --dev

EXPOSE 8000
CMD ["uv", "run", "python", "-m", "http.server", "8000"]
