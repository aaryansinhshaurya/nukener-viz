"""Keep chunk predictions that cannot be aligned to an exact text span."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_optional_entity_offsets"
down_revision = "20261013_chunk_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("entities", "start_char", existing_type=sa.Integer(), nullable=True)
    op.alter_column("entities", "end_char", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("entities", "end_char", existing_type=sa.Integer(), nullable=False)
    op.alter_column("entities", "start_char", existing_type=sa.Integer(), nullable=False)
