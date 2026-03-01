#!/bin/sh
source .venv/bin/activate
export FLASK_DEBUG=1
DEFAULT_PORT=8080
python -u -m flask --app main run --host 0.0.0.0 --port ${PORT:-$DEFAULT_PORT}