import sqlalchemy as sa
from alembic import op

revision = "0010_default_eligibility"
down_revision = "0009_cleaner_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cleaners", sa.Column("eligibility_policy", sa.Text(), nullable=False, server_default="allowlist"))
    op.create_check_constraint(
        "ck_cleaners_eligibility_policy", "cleaners", "eligibility_policy IN ('allowlist','all_except_exclusions')"
    )
    op.alter_column("cleaners", "eligibility_policy", server_default="all_except_exclusions")


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM cleaners WHERE eligibility_policy = 'all_except_exclusions')")
    ):
        raise RuntimeError(
            "Default eligibility requires a reviewed transition before restoring allowlist-only behavior."
        )
    op.drop_constraint("ck_cleaners_eligibility_policy", "cleaners", type_="check")
    op.drop_column("cleaners", "eligibility_policy")
