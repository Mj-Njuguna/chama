#!/bin/sh
# Runs roles.sql inside the db container as the bootstrap superuser (local socket).
# Invoked by `just db-roles`; all values come from the db service environment in compose.yaml.
set -eu
exec psql --quiet --no-psqlrc \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set dbname="$POSTGRES_DB" \
    --set owner_password="$APP_OWNER_PASSWORD" \
    --set rw_password="$APP_RW_PASSWORD" \
    --file /srv/db/roles.sql
