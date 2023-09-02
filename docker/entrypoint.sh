#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

python manage.py collectstatic --noinput
python manage.py makemigrations users books movies games music sync mastodon management collection
python manage.py makemigrations
python manage.py migrate users
python manage.py migrate

exec "$@"
