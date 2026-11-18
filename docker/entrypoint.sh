#!/bin/bash
set -euo pipefail
mkdir -p /workdir /run
chown builder:builder /workdir /run
# Only the disposable recipe snapshot may need repair after a root-run sync.
if [ -d /workdir/code/recipe ]; then
  find -P /workdir/code/recipe -xdev \( ! -user builder -o ! -group builder \) \
    -exec chown -h builder:builder {} +
fi
exec runuser -u builder -- /usr/local/bin/build.sh "$@"
