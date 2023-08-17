# syntax=docker/dockerfile:1
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN useradd -U app
COPY . /neodb
RUN mkdir -p /www
WORKDIR /neodb
RUN mv neodb-takahe /takahe
RUN cp misc/neodb-manage misc/takahe-manage /bin
RUN --mount=type=cache,target=/var/cache/apt apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        busybox \
        postgresql-client \
        nginx \
        opencc \
        git
RUN busybox --install
COPY misc/nginx.conf.d/* /etc/nginx/conf.d/

RUN --mount=type=cache,target=/root/.cache python3 -m pip install --upgrade -r requirements.txt

RUN --mount=type=cache,target=/root/.cache cd /takahe && python3 -m pip install --upgrade -r requirements.txt

RUN apt-get purge -y --auto-remove \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python3 manage.py compilescss \
    && python3 manage.py collectstatic --noinput

RUN cd /takahe && TAKAHE_DATABASE_SERVER="postgres://x@y/z" TAKAHE_SECRET_KEY="t" TAKAHE_MAIN_DOMAIN="x.y" python3 manage.py collectstatic --noinput

USER app:app

# invoke check by default
CMD [ "sh", "-c", 'python3 /neodb/manage.py check && TAKAHE_DATABASE_SERVER="postgres://x@y/z" TAKAHE_SECRET_KEY="t" TAKAHE_MAIN_DOMAIN="x.y" python3 manage.py collectstatic --noinput python3 /takahe/manage.py check' ]
