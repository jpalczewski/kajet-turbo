#!/usr/bin/env bash
# Poll a detached container until it's running and a ready-check command
# succeeds, or fail loudly (dumping container logs) once it stops running or
# the timeout elapses. Shared by the app/ingress image smoke tests in
# .github/actions/validate-image/action.yml so the polling loop only exists
# once.
#
# Usage: scripts/wait-for-container.sh <container-name> <timeout-seconds> -- <ready-check-command...>

set -euo pipefail

if [[ $# -lt 3 || "$3" != "--" ]]; then
  echo "usage: $0 <container-name> <timeout-seconds> -- <ready-check-command...>" >&2
  exit 2
fi

container="$1"
timeout="$2"
shift 3

deadline=$((SECONDS + timeout))
while (( SECONDS < deadline )); do
  if [[ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]]; then
    docker logs "$container"
    exit 1
  fi
  if "$@"; then
    exit 0
  fi
  sleep 1
done

docker logs "$container"
exit 1
