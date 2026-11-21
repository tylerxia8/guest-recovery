import sqlalchemy as sa
from alembic import op

revision = "0011_cleaner_onboarding"
down_revision = "0010_default_eligibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cleaner_access_links", sa.Column("purpose", sa.Text(), nullable=False, server_default="jobs"))
    op.create_check_constraint(
        "ck_cleaner_access_links_purpose", "cleaner_access_links", "purpose IN ('jobs','onboarding')"
    )
    op.drop_constraint("ck_audit_logs_actor_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_actor_type", "audit_logs", "actor_type IN ('operator','anonymous','system','cleaner')"
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM audit_logs WHERE actor_type = 'cleaner')")):
        raise RuntimeError("Cleaner audit history requires a reviewed transition before downgrade.")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cleaner_access_links WHERE purpose = 'onboarding')")):
        raise RuntimeError("Onboarding access requires a reviewed transition before downgrade.")
    op.drop_constraint("ck_audit_logs_actor_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_actor_type", "audit_logs", "actor_type IN ('operator','anonymous','system')"
    )
    op.drop_constraint("ck_cleaner_access_links_purpose", "cleaner_access_links", type_="check")
    op.drop_column("cleaner_access_links", "purpose")
