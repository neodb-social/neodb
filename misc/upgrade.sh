#!/bin/sh
[ -f manage.py ] || exit
echo Dry Run MakeMigrations:
python3 manage.py makemigrations --dry-run
echo Planned Migrations:
python3 manage.py migrate --plan

while true; do
	read -p "Do you wish to continue? (yes/no) " yn
	case $yn in
		[Yy]* ) break;;
		[Nn]* ) exit;;
	esac
done

echo "Generating static files..."
python3 manage.py sass common/static/sass/boofilsic.sass common/static/css/boofilsic.min.css -t compressed || exit $?
python3 manage.py sass common/static/sass/boofilsic.sass common/static/css/boofilsic.css || exit $?
python3 manage.py collectstatic --noinput || exit $?

echo "Migrating database..."
python3 manage.py migrate || exit $?

echo "Checking..."
python3 manage.py check

echo "Done. You may reload app, worker and cron"
