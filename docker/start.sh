#!/bin/bash

cd /app

if [ $# -eq 0 ]; then
    echo "Usage: start.sh <server|rq>"
    exit 1
fi

PROCESS_TYPE=$1

if [ "$PROCESS_TYPE" = "server" ]; then
    if [ "$DJANGO_DEBUG" = "true" ]; then
        gunicorn \
            --reload \
            --bind 0.0.0.0:8000 \
            --workers 2 \
            --worker-class eventlet \
            --log-level DEBUG \
            --access-logfile "-" \
            --error-logfile "-" \
            boofilsic.wsgi
    else
        gunicorn \
            --bind 0.0.0.0:8000 \
            --workers 2 \
            --worker-class eventlet \
            --log-level DEBUG \
            --access-logfile "-" \
            --error-logfile "-" \
            boofilsic.wsgi
    fi
elif [ "$PROCESS_TYPE" = "rq" ]; then
    rqworker --with-scheduler doufen export mastodon
fi
