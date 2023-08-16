NiceDB / NeoDB - Getting Start
==============================
This is a very basic guide with limited detail, contributions welcomed

## Table of Contents
- [NiceDB / NeoDB - Getting Start](#nicedb--neodb---getting-start)
  - [Table of Contents](#table-of-contents)
  - [0 Run in Docker](#0-run-in-docker)
  - [1 Manual Install](#1-manual-install)
    - [1.1 Database](#11-database)
    - [1.2 Configuration](#12-configuration)
    - [1.3 Packages and Build](#13-packages-and-build)
  - [2 Start services](#2-start-services)
  - [3 Migrate from an earlier version](#3-migrate-from-an-earlier-version)
  - [4 Add Cron Jobs (optional)](#4-add-cron-jobs-optional)
  - [5 Index and Search (optional)](#5-index-and-search-optional)

0 Run in Docker
---------------

```
cp neodb.env.dist neodb.env  # update this configuration

docker-compose up
```

1 Manual Install
----------------
Install PostgreSQL, Redis and Python (3.10 or above) if not yet

### 1.1 Database
Setup database
```
CREATE DATABASE neodb ENCODING 'UTF8' LC_COLLATE='en_US.UTF-8' LC_CTYPE='en_US.UTF-8' TEMPLATE template0;
CREATE ROLE neodb with LOGIN ENCRYPTED PASSWORD 'abadface';
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
NeoDB uses [PDM](https://pdm.fming.dev) as the package manager to install and manage dependencies.
Please visit the [PDM installation guide](https://pdm.fming.dev/latest/#installation) to install it.

Install all dependencies
```
pdm install
```

Quick check
```
pdm run manage.py check
```

Initialize database
```
pdm run manage.py migrate
```

Build static assets (production only)
```
pdm run manage.py compilescss
pdm run manage.py collectstatic
```

2 Start services
--------------
Make sure PostgreSQL and Redis are running

Start job queue server
```
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES  # required and only for macOS, otherwise it may crash
pdm run manage.py rqworker --with-scheduler import export mastodon fetch crawl
```

Run web server in dev mode
```
pdm run manage.py runserver
```

It should be ready to serve from here, to run web server for production, consider `gunicorn -w 8 boofilsic.wsgi` in systemd or sth similar.

To show all available tasks, you can run `pdm run --list`.


3 Migrate from an earlier version
-------------------------------
Update database
```
pdm run manage.py migrate
```

Rebuild static assets
```
pdm build_static
```

4 Add Cron Jobs (optional)
-------------
add `pdm run python manage.py refresh_mastodon` to crontab to run hourly, it will refresh cached users' follow/mute/block from mastodon

5 Index and Search (optional)
----------------

Build initial index, it may take a few minutes or hours depending on the data size.
```
pdm run manage.py index --init
pdm run manage.py index --reindex```

6 Other maintenance tasks (optional)
-----------------------
Requeue failed import jobs
```
pdm run rq requeue --all --queue import
```

Run Test Coverage
```
pdm run coverage run --source='.' manage.py test
pdm run coverage report
```

Enable Developer Console
```
pdm run manage.py createapplication --client-id NEODB_DEVELOPER_CONSOLE --skip-authorization --name 'NeoDB Developer Console' --redirect-uris 'https://example.org/lol'  confidential authorization-code
```

7 Frequently Asked Questions
------

### I got Error: “无效的登录回调地址”.

Check `REDIRECT_URIS` in `settings.py`, the final value should be `"http://localhost/account/login/oauth"` or sth similar. If you are specifying a port, add the port to the localhost address.

If any change was made to `REDIRECT_URIS`, existing apps registered in Mastodon are no longer valid, so delete the app record in the database:
```
delete from mastodon_mastodonapplication;
```
