#!/bin/sh
set -eu
mkdir -p /tmp/ctxd-zenml-source
ctxd backend set ctxfs
ctxd folders add /tmp/ctxd-zenml-source --name ZenMLExample
python /example/source_context.py
