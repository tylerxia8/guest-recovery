import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_cleaner_reading_languages"
down_revision = "0021_onboarding_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cleaners",
        sa.Column("reading_languages", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_check_constraint(
        "ck_cleaner_reading_languages",
        "cleaners",
        "jsonb_typeof(reading_languages) = 'array' "
        'AND reading_languages <@ \'["en","es","pt"]\'::jsonb '
        "AND jsonb_array_length(reading_languages) = "
        "((reading_languages ? 'en')::int + (reading_languages ? 'es')::int + (reading_languages ? 'pt')::int)",
    )


def downgrade() -> None:
    op.execute("SET LOCAL row_security = off")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cleaners WHERE reading_languages <> '[]'::jsonb)")):
        raise RuntimeError("Export cleaner reading languages before downgrading.")
    op.drop_constraint("ck_cleaner_reading_languages", "cleaners", type_="check")
    op.drop_column("cleaners", "reading_languages")
