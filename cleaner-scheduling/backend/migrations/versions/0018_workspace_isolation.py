from alembic import op

revision = "0018_workspace_isolation"
down_revision = "0017_offer_emails"
branch_labels = None
depends_on = None

TABLES = """ARRAY[
    'operator_profiles', 'policy_versions', 'audit_logs', 'webhook_events', 'outbox_events',
    'idempotency_records', 'properties', 'reservation_sync_runs', 'reservations',
    'property_instructions', 'exceptions', 'cleaning_tasks', 'reservation_import_previews',
    'cleaners', 'cleaner_property_eligibility', 'cleaner_daily_availability', 'cleaner_rates',
    'routing_snapshots', 'schedule_days', 'schedule_runs', 'cleaner_schedule_versions',
    'assignments', 'payment_batches', 'payment_line_items', 'sms_messages', 'acknowledgments',
    'cleaner_access_links', 'cleaner_weekly_hours', 'email_deliveries', 'cleaning_offers'
]"""


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'cleaning_app'
                AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
            ) THEN
                RAISE EXCEPTION 'cleaning_app must be an unprivileged role without BYPASSRLS';
            END IF;
        END $$;
        CREATE TABLE public.workspaces (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            name text NOT NULL CHECK (length(trim(name)) > 0)
        );
        INSERT INTO public.workspaces (id, name)
        VALUES ('00000000-0000-0000-0000-000000000001', 'Legacy workspace');
        GRANT SELECT, INSERT ON public.workspaces TO cleaning_app;
    """)
    op.execute(f"""
        DO $$
        DECLARE
            tables text[] := {TABLES};
            table_name text;
            item record;
            key_column text;
            definition text;
        BEGIN
            FOREACH table_name IN ARRAY tables LOOP
                IF EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = table_name
                    AND pg_has_role('cleaning_app', c.relowner, 'MEMBER')
                ) THEN
                    RAISE EXCEPTION 'cleaning_app cannot own business tables or inherit their owner';
                END IF;
                EXECUTE format(
                    'ALTER TABLE public.%I ADD COLUMN workspace_id uuid NOT NULL '
                    'DEFAULT %L::uuid REFERENCES public.workspaces(id)',
                    table_name, '00000000-0000-0000-0000-000000000001'
                );
                EXECUTE format(
                    'ALTER TABLE public.%I ALTER COLUMN workspace_id SET DEFAULT '
                    'nullif(current_setting(''app.workspace_id'', true), '''')::uuid', table_name
                );
                key_column := CASE WHEN table_name = 'cleaner_weekly_hours' THEN 'cleaner_id' ELSE 'id' END;
                EXECUTE format(
                    'ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (workspace_id, %I)',
                    table_name, 'uq_workspace_' || table_name, key_column
                );
            END LOOP;
            FOR item IN
                SELECT c.conname, t.relname, pg_get_constraintdef(c.oid) AS definition
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_class target ON target.oid = c.confrelid
                WHERE n.nspname = 'public' AND t.relname = ANY(tables)
                AND c.contype = 'f' AND target.relname = ANY(tables)
            LOOP
                definition := replace(item.definition, '(', '(workspace_id, ');
                EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', item.relname, item.conname);
                EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s',
                    item.relname, item.conname, definition);
            END LOOP;
            FOR item IN
                SELECT c.conname, t.relname, c.contype, pg_get_constraintdef(c.oid) AS definition
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public' AND t.relname = ANY(tables)
                AND c.contype IN ('u', 'x') AND c.conname != 'uq_workspace_' || t.relname
            LOOP
                definition := regexp_replace(item.definition, '\\(',
                    CASE WHEN item.contype = 'x' THEN '(workspace_id WITH =, ' ELSE '(workspace_id, ' END);
                EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', item.relname, item.conname);
                EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s',
                    item.relname, item.conname, definition);
            END LOOP;
            FOR item IN
                SELECT idx.relname, pg_get_indexdef(i.indexrelid) AS definition
                FROM pg_index i
                JOIN pg_class t ON t.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_class idx ON idx.oid = i.indexrelid
                WHERE n.nspname = 'public' AND t.relname = ANY(tables) AND i.indisunique
                AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)
            LOOP
                definition := regexp_replace(item.definition, '\\(', '(workspace_id, ');
                EXECUTE format('DROP INDEX public.%I', item.relname);
                EXECUTE definition;
            END LOOP;
            FOREACH table_name IN ARRAY tables LOOP
                EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
                EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
                EXECUTE format(
                    'CREATE POLICY workspace_isolation ON public.%I '
                    'USING (workspace_id = nullif(current_setting(''app.workspace_id'', true), '''')::uuid) '
                    'WITH CHECK (workspace_id = nullif(current_setting(''app.workspace_id'', true), '''')::uuid)',
                    table_name
                );
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE
            tables text[] := {TABLES};
            table_name text;
            item record;
            has_other_workspace boolean;
            definition text;
        BEGIN
            LOCK TABLE public.workspaces IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (
                SELECT 1 FROM public.workspaces WHERE id != '00000000-0000-0000-0000-000000000001'::uuid
            ) THEN
                RAISE EXCEPTION 'Nonlegacy workspaces require a reviewed transition before downgrade';
            END IF;
            FOREACH table_name IN ARRAY tables LOOP
                EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', table_name);
                EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', table_name);
                EXECUTE format(
                    'SELECT EXISTS (SELECT 1 FROM public.%I WHERE workspace_id != %L::uuid)',
                    table_name, '00000000-0000-0000-0000-000000000001'
                ) INTO has_other_workspace;
                IF has_other_workspace THEN
                    RAISE EXCEPTION 'Nonlegacy business data requires a reviewed transition before downgrade';
                END IF;
            END LOOP;
            FOR item IN
                SELECT c.conname, t.relname, pg_get_constraintdef(c.oid) AS definition
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_class target ON target.oid = c.confrelid
                WHERE n.nspname = 'public' AND t.relname = ANY(tables)
                AND c.contype = 'f' AND target.relname = ANY(tables)
            LOOP
                definition := replace(item.definition, '(workspace_id, ', '(');
                EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', item.relname, item.conname);
                EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s',
                    item.relname, item.conname, definition);
            END LOOP;
            FOR item IN
                SELECT c.conname, t.relname, pg_get_constraintdef(c.oid) AS definition
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public' AND t.relname = ANY(tables)
                AND c.contype IN ('u', 'x') AND c.conname != 'uq_workspace_' || t.relname
            LOOP
                definition := replace(replace(item.definition,
                    '(workspace_id, ', '('), '(workspace_id WITH =, ', '(');
                EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', item.relname, item.conname);
                EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s',
                    item.relname, item.conname, definition);
            END LOOP;
            FOR item IN
                SELECT idx.relname, pg_get_indexdef(i.indexrelid) AS definition
                FROM pg_index i
                JOIN pg_class t ON t.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_class idx ON idx.oid = i.indexrelid
                WHERE n.nspname = 'public' AND t.relname = ANY(tables) AND i.indisunique
                AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)
            LOOP
                definition := replace(item.definition, '(workspace_id, ', '(');
                EXECUTE format('DROP INDEX public.%I', item.relname);
                EXECUTE definition;
            END LOOP;
            FOREACH table_name IN ARRAY tables LOOP
                EXECUTE format('DROP POLICY workspace_isolation ON public.%I', table_name);
                EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', table_name);
                EXECUTE format('ALTER TABLE public.%I DROP COLUMN workspace_id', table_name);
            END LOOP;
            DROP TABLE public.workspaces;
        END $$;
    """)
