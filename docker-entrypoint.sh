#!/bin/sh
# Many hosts mount volumes root-owned. As root: make the data dir writable for the app
# user, then drop privileges. As any other user: just run the command.
set -e

if [ "$(id -u)" = "0" ]; then
    DATA_DIR="${FPL_DATA_DIR:-/data}"
    mkdir -p "$DATA_DIR"
    chown -R 10001:10001 "$DATA_DIR" || echo "warning: could not chown $DATA_DIR" >&2
    exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
fi

exec "$@"
