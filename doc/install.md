NiceDB / NeoDB - Getting Start
==============================
This is a very basic guide with limited detail, contributions welcomed

Install
-------
Install PostgreSQL, Redis and Python (3.10 or above) if not yet

Setup database
```
CREATE DATABASE neodb ENCODING 'UTF8' LC_COLLATE='en_US.UTF-8' LC_CTYPE='en_US.UTF-8' TEMPLATE template0;
\c neodb;
CREATE ROLE neodb with LOGIN ENCRYPTED PASSWORD 'abadface';
GRANT ALL ON DATABASE neodb TO neodb;
```

Create and edit your own configuration file (optional but very much recommended)
```
mkdir mysite && cp boofilsic/settings.py mysite/
export DJANGO_SETTINGS_MODULE=mysite.settings
```
Alternatively you can have a configuration file import `boofilsic/settings.py` then override it:
```
from boofilsic.settings import *

SECRET_KEY = "my_key"
```
More details on `settings.py` in [configuration.md](configuration.md)

Create and use `venv` as you normally would, then install packages
```
python3 -m pip install -r requirements.txt

```

Quick check
```
python3 manage.py check
```

Initialize database
```
python3 manage.py migrate
```

Build static assets (production only)
```
python3 manage.py compilescss
python3 manage.py collectstatic
```


Start services
--------------
Make sure PostgreSQL and Redis are running

Start job queue server
```
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES  # required and only for macOS, otherwise it may crash
python3 manage.py rqworker --with-scheduler import export mastodon fetch crawl
```

Run web server in dev mode
```
python3 manage.py runserver
```

It should be ready to serve from here, to run web server for production, consider `gunicorn -w 8 boofilsic.wsgi` in systemd or sth similar


Migrate from an earlier version
-------------------------------
Update database
```
python3 manage.py migrate
```

Rebuild static assets
```
python3 manage.py sass common/static/sass/boofilsic.sass common/static/css/boofilsic.min.css -t compressed
python3 manage.py sass common/static/sass/boofilsic.sass common/static/css/boofilsic.css
python3 manage.py collectstatic
```

Add Cron Jobs
-------------
add `python manage.py refresh_mastodon` to crontab to run hourly, it will refresh cached users' follow/mute/block from mastodon

Index and Search
----------------
Install TypeSense or Meilisearch, change `SEARCH_BACKEND` and coniguration for search server in `settings.py`

Build initial index, it may take a few minutes or hours
```
python3 manage.py index --init
python3 manage.py index --reindex
```

Other maintenance tasks
-----------------------
Requeue failed import jobs
```
rq requeue --all --queue import
```

Run in Docker
```
docker-compose build
docker-compose up
```

Run Tests
```
coverage run --source='.' manage.py test
coverage report
```
