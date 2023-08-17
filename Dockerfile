# syntax=docker/dockerfile:1
FROM python:3.11-slim-bullseye
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY . /neodb
WORKDIR /neodb
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        busybox \
        postgresql-client \
        nginx \
        opencc \
        git
COPY misc/nginx.conf.d/* /etc/nginx/conf.d/
RUN echo >> /etc/nginx/nginx.conf
RUN echo 'daemon off;' >> /etc/nginx/nginx.conf
RUN python3 -m pip install --no-cache-dir --upgrade -r requirements.txt
RUN apt-get purge -y --auto-remove \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python3 manage.py compilescss \
    && python3 manage.py collectstatic --noinput
RUN cp -R misc/www /www
RUN mv static /www/static

# invoke check by default
CMD [ "python3", "/neodb/manage.py", "check" ]
