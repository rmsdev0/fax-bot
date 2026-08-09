#!/usr/bin/env bash
# Run web + worker in one task (they share the /data volume and the database).
# If either process dies, exit so the platform restarts the task.
set -e

uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 &
uv run python -m app.worker.run &

wait -n
echo "a fax-bot process exited; restarting machine" >&2
exit 1
