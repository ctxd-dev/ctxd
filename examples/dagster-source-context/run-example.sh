#!/bin/sh
set -eu
mkdir -p /tmp/ctxd-dagster-source /tmp/dagster
printf 'telemetry:\n  enabled: false\n' > /tmp/dagster/dagster.yaml
ctxd backend set ctxfs
ctxd folders add /tmp/ctxd-dagster-source --name DagsterExample
python /example/source_context.py
