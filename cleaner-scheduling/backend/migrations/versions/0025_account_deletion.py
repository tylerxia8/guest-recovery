from alembic import op

revision = "0025_account_deletion"
down_revision = "0024_manager_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE workspaces ADD COLUMN deleted_at timestamptz;
        ALTER TABLE manager_accounts ADD COLUMN deletion_key_hash text;
        ALTER TABLE manager_accounts ADD COLUMN deletion_request_hash text;
        ALTER TABLE manager_accounts ADD COLUMN deletion_session_hash text;
        ALTER TABLE manager_accounts ADD COLUMN deleted_at timestamptz;
        GRANT UPDATE (name, deleted_at) ON workspaces TO cleaning_app;
        GRANT UPDATE (email, password_hash, signup_request_hash, deletion_key_hash,
            deletion_request_hash, deletion_session_hash, deleted_at) ON manager_accounts TO cleaning_app;
        DO $$
        DECLARE item record;
        BEGIN
            FOR item IN
                SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                AND a.attname = 'workspace_id' AND NOT a.attisdropped
                AND c.relname != 'manager_accounts'
            LOOP
                EXECUTE format(
                    'CREATE POLICY workspace_active ON public.%I AS RESTRICTIVE '
                    'USING (EXISTS (SELECT 1 FROM public.workspaces w '
                    'WHERE w.id = workspace_id AND w.deleted_at IS NULL)) '
                    'WITH CHECK (EXISTS (SELECT 1 FROM public.workspaces w '
                    'WHERE w.id = workspace_id AND w.deleted_at IS NULL))', item.relname
                );
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        DECLARE item record;
        BEGIN
            IF EXISTS (SELECT 1 FROM workspaces WHERE deleted_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Deleted accounts require a reviewed transition before downgrade';
            END IF;
            FOR item IN SELECT tablename FROM pg_policies
                WHERE schemaname = 'public' AND policyname = 'workspace_active'
            LOOP
                EXECUTE format('DROP POLICY workspace_active ON public.%I', item.tablename);
            END LOOP;
        END $$;
        REVOKE UPDATE (name, deleted_at) ON workspaces FROM cleaning_app;
        REVOKE UPDATE (email, password_hash, signup_request_hash, deletion_key_hash,
            deletion_request_hash, deletion_session_hash, deleted_at) ON manager_accounts FROM cleaning_app;
        ALTER TABLE manager_accounts DROP COLUMN deletion_key_hash,
            DROP COLUMN deletion_request_hash, DROP COLUMN deletion_session_hash, DROP COLUMN deleted_at;
        ALTER TABLE workspaces DROP COLUMN deleted_at;
    """)
