FROM apache/airflow:3.3.2-python3.12@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1

RUN pip install --no-cache-dir "apache-airflow==3.3.2" "ctxd==0.1.16"
ENV AIRFLOW__CORE__LOAD_EXAMPLES=False
COPY --chown=airflow:root ctxd_source_refresh.py /opt/airflow/dags/ctxd_source_refresh.py
COPY --chown=airflow:root run-example.sh /opt/airflow/run-example.sh
CMD ["bash", "/opt/airflow/run-example.sh"]
