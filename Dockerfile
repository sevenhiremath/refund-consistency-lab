FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LAB_DB_PATH=/srv/var/lab.db \
    PORT=7860

# Run as an unprivileged user. Some hosts also expect UID 1000.
RUN useradd --create-home --uid 1000 app

WORKDIR /srv

COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app app ./app
# The scenario fixtures ship with the image. Without them the picker is empty.
COPY --chown=app:app fixtures ./fixtures

# Optional build-time SHA. Left empty so a platform value such as RENDER_GIT_COMMIT
# is not masked by an image default.
ARG GIT_SHA=
ENV LAB_GIT_SHA=${GIT_SHA}

RUN pip install --no-cache-dir . \
    && mkdir -p /srv/var \
    && chown app:app /srv/var

USER app
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','7860'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/health', timeout=3).status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]