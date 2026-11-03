FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

RUN pip install --no-cache-dir "dlt[duckdb]==1.32.0" duckdb==1.5.6 ctxd==0.1.16 \
    && useradd --create-home runner
WORKDIR /example
COPY source_snapshot.py ./
USER runner
ENV RUNTIME__DLTHUB_TELEMETRY=false
CMD ["python", "/example/source_snapshot.py"]
