# CowAgent 企业版多租户改造 — 最终方案

> 日期: 2026-05-28
> 状态: **Approved**
> 基于讨论确认，精简 Phase 1 范围，推迟复杂特性到后续 Phase

---

## 0. 关键决策记录

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 隔离模型 | 行级隔离（tenant_id） | MVP ≤5 租户，容器化过重 |
| 后端框架 | Flask + SQLAlchemy | CowAgent 已用 Flask，保持一致 |
| 数据库 | PostgreSQL + pgvector | 替代 SQLite，支持并发和向量搜索 |
| 租户ID透传 | contextvars | 消息处理链跨越 HTTP 边界，Depends 不可达 |
| 前端方案 | React + Ant Design | 后续 SP8 实现 |
| 功能开关 | saas_mode 配置项 | false 时行为与原版完全一致 |

### 推迟到后续 Phase 的特性

| 特性 | 原提议 Phase | 推迟到 | 理由 |
|------|-------------|--------|------|
| Hash 分区（固定128个）+ Redis 路由缓存 | Phase 1 | Phase 2 | MVP ≤5 租户，直接查表+索引即可 |
| Parquet + FDW 冷数据查询 | Phase 1 | Phase 3 | MVP 无冷数据，按时间分区足够 |
| 微批 Merkle 树审计链 | Phase 1 | Phase 4 | MVP 并发低，简单 INSERT 即可 |
| K8s 部署 | Phase 1 | Phase 2 | MVP 用 Docker Compose |
| 租户自动初始化编排器 | Phase 1 | Phase 2 | MVP 用脚本/SQL 手动创建 |
| Prometheus + Grafana 监控 | Phase 1 | Phase 2 | MVP 用日志 |

---

## 1. Phase 1 精简范围（2 周）

### 1.1 交付物

| # | 交付物 | 文件 | 改动类型 |
|---|--------|------|----------|
| 1 | TenantContext + contextvars | `common/tenant.py` | 新增 |
| 2 | SaaS 平台层 | `saas/` 目录 | 新增 |
| 3 | PostgreSQL 核心表 | `saas/models.py` | 新增 |
| 4 | Flask 租户中间件 | `saas/middleware.py` | 新增 |
| 5 | 租户级配置 | `config.py` | 小改 |
| 6 | Context 增加 tenant_id | `bridge/context.py` | 小改 |
| 7 | BridgeManager 按租户实例化 | `bridge/bridge.py` | 中改 |
| 8 | AgentBridge 按租户隔离 | `bridge/agent_bridge.py` | 中改 |
| 9 | PostgresMemoryStorage | `agent/memory/storage.py` | 大改 |
| 10 | 基础隔离测试 | `tests/test_tenant_isolation.py` | 新增 |

### 1.2 不包含

- JWT 认证（SP2/Phase 2）
- 管理后台 API（SP3/Phase 2）
- 知识库管理（SP7/Phase 3）
- 前端界面（SP8/Phase 3）
- IM 渠道多租户（Phase 2）
- 计费系统（Phase 3）
- K8s 部署（Phase 2）

---

## 2. 架构设计

### 2.1 租户上下文透传

**核心问题**：CowAgent 的消息处理链跨越 HTTP 请求边界，FastAPI 的 `Depends` 或 Flask 的 `request` 对象无法到达业务层。

**解决方案**：使用 Python `contextvars.ContextVar` 在线程/协程间透传 `tenant_id`。

```
HTTP Request
  → Flask before_request: 提取 tenant_id → contextvars.set(tenant_id)
  → Channel.receive(): contextvars.get(tenant_id) → 注入 Context.kwargs
  → Bridge.handle(context): context["tenant_id"]
  → AgentBridge.get_agent(tenant_id, session_id)
  → Agent.run(): contextvars.get(tenant_id) 全局可用
  → MemoryStorage: SQL WHERE tenant_id = ?
```

### 2.2 模块依赖关系

```
common/tenant.py (TenantContext, get_tenant_id, set_tenant_id)
       ↑
       ├── config.py (conf_tenant, 租户级配置)
       ├── bridge/context.py (Context 增加 tenant_id)
       ├── bridge/bridge.py (BridgeManager: tenant_id → Bridge)
       ├── bridge/agent_bridge.py (AgentBridge: tenant_id → agents)
       ├── agent/memory/storage.py (PostgreSQLStorage + tenant_id)
       └── channel/chat_channel.py (消息接收时注入 tenant_id)
```

---

## 3. 详细设计

### 3.1 TenantContext + contextvars

**文件**: `common/tenant.py`

```python
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

_tenant_ctx: ContextVar[Optional["TenantContext"]] = ContextVar("tenant_ctx", default=None)

@dataclass
class TenantContext:
    tenant_id: str
    name: str = ""
    plan: str = "basic"
    config_override: Dict[str, Any] = field(default_factory=dict)
    max_concurrent: int = 5
    max_tokens_monthly: int = 100000

def set_tenant(ctx: TenantContext) -> None:
    _tenant_ctx.set(ctx)

def get_tenant() -> Optional[TenantContext]:
    return _tenant_ctx.get()

def get_tenant_id() -> Optional[str]:
    ctx = _tenant_ctx.get()
    return ctx.tenant_id if ctx else None

def clear_tenant() -> None:
    _tenant_ctx.set(None)
```

**设计决策**：
- 使用 `ContextVar` 而非线程局部变量，因为 CowAgent 使用线程池处理消息
- `TenantContext` 包含租户基础信息，避免频繁查库
- `config_override` 存储租户级配置覆盖，延迟加载

### 3.2 Context 改造

**文件**: `bridge/context.py`

在现有 `Context` 类中增加 `tenant_id` 支持：

```python
class Context:
    def __init__(self, type: ContextType = None, content=None, kwargs=dict()):
        self.type = type
        self.content = content
        self.kwargs = kwargs
        # 自动从 contextvars 注入 tenant_id
        from common.tenant import get_tenant_id
        if "tenant_id" not in kwargs and get_tenant_id():
            self.kwargs["tenant_id"] = get_tenant_id()

    @property
    def tenant_id(self) -> Optional[str]:
        return self.kwargs.get("tenant_id")
```

**影响范围**：所有创建 `Context` 的地方自动获得 `tenant_id`，无需逐个修改。

### 3.3 Config 系统多租户化

**文件**: `config.py`

**现状**：`conf()` 返回全局单例 `Config` 对象（dict 子类），所有模块共享。

**改造策略**：新增 `conf_tenant()` 函数，返回租户级配置视图，**不修改** `conf()` 的行为。

```python
_config_tenant_overrides: Dict[str, dict] = {}  # tenant_id → override dict

def conf_tenant(tenant_id: str = None) -> Config:
    """获取租户级配置：全局配置 + 租户覆盖"""
    base = conf()  # 全局配置
    tid = tenant_id or get_tenant_id()
    if not tid:
        return base

    override = _config_tenant_overrides.get(tid, {})
    if not override:
        return base

    merged = Config(base.copy())
    merged.update(override)
    return merged

def set_tenant_config(tenant_id: str, override: dict) -> None:
    """设置租户配置覆盖"""
    _config_tenant_overrides[tenant_id] = override

def load_tenant_configs_from_db() -> None:
    """从数据库加载所有租户配置覆盖"""
    from saas.models import Tenant
    tenants = Tenant.query.all()
    for t in tenants:
        if t.config_override:
            _config_tenant_overrides[t.tenant_id] = t.config_override
```

**渐进式迁移**：
- Phase 1：`conf()` 保持不变，新代码用 `conf_tenant()`
- Phase 2：逐步将关键路径的 `conf()` 替换为 `conf_tenant()`
- Phase 3：`conf()` 内部自动检测 contextvars 中的 tenant_id

### 3.4 Bridge 去单例

**文件**: `bridge/bridge.py`

**现状**：`Bridge` 是 `@singleton`，全局唯一，`self.bots = {}` 缓存所有 Bot 实例。

**改造**：引入 `BridgeManager`，按租户管理 Bridge 实例。

```python
class BridgeManager:
    _instance = None
    _bridges: Dict[str, Bridge] = {}  # tenant_id → Bridge

    @classmethod
    def get_instance(cls) -> "BridgeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_bridge(self, tenant_id: str = None) -> Bridge:
        tid = tenant_id or get_tenant_id() or "_default"
        if tid not in self._bridges:
            self._bridges[tid] = Bridge()
        return self._bridges[tid]

    def remove_bridge(self, tenant_id: str) -> None:
        if tenant_id in self._bridges:
            del self._bridges[tenant_id]
```

**Bridge 类改造**：
- 移除 `@singleton` 装饰器
- `conf()` 调用改为 `conf_tenant()`
- 保持内部逻辑不变

**兼容性**：保留 `bridge()` 全局函数，默认返回 `_default` Bridge，确保未改造的代码正常运行。

### 3.5 AgentBridge 按租户隔离

**文件**: `bridge/agent_bridge.py`

**现状**：`self.agents = {}` 按 `session_id` 映射 Agent，无租户维度。

**改造**：增加租户维度，`self.agents` 改为嵌套字典。

```python
class AgentBridge:
    def __init__(self):
        self.agents: Dict[str, Dict[str, Agent]] = {}  # tenant_id → {session_id → Agent}

    def get_agent(self, tenant_id: str, session_id: str) -> Optional[Agent]:
        return self.agents.get(tenant_id, {}).get(session_id)

    def create_agent(self, tenant_id: str, session_id: str, **kwargs) -> Agent:
        if tenant_id not in self.agents:
            self.agents[tenant_id] = {}
        agent = self._create_agent_instance(tenant_id, session_id, **kwargs)
        self.agents[tenant_id][session_id] = agent
        return agent

    def remove_tenant_agents(self, tenant_id: str) -> None:
        if tenant_id in self.agents:
            del self.agents[tenant_id]
```

### 3.6 PostgreSQL Schema

**文件**: `saas/models.py`

```sql
-- 核心租户表
CREATE TABLE tenants (
    tenant_id   VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    plan        VARCHAR(32) NOT NULL DEFAULT 'basic',
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    logo_url    VARCHAR(512),
    brand_color VARCHAR(16),
    config_override JSONB DEFAULT '{}',
    max_concurrent  INTEGER DEFAULT 5,
    max_tokens_monthly INTEGER DEFAULT 100000,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- 用户表
CREATE TABLE users (
    user_id     SERIAL PRIMARY KEY,
    tenant_id   VARCHAR(64) REFERENCES tenants(tenant_id),
    email       VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role        VARCHAR(16) NOT NULL DEFAULT 'admin',
    name        VARCHAR(255),
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- API Key 表
CREATE TABLE api_keys (
    key_id      SERIAL PRIMARY KEY,
    tenant_id   VARCHAR(64) REFERENCES tenants(tenant_id),
    api_key     VARCHAR(128) NOT NULL UNIQUE,
    name        VARCHAR(255),
    permissions JSONB DEFAULT '[]',
    expires_at  TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- 用量统计表
CREATE TABLE usage_records (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   VARCHAR(64) NOT NULL,
    user_id     INTEGER,
    model       VARCHAR(64),
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    request_id  VARCHAR(128),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- 记忆块表（替代 SQLite chunks）
CREATE TABLE memory_chunks (
    id          VARCHAR(128) PRIMARY KEY,
    tenant_id   VARCHAR(64) NOT NULL,
    user_id     VARCHAR(64),
    scope       VARCHAR(16) NOT NULL DEFAULT 'shared',
    source      VARCHAR(16) NOT NULL DEFAULT 'memory',
    path        VARCHAR(512) NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    hash        VARCHAR(64) NOT NULL,
    metadata    JSONB,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- pgvector 向量索引
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE memory_chunks ADD COLUMN embedding vector(1536);
CREATE INDEX idx_memory_chunks_embedding ON memory_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 全文搜索索引（替代 FTS5）
ALTER TABLE memory_chunks ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED;
CREATE INDEX idx_memory_chunks_search ON memory_chunks USING gin(search_vector);

-- 文件元数据表
CREATE TABLE memory_files (
    path        VARCHAR(512) PRIMARY KEY,
    tenant_id   VARCHAR(64) NOT NULL,
    source      VARCHAR(16) NOT NULL DEFAULT 'memory',
    hash        VARCHAR(64) NOT NULL,
    mtime       INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_memory_chunks_tenant ON memory_chunks(tenant_id);
CREATE INDEX idx_memory_chunks_tenant_user ON memory_chunks(tenant_id, user_id);
CREATE INDEX idx_memory_files_tenant ON memory_files(tenant_id);
CREATE INDEX idx_usage_records_tenant ON usage_records(tenant_id);
CREATE INDEX idx_usage_records_tenant_created ON usage_records(tenant_id, created_at);
CREATE INDEX idx_users_tenant ON users(tenant_id);
CREATE INDEX idx_api_keys_tenant ON api_keys(tenant_id);
```

**Phase 2 预留**：表结构预留分区键（`tenant_id` 在联合索引首位），后续可直接 `ALTER TABLE ... PARTITION BY HASH`。

### 3.7 MemoryStorage 改造

**文件**: `agent/memory/storage.py`

**改造策略**：新增 `PostgresMemoryStorage` 类，与现有 `MemoryStorage` 并存，通过配置选择。

```python
class PostgresMemoryStorage:
    """PostgreSQL + pgvector storage with tenant isolation"""

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.Session = sessionmaker(bind=self.engine)

    def save_chunk(self, chunk: MemoryChunk, tenant_id: str):
        with self.Session() as session:
            db_chunk = DBMemoryChunk(
                id=chunk.id, tenant_id=tenant_id, user_id=chunk.user_id,
                scope=chunk.scope, source=chunk.source, path=chunk.path,
                start_line=chunk.start_line, end_line=chunk.end_line,
                text=chunk.text, embedding=chunk.embedding,
                hash=chunk.hash, metadata=chunk.metadata,
            )
            session.merge(db_chunk)
            session.commit()

    def search_vector(self, query_embedding, tenant_id, user_id=None, scopes=None, limit=10):
        with self.Session() as session:
            query = session.query(DBMemoryChunk).filter(
                DBMemoryChunk.tenant_id == tenant_id,
                DBMemoryChunk.embedding.isnot(None)
            )
            if user_id:
                query = query.filter(
                    or_(DBMemoryChunk.scope == 'shared', DBMemoryChunk.user_id == user_id)
                )
            if scopes:
                query = query.filter(DBMemoryChunk.scope.in_(scopes))
            query = query.order_by(
                DBMemoryChunk.embedding.cosine_distance(query_embedding)
            ).limit(limit)
            return [self._to_search_result(r) for r in query.all()]

    def search_keyword(self, query_text, tenant_id, user_id=None, scopes=None, limit=10):
        with self.Session() as session:
            ts_query = func.plainto_tsquery('simple', query_text)
            q = session.query(
                DBMemoryChunk,
                func.ts_rank(DBMemoryChunk.search_vector, ts_query).label('rank')
            ).filter(
                DBMemoryChunk.tenant_id == tenant_id,
                DBMemoryChunk.search_vector.op('@@')(ts_query)
            )
            if user_id:
                q = q.filter(
                    or_(DBMemoryChunk.scope == 'shared', DBMemoryChunk.user_id == user_id)
                )
            q = q.order_by(text('rank DESC')).limit(limit)
            return [self._to_search_result(r[0], r[1]) for r in q.all()]
```

### 3.8 SaaS 模块结构

```
saas/
├── __init__.py
├── models.py          # SQLAlchemy models (Tenant, User, ApiKey, etc.)
├── database.py        # PostgreSQL 连接管理
├── middleware.py       # Flask before_request 租户识别
├── config_loader.py   # 从数据库加载租户配置
└── api/
    ├── __init__.py
    ├── tenant.py      # 租户管理 API (SP3)
    └── auth.py        # 认证 API (SP2)
```

### 3.9 Flask 中间件

**文件**: `saas/middleware.py`

```python
from flask import request, g
from common.tenant import set_tenant, clear_tenant, TenantContext
from saas.models import Tenant

def tenant_middleware():
    """Flask before_request 钩子：识别租户并注入上下文"""
    tenant_id = request.headers.get("X-Tenant-ID")

    # API Key / JWT 提取（SP2 实现）
    if not tenant_id:
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        if api_key:
            tenant_id = _resolve_api_key(api_key)

    if tenant_id:
        tenant = Tenant.query.get(tenant_id)
        if tenant and tenant.status == 'active':
            set_tenant(TenantContext(
                tenant_id=tenant.tenant_id, name=tenant.name,
                plan=tenant.plan, config_override=tenant.config_override or {},
                max_concurrent=tenant.max_concurrent,
                max_tokens_monthly=tenant.max_tokens_monthly,
            ))
            g.tenant = tenant
            return

    if request.path.startswith("/api/"):
        from flask import jsonify
        return jsonify({"error": "Missing tenant identification"}), 400

def tenant_cleanup(exception=None):
    """Flask teardown 钩子：清理租户上下文"""
    clear_tenant()
```

### 3.10 Channel 消息注入

**文件**: `channel/chat_channel.py`

```python
def handle(self, context: Context):
    from common.tenant import get_tenant_id
    tenant_id = get_tenant_id()
    if tenant_id and "tenant_id" not in context.kwargs:
        context.kwargs["tenant_id"] = tenant_id
    # 原有逻辑不变
```

IM 渠道根据 AppID/CorpID 映射到 tenant_id（Phase 2 实现）。

---

## 4. 数据迁移

### 4.1 SQLite → PostgreSQL 迁移脚本

**文件**: `scripts/migrate_sqlite_to_pg.py`

```python
def migrate_memory_chunks(sqlite_path: str, pg_url: str, default_tenant_id: str):
    """将 SQLite 中的 memory_chunks 迁移到 PostgreSQL"""
    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_engine = create_engine(pg_url)

    rows = sqlite_conn.execute("SELECT * FROM chunks").fetchall()
    for row in rows:
        chunk = DBMemoryChunk(
            id=row['id'], tenant_id=default_tenant_id,
            user_id=row['user_id'], scope=row['scope'],
            source=row['source'], path=row['path'],
            start_line=row['start_line'], end_line=row['end_line'],
            text=row['text'], embedding=_decode_embedding(row['embedding']),
            hash=row['hash'],
            metadata=json.loads(row['metadata']) if row['metadata'] else None,
        )
        session.merge(chunk)
    session.commit()
```

---

## 5. 兼容性策略

| 组件 | 兼容方式 |
|------|----------|
| `conf()` | 保持不变，返回全局配置。未改造代码继续工作 |
| `Bridge` | 保留 `@singleton` 的全局实例，新增 `BridgeManager` |
| `MemoryStorage` | 保留 SQLite 版本，新增 `PostgresMemoryStorage`，通过配置选择 |
| `Context` | 增加 `tenant_id` 属性但不影响现有逻辑 |
| Channel | IM 渠道暂不改造，Web Console 先行 |

### 功能开关

```json
{
    "saas_mode": true,
    "database_url": "postgresql://...",
    "default_tenant_id": "default"
}
```

当 `saas_mode = false` 时，所有多租户逻辑不生效，CowAgent 行为与原版一致。

---

## 6. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| contextvars 在线程池中丢失 | CowAgent 使用 `threading.Thread`，contextvars 在同一线程内有效；若使用线程池需用 `copy_context()` |
| Bridge 去单例后内存增长 | 每个租户一个 Bridge 实例，每个实例含 bots 缓存。设置 LRU 淘汰策略 |
| pgvector 性能 | 小规模（<10万条）用 ivfflat 索引；大规模切换到 hnsw |
| 迁移数据丢失 | 迁移前备份 SQLite，迁移后校验行数和哈希 |
| 配置覆盖冲突 | 租户配置只允许覆盖白名单内的 key |

---

## 7. 验收标准

1. 创建两个租户，各自配置不同模型，对话时使用各自模型
2. 租户 A 的记忆数据在租户 B 的搜索中不可见
3. 停用租户后，该租户的 Agent 停止响应
4. `saas_mode = false` 时，CowAgent 行为与原版完全一致
5. 迁移脚本执行后，原有数据在新库中可正常检索

---

## 8. 8 周路线图

```
Week 1-2  → Phase 1: 核心基座与隔离（本文档）✅
Week 3-4  → Phase 2: 数据架构与性能（Hash分区 + Redis + K8s + 认证）
Week 5-6  → Phase 3: 智能控制与降级（计费 + 知识库 + 冷热分离）
Week 7-8  → Phase 4: 合规审计与上线（Merkle审计 + 监控 + 前端）
```
