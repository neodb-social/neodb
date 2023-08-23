# syntax=docker/dockerfile:1
FROM python:3.11-slim as build
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN --mount=type=cache,sharing=locked,target=/var/cache/apt apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev python3-venv opencc git

COPY requirements.txt /neodb/
WORKDIR /neodb
RUN python -m venv /neodb-venv
RUN --mount=type=cache,sharing=locked,target=/root/.cache /neodb-venv/bin/python3 -m pip install --upgrade -r requirements.txt

COPY neodb-takahe/requirements.txt /takahe/
WORKDIR /takahe
RUN python -m venv /takahe-venv
RUN --mount=type=cache,sharing=locked,target=/root/.cache /takahe-venv/bin/python3 -m pip install --upgrade -r requirements.txt

RUN apt-get purge -y --auto-remove build-essential && rm -rf /var/lib/apt/lists/*

# runtime stage
FROM python:3.11-slim as runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN --mount=type=cache,sharing=locked,target=/var/cache/apt-run apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev \
        busybox \
        nginx \
        gettext-base \
        opencc
RUN busybox --install

COPY . /neodb
WORKDIR /neodb
COPY --from=build /neodb-venv /neodb-venv
RUN /neodb-venv/bin/python3 manage.py compilescss
RUN /neodb-venv/bin/python3 manage.py collectstatic --noinput

RUN mv /neodb/neodb-takahe /takahe
WORKDIR /takahe
COPY --from=build /takahe-venv /takahe-venv
RUN pwd && ls
RUN TAKAHE_DATABASE_SERVER="postgres://x@y/z" TAKAHE_SECRET_KEY="t" TAKAHE_MAIN_DOMAIN="x.y" /takahe-venv/bin/python3 manage.py collectstatic --noinput

WORKDIR /neodb
COPY misc/bin/* /bin/
RUN mkdir -p /www
RUN useradd -U app
RUN rm -rf /var/lib/apt/lists/*

USER app:app

# invoke check by default
CMD [ "neodb-hello"]
