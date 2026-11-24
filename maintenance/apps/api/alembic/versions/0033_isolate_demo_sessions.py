"""Restrict public demo sessions to explicitly seeded fictional users."""

from pathlib import Path

from alembic import op
from psycopg import ClientCursor

revision = "0033_isolate_demo_sessions"
down_revision = "0032_operations_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION public.app_demo_sample_session(p_role text)
        RETURNS TABLE (user_id uuid, organization_id uuid, display_name text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT u.id, u.organization_id, u.display_name
          FROM public.users AS u
          JOIN public.role_grants AS g
            ON g.organization_id = u.organization_id AND g.user_id = u.id
          WHERE current_database() = 'grok_demo_sample'
            AND u.organization_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'
            AND u.issuer = 'https://local.invalid/dev-auth'
            AND u.id = CASE p_role
              WHEN 'property_manager' THEN 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3'
              WHEN 'accounting_staff' THEN 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb4'
              WHEN 'tenant' THEN 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb5'
              WHEN 'prospect' THEN 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb6'
              WHEN 'owner' THEN 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb7'
              WHEN 'contractor' THEN 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1'
            END::uuid
            AND g.role = p_role
            AND g.resource_type IS NULL
            AND g.active_from <= clock_timestamp()
            AND (g.active_until IS NULL OR g.active_until > clock_timestamp())
          LIMIT 1;
        $$;
    """)


def downgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "sql" / "0009_demo_sample_up.sql"
    raw = op.get_bind().connection.driver_connection
    with ClientCursor(raw) as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))
