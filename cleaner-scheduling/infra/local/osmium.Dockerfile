FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends osmium-tool=1.18.0-1 && rm -rf /var/lib/apt/lists/*
