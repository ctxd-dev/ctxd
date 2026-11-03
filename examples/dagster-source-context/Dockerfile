FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

RUN pip install --no-cache-dir dagster==1.13.28 ctxd==0.1.16 \
    && useradd --create-home runner
WORKDIR /example
COPY source_context.py run-example.sh ./
USER runner
ENV DAGSTER_HOME=/tmp/dagster
CMD ["sh", "/example/run-example.sh"]
