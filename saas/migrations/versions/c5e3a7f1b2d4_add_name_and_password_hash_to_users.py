"""add name and password_hash to users

Revision ID: c5e3a7f1b2d4
Revises: b4d2f6e8a1c3
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

revision = "c5e3a7f1b2d4"
down_revision = "b4d2f6e8a1c3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("name", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("users", "password_hash")
    op.drop_column("users", "name")
