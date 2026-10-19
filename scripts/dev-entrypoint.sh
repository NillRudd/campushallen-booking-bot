#!/bin/sh
set -eu
umask 077
if [ "${1:-}" = "uvicorn" ]; then
    python -m campushallen.cli init-dev
    alembic upgrade head
fi
exec "$@"
