#!/bin/sh
set -eu
mkdir -p /tmp/ctxd-airflow-source
ctxd backend set ctxfs
ctxd folders add /tmp/ctxd-airflow-source --name AirflowExample
airflow db migrate
python /opt/airflow/dags/ctxd_source_refresh.py
