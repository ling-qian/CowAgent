"""add_agent_config_and_knowledge_file_tables

Revision ID: b4d2f6e8a1c3
Revises: a3c1e5f29d7b
Create Date: 2026-06-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d2f6e8a1c3'
down_revision: Union[str, Sequence[str], None] = 'a3c1e5f29d7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """添加 Agent 配置表和知识库文件表"""

    # Agent 配置表 — 每个租户一个独立 Agent
    op.create_table(
        'agent_config',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, unique=True),
        sa.Column('name', sa.String(100), nullable=False, server_default='My Agent'),
        sa.Column('avatar_url', sa.String(500), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        # Core LLM Config
        sa.Column('system_prompt', sa.Text, nullable=True, server_default='You are a helpful assistant.'),
        sa.Column('model', sa.String(100), nullable=True, server_default='deepseek-chat'),
        sa.Column('api_key', sa.String(200), nullable=True),
        sa.Column('api_base', sa.String(200), nullable=True),
        # Capability Config (JSON)
        sa.Column('plugins', sa.Text, nullable=True, server_default='[]'),
        sa.Column('tools', sa.Text, nullable=True, server_default='[]'),
        sa.Column('knowledge_ids', sa.Text, nullable=True, server_default='[]'),
        # Behavior Parameters
        sa.Column('max_steps', sa.Integer, nullable=True, server_default='15'),
        sa.Column('temperature', sa.Float, nullable=True, server_default='0.7'),
        sa.Column('enable_thinking', sa.Boolean, nullable=True, server_default='true'),
        sa.Column('reasoning_effort', sa.String(10), nullable=True, server_default='high'),
        # Metadata
        sa.Column('config_version', sa.Integer, nullable=True, server_default='1'),
        sa.Column('is_active', sa.Boolean, nullable=True, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # 知识库文件表
    op.create_table(
        'knowledge_file',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=False),
        sa.Column('file_size', sa.Integer, nullable=True),
        sa.Column('file_type', sa.String(20), nullable=True),
        sa.Column('chunk_count', sa.Integer, nullable=True, server_default='0'),
        sa.Column('status', sa.String(20), nullable=True, server_default='pending'),
        sa.Column('error_msg', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    """回滚 Agent 配置表和知识库文件表"""
    op.drop_table('knowledge_file')
    op.drop_table('agent_config')
