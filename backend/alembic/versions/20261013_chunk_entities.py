"""Store ordered document chunks and entity confidence for display."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_chunk_entities"
down_revision = "20260919_platform_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("sentences", "chunks")
    op.alter_column("chunks", "sentence_id_external", new_column_name="chunk_id_external")
    op.alter_column("entities", "sentence_id", new_column_name="chunk_id")
    op.drop_constraint("uq_document_sentence", "chunks", type_="unique")
    op.create_unique_constraint("uq_document_chunk", "chunks", ["document_id", "chunk_id_external"])
    op.add_column("chunks", sa.Column("position", sa.Integer(), server_default="0", nullable=False))
    op.add_column("entities", sa.Column("confidence_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("entities", "confidence_score")
    op.drop_column("chunks", "position")
    op.drop_constraint("uq_document_chunk", "chunks", type_="unique")
    op.alter_column("entities", "chunk_id", new_column_name="sentence_id")
    op.alter_column("chunks", "chunk_id_external", new_column_name="sentence_id_external")
    op.create_unique_constraint("uq_document_sentence", "chunks", ["document_id", "sentence_id_external"])
    op.rename_table("chunks", "sentences")
