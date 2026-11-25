# syntax=docker/dockerfile:1
# One image for app, worker and beat. Targets: dev (with dev deps, code bind-mounted) and prod.

FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

FROM base AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project
ENV DJANGO_SETTINGS_MODULE=config.settings.dev
COPY . .
# Dev runs as root on purpose: the source is bind-mounted and tools write into it (pytest cache,
# coverage, openapi/schema.yaml, uv.lock), which fails for a non-root user on Linux CI hosts.
# The prod target below runs as the unprivileged "app" user.
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM base AS prod
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev
ENV DJANGO_SETTINGS_MODULE=config.settings.prod
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
