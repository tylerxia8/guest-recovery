FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
WORKDIR /app
COPY --chown=app:app guest-recovery/*.py /app/
COPY --chown=app:app guest-recovery/web /app/web
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATABASE_PATH=/data/recovery.sqlite3 ENV_FILE=/data/.env BIND_HOST=0.0.0.0 PORT=3000
USER app
EXPOSE 3000
CMD ["python3", "entrypoint.py"]
