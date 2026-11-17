FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python -m venv "${VIRTUAL_ENV}"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 story2script \
    && useradd --uid 10001 --gid story2script --no-create-home story2script \
    && mkdir -p \
        /app \
        /data/cache/llm \
        /data/config \
        /data/files \
        /data/jobs \
        /data/metrics \
        /data/sessions \
    && chown -R story2script:story2script /app /data

COPY --from=builder /opt/venv /opt/venv
COPY --chown=story2script:story2script src /app/src
COPY --chown=story2script:story2script examples /app/examples
COPY --chown=story2script:story2script schema /app/schema

WORKDIR /app
USER story2script

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3); assert response.status == 200 and json.load(response).get('status') == 'ok'"]

CMD ["uvicorn", "story2script.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
