FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
WORKDIR /app
COPY guest-recovery/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --timeout 30 --retries 2 -r requirements.txt \
    sqlalchemy==2.1.8 pydantic==2.14.3 pydantic-settings==2.16.2 "psycopg[binary]==3.3.8" cryptography==50.0.2
ENV INSTRUCTION_MODEL_PATH=/opt/instruction-model HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
COPY guest-recovery/instruction_search.py /app/instruction_search.py
RUN python3 instruction_search.py --prepare-model
COPY --chown=app:app cleaner-scheduling/backend/app /scheduler/backend/app
RUN PYTHONPATH=/scheduler/backend python -c "from app.services import guest_recovery; assert callable(guest_recovery.handle)"
ENV INTERNAL_SCHEDULER_BACKEND=/scheduler/backend
COPY --chown=app:app guest-recovery/*.py /app/
COPY --chown=app:app guest-recovery/web /app/web
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATABASE_PATH=/data/recovery.sqlite3 ENV_FILE=/data/.env BIND_HOST=0.0.0.0 PORT=3000
USER app
EXPOSE 3000
CMD ["python3", "entrypoint.py"]
