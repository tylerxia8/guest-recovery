FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
WORKDIR /app
COPY guest-recovery/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --timeout 30 --retries 2 -r requirements.txt
ENV INSTRUCTION_MODEL_PATH=/opt/instruction-model HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
COPY guest-recovery/instruction_search.py /app/instruction_search.py
RUN python3 instruction_search.py --prepare-model
COPY --chown=app:app guest-recovery/*.py /app/
COPY --chown=app:app guest-recovery/web /app/web
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATABASE_PATH=/data/recovery.sqlite3 ENV_FILE=/data/.env BIND_HOST=0.0.0.0 PORT=3000
USER app
EXPOSE 3000
CMD ["python3", "entrypoint.py"]
