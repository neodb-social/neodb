NiceDB / NeoDB - Getting Start
==============================
This is a very basic guide with limited detail, contributions welcomed

## Table of Contents
- [Run in Docker](#0-run-in-docker)
- [1 Install](#1-manual-install)
  * [1.1 Database](#11-database)
  * [1.2 Configuration](#12-configuration)
  * [1.3 Packages and Build](#13-packages-and-build)
- [2 Start services](#2-start-services)
- [3 Migrate from an earlier version](#3-migrate-from-an-earlier-version)
- [4 Add Cron Jobs (optional)](#4-add-cron-jobs-optional)
- [5 Index and Search (optional)](#5-index-and-search-optional)
- [6 Other maintenance tasks (optional)](#6-other-maintenance-tasks-optional)
- [7 Frequently Asked Questions](#7-frequently-asked-questions)



0 Run in Docker
---------------

Recommended, see [Docker Installation](install-docker.md)

1 Manual Install
----------------
Install PostgreSQL, Redis and Python (3.10 or above) if not yet

### 1.1 Database
Setup database
```
CREATE ROLE neodb with LOGIN ENCRYPTED PASSWORD 'abadface';
CREATE DATABASE neodb ENCODING 'UTF8' LC_COLLATE='en_US.UTF-8' LC_CTYPE='en_US.UTF-8' TEMPLATE template0;
GRANT ALL ON DATABASE neodb TO neodb;
```

### 1.2 Configuration
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

The most important configurations to setup are:

- `MASTODON_ALLOW_ANY_SITE` set to `True` so that user can login via any Mastodon API compatible sites (e.g. Mastodon/Pleroma)
- `REDIRECT_URIS` should be `SITE_INFO["site_url"] + "/account/login/oauth"`. If you want to run **on local**, `SITE_INFO["site_url"]` should be set to `"http://localhost/"`

More details on `settings.py` in [configuration.md](configuration.md)

### 1.3 Packages and Build
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


2 Start services
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


3 Migrate from an earlier version
-------------------------------
Update database
```
python3 manage.py migrate
```

Rebuild static assets
```
python3 manage.py compilescss
python3 manage.py collectstatic
```

4 Add Cron Jobs (optional)
-------------
add `python manage.py refresh_mastodon` to crontab to run hourly, it will refresh cached users' follow/mute/block from mastodon

5 Index and Search (optional)
----------------
Install TypeSense or Meilisearch, change `SEARCH_BACKEND` and coniguration for search server in `settings.py`

Build initial index, it may take a few minutes or hours
```
python3 manage.py index --init
python3 manage.py index --reindex
```

6 Other maintenance tasks (optional)
-----------------------
Requeue failed import jobs
```
rq requeue --all --queue import
```

Run Tests
```
coverage run --source='.' manage.py test
coverage report
```

Enable Developer Console
```
python3 manage.py createapplication --client-id NEODB_DEVELOPER_CONSOLE --skip-authorization --name 'NeoDB Developer Console' --redirect-uris 'https://example.org/lol'  confidential authorization-code
```

7 Frequently Asked Questions
------

### I got Error: “无效的登录回调地址”.

Check `REDIRECT_URIS` in `settings.py`, the final value should be `"http://localhost/account/login/oauth"` or sth similar. If you are specifying a port, add the port to the localhost address.

If any change was made to `REDIRECT_URIS`, existing apps registered in Mastodon are no longer valid, so delete the app record in the database:
```
delete from mastodon_mastodonapplication;
```
