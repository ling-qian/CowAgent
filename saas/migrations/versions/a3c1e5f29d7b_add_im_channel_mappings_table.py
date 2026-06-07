"""add_im_channel_mappings_table

Revision ID: a3c1e5f29d7b
Revises: 7fb840c4383d
Create Date: 2026-06-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c1e5f29d7b'
down_revision: Union[str, Sequence[str], None] = '7fb840c4383d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """添加 IM 渠道映射表"""
    op.create_table(
        'im_channel_mappings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('channel_type', sa.String(32), nullable=False),
        sa.Column('app_id', sa.String(256), nullable=False),
        sa.Column('app_secret', sa.String(256), nullable=True),
        sa.Column('extra_config', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('channel_type', 'app_id', name='uq_im_channel_app'),
    )


def downgrade() -> None:
    """回滚 IM 渠道映射表"""
    op.drop_table('im_channel_mappings')
