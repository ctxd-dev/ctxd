FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

RUN pip install --no-cache-dir "prefect==3.9.1" "ctxd==0.1.16"
ENV PREFECT_HOME=/tmp/ctxd-prefect-state \
    PREFECT_SERVER_EPHEMERAL_ENABLED=true \
    PREFECT_SERVER_EPHEMERAL_STARTUP_TIMEOUT_SECONDS=60 \
    PREFECT_SERVER_ANALYTICS_ENABLED=false \
    PREFECT_LOGGING_TO_API_ENABLED=false
WORKDIR /example
COPY refresh_context.py .
CMD ["python", "refresh_context.py"]
