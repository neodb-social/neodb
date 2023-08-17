# syntax=docker/dockerfile:1
FROM python:3.11-slim-bullseye as build
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential libpq-dev git opencc \
  && rm -rf /var/lib/apt/lists/* && pip install -U pdm
WORKDIR /neodb
COPY pyproject.toml pdm.lock /neodb/
RUN pdm install --prod

FROM python:3.11-slim-bullseye as runtime
RUN useradd -U app_user && mkdir -p /web/www && chown -R app_user:app_user /web

USER app_user:app_user
WORKDIR /neodb
COPY --chown=app_user:app_user . .
COPY --from=build --chown=app_user:app_user /neodb/.venv ./.venv
RUN .venv/bin/python3 manage.py compilescss \
    && .venv/bin/python3 manage.py collectstatic --noinput \
    && mv static/ /web/www/ && cp -r misc/www/* /web/www/ \
    && cp -r misc/nginx.conf.d /web/

# invoke check by default
CMD [ ".venv/bin/python3", "/neodb/manage.py", "check" ]
