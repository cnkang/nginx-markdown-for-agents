#!/bin/bash -eu
# ClusterFuzzLite/OSS-Fuzz entrypoint.
#
# ClusterFuzzLite invokes /src/build.sh inside its build container. Keep the
# repo-root wrapper small and delegate the project-specific logic to the
# tracked .clusterfuzzlite script.

if [ -f /src/.clusterfuzzlite/build.sh ]; then
    exec /bin/bash /src/.clusterfuzzlite/build.sh "$@"
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "${script_dir}/.clusterfuzzlite/build.sh" "$@"
