"""Document metadata, invitations, password resets, and recoverable deletion."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260919_platform_release"
down_revision = "ff89424a6073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("documents", sa.Column("filename", sa.String(500), nullable=True))
    op.add_column("documents", sa.Column("source", sa.String(500), nullable=True))
    op.add_column("documents", sa.Column("cleaned_title", sa.String(500), nullable=True))
    op.add_column("projects", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "project_invitations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "OWNER",
                "REVIEWER",
                "VIEWER",
                name="projectrole",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "email",
            name="uq_project_invitation_email",
        ),
    )

    op.create_table(
        "password_resets",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("password_resets")
    op.drop_table("project_invitations")
    op.drop_column("projects", "deleted_at")
    op.drop_column("documents", "cleaned_title")
    op.drop_column("documents", "source")
    op.drop_column("documents", "filename")
    op.drop_column("users", "session_version")