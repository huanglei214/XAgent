FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    XAGENT_HOME=/root/.xagent

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY xagent ./xagent

RUN uv sync --frozen --no-dev

ENTRYPOINT ["/app/.venv/bin/xagent"]
CMD ["gateway"]
