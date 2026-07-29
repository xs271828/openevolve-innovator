FROM python:3.12-slim

ARG OPENEVOLVE_VERSION=0.3.2
WORKDIR /experiment

COPY requirements-evaluator.txt /tmp/requirements-evaluator.txt
RUN python -m pip install --no-cache-dir "openevolve==${OPENEVOLVE_VERSION}" \
    && if [ -s /tmp/requirements-evaluator.txt ]; then \
         python -m pip install --no-cache-dir -r /tmp/requirements-evaluator.txt; \
       fi

ENTRYPOINT ["openevolve-run"]
