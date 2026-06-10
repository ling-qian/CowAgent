# Multi-Agent Customization Design Proposal

> Date: 2026-06-10
> Status: Draft
> Author: CowAgent SaaS Team

## 1. Overview

### 1.1 Goal

Enable each tenant to create a fully customized AI Agent with independent personality, model, plugins, knowledge base, and tools — moving from "shared generic assistant" to "personalized AI agent per tenant".

### 1.2 Design Principles

- **Agent Factory pattern**: Create independent Agent instances per tenant config, bypassing global config override
- **One Agent per tenant first**: Architecture supports future multi-Agent expansion
- **Deep customization**: System prompt, model, plugins, knowledge base, custom tools, behavior parameters
- **Dual audience**: Visual UI for non-technical users, JSON/API for technical users

### 1.3 Current vs Target

| Capability | Current | Target |
|---|---|---|
| System prompt | Tenant-level override via config | Per-Agent stored in DB |
| Model selection | Shared global model | Per-Agent model config |
| Plugins | All tenants share same plugins | Per-Agent plugin enable/disable |
| Knowledge base | Shared global knowledge | Per-Agent independent knowledge |
| Custom tools | Not supported | Per-Agent tool definitions |
| Behavior params | Shared global defaults | Per-Agent temperature, max_steps, etc. |
| Multi-Agent | Not supported | Architecture ready, UI supports 1 per tenant now |

---

## 2. Architecture

### 2.1 Agent Factory Pattern

```
┌─────────────────────────────────────────────────────┐
│                    Flask API Layer                    │
│                                                      │
│  POST /api/chat/completions                          │
│    ↓                                                 │
│  1. Load AgentConfig from DB by tenant_id            │
│    ↓                                                 │
│  2. AgentFactory.get_or_create(tenant_id, config)    │
│    ↓                                                 │
│  3. Independent Agent instance (with its own          │
│     system_prompt, model, tools, knowledge)           │
│    ↓                                                 │
│  4. agent.handle_message(message) → reply            │
│    ↓                                                 │
│  5. Cache agent instance for session reuse            │
└─────────────────────────────────────────────────────┘
```

### 2.2 Key Components

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   AgentFactory    │────▶│   Agent Instance  │────▶│   LLM Provider   │
│                  │     │                  │     │  (model + api_key)│
│ - get_or_create  │     │ - system_prompt  │     └──────────────────┘
│ - destroy        │     │ - model config   │
│ - list_active    │     │ - tools[]        │     ┌──────────────────┐
│                  │     │ - knowledge      │────▶│  Plugin Manager  │
└──────────────────┘     │ - temperature    │     │  (per-agent scope)│
                         │ - max_steps      │     └──────────────────┘
                         └──────────────────┘
                                            │
                                            ▼
                         ┌──────────────────┐
                         │  Knowledge Store  │
                         │  (per-tenant files)│
                         └──────────────────┘
```

### 2.3 Request Flow

```
User sends message with API Key
  → Middleware: authenticate, resolve tenant_id
  → Chat API: load AgentConfig for tenant
  → AgentFactory: get cached Agent or create new
    → If new:
      1. Build system_prompt from config
      2. Create LLM client with tenant's model/api_key
      3. Load enabled plugins as tools
      4. Load knowledge files into context
      5. Create Agent instance with all params
    → If cached:
      1. Verify config hasn't changed (version check)
      2. If changed, destroy and recreate
  → Agent.handle_message(message) → Reply
  → Record usage, return response
```

---

## 3. Data Model

### 3.1 AgentConfig Table

```sql
CREATE TABLE agent_config (
    id              VARCHAR(36) PRIMARY KEY,
    tenant_id       VARCHAR(36) NOT NULL REFERENCES tenant(id) UNIQUE,
    name            VARCHAR(100) NOT NULL DEFAULT 'My Agent',
    avatar_url      VARCHAR(500),
    description     TEXT,

    -- Core LLM Config
    system_prompt   TEXT DEFAULT 'You are a helpful assistant.',
    model           VARCHAR(100) DEFAULT 'agnes-2.0-flash',
    api_key         VARCHAR(200),          -- Tenant's own LLM key (optional)
    api_base        VARCHAR(200),          -- Tenant's own API base (optional)

    -- Capability Config (JSON)
    plugins         JSON DEFAULT '[]',     -- ["web_search", "code_interpreter"]
    tools           JSON DEFAULT '[]',     -- Custom tool definitions
    knowledge_ids   JSON DEFAULT '[]',     -- Knowledge file ID list

    -- Behavior Parameters
    max_steps       INTEGER DEFAULT 15,
    temperature     FLOAT DEFAULT 0.7,
    enable_thinking BOOLEAN DEFAULT TRUE,
    reasoning_effort VARCHAR(10) DEFAULT 'high',

    -- Metadata
    config_version  INTEGER DEFAULT 1,     -- Incremented on each update
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
```

### 3.2 KnowledgeFile Table

```sql
CREATE TABLE knowledge_file (
    id          VARCHAR(36) PRIMARY KEY,
    tenant_id   VARCHAR(36) NOT NULL REFERENCES tenant(id),
    filename    VARCHAR(255) NOT NULL,
    file_path   VARCHAR(500) NOT NULL,
    file_size   INTEGER,
    file_type   VARCHAR(20),           -- pdf, txt, md, json, csv
    chunk_count INTEGER DEFAULT 0,
    status      VARCHAR(20) DEFAULT 'pending',  -- pending/processing/ready/error
    error_msg   TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

### 3.3 SQLAlchemy Models

```python
class AgentConfig(db.Model):
    __tablename__ = 'agent_config'

    id              = Column(String(36), primary_key=True, default=lambda: uuid4().hex)
    tenant_id       = Column(String(36), ForeignKey('tenant.id'), nullable=False, unique=True)
    name            = Column(String(100), nullable=False, default="My Agent")
    avatar_url      = Column(String(500))
    description     = Column(Text)

    system_prompt   = Column(Text, default="You are a helpful assistant.")
    model           = Column(String(100), default="agnes-2.0-flash")
    api_key         = Column(String(200))
    api_base        = Column(String(200))

    plugins         = Column(JSON, default=list)
    tools           = Column(JSON, default=list)
    knowledge_ids   = Column(JSON, default=list)

    max_steps       = Column(Integer, default=15)
    temperature     = Column(Float, default=0.7)
    enable_thinking = Column(Boolean, default=True)
    reasoning_effort = Column(String(10), default="high")

    config_version  = Column(Integer, default=1)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", backref="agent_config")


class KnowledgeFile(db.Model):
    __tablename__ = 'knowledge_file'

    id          = Column(String(36), primary_key=True, default=lambda: uuid4().hex)
    tenant_id   = Column(String(36), ForeignKey('tenant.id'), nullable=False)
    filename    = Column(String(255), nullable=False)
    file_path   = Column(String(500), nullable=False)
    file_size   = Column(Integer)
    file_type   = Column(String(20))
    chunk_count = Column(Integer, default=0)
    status      = Column(String(20), default="pending")
    error_msg   = Column(Text)
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tenant = relationship("Tenant", backref="knowledge_files")
```

---

## 4. Core Module: AgentFactory

### 4.1 Design

```python
# saas/agent_factory.py

class AgentFactory:
    """Create and manage per-tenant Agent instances"""

    _instances: Dict[str, Agent] = {}        # tenant_id → Agent
    _versions: Dict[str, int] = {}            # tenant_id → config_version
    _lock = threading.Lock()

    @classmethod
    def get_or_create(cls, tenant_id: str, config: AgentConfig) -> Agent:
        """Get cached Agent or create new one from config"""
        with cls._lock:
            cached_version = cls._versions.get(tenant_id)
            if cached_version == config.config_version and tenant_id in cls._instances:
                return cls._instances[tenant_id]

            # Config changed or not cached — create new
            agent = cls._create_agent(config)
            cls._instances[tenant_id] = agent
            cls._versions[tenant_id] = config.config_version
            return agent

    @classmethod
    def _create_agent(cls, config: AgentConfig) -> Agent:
        """Build an Agent instance from AgentConfig"""
        # 1. Build LLM client
        llm = cls._create_llm(config)

        # 2. Load tools from plugins + custom tools
        tools = cls._load_tools(config)

        # 3. Load knowledge context
        knowledge_context = cls._load_knowledge(config)

        # 4. Build full system prompt (base + knowledge)
        full_prompt = config.system_prompt
        if knowledge_context:
            full_prompt += f"\n\n## Knowledge Base\n{knowledge_context}"

        # 5. Create Agent instance
        agent = Agent(
            system_prompt=full_prompt,
            llm=llm,
            tools=tools,
            max_steps=config.max_steps,
            temperature=config.temperature,
            enable_thinking=config.enable_thinking,
        )
        return agent

    @classmethod
    def _create_llm(cls, config: AgentConfig):
        """Create LLM client from config"""
        from agent.protocol.agent import AgentLLMModel
        # Use tenant's own key if provided, else platform default
        api_key = config.api_key or get_platform_api_key()
        api_base = config.api_base or get_platform_api_base()
        return AgentLLMModel(model=config.model, api_key=api_key, api_base=api_base)

    @classmethod
    def _load_tools(cls, config: AgentConfig) -> list:
        """Load tools: enabled plugins + custom tool definitions"""
        tools = []
        # Load enabled plugins
        if config.plugins:
            pm = PluginManager.getInstance()
            for plugin_name in config.plugins:
                plugin = pm.get_plugin(plugin_name)
                if plugin:
                    tools.extend(plugin.get_tools())
        # Load custom tool definitions
        if config.tools:
            for tool_def in config.tools:
                tools.append(create_tool_from_def(tool_def))
        return tools

    @classmethod
    def _load_knowledge(cls, config: AgentConfig) -> str:
        """Load knowledge files content for context injection"""
        if not config.knowledge_ids:
            return ""
        parts = []
        for fid in config.knowledge_ids:
            kf = KnowledgeFile.query.get(fid)
            if kf and kf.status == "ready":
                with open(kf.file_path, 'r') as f:
                    parts.append(f"### {kf.filename}\n{f.read()}")
        return "\n\n".join(parts)

    @classmethod
    def destroy(cls, tenant_id: str):
        """Remove cached Agent instance"""
        with cls._lock:
            cls._instances.pop(tenant_id, None)
            cls._versions.pop(tenant_id, None)

    @classmethod
    def list_active(cls) -> list:
        """List all active Agent tenant_ids"""
        return list(cls._instances.keys())
```

### 4.2 Config Version Mechanism

Every time `AgentConfig` is updated, `config_version` increments. The factory checks this version to decide whether to recreate the Agent:

```
Update AgentConfig → config_version += 1
Next request → Factory sees version mismatch → Destroy old Agent → Create new
```

This avoids stale config while keeping hot-path fast (no DB read on every request — just version comparison).

---

## 5. API Design

### 5.1 Agent Config API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/config` | Get current tenant's Agent config |
| PUT | `/api/agent/config` | Update Agent config (full or partial) |
| POST | `/api/agent/config/reset` | Reset to default config |

**GET /api/agent/config**
```json
{
  "id": "abc123",
  "name": "Customer Service Bot",
  "system_prompt": "You are a helpful customer service agent...",
  "model": "agnes-2.0-flash",
  "api_key": "sk-***masked***",
  "api_base": "https://apihub.agnes-ai.com/v1",
  "plugins": ["web_search"],
  "tools": [],
  "knowledge_ids": ["kf001", "kf002"],
  "max_steps": 15,
  "temperature": 0.7,
  "enable_thinking": true,
  "config_version": 3,
  "is_active": true,
  "created_at": "2026-06-10T10:00:00Z",
  "updated_at": "2026-06-10T12:00:00Z"
}
```

**PUT /api/agent/config** (partial update supported)
```json
{
  "name": "Sales Assistant",
  "system_prompt": "You are a sales assistant who helps customers find products.",
  "plugins": ["web_search", "code_interpreter"],
  "temperature": 0.8
}
```

### 5.2 Knowledge Base API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/knowledge` | List knowledge files |
| POST | `/api/agent/knowledge/upload` | Upload knowledge file |
| DELETE | `/api/agent/knowledge/:id` | Delete knowledge file |
| POST | `/api/agent/knowledge/:id/reprocess` | Reprocess failed file |

**POST /api/agent/knowledge/upload**
- Accepts `multipart/form-data` with file field
- Supported types: `.pdf`, `.txt`, `.md`, `.json`, `.csv`
- Max file size: 10MB (configurable per plan)
- Processing: extract text → chunk → store

### 5.3 Plugin Registry API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/plugins` | List all available plugins with status |
| GET | `/api/agent/plugins/:name` | Get plugin details |

**GET /api/agent/plugins**
```json
{
  "plugins": [
    {
      "name": "web_search",
      "display_name": "Web Search",
      "description": "Search the web for information",
      "enabled": true,
      "category": "search"
    },
    {
      "name": "code_interpreter",
      "display_name": "Code Interpreter",
      "description": "Execute Python code",
      "enabled": false,
      "category": "compute"
    }
  ]
}
```

### 5.4 Chat API Changes

The existing `/api/chat/completions` remains the same interface. Internally, `chat_engine.py` switches from Bridge-based to Factory-based:

```
Before:  chat() → _apply_tenant_config() → Bridge.fetch_agent_reply()
After:   chat() → load AgentConfig → AgentFactory.get_or_create() → agent.handle_message()
```

### 5.5 Custom Tools API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/tools` | List custom tools |
| POST | `/api/agent/tools` | Add custom tool |
| PUT | `/api/agent/tools/:id` | Update custom tool |
| DELETE | `/api/agent/tools/:id` | Delete custom tool |

**Custom Tool Definition Format (OpenAI-compatible)**
```json
{
  "name": "get_weather",
  "description": "Get current weather for a city",
  "parameters": {
    "type": "object",
    "properties": {
      "city": { "type": "string", "description": "City name" }
    },
    "required": ["city"]
  },
  "endpoint": "https://api.example.com/weather",
  "method": "GET",
  "headers": { "X-API-Key": "xxx" }
}
```

Custom tools are stored in `AgentConfig.tools` JSON field. At runtime, they are converted to function-calling tool definitions that the Agent can invoke. When the LLM decides to call a custom tool, the factory executes the HTTP request to the configured endpoint.

---

## 6. Chat Engine Refactoring

### 6.1 Current Flow (Bridge-based)

```python
def chat(tenant_id, message, session_id, ...):
    original_config = _apply_tenant_config(tenant_id)  # Override global config
    bridge = _get_bridge()                              # Get singleton Bridge
    bridge.reset_bot()                                  # Rebuild bot routing
    reply = bridge.fetch_agent_reply(query, context)    # Call through Bridge
    _restore_config(original_config)                    # Restore global config
```

### 6.2 New Flow (Factory-based)

```python
def chat(tenant_id, message, session_id, ...):
    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        config = _create_default_config(tenant_id)

    agent = AgentFactory.get_or_create(tenant_id, config)

    # Session management: each tenant's agent maintains its own message history
    if session_id:
        agent.load_session(session_id)

    reply = agent.handle_message(message)

    if session_id:
        agent.save_session(session_id)

    _record_usage(tenant_id, ...)
    return {"session_id": session_id, "content": reply.content, ...}
```

### 6.3 Migration Strategy

- Phase 1: Add AgentFactory alongside existing Bridge path
- Phase 2: Add feature flag `use_agent_factory` in AgentConfig
- Phase 3: Default to Factory for new tenants, Bridge for legacy
- Phase 4: Remove Bridge path entirely

---

## 7. Frontend: Agent Builder UI

### 7.1 Page Structure

```
/agent-builder  (new page in admin panel)
├── Tab 1: Basic Settings
│   ├── Agent Name
│   ├── Avatar Upload
│   ├── Description
│   └── System Prompt (rich text editor)
│
├── Tab 2: Model & API
│   ├── Model Selector (dropdown)
│   ├── API Key (masked input, optional)
│   ├── API Base URL (optional)
│   ├── Temperature Slider (0.0 - 2.0)
│   ├── Max Steps Slider (1 - 50)
│   └── Enable Thinking Toggle
│
├── Tab 3: Plugins & Tools
│   ├── Plugin Cards (toggle enable/disable)
│   │   ├── Web Search
│   │   ├── Code Interpreter
│   │   ├── Image Generation
│   │   └── ...
│   └── Custom Tools
│       ├── Add Tool Button
│       └── Tool List (edit/delete)
│
├── Tab 4: Knowledge Base
│   ├── Upload Area (drag & drop)
│   ├── File List (status, delete, reprocess)
│   └── Storage Usage (used/limit per plan)
│
└── Tab 5: Preview & Test
    ├── Chat Preview Window
    └── Config Summary
```

### 7.2 Component Design

```jsx
// AgentBuilder.jsx
< Tabs activeKey={tab} onChange={setTab} >
  < TabPane tab="Basic" key="basic">
    <AgentBasicSettings config={config} onChange={handleChange} />
  </TabPane>
  < TabPane tab="Model" key="model">
    <AgentModelSettings config={config} onChange={handleChange} />
  </TabPane>
  < TabPane tab="Plugins" key="plugins">
    <AgentPluginSettings config={config} onChange={handleChange} />
  </TabPane>
  < TabPane tab="Knowledge" key="knowledge">
    <AgentKnowledgeSettings config={config} onChange={handleChange} />
  </TabPane>
  < TabPane tab="Preview" key="preview">
    <AgentPreview config={config} />
  </TabPane>
</Tabs>

<Footer>
  <Button onClick={saveConfig}>Save Changes</Button>
  <Button onClick={resetConfig}>Reset to Default</Button>
</Footer>
```

### 7.3 Navigation Update

Add "Agent Builder" menu item in sidebar:
```
AI 对话 → /chat
Agent Builder → /agent-builder    ← NEW
Dashboard → /dashboard
API Keys → /keys
Settings → /settings
Webhooks → /webhooks
```

---

## 8. Plugin System Integration

### 8.1 Available Plugins Registry

```python
# saas/plugin_registry.py

PLUGIN_REGISTRY = {
    "web_search": {
        "display_name": "Web Search",
        "description": "Search the web for real-time information",
        "category": "search",
        "required_config": [],
        "plan_availability": ["free", "pro", "enterprise"],
    },
    "code_interpreter": {
        "display_name": "Code Interpreter",
        "description": "Execute Python code in a sandbox",
        "category": "compute",
        "required_config": [],
        "plan_availability": ["pro", "enterprise"],
    },
    "image_generation": {
        "display_name": "Image Generation",
        "description": "Generate images from text descriptions",
        "category": "creative",
        "required_config": ["image_api_key"],
        "plan_availability": ["pro", "enterprise"],
    },
    "knowledge_qa": {
        "display_name": "Knowledge Q&A",
        "description": "Answer questions from uploaded knowledge base",
        "category": "knowledge",
        "required_config": ["knowledge_ids"],
        "plan_availability": ["pro", "enterprise"],
    },
    "summarize": {
        "display_name": "Summarizer",
        "description": "Summarize long documents and conversations",
        "category": "productivity",
        "required_config": [],
        "plan_availability": ["free", "pro", "enterprise"],
    },
}
```

### 8.2 Plan-Based Plugin Access

| Plugin | Free | Pro | Enterprise |
|--------|------|-----|------------|
| Web Search | ✅ | ✅ | ✅ |
| Summarizer | ✅ | ✅ | ✅ |
| Code Interpreter | ❌ | ✅ | ✅ |
| Image Generation | ❌ | ✅ | ✅ |
| Knowledge Q&A | ❌ | ✅ | ✅ |
| Custom Tools | ❌ | ❌ | ✅ |

### 8.3 Plugin Loading Flow

```
AgentConfig.plugins = ["web_search", "code_interpreter"]
  → Filter by plan availability
  → For each plugin:
    → Get PluginManager instance
    → Load plugin's tool definitions
    → Register as Agent tools
  → Inject into Agent instance
```

---

## 9. Knowledge Base Processing

### 9.1 Upload Flow

```
User uploads file (PDF/TXT/MD/JSON/CSV)
  → Validate file type and size
  → Save to /data/knowledge/{tenant_id}/{file_id}.{ext}
  → Create KnowledgeFile record (status=pending)
  → Background processing:
    1. Extract text (PyPDF2 for PDF, direct read for text)
    2. Chunk into ~500 token segments
    3. Store chunks in /data/knowledge/{tenant_id}/{file_id}.chunks.json
    4. Update KnowledgeFile (status=ready, chunk_count=N)
  → Auto-add to AgentConfig.knowledge_ids
```

### 9.2 Knowledge Injection Strategy

Two approaches for injecting knowledge into Agent context:

**Option A: System Prompt Injection (Simple, Recommended for v1)**
- Concatenate knowledge text into system prompt
- Pros: Simple, works with any LLM
- Cons: Token limit (knowledge must fit in context window)

**Option B: RAG Retrieval (Future)**
- Embed chunks, retrieve relevant chunks per query
- Pros: No context window limit, more precise
- Cons: Requires embedding model, more complex

**Decision**: Start with Option A (system prompt injection) for v1. Add RAG in future iteration.

### 9.3 Plan-Based Knowledge Limits

| Plan | Max Files | Max Total Size |
|------|-----------|----------------|
| Free | 0 | 0 MB |
| Pro | 10 | 50 MB |
| Enterprise | 50 | 500 MB |

---

## 10. Custom Tools Runtime

### 10.1 Tool Definition Schema

```json
{
  "id": "tool_001",
  "name": "get_weather",
  "description": "Get current weather for a city",
  "parameters": {
    "type": "object",
    "properties": {
      "city": { "type": "string", "description": "City name" }
    },
    "required": ["city"]
  },
  "execution": {
    "type": "http",
    "url": "https://api.example.com/weather",
    "method": "GET",
    "headers": { "Authorization": "Bearer xxx" },
    "query_params": { "q": "{{city}}" },
    "response_path": "$.weather.description"
  }
}
```

### 10.2 Execution Flow

```
LLM decides to call tool "get_weather" with args {"city": "Beijing"}
  → Agent intercepts tool_call
  → Look up tool definition in AgentConfig.tools
  → Build HTTP request:
    - URL: https://api.example.com/weather
    - Method: GET
    - Query: ?q=Beijing
    - Headers: Authorization: Bearer xxx
  → Execute request (timeout: 10s)
  → Extract response_path from JSON response
  → Return tool result to LLM
  → LLM continues reasoning with tool result
```

### 10.3 Security

- Tool execution runs in sandboxed environment
- Only HTTPS endpoints allowed
- Request timeout: 10 seconds
- Response size limit: 1MB
- No internal network access (block 10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x)
- API keys in tool definitions are encrypted at rest

---

## 11. Session Management

### 11.1 Per-Tenant Agent Sessions

```
AgentFactory maintains:
  _instances: { tenant_id: Agent }

Each Agent internally maintains:
  messages: [{ role, content }]

Session isolation:
  - Each tenant's Agent has its own message history
  - Multiple sessions per tenant via session_id
  - Session data stored in Agent's internal memory
```

### 11.2 Session Persistence

For multi-session support (future), sessions will be stored in DB:

```python
class AgentSession(db.Model):
    id          = Column(String(36), primary_key=True)
    tenant_id   = Column(String(36), ForeignKey('tenant.id'))
    agent_config_id = Column(String(36), ForeignKey('agent_config.id'))
    messages    = Column(JSON, default=list)
    created_at  = Column(DateTime)
    updated_at  = Column(DateTime)
```

For v1 (one Agent per tenant), we use the Agent's in-memory message list with the existing session tracking mechanism.

---

## 12. Billing & Quota Integration

### 12.1 Plan-Based Feature Matrix

| Feature | Free | Pro | Enterprise |
|---------|------|-----|------------|
| Custom system prompt | ✅ | ✅ | ✅ |
| Model selection | 1 model | All models | All models + custom |
| Plugins | 2 basic | All | All + custom |
| Knowledge base | ❌ | 10 files / 50MB | 50 files / 500MB |
| Custom tools | ❌ | 3 tools | Unlimited |
| Max steps | 5 | 15 | 50 |
| Temperature control | ❌ | ✅ | ✅ |
| Thinking mode | ❌ | ✅ | ✅ |

### 12.2 Quota Enforcement

```python
def validate_agent_config(config: AgentConfig, tenant: Tenant):
    plan_limits = PLAN_LIMITS[tenant.plan]

    # Plugin count
    if len(config.plugins) > plan_limits["max_plugins"]:
        raise QuotaExceededError(f"Max {plan_limits['max_plugins']} plugins for {tenant.plan} plan")

    # Knowledge files
    if len(config.knowledge_ids) > plan_limits["max_knowledge_files"]:
        raise QuotaExceededError(f"Max {plan_limits['max_knowledge_files']} knowledge files")

    # Custom tools
    if len(config.tools) > plan_limits["max_custom_tools"]:
        raise QuotaExceededError(f"Max {plan_limits['max_custom_tools']} custom tools")

    # Max steps
    if config.max_steps > plan_limits["max_steps"]:
        config.max_steps = plan_limits["max_steps"]

    # Plugin availability
    for plugin in config.plugins:
        if tenant.plan not in PLUGIN_REGISTRY[plugin]["plan_availability"]:
            raise QuotaExceededError(f"Plugin '{plugin}' not available for {tenant.plan} plan")
```

---

## 13. File Structure

### 13.1 New Files

```
saas/
├── agent_factory.py          # Agent factory (core new module)
├── plugin_registry.py        # Plugin registry and plan-based access
├── knowledge_processor.py    # Knowledge file processing
├── custom_tool_executor.py   # Custom tool HTTP execution
├── api/
│   ├── agent.py              # Agent config API endpoints
│   └── knowledge.py          # Knowledge base API endpoints
admin-panel/src/
├── pages/
│   └── AgentBuilder.jsx      # Agent builder page (new)
├── components/
│   ├── AgentBasicSettings.jsx
│   ├── AgentModelSettings.jsx
│   ├── AgentPluginSettings.jsx
│   ├── AgentKnowledgeSettings.jsx
│   └── AgentPreview.jsx
```

### 13.2 Modified Files

```
saas/database.py              # Add AgentConfig, KnowledgeFile models
saas/chat_engine.py           # Refactor to use AgentFactory
app.py                        # Register new blueprints
admin-panel/src/App.jsx       # Add Agent Builder route and menu
admin-panel/src/api/client.js # Add Agent API functions
```

---

## 14. Implementation Phases

### Phase 1: Foundation (Core)
1. Add `AgentConfig` and `KnowledgeFile` models to database.py
2. Implement `AgentFactory` with basic Agent creation
3. Refactor `chat_engine.py` to use Factory
4. Add Agent Config API endpoints
5. Add database migration

### Phase 2: Plugin System
6. Implement `plugin_registry.py`
7. Add plugin enable/disable per tenant
8. Add Plugin API endpoints
9. Plan-based plugin access control

### Phase 3: Knowledge Base
10. Implement `knowledge_processor.py`
11. Add Knowledge upload/delete API
12. Knowledge injection into Agent context
13. Plan-based knowledge limits

### Phase 4: Custom Tools
14. Implement `custom_tool_executor.py`
15. Add Custom Tools API
16. Tool execution sandbox and security

### Phase 5: Frontend
17. Build AgentBuilder page with all tabs
18. Add navigation menu item
19. Real-time preview and testing

### Phase 6: Testing & Polish
20. Comprehensive test suite
21. Security audit
22. Performance optimization (Agent caching, knowledge caching)

---

## 15. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Agent instance memory usage | High with many tenants | LRU cache with max size, destroy idle agents |
| Knowledge injection exceeds context window | Agent fails | Truncate knowledge, warn user about size |
| Custom tool endpoint security | SSRF, data exfiltration | Block internal IPs, timeout, size limit, HTTPS only |
| Concurrent config updates | Race condition | config_version + optimistic locking |
| Plugin compatibility | Some plugins may not work independently | Test each plugin in isolation, document compatibility |
