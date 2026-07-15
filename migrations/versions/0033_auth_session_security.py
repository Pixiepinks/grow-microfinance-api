"""Add authentication session security fields"""

from alembic import op
import sqlalchemy as sa

revision = "0033_auth_session_security"
down_revision = "0032_disburse_read_indexes"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("password_changed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("locked_until", sa.DateTime(), nullable=True))

    op.create_table(
        "password_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_password_history_user_id"), "password_history", ["user_id"])
    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jti", sa.String(length=36), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("token_type", sa.String(length=16), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_revoked_tokens_jti"), "revoked_tokens", ["jti"])
    op.create_index(op.f("ix_revoked_tokens_user_id"), "revoked_tokens", ["user_id"])


def downgrade():
    op.drop_index(op.f("ix_revoked_tokens_user_id"), table_name="revoked_tokens")
    op.drop_index(op.f("ix_revoked_tokens_jti"), table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    op.drop_index(op.f("ix_password_history_user_id"), table_name="password_history")
    op.drop_table("password_history")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("locked_until")
        batch.drop_column("failed_login_attempts")
        batch.drop_column("token_version")
        batch.drop_column("password_changed_at")
        batch.drop_column("must_change_password")
