from alembic import op

revision = "0032_operations_workspace"
down_revision = "0031_maintenance_bridge"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        GRANT SELECT (id, settings) ON public.organizations TO grok_auth;
        GRANT INSERT ON public.organizations, public.users, public.role_grants TO grok_auth;
        CREATE FUNCTION public.app_provision_operations_workspace(workspace uuid, account uuid, grant_id uuid)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path TO pg_catalog, public
        AS $$
        BEGIN
            IF workspace IS NULL OR account IS NULL OR grant_id IS NULL THEN
                RETURN false;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('lucena-workspace:' || workspace::text, 0));

            IF EXISTS (SELECT 1 FROM public.organizations WHERE id = workspace) THEN
                RETURN EXISTS (
                    SELECT 1
                    FROM public.organizations AS org
                    JOIN public.users AS manager ON manager.organization_id = org.id
                    WHERE org.id = workspace
                      AND org.settings ->> 'lucena_account_id' = account::text
                      AND manager.id = account
                      AND manager.issuer = 'lucena-operations'
                      AND manager.subject = account::text
                );
            END IF;

            INSERT INTO public.organizations (id, organization_id, name, settings, default_timezone)
            VALUES (workspace, workspace, 'Lucena workspace',
                    jsonb_build_object('lucena_account_id', account::text), 'America/New_York');
            INSERT INTO public.users (id, organization_id, issuer, subject, display_name)
            VALUES (account, workspace, 'lucena-operations', account::text, 'Lucena manager');
            INSERT INTO public.role_grants (id, organization_id, user_id, role, active_from)
            VALUES (grant_id, workspace, account, 'property_manager', clock_timestamp());
            RETURN true;
        END;
        $$;
        ALTER FUNCTION public.app_provision_operations_workspace(uuid, uuid, uuid) OWNER TO grok_auth;
        REVOKE ALL ON FUNCTION public.app_provision_operations_workspace(uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.app_provision_operations_workspace(uuid, uuid, uuid) TO grok_app;
    """)


def downgrade():
    op.execute("""
        DROP FUNCTION public.app_provision_operations_workspace(uuid, uuid, uuid);
        REVOKE SELECT (id, settings) ON public.organizations FROM grok_auth;
        REVOKE INSERT ON public.organizations, public.users, public.role_grants FROM grok_auth;
    """)
