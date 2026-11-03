FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

RUN pip install --no-cache-dir "zenml[local]==0.98.0" ctxd==0.1.16 \
    && useradd --create-home runner
WORKDIR /example
COPY source_context.py run-example.sh ./
USER runner
ENV ZENML_ANALYTICS_OPT_IN=false \
    ZENML_CONFIG_PATH=/tmp/zenml
CMD ["sh", "/example/run-example.sh"]
