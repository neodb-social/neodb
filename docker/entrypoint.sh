#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

source .venv/bin/activate

python manage.py collectstatic --noinput
python manage.py migrate

exec "$@"
