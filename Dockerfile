# syntax=docker/dockerfile:1
FROM python:3.11-slim-bullseye as build
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY . /neodb
WORKDIR /neodb
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential libpq-dev git opencc \
  && rm -rf /var/lib/apt/lists/* && pip install -U pdm
WORKDIR /app
COPY pyproject.toml pdm.lock /app/
RUN pdm install --prod

FROM python:3.11-slim-bullseye as runtime
RUN useradd -U app_user \
    && install -d -m 0755 -o app_user -g app_user /app/static

WORKDIR /app
USER app_user:app_user
COPY --chown=app_user:app_user . .
COPY --from=build --chown=app_user:app_user /app/.venv ./.venv
RUN .venv/bin/python3 manage.py compilescss \
    && .venv/bin/python3 manage.py collectstatic --noinput \
    && cp -a misc/www/* /www/

# invoke check by default
CMD [ ".venv/bin/python3", "/neodb/manage.py", "check" ]
