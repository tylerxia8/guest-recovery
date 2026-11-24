"""Provision multiple verified Cleaner managers in one Maintenance workspace."""

from alembic import op

revision = "0034_operations_managers"
down_revision = "0033_isolate_demo_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION public.app_provision_operations_manager(
            workspace uuid, owner_account uuid, manager_account uuid, grant_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path TO pg_catalog, public
        AS $$
        BEGIN
            IF workspace IS NULL OR owner_account IS NULL OR manager_account IS NULL OR grant_id IS NULL THEN
                RETURN false;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('lucena-workspace:' || workspace::text, 0));

            IF NOT EXISTS (SELECT 1 FROM public.organizations WHERE id = workspace) THEN
                INSERT INTO public.organizations (id, organization_id, name, settings, default_timezone)
                VALUES (workspace, workspace, 'Lucena workspace',
                        jsonb_build_object('lucena_account_id', owner_account::text), 'America/New_York');
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM public.organizations AS org
                WHERE org.id = workspace AND org.settings ->> 'lucena_account_id' = owner_account::text
            ) THEN
                RETURN false;
            END IF;

            INSERT INTO public.users (id, organization_id, issuer, subject, display_name)
            VALUES (manager_account, workspace, 'lucena-operations', manager_account::text, 'Lucena manager')
            ON CONFLICT (id) DO NOTHING;
            IF FOUND THEN
                INSERT INTO public.role_grants (id, organization_id, user_id, role, active_from)
                VALUES (grant_id, workspace, manager_account, 'property_manager', clock_timestamp());
            END IF;

            RETURN EXISTS (
                SELECT 1 FROM public.users AS manager
                WHERE manager.id = manager_account AND manager.organization_id = workspace
                  AND manager.issuer = 'lucena-operations' AND manager.subject = manager_account::text
            );
        END;
        $$;
        ALTER FUNCTION public.app_provision_operations_manager(uuid, uuid, uuid, uuid) OWNER TO grok_auth;
        REVOKE ALL ON FUNCTION public.app_provision_operations_manager(uuid, uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.app_provision_operations_manager(uuid, uuid, uuid, uuid) TO grok_app;
    """)


def downgrade():
    op.execute("DROP FUNCTION public.app_provision_operations_manager(uuid, uuid, uuid, uuid)")
