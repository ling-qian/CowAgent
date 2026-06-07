"""initial_saas_schema

Revision ID: 7fb840c4383d
Revises:
Create Date: 2026-06-04 11:27:14.527985

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7fb840c4383d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 SaaS 管理表 + pgvector 扩展"""

    # pgvector 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 租户表
    op.create_table(
        'tenants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('slug', sa.String(64), nullable=False, unique=True),
        sa.Column('plan', sa.String(32), nullable=False, server_default='free'),
        sa.Column('config_json', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # 用户表
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('email', sa.String(256), nullable=False),
        sa.Column('display_name', sa.String(128), nullable=True),
        sa.Column('role', sa.String(32), nullable=False, server_default='member'),
        sa.Column('sso_uid', sa.String(256), nullable=True, unique=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'email', name='uq_user_tenant_email'),
    )

    # API Key 表
    op.create_table(
        'api_keys',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('key_hash', sa.String(128), nullable=False, unique=True),
        sa.Column('key_prefix', sa.String(8), nullable=False),
        sa.Column('name', sa.String(128), nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime, nullable=True),
    )

    # 用量记录表
    op.create_table(
        'usage_records',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('api_key_id', sa.String(36), nullable=True),
        sa.Column('metric', sa.String(64), nullable=False),
        sa.Column('value', sa.Integer, nullable=False, server_default='0'),
        sa.Column('period', sa.String(7), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # 审计日志表
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False, index=True),
        sa.Column('user_id', sa.String(36), nullable=True),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('resource_type', sa.String(64), nullable=False),
        sa.Column('resource_id', sa.String(36), nullable=True),
        sa.Column('detail', sa.Text, nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # 记忆块表
    op.create_table(
        'memory_chunks',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('user_id', sa.String, nullable=True),
        sa.Column('scope', sa.String(16), nullable=False, server_default='shared'),
        sa.Column('source', sa.String(16), nullable=False, server_default='memory'),
        sa.Column('path', sa.String, nullable=False),
        sa.Column('start_line', sa.Integer, nullable=False),
        sa.Column('end_line', sa.Integer, nullable=False),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('hash', sa.String, nullable=False),
        sa.Column('chunk_meta', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=True, server_default=sa.func.now()),
    )

    # 记忆文件表
    op.create_table(
        'memory_files',
        sa.Column('path', sa.String, primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('source', sa.String(16), nullable=False, server_default='memory'),
        sa.Column('hash', sa.String, nullable=False),
        sa.Column('mtime', sa.BigInteger, nullable=False),
        sa.Column('size', sa.BigInteger, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=True, server_default=sa.func.now()),
    )

    # 索引
    op.create_index('idx_mc_tenant_user', 'memory_chunks', ['tenant_id', 'user_id'])
    op.create_index('idx_mc_tenant_path_hash', 'memory_chunks', ['tenant_id', 'path', 'hash'])

    # pgvector 索引（需要足够数据量，创建可能失败，忽略错误）
    try:
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_mc_embedding "
            "ON memory_chunks USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )
    except Exception:
        pass

    # 全文搜索索引
    try:
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_mc_text_fts "
            "ON memory_chunks USING gin(to_tsvector('simple', text))"
        )
    except Exception:
        pass


def downgrade() -> None:
    """回滚所有表"""
    op.drop_table('memory_files')
    op.drop_table('memory_chunks')
    op.drop_table('audit_logs')
    op.drop_table('usage_records')
    op.drop_table('api_keys')
    op.drop_table('users')
    op.drop_table('tenants')
    op.execute("DROP EXTENSION IF EXISTS vector")
