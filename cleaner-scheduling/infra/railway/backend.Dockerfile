FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN pip install --no-cache-dir --timeout 30 uv==0.13.7
RUN groupadd --gid 10001 turnover && useradd --uid 10001 --gid turnover --no-create-home turnover
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini backend/hosted_startup.py ./
CMD ["python", "hosted_startup.py"]
