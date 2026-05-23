ARG IMAGE_HOST=python
ARG IMAGE_LABEL=3.12-slim

FROM ${IMAGE_HOST}:${IMAGE_LABEL}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GUNICORN_CMD_ARGS="--workers 8"

COPY requirements.lock requirements.lock

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libxslt1.1 \
        nginx \
        busybox \
        postgresql-client \
    && python -m pip install --no-cache-dir -r requirements.lock \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /cache

# Python mimetypes package is missing some common mappings
COPY docker/mime.types /etc/mime.types

# Configure nginx
COPY docker/nginx.conf /etc/nginx/
COPY docker/nginx.conf.d/* /etc/nginx/conf.d/

COPY . /takahe

# Sanity-test the nginx config
RUN nginx -t

WORKDIR /takahe

RUN TAKAHE_DEBUG="true" python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["bash", "docker/run.sh"]
