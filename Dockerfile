# syntax=docker/dockerfile:1
FROM python:3.11-slim-bullseye as build
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential libpq-dev git opencc \
  && rm -rf /var/lib/apt/lists/* && pip install -U pdm
WORKDIR /env
COPY pyproject.toml pdm.lock /env/
RUN pdm install --prod

FROM python:3.11-slim-bullseye as runtime
RUN useradd -U app_user

USER app_user:app_user
WORKDIR /neodb
COPY --chown=app_user:app_user . .
COPY --from=build --chown=app_user:app_user /env/.venv ./.venv
RUN .venv/bin/python3 manage.py compilescss \
    && .venv/bin/python3 manage.py collectstatic --noinput \
    && cp -a misc/www/* /www/

# invoke check by default
CMD [ ".venv/bin/python3", "/neodb/manage.py", "check" ]
