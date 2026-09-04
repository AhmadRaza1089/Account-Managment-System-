#!/bin/sh
# Bring the database up to date before running anything, so upgrading the
# image is enough to upgrade the schema.
set -e

if [ "${SKIP_MIGRATIONS:-}" != "1" ]; then
    account-manager init-db >/dev/null
fi

exec "$@"
