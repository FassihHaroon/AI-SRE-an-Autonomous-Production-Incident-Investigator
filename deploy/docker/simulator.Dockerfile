# syntax=docker/dockerfile:1.7
# Simulator image: every component of the SIMULATED production environment (shop services,
# deployer, chaos controller, load generator, seed job). The compose `command` picks the
# component:  python -m ai_sre_simulator <component>
# Build context: repository root.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app

# 1) Dependencies only (cached until uv.lock or a pyproject changes).
COPY pyproject.toml uv.lock .python-version ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY simulator/pyproject.toml simulator/pyproject.toml
COPY evals/pyproject.toml evals/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package ai-sre-simulator

# 2) Application code and scenario definitions.
COPY simulator simulator
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package ai-sre-simulator

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    SIM_SCENARIO_DIR=/app/simulator/scenarios
LABEL org.opencontainers.image.description="AI SRE simulated production environment (NOT real production)"
EXPOSE 8000
ENTRYPOINT ["python", "-m", "ai_sre_simulator"]
