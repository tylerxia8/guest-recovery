import sqlalchemy as sa
from alembic import op

revision = "0023_cleaner_preferred_language"
down_revision = "0022_cleaner_reading_languages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cleaners", sa.Column("preferred_language", sa.Text(), nullable=True))
    op.create_check_constraint("ck_cleaner_preferred_language", "cleaners", "preferred_language IN ('en', 'es', 'pt')")


def downgrade() -> None:
    op.execute("SET LOCAL row_security = off")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cleaners WHERE preferred_language IS NOT NULL)")):
        raise RuntimeError("Export cleaner language preferences before downgrading.")
    op.drop_constraint("ck_cleaner_preferred_language", "cleaners", type_="check")
    op.drop_column("cleaners", "preferred_language")
