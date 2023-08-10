# syntax=docker/dockerfile:1
FROM python:3.8-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential libpq-dev git \
  && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -rf /tmp/requirements.txt \
    && useradd -U app_user \
    && install -d -m 0755 -o app_user -g app_user /app/static

ENV DJANGO_SETTINGS_MODULE=yoursettings.dev
WORKDIR /app
USER app_user:app_user
COPY --chown=app_user:app_user . .
RUN chmod +x docker/*.sh

# Section 6- Docker Run Checks and Configurations
ENTRYPOINT [ "docker/entrypoint.sh" ]

CMD [ "docker/start.sh", "server" ]
