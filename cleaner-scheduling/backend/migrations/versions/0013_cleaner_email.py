import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_cleaner_email"
down_revision = "0012_standard_cleaning_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cleaners", sa.Column("email_ciphertext", postgresql.BYTEA(), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cleaners WHERE email_ciphertext IS NOT NULL)")):
        raise RuntimeError("Saved cleaner emails require a reviewed data transition before downgrade.")
    op.drop_column("cleaners", "email_ciphertext")
