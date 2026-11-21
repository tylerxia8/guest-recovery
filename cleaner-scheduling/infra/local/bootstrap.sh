#!/usr/bin/env bash
# Create local roles and a gitignored .env. Passwords are local fixtures only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT}/backend/.env"

if ! command -v psql >/dev/null 2>&1; then
  echo "PostgreSQL client is required." >&2
  exit 1
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cleaning_migrator') THEN
    CREATE ROLE cleaning_migrator LOGIN PASSWORD 'local-dev-only';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cleaning_app') THEN
    CREATE ROLE cleaning_app LOGIN PASSWORD 'local-dev-only';
  END IF;
END
$$;
SQL

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='cleaning'" | grep -q 1; then
  sudo -u postgres createdb -O cleaning_migrator cleaning
fi
sudo -u postgres psql -d cleaning -v ON_ERROR_STOP=1 -c "GRANT CONNECT ON DATABASE cleaning TO cleaning_app;"

if [[ ! -f "${ENV_FILE}" ]]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  cat > "${ENV_FILE}" <<EOF
APP_ENV=development
OPERATIONAL_TIMEZONE=America/New_York
RESERVATION_PROVIDER=mock
SMS_PROVIDER=mock
ROUTING_PROVIDER=mock
AUTH_PROVIDER=mock
PRODUCTION_INTEGRATIONS_ENABLED=false
PRODUCTION_DISPATCH_ENABLED=false
DATABASE_URL=postgresql+psycopg://cleaning_app:local-dev-only@127.0.0.1:5432/cleaning
MIGRATION_DATABASE_URL=postgresql+psycopg://cleaning_migrator:local-dev-only@127.0.0.1:5432/cleaning
REDIS_URL=redis://127.0.0.1:6379/0
AUTH_ISSUER=http://localhost/mock-issuer
AUTH_AUDIENCE=cleaning-local
AUTH_MOCK_SECRET=${SECRET}
HOSPITABLE_CREDENTIAL_SECRET_REF=
HOSPITABLE_WEBHOOK_SECRET_REF=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_MESSAGING_SERVICE_SID=
GOOGLE_MAPS_API_KEY=
CORS_ORIGINS=http://127.0.0.1:3000
POLICY_APPROVAL_REQUIRED=true
EOF
  echo "Wrote ${ENV_FILE}"
else
  echo "Left existing ${ENV_FILE} unchanged"
fi
