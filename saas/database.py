# encoding:utf-8
"""
SQLAlchemy 数据库模型与初始化

Phase 1 表结构：
- tenants: 租户主表
- users: 用户表（关联租户）
- api_keys: API 密钥表（关联租户）
- usage_records: 用量记录表（关联租户）
- memory_chunks: 记忆块表（pgvector 向量搜索）
- memory_files: 文件元数据表
"""

import uuid
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _gen_id():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 模型定义
# ---------------------------------------------------------------------------

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.String(36), primary_key=True, default=_gen_id)
    name = db.Column(db.String(128), nullable=False, unique=True)
    slug = db.Column(db.String(64), nullable=False, unique=True)
    plan = db.Column(db.String(32), nullable=False, default="free")
    config_json = db.Column(db.Text, nullable=True)  # JSON 格式的租户级配置覆盖
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    users = db.relationship("User", backref="tenant", lazy="dynamic")
    api_keys = db.relationship("ApiKey", backref="tenant", lazy="dynamic")

    def __repr__(self):
        return f"<Tenant {self.slug}>"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=_gen_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    email = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(128), nullable=True)
    role = db.Column(db.String(32), nullable=False, default="member")  # owner / admin / member
    sso_uid = db.Column(db.String(256), nullable=True, unique=True)  # SSO 身份标识 (provider:id)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
    )

    def __repr__(self):
        return f"<User {self.email} @ {self.tenant_id}>"


class ApiKey(db.Model):
    __tablename__ = "api_keys"

    id = db.Column(db.String(36), primary_key=True, default=_gen_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    key_hash = db.Column(db.String(128), nullable=False, unique=True)  # SHA256(api_key_raw)
    key_prefix = db.Column(db.String(8), nullable=False)  # 前8位，用于展示和检索
    name = db.Column(db.String(128), nullable=True)  # 密钥标签
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    last_used_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<ApiKey {self.key_prefix}... @ {self.tenant_id}>"


class UsageRecord(db.Model):
    __tablename__ = "usage_records"

    id = db.Column(db.String(36), primary_key=True, default=_gen_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    api_key_id = db.Column(db.String(36), db.ForeignKey("api_keys.id"), nullable=True)
    metric = db.Column(db.String(64), nullable=False)  # "llm_tokens" / "api_calls" / ...
    value = db.Column(db.Integer, nullable=False, default=0)
    period = db.Column(db.String(7), nullable=False)  # "2026-06" 格式
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "api_key_id", "metric", "period",
                            name="uq_usage_tenant_key_metric_period"),
    )

    def __repr__(self):
        return f"<UsageRecord {self.metric}={self.value} @ {self.tenant_id}>"


class AuditLog(db.Model):
    """审计日志表"""
    __tablename__ = "audit_logs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), nullable=False, index=True)
    user_id = db.Column(db.String(36), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    resource_type = db.Column(db.String(64), nullable=False)
    resource_id = db.Column(db.String(36), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AuditLog {self.action} {self.resource_type}/{self.resource_id}>"


class WebhookEndpoint(db.Model):
    """Webhook 端点表"""
    __tablename__ = "webhook_endpoints"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    url = db.Column(db.String(512), nullable=False)
    secret = db.Column(db.String(64), nullable=False)
    events = db.Column(db.Text, nullable=False, default="[]")  # JSON array of event types
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_delivery_at = db.Column(db.DateTime, nullable=True)
    last_delivery_status = db.Column(db.String(16), nullable=True)

    def __repr__(self):
        return f"<WebhookEndpoint {self.url} @ {self.tenant_id}>"


class TenantPluginConfig(db.Model):
    """租户级插件配置表"""
    __tablename__ = "tenant_plugin_configs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), nullable=False, index=True)
    plugin_name = db.Column(db.String(64), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    priority = db.Column(db.Integer, nullable=True)
    config_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "plugin_name", name="uq_tenant_plugin"),
    )

    def __repr__(self):
        return f"<TenantPluginConfig {self.plugin_name} @ {self.tenant_id}>"


class IMChannelMapping(db.Model):
    """IM 渠道映射表 — 将 IM 平台的 AppID 映射到租户

    支持的渠道类型：
    - feishu: 飞书应用 (feishu_app_id)
    - dingtalk: 钉钉应用 (dingtalk_client_id)
    - wechat_mp: 微信公众号 (wechat_mp_app_id)
    - wechat_com: 企业微信 (wechat_com_corp_id)
    - wechat_kf: 微信客服 (wechat_kf_corp_id)
    - wecom_bot: 企微机器人 (wecom_bot_key)
    """
    __tablename__ = "im_channel_mappings"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    channel_type = db.Column(db.String(32), nullable=False)  # feishu / dingtalk / wechat_mp / ...
    app_id = db.Column(db.String(256), nullable=False)  # IM 平台的应用 ID
    app_secret = db.Column(db.String(256), nullable=True)  # 加密存储的应用密钥
    extra_config = db.Column(db.Text, nullable=True)  # JSON 格式的额外配置（token, encoding_aes_key 等）
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("channel_type", "app_id", name="uq_im_channel_app"),
    )

    def __repr__(self):
        return f"<IMChannelMapping {self.channel_type}:{self.app_id} @ {self.tenant_id}>"


class AgentConfig(db.Model):
    """租户级 Agent 配置表 — 每个租户一个独立 Agent"""
    __tablename__ = "agent_config"

    id = db.Column(db.String(36), primary_key=True, default=_gen_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False, default="My Agent")
    avatar_url = db.Column(db.String(500))
    description = db.Column(db.Text)

    # Core LLM Config
    system_prompt = db.Column(db.Text, default="You are a helpful assistant.")
    model = db.Column(db.String(100), default="deepseek-chat")
    api_key = db.Column(db.String(200))       # Tenant's own LLM key (optional)
    api_base = db.Column(db.String(200))      # Tenant's own API base (optional)

    # Capability Config (JSON)
    plugins = db.Column(db.Text)                     # JSON: ["web_search", "code_interpreter"], NULL=use defaults
    tools = db.Column(db.Text)                       # JSON: custom tool definitions, NULL=empty
    knowledge_ids = db.Column(db.Text)               # JSON: knowledge file ID list, NULL=empty

    # Behavior Parameters
    max_steps = db.Column(db.Integer, default=15)
    temperature = db.Column(db.Float, default=0.7)
    enable_thinking = db.Column(db.Boolean, default=True)
    reasoning_effort = db.Column(db.String(10), default="high")

    # Metadata
    config_version = db.Column(db.Integer, default=1)  # Incremented on each update
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    # Relationships
    tenant = db.relationship("Tenant", backref="agent_config")

    def get_plugins(self) -> list:
        """解析 plugins JSON 字段

        Returns:
            None if never configured (use plan defaults),
            [] if explicitly set to empty (no plugins),
            list of plugin names otherwise.
        """
        import json as _json
        if self.plugins is None:
            return None  # 未配置，使用计划默认值
        try:
            return _json.loads(self.plugins)
        except (ValueError, TypeError):
            return []

    def set_plugins(self, val: list):
        import json as _json
        self.plugins = _json.dumps(val)

    def get_tools(self) -> list:
        """解析 tools JSON 字段"""
        import json as _json
        if self.tools is None:
            return []
        try:
            return _json.loads(self.tools)
        except (ValueError, TypeError):
            return []

    def set_tools(self, val: list):
        import json as _json
        self.tools = _json.dumps(val)

    def get_knowledge_ids(self) -> list:
        """解析 knowledge_ids JSON 字段"""
        import json as _json
        if self.knowledge_ids is None:
            return []
        try:
            return _json.loads(self.knowledge_ids)
        except (ValueError, TypeError):
            return []

    def set_knowledge_ids(self, val: list):
        import json as _json
        self.knowledge_ids = _json.dumps(val)

    def __repr__(self):
        return f"<AgentConfig {self.name} @ {self.tenant_id}>"


class KnowledgeFile(db.Model):
    """知识库文件表 — 管理租户上传的知识文件"""
    __tablename__ = "knowledge_file"

    id = db.Column(db.String(36), primary_key=True, default=_gen_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer)
    file_type = db.Column(db.String(20))  # pdf, txt, md, json, csv
    chunk_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="pending")  # pending/processing/ready/error
    error_msg = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    # Relationships
    tenant = db.relationship("Tenant", backref="knowledge_files")

    def __repr__(self):
        return f"<KnowledgeFile {self.filename} @ {self.tenant_id}>"


class MemoryChunkRecord(db.Model):
    """记忆块表（对应 PostgresMemoryStorage 创建的 memory_chunks 表）

    注意：此模型仅用于 Flask 管理 API 查询统计，不用于数据写入。
    实际的 CRUD 操作由 PostgresMemoryStorage 通过原始 SQL 完成，
    因为 pgvector 的 vector 类型需要特殊处理。
    """
    __tablename__ = "memory_chunks"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.String, primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = db.Column(db.String, nullable=True)
    scope = db.Column(db.String(16), nullable=False, default="shared")
    source = db.Column(db.String(16), nullable=False, default="memory")
    path = db.Column(db.String, nullable=False)
    start_line = db.Column(db.Integer, nullable=False)
    end_line = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    hash = db.Column(db.String, nullable=False)
    chunk_meta = db.Column(db.Text, nullable=True)  # JSONB (renamed from 'metadata' to avoid SQLAlchemy reserved word)
    created_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<MemoryChunkRecord {self.id} @ {self.tenant_id}>"


class MemoryFileRecord(db.Model):
    """文件元数据表（对应 PostgresMemoryStorage 创建的 memory_files 表）"""
    __tablename__ = "memory_files"
    __table_args__ = {"extend_existing": True}

    path = db.Column(db.String, primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    source = db.Column(db.String(16), nullable=False, default="memory")
    hash = db.Column(db.String, nullable=False)
    mtime = db.Column(db.BigInteger, nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<MemoryFileRecord {self.path} @ {self.tenant_id}>"


# ---------------------------------------------------------------------------
# 数据库初始化
# ---------------------------------------------------------------------------

def init_db(app=None, database_uri=None):
    """初始化 SQLAlchemy，创建表（如果不存在）。

    Args:
        app: Flask app 实例（如果传入则同时 init_app）
        database_uri: PostgreSQL 连接串，如不传则从环境变量 DATABASE_URL 读取
    """
    if app is not None:
        uri = database_uri or app.config.get("SQLALCHEMY_DATABASE_URI")
        if uri:
            app.config["SQLALCHEMY_DATABASE_URI"] = uri
        app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
        # SQLite 不支持 pool_size/max_overflow，仅 PostgreSQL 需要
        is_sqlite = (uri or "").startswith("sqlite")
        if not is_sqlite:
            app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
                "pool_size": 5,
                "max_overflow": 10,
                "pool_pre_ping": True,
            })
        db.init_app(app)
        with app.app_context():
            db.create_all()
    else:
        # 无 Flask app 模式（用于脚本或测试）
        if database_uri:
            db.engine = db.create_engine(database_uri)
        db.create_all()
