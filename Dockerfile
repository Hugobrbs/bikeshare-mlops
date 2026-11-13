FROM python:3.12-slim-bookworm AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app

# les dépendances d'abord : cette couche n'est refaite que si uv.lock change
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM build AS build-pipeline
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra pipeline


FROM python:3.12-slim-bookworm AS runtime
ARG APP_REVISION=dev
ENV APP_REVISION=${APP_REVISION} \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 1000 app
WORKDIR /app


FROM runtime AS pipeline
COPY --from=build-pipeline /opt/venv /opt/venv
COPY config ./config
RUN mkdir -p data && chown app data
USER app
CMD ["bikeshare", "pipeline", "start"]


FROM runtime AS api
COPY --from=build /opt/venv /opt/venv
USER app
EXPOSE 8001
CMD ["bikeshare", "api", "serve"]
