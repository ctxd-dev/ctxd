FROM ghcr.io/ctxddev/ctxd-mcp@sha256:44323c50f410893e4d2e7f778e9d1a4da36e4a44a46abeaed25de5c91ff0eb11
USER root
RUN pip install --no-cache-dir apache-hamilton==1.90.0 && pip check
WORKDIR /example
COPY run.py /example/run.py
USER ctxd
ENTRYPOINT ["python", "/example/run.py"]
