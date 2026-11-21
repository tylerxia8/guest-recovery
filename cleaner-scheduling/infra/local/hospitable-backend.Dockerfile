FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN pip install --no-cache-dir --timeout 30 uv==0.13.7
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --all-groups
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
