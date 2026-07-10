# GovCon Compliance Workbench — advisory, synthetic-data tool.
# Multi-stage: build a venv with uv, then a slim runtime. NOT a certified
# system; runs behind a reverse proxy that terminates TLS (see docs/DEPLOY.md).
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# Dependency layer (cached until pyproject/lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra ai
# App layer.
COPY . .
RUN uv sync --frozen --extra ai

FROM python:3.12-slim AS runtime
# Non-root: the app writes nothing outside its workspace dirs.
RUN useradd --create-home --uid 10001 govcon
WORKDIR /app
COPY --from=build --chown=govcon:govcon /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    GOVCON_DATA_MODE=synthetic \
    GOVCON_DB_URL=sqlite:////data/govcon.db
USER govcon
EXPOSE 8000
# Migrate on boot (idempotent), then serve. --workspaces enables per-request
# workspace routing; drop it for single-DB mode.
CMD ["sh", "-c", "alembic upgrade head && govcon serve --host 0.0.0.0 --port 8000 --workspaces"]
