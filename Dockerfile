# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
ARG dev
ARG buildver="dev-unknown"
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_COMPILE_BYTECODE=0
ENV PYTHONUNBUFFERED=1

RUN --mount=type=cache,sharing=locked,target=/var/cache/apt apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev git

COPY --from=ghcr.io/astral-sh/uv:0.8.8 /uv /uvx /bin/

COPY . /neodb
# TODO: use --exclude once it's supported in stable syntax
RUN mv /neodb/neodb-takahe /takahe

RUN echo "${buildver}" > /etc/neodb_version \
 && echo "__version__ = \"${buildver}\"" > /neodb/boofilsic/__init__.py \
 && echo "__version__ = \"${buildver}\"" > /takahe/takahe/neodb.py

WORKDIR /neodb
RUN uv venv /neodb-venv
ENV VIRTUAL_ENV=/neodb-venv
RUN find misc/wheels-cache -type f | xargs -n 1 uv pip install --python /neodb-venv/bin/python || echo incompatible wheel ignored
RUN rm -rf misc/wheels-cache
RUN --mount=type=cache,sharing=locked,target=/root/.cache uv sync --active $(if [ -z "$dev" ]; then echo "--no-dev"; fi)

WORKDIR /takahe
RUN uv venv /takahe-venv
ENV VIRTUAL_ENV=/takahe-venv
RUN --mount=type=cache,sharing=locked,target=/root/.cache uv sync --active $(if [ -z "$dev" ]; then echo "--no-dev"; fi)

# runtime stage
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN --mount=type=cache,sharing=locked,target=/var/cache/apt-run apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev \
    busybox \
    nginx \
    gettext-base \
 && busybox --install \
 && apt-get install -y --no-install-recommends postgresql-client redis-tools gettext \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -U app && mkdir -p /www
# postgresql and redis cli are not required, but install for development convenience

COPY --from=build /etc/neodb_version /etc/neodb_version
COPY --from=build /neodb /neodb
COPY --from=build /takahe /takahe
COPY --from=build /neodb-venv /neodb-venv
COPY --from=build /takahe-venv /takahe-venv

WORKDIR /neodb
RUN /neodb-venv/bin/django-admin compilemessages \
 && NEODB_SECRET_KEY="t" NEODB_SITE_DOMAIN="x.y" NEODB_SITE_NAME="z" /neodb-venv/bin/python3 manage.py compilescss \
 && NEODB_SECRET_KEY="t" NEODB_SITE_DOMAIN="x.y" NEODB_SITE_NAME="z" /neodb-venv/bin/python3 manage.py collectstatic --noinput

WORKDIR /takahe
RUN TAKAHE_DATABASE_SERVER="postgres://x@y/z" TAKAHE_SECRET_KEY="t" TAKAHE_MAIN_DOMAIN="x.y" /takahe-venv/bin/python3 manage.py collectstatic --noinput

WORKDIR /neodb
COPY misc/bin/* /bin/

USER app:app

CMD [ "neodb-hello"]
