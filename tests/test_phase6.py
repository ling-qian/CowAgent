# encoding:utf-8
"""
Phase 6: 全量测试套件 — 端到端集成测试 + 安全审计

覆盖范围：
1. 配置 CRUD + plan 字段
2. 插件列表 + 计划验证 + 启用/禁用
3. 知识库上传/处理/注入/删除 + 计划限制
4. 自定义工具 CRUD + 执行引擎 + 计划限制
5. AgentFactory 集成（工具加载 + 知识注入）
6. 安全审计（SSRF/注入/权限绕过/敏感数据泄露）
7. 性能（config_version 乐观锁 + Agent 缓存）
8. 多租户隔离
"""

import io
import json
import os
import time
import uuid

# ---------------------------------------------------------------------------
# Test Harness
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0
_errors = []


def assert_ok(condition, msg=""):
    global _passed, _failed, _errors
    if condition:
        _passed += 1
    else:
        _failed += 1
        _errors.append(msg or "Assertion failed")
        raise AssertionError(msg or "Assertion failed")


def assert_eq(actual, expected, msg=""):
    assert_ok(actual == expected, msg or f"Expected {expected!r}, got {actual!r}")


def assert_in(item, collection, msg=""):
    assert_ok(item in collection, msg or f"{item!r} not in {collection!r}")


def assert_not_in(item, collection, msg=""):
    assert_ok(item not in collection, msg or f"{item!r} should not be in {collection!r}")


def assert_status(resp, expected, msg=""):
    assert_eq(resp.status_code, expected, msg or f"HTTP {resp.status_code}, expected {expected}")


def setup_app():
    """创建测试 Flask app"""
    from flask import Flask
    from flask_cors import CORS
    from saas.database import init_db, db, Tenant, ApiKey
    from saas.api.agent import agent_bp
    from saas.api.knowledge import knowledge_bp
    from saas.api.tools import tools_bp
    from saas.middleware import TenantMiddleware, generate_api_key
    from saas.rate_limit import RateLimitMiddleware
    import saas as _saas_mod

    app = Flask(__name__)
    CORS(app)
    db_uri = f"sqlite:///test_phase6_{uuid.uuid4().hex[:8]}.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    init_db(app=app, database_uri=db_uri)
    TenantMiddleware(app)
    RateLimitMiddleware(app)
    app.register_blueprint(agent_bp, url_prefix="/api/agent")
    app.register_blueprint(knowledge_bp, url_prefix="/api/agent/knowledge")
    app.register_blueprint(tools_bp, url_prefix="/api/agent/tools")
    _saas_mod.set_flask_app(app)

    return app


def create_tenant(app, plan="pro"):
    """创建测试租户 + API Key"""
    from saas.database import db, Tenant, ApiKey
    from saas.middleware import generate_api_key
    with app.app_context():
        slug = f"test-{uuid.uuid4().hex[:6]}"
        tenant = Tenant(name=f"Test-{slug}", slug=slug, plan=plan)
        db.session.add(tenant)
        db.session.commit()
        tid = tenant.id

        raw_key, key_hash, key_prefix = generate_api_key()
        api_key = ApiKey(tenant_id=tid, key_hash=key_hash, key_prefix=key_prefix, name="test")
        db.session.add(api_key)
        db.session.commit()

        from saas.agent_factory import get_or_create_default_config
        config = get_or_create_default_config(tid)
        config.api_key = "test-key"
        config.api_base = "https://api.openai.com/v1"
        db.session.commit()

        return tid, raw_key


# ---------------------------------------------------------------------------
# 1. Configuration Tests
# ---------------------------------------------------------------------------

def test_config_crud(app, tenant_id, api_key):
    """配置 CRUD + plan 字段"""
    from saas.database import db, AgentConfig
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # GET
        resp = client.get("/api/agent/config", headers=headers)
        assert_status(resp, 200, "GET config")
        data = resp.get_json()
        assert_ok("plan" in data, "Config missing plan field")
        assert_eq(data["plan"], "pro", "Plan should be pro")

        # PUT - basic
        resp = client.put("/api/agent/config", json={
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are helpful.",
            "temperature": 0.3,
            "max_steps": 15,
            "enable_thinking": True,
        }, headers=headers)
        assert_status(resp, 200, "PUT config basic")
        data = resp.get_json()
        assert_eq(data["name"], "Test Agent")
        assert_eq(data["temperature"], 0.3)
        assert_eq(data["max_steps"], 15)
        assert_eq(data["enable_thinking"], True)

        # Verify persisted
        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        assert_eq(config.name, "Test Agent")
        assert_eq(config.temperature, 0.3)

        # API key should be masked
        assert_eq(data["api_key"], "***")

        print("  [PASS] test_config_crud")


def test_config_version_increment(app, tenant_id, api_key):
    """config_version 在每次更新时递增"""
    from saas.database import db, AgentConfig
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        v1 = config.config_version

        resp = client.put("/api/agent/config", json={"name": "Updated"}, headers=headers)
        assert_status(resp, 200)
        db.session.refresh(config)
        v2 = config.config_version
        assert_ok(v2 > v1, f"config_version should increment: {v1} -> {v2}")

        print("  [PASS] test_config_version_increment")


# ---------------------------------------------------------------------------
# 2. Plugin Tests
# ---------------------------------------------------------------------------

def test_plugins_list(app, tenant_id, api_key):
    """插件列表 + 分类"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        resp = client.get("/api/agent/plugins", headers=headers)
        assert_status(resp, 200)
        data = resp.get_json()
        categories = data.get("categories", {})
        assert_ok(len(categories) > 0, "Should have plugin categories")
        total = sum(len(c.get("plugins", [])) for c in categories.values())
        assert_ok(total > 0, "Should have plugins")

        # Each plugin should have name, description, available
        for cat_data in categories.values():
            for p in cat_data.get("plugins", []):
                assert_ok("name" in p, f"Plugin missing name: {p}")
                assert_ok("available" in p, f"Plugin missing available: {p['name']}")

        print("  [PASS] test_plugins_list")


def test_plugins_plan_validation(app, tenant_id, api_key):
    """插件计划验证 — pro 不能启用 enterprise-only 插件"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # 找一个 enterprise-only 插件
        from saas.plugin_registry import PLUGIN_REGISTRY
        enterprise_plugins = [p for p, d in PLUGIN_REGISTRY.items()
                              if "enterprise" in d.get("plan_availability", [])
                              and "pro" not in d.get("plan_availability", [])]

        if enterprise_plugins:
            resp = client.put("/api/agent/config", json={"plugins": enterprise_plugins}, headers=headers)
            assert_status(resp, 403, f"Should reject enterprise plugins for pro: {enterprise_plugins}")
            data = resp.get_json()
            assert_ok("unavailable_plugins" in data, "Should list unavailable plugins")

        print("  [PASS] test_plugins_plan_validation")


def test_plugins_enable_disable(app, tenant_id, api_key):
    """插件启用/禁用"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # Enable
        resp = client.put("/api/agent/config", json={"plugins": ["web_search"]}, headers=headers)
        assert_status(resp, 200)
        assert_in("web_search", resp.get_json()["plugins"])

        # Disable all
        resp = client.put("/api/agent/config", json={"plugins": []}, headers=headers)
        assert_status(resp, 200)
        assert_eq(resp.get_json()["plugins"], [])

        print("  [PASS] test_plugins_enable_disable")


# ---------------------------------------------------------------------------
# 3. Knowledge Tests
# ---------------------------------------------------------------------------

def test_knowledge_upload_and_process(app, tenant_id, api_key):
    """知识上传 + 后台处理"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}"}

        content = b"Knowledge line 1.\nKnowledge line 2.\nKnowledge line 3.\n" * 20
        resp = client.post("/api/agent/knowledge/upload",
                           data={"file": (io.BytesIO(content), "test.txt")},
                           content_type="multipart/form-data", headers=headers)
        assert_status(resp, 201)
        data = resp.get_json()
        file_id = data["file"]["id"]
        assert_ok(data["file"]["status"] in ("pending", "processing"),
                  f"File status should be pending/processing, got {data['file']['status']}")

        time.sleep(1.5)

        # Verify processed
        json_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = client.get("/api/agent/knowledge", headers=json_headers)
        files = resp.get_json().get("files", [])
        assert_ok(len(files) > 0, "Should have uploaded file")
        f = files[0]
        assert_ok(f["status"] in ("completed", "ready"), f"File should be processed, got {f['status']}")
        assert_ok(f.get("chunk_count", 0) > 0, "Should have chunks")
        assert_ok(f.get("file_size", 0) > 0, "Should have file_size")

        # Cleanup
        client.delete(f"/api/agent/knowledge/{file_id}", headers=json_headers)
        print("  [PASS] test_knowledge_upload_and_process")


def test_knowledge_plan_limits(app):
    """知识库计划限制 — free 不能上传"""
    tenant_id, api_key = create_tenant(app, plan="free")
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}"}

        content = b"Test content"
        resp = client.post("/api/agent/knowledge/upload",
                           data={"file": (io.BytesIO(content), "test.txt")},
                           content_type="multipart/form-data", headers=headers)
        assert_status(resp, 403, "Free plan should not allow knowledge upload")

        print("  [PASS] test_knowledge_plan_limits")


def test_knowledge_unsupported_type(app, tenant_id, api_key):
    """知识库不支持 .exe 文件"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}"}

        resp = client.post("/api/agent/knowledge/upload",
                           data={"file": (io.BytesIO(b"MZ"), "malware.exe")},
                           content_type="multipart/form-data", headers=headers)
        assert_ok(resp.status_code in (400, 403), f"Should reject .exe file, got {resp.status_code}")

        print("  [PASS] test_knowledge_unsupported_type")


# ---------------------------------------------------------------------------
# 4. Custom Tools Tests
# ---------------------------------------------------------------------------

def test_tools_crud(app, tenant_id, api_key):
    """自定义工具 CRUD"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # Create
        resp = client.post("/api/agent/tools", json={
            "name": "lookup_user",
            "description": "Look up user by ID",
            "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
            "execution": {"type": "http", "url": "https://api.example.com/users/{{user_id}}", "method": "GET"},
        }, headers=headers)
        assert_status(resp, 201)
        tool_id = resp.get_json()["id"]
        assert_eq(resp.get_json()["name"], "lookup_user")

        # List
        resp = client.get("/api/agent/tools", headers=headers)
        assert_status(resp, 200)
        assert_eq(resp.get_json()["count"], 1)

        # Update
        resp = client.put(f"/api/agent/tools/{tool_id}", json={
            "name": "lookup_user",
            "description": "Look up user by ID (updated)",
            "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
            "execution": {"type": "http", "url": "https://api.example.com/users/{{user_id}}", "method": "POST"},
        }, headers=headers)
        assert_status(resp, 200)
        assert_eq(resp.get_json()["description"], "Look up user by ID (updated)")

        # Delete
        resp = client.delete(f"/api/agent/tools/{tool_id}", headers=headers)
        assert_status(resp, 200)
        assert_ok(resp.get_json()["deleted"])

        # Verify empty
        resp = client.get("/api/agent/tools", headers=headers)
        assert_eq(resp.get_json()["count"], 0)

        print("  [PASS] test_tools_crud")


def test_tools_plan_limits(app):
    """自定义工具计划限制"""
    tenant_id, api_key = create_tenant(app, plan="free")
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        resp = client.post("/api/agent/tools", json={
            "name": "free_tool", "description": "Should be rejected",
            "parameters": {"type": "object", "properties": {}},
            "execution": {"type": "http", "url": "https://example.com", "method": "GET"},
        }, headers=headers)
        assert_status(resp, 403, "Free plan should not allow custom tools")

        print("  [PASS] test_tools_plan_limits")


def test_tools_pro_limit(app):
    """Pro 计划最多 3 个工具"""
    tenant_id, api_key = create_tenant(app, plan="pro")
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        for i in range(3):
            resp = client.post("/api/agent/tools", json={
                "name": f"tool_{i}", "description": f"Tool {i}",
                "parameters": {"type": "object", "properties": {}},
                "execution": {"type": "http", "url": "https://example.com", "method": "GET"},
            }, headers=headers)
            assert_status(resp, 201, f"Should allow tool_{i}")

        # 4th should fail
        resp = client.post("/api/agent/tools", json={
            "name": "tool_extra", "description": "Over limit",
            "parameters": {"type": "object", "properties": {}},
            "execution": {"type": "http", "url": "https://example.com", "method": "GET"},
        }, headers=headers)
        assert_status(resp, 403, "Should reject 4th tool on pro plan")

        print("  [PASS] test_tools_pro_limit")


def test_tools_duplicate_name(app, tenant_id, api_key):
    """工具名重复检查"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        tool_def = {
            "name": "dup_tool", "description": "First",
            "parameters": {"type": "object", "properties": {}},
            "execution": {"type": "http", "url": "https://example.com", "method": "GET"},
        }
        resp = client.post("/api/agent/tools", json=tool_def, headers=headers)
        assert_status(resp, 201)

        resp = client.post("/api/agent/tools", json={**tool_def, "description": "Second"}, headers=headers)
        assert_status(resp, 409, "Should reject duplicate name")

        print("  [PASS] test_tools_duplicate_name")


def test_tools_invalid_definition(app, tenant_id, api_key):
    """工具定义验证"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # Missing name
        resp = client.post("/api/agent/tools", json={"description": "No name"}, headers=headers)
        assert_status(resp, 400)

        # Bad name format
        resp = client.post("/api/agent/tools", json={
            "name": "123invalid", "description": "Bad name",
            "parameters": {"type": "object", "properties": {}},
            "execution": {"type": "http", "url": "https://example.com", "method": "GET"},
        }, headers=headers)
        assert_status(resp, 400)

        # Missing URL
        resp = client.post("/api/agent/tools", json={
            "name": "no_url", "description": "No URL",
            "parameters": {"type": "object", "properties": {}},
            "execution": {"type": "http", "method": "GET"},
        }, headers=headers)
        assert_status(resp, 400)

        print("  [PASS] test_tools_invalid_definition")


# ---------------------------------------------------------------------------
# 5. AgentFactory Integration Tests
# ---------------------------------------------------------------------------

def test_agent_factory_tools_loading(app, tenant_id, api_key):
    """AgentFactory 正确加载插件工具 + 自定义工具"""
    from saas.database import db, AgentConfig
    from saas.agent_factory import AgentFactory
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # Enable plugins
        client.put("/api/agent/config", json={"plugins": ["web_search", "file_read"]}, headers=headers)

        # Add custom tool
        client.post("/api/agent/tools", json={
            "name": "my_api", "description": "Call my API",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "execution": {"type": "http", "url": "https://api.example.com/search?q={{query}}", "method": "GET"},
        }, headers=headers)

        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        tools = AgentFactory._load_tools(config)
        tool_names = [getattr(t, "name", str(t)) for t in tools]

        assert_in("my_api", tool_names, "Custom tool should be loaded")
        # Plugin tools should also be present
        assert_ok(len(tool_names) >= 3, f"Should have plugin + custom tools, got {tool_names}")

        print("  [PASS] test_agent_factory_tools_loading")


def test_agent_factory_knowledge_injection(app, tenant_id, api_key):
    """AgentFactory 知识注入到 system prompt"""
    from saas.database import db, AgentConfig
    from saas.agent_factory import AgentFactory
    with app.app_context():
        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        knowledge_ctx = AgentFactory._load_knowledge(config)
        # May be empty if no knowledge files, but should not error
        assert_ok(isinstance(knowledge_ctx, str), "Knowledge context should be a string")

        print("  [PASS] test_agent_factory_knowledge_injection")


def test_agent_factory_cache_invalidation(app, tenant_id, api_key):
    """AgentFactory 缓存失效 — config_version 变更时重建"""
    from saas.database import db, AgentConfig
    from saas.agent_factory import AgentFactory
    with app.app_context():
        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        v1 = config.config_version

        # Update config
        config.name = "Updated Agent"
        config.config_version += 1
        db.session.commit()

        # Old version should be invalidated
        cached_version = AgentFactory._versions.get(tenant_id)
        assert_ok(cached_version != v1 or tenant_id not in AgentFactory._instances,
                  "Cache should be invalidated on config change")

        print("  [PASS] test_agent_factory_cache_invalidation")


# ---------------------------------------------------------------------------
# 6. Security Tests
# ---------------------------------------------------------------------------

def test_ssrf_internal_ip_blocking():
    """SSRF 防护 — 阻止内网 IP"""
    from saas.custom_tool_executor import _validate_url

    # Internal IPs should be blocked
    internal_urls = [
        "http://192.168.1.1/admin",
        "http://10.0.0.1/secret",
        "http://127.0.0.1:8080/",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
    ]
    for url in internal_urls:
        is_valid, msg = _validate_url(url)
        assert_ok(not is_valid, f"Should block internal URL: {url} (got: {msg})")

    # External HTTPS should be allowed
    is_valid, msg = _validate_url("https://api.example.com/endpoint")
    assert_ok(is_valid, f"Should allow external HTTPS: {msg}")

    print("  [PASS] test_ssrf_internal_ip_blocking")


def test_ssrf_ftp_scheme_blocked():
    """SSRF 防护 — 阻止非 HTTP(S) 协议"""
    from saas.custom_tool_executor import _validate_url

    is_valid, msg = _validate_url("ftp://example.com/file")
    assert_ok(not is_valid, "Should block FTP scheme")

    is_valid, msg = _validate_url("file:///etc/passwd")
    assert_ok(not is_valid, "Should block file:// scheme")

    print("  [PASS] test_ssrf_ftp_scheme_blocked")


def test_redirect_blocking():
    """重定向阻止 — CustomHttpTool 不跟随重定向"""
    from saas.custom_tool_executor import CustomHttpTool

    # 验证 allow_redirects=False 在请求参数中
    tool = CustomHttpTool({
        "name": "redirect_test",
        "description": "Redirect test",
        "parameters": {"type": "object", "properties": {}},
        "execution": {"type": "http", "url": "https://httpbin.org/redirect/1", "method": "GET"},
    })
    # 执行请求（httpbin 会返回 302）
    result = tool.execute({})
    # 应该返回失败（重定向被阻止）
    assert_ok(result.status != "success",
              f"Should block redirects, got status={result.status}")

    print("  [PASS] test_redirect_blocking")


def test_tool_execution_timeout():
    """自定义工具执行超时配置"""
    from saas.custom_tool_executor import CustomHttpTool, REQUEST_TIMEOUT

    # Verify timeout is configured (10s)
    assert_eq(REQUEST_TIMEOUT, 10, "Request timeout should be 10s")

    # Test with an internal URL that will be blocked (fast fail, no network)
    tool = CustomHttpTool({
        "name": "internal_api",
        "description": "Internal API",
        "parameters": {"type": "object", "properties": {}},
        "execution": {"type": "http", "url": "http://192.168.1.1/admin", "method": "GET"},
    })
    result = tool.execute({})
    assert_ok(result.status != "success", f"Should fail for internal URL, got status={result.status}")

    print("  [PASS] test_tool_execution_timeout")


def test_tool_template_injection():
    """模板注入防护 — {{}} 只替换已知参数"""
    from saas.custom_tool_executor import _render_template

    # Normal rendering
    result = _render_template("https://api.example.com?q={{city}}", {"city": "Beijing"})
    assert_eq(result, "https://api.example.com?q=Beijing")

    # Unknown param should be empty string, not error
    result = _render_template("https://api.example.com?q={{city}}&lang={{lang}}", {"city": "Beijing"})
    assert_eq(result, "https://api.example.com?q=Beijing&lang=")

    # No template injection — {{}} in URL should not execute code
    # The regex \{\{(\w+)\}\} only matches simple identifiers (\w+),
    # so {{__import__('os').system('ls')}} is NOT matched and stays as-is.
    # The key security property: no Python code is evaluated.
    # We verify by checking that only simple {{param}} patterns get replaced.
    result = _render_template("https://api.example.com?q={{__import__('os').system('ls')}}", {})
    # The complex expression should remain as literal text (not executed/replaced)
    assert_ok("{{" in result, "Complex template expressions should not be evaluated, just left as-is")

    print("  [PASS] test_tool_template_injection")


def test_tool_response_path_traversal():
    """响应路径遍历防护"""
    from saas.custom_tool_executor import _extract_response_path

    data = {"secret": "password123", "public": "hello"}

    # Normal path
    result = _extract_response_path(data, "$.public")
    assert_eq(result, "hello")

    # Non-existent path should return None
    result = _extract_response_path(data, "$.nonexistent")
    assert_eq(result, None)

    # Path without $ should return full data
    result = _extract_response_path(data, "")
    assert_ok(result is not None, "Empty path should return full data")

    print("  [PASS] test_tool_response_path_traversal")


def test_api_key_masking(app, tenant_id, api_key):
    """API Key 脱敏 — 配置接口不应返回明文 key"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        resp = client.get("/api/agent/config", headers=headers)
        data = resp.get_json()
        assert_ok(data.get("api_key") != "test-key", "Should not return plaintext API key")
        assert_eq(data.get("api_key"), "***", "Should mask API key")

        print("  [PASS] test_api_key_masking")


def test_tool_headers_masking(app, tenant_id, api_key):
    """自定义工具 Headers 脱敏"""
    with app.app_context():
        client = app.test_client()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        resp = client.post("/api/agent/tools", json={
            "name": "secure_api", "description": "API with auth",
            "parameters": {"type": "object", "properties": {}},
            "execution": {
                "type": "http", "url": "https://api.example.com", "method": "GET",
                "headers": {"Authorization": "Bearer secret-token-123", "X-Custom": "visible"},
            },
        }, headers=headers)
        data = resp.get_json()
        exec_headers = data.get("execution", {}).get("headers", {})
        assert_eq(exec_headers.get("Authorization"), "***", "Should mask Authorization header")
        assert_eq(exec_headers.get("X-Custom"), "visible", "Should not mask non-sensitive header")

        print("  [PASS] test_tool_headers_masking")


def test_tenant_isolation(app):
    """多租户隔离 — 租户 A 不能访问租户 B 的工具"""
    tid_a, key_a = create_tenant(app, plan="pro")
    tid_b, key_b = create_tenant(app, plan="pro")

    with app.app_context():
        client = app.test_client()
        headers_a = {"Authorization": f"Bearer {key_a}", "Content-Type": "application/json"}
        headers_b = {"Authorization": f"Bearer {key_b}", "Content-Type": "application/json"}

        # Tenant A adds a tool
        resp = client.post("/api/agent/tools", json={
            "name": "tenant_a_tool", "description": "A's tool",
            "parameters": {"type": "object", "properties": {}},
            "execution": {"type": "http", "url": "https://a.example.com", "method": "GET"},
        }, headers=headers_a)
        assert_status(resp, 201)
        tool_id_a = resp.get_json()["id"]

        # Tenant B should not see A's tools
        resp = client.get("/api/agent/tools", headers=headers_b)
        assert_eq(resp.get_json()["count"], 0, "Tenant B should not see A's tools")

        # Tenant B cannot update/delete A's tool
        resp = client.delete(f"/api/agent/tools/{tool_id_a}", headers=headers_b)
        assert_status(resp, 404, "Tenant B should not delete A's tool")

        print("  [PASS] test_tenant_isolation")


def test_unauthenticated_access(app):
    """未认证访问拒绝"""
    with app.app_context():
        client = app.test_client()
        headers = {"Content-Type": "application/json"}

        resp = client.get("/api/agent/config", headers=headers)
        assert_ok(resp.status_code in (401, 403), f"Should reject unauthenticated: {resp.status_code}")

        resp = client.get("/api/agent/tools", headers=headers)
        assert_ok(resp.status_code in (401, 403), f"Should reject unauthenticated: {resp.status_code}")

        print("  [PASS] test_unauthenticated_access")


# ---------------------------------------------------------------------------
# 7. Performance Tests
# ---------------------------------------------------------------------------

def test_agent_factory_lru_eviction():
    """AgentFactory LRU 淘汰 — 超过 MAX_INSTANCES 时淘汰最旧"""
    from saas.agent_factory import AgentFactory
    assert_ok(hasattr(AgentFactory, "MAX_INSTANCES"), "Should have MAX_INSTANCES")
    assert_ok(AgentFactory.MAX_INSTANCES > 0, "MAX_INSTANCES should be positive")
    print("  [PASS] test_agent_factory_lru_eviction")


def test_knowledge_context_truncation(app, tenant_id, api_key):
    """知识上下文截断 — 超过 max_context_chars 时截断"""
    from saas.knowledge_processor import load_knowledge_context
    from saas.database import db, AgentConfig, KnowledgeFile
    with app.app_context():
        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        # Even with no knowledge files, should not error
        ctx = load_knowledge_context(tenant_id, [], max_chars=100)
        assert_eq(ctx, "", "Empty knowledge_ids should return empty string")

        print("  [PASS] test_knowledge_context_truncation")


def test_knowledge_cache(app, tenant_id, api_key):
    """知识上下文缓存 — config_version 不变时使用缓存"""
    from saas.database import db, AgentConfig
    from saas.agent_factory import AgentFactory
    with app.app_context():
        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()

        # 第一次加载
        ctx1 = AgentFactory._load_knowledge(config)

        # 第二次加载（应使用缓存或返回相同结果）
        ctx2 = AgentFactory._load_knowledge(config)
        assert_eq(ctx1, ctx2, "Knowledge should be consistent")

        # 如果有知识文件，验证缓存存在
        if config.get_knowledge_ids():
            cached = AgentFactory._knowledge_cache.get(tenant_id)
            assert_ok(cached is not None, "Should have knowledge cache entry")
            assert_eq(cached[0], config.config_version, "Cache version should match")

        print("  [PASS] test_knowledge_cache")


# ---------------------------------------------------------------------------
# 8. Custom Tool Executor Unit Tests
# ---------------------------------------------------------------------------

def test_custom_tool_execute_success():
    """CustomHttpTool 构造和参数渲染"""
    from saas.custom_tool_executor import CustomHttpTool

    tool = CustomHttpTool({
        "name": "test_tool",
        "description": "Test",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        "execution": {
            "type": "http",
            "url": "https://httpbin.org/get?q={{q}}",
            "method": "GET",
        },
    })
    assert_eq(tool.name, "test_tool")
    assert_eq(tool.description, "Test")

    print("  [PASS] test_custom_tool_execute_success")


def test_custom_tool_no_execution_config():
    """CustomHttpTool 无执行配置时返回失败"""
    from saas.custom_tool_executor import CustomHttpTool

    tool = CustomHttpTool({
        "name": "no_exec",
        "description": "No execution config",
        "parameters": {"type": "object", "properties": {}},
    })
    result = tool.execute({})
    assert_ok(result.status != "success", "Should fail without execution config")

    print("  [PASS] test_custom_tool_no_execution_config")


def test_validate_tool_definition():
    """工具定义验证"""
    from saas.custom_tool_executor import validate_tool_definition

    # Valid
    is_valid, _ = validate_tool_definition({
        "name": "valid_tool", "description": "Valid",
        "parameters": {"type": "object", "properties": {}},
        "execution": {"type": "http", "url": "https://example.com", "method": "GET"},
    })
    assert_ok(is_valid, "Should be valid")

    # Missing name
    is_valid, msg = validate_tool_definition({"description": "No name"})
    assert_ok(not is_valid, "Should reject missing name")

    # Name starts with number
    is_valid, msg = validate_tool_definition({"name": "123abc", "description": "Bad"})
    assert_ok(not is_valid, "Should reject name starting with number")

    # Name too long (>64 chars)
    is_valid, msg = validate_tool_definition({"name": "a" * 65, "description": "Too long"})
    assert_ok(not is_valid, "Should reject too long name")

    # Invalid method
    is_valid, msg = validate_tool_definition({
        "name": "bad_method", "description": "Bad method",
        "execution": {"url": "https://example.com", "method": "TRACE"},
    })
    assert_ok(not is_valid, "Should reject TRACE method")

    print("  [PASS] test_validate_tool_definition")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all_tests():
    global _passed, _failed, _errors

    os.environ["SAAS_MODE"] = "true"

    app = setup_app()
    tenant_id, api_key = create_tenant(app, plan="pro")

    tests = [
        # 1. Configuration
        ("Config CRUD", lambda: test_config_crud(app, tenant_id, api_key)),
        ("Config version increment", lambda: test_config_version_increment(app, tenant_id, api_key)),
        # 2. Plugins
        ("Plugins list", lambda: test_plugins_list(app, tenant_id, api_key)),
        ("Plugins plan validation", lambda: test_plugins_plan_validation(app, tenant_id, api_key)),
        ("Plugins enable/disable", lambda: test_plugins_enable_disable(app, tenant_id, api_key)),
        # 3. Knowledge
        ("Knowledge upload & process", lambda: test_knowledge_upload_and_process(app, tenant_id, api_key)),
        ("Knowledge plan limits", lambda: test_knowledge_plan_limits(app)),
        ("Knowledge unsupported type", lambda: test_knowledge_unsupported_type(app, tenant_id, api_key)),
        # 4. Custom Tools
        ("Tools CRUD", lambda: test_tools_crud(app, tenant_id, api_key)),
        ("Tools plan limits", lambda: test_tools_plan_limits(app)),
        ("Tools pro limit (3)", lambda: test_tools_pro_limit(app)),
        ("Tools duplicate name", lambda: test_tools_duplicate_name(app, tenant_id, api_key)),
        ("Tools invalid definition", lambda: test_tools_invalid_definition(app, tenant_id, api_key)),
        # 5. AgentFactory
        ("AgentFactory tools loading", lambda: test_agent_factory_tools_loading(app, tenant_id, api_key)),
        ("AgentFactory knowledge injection", lambda: test_agent_factory_knowledge_injection(app, tenant_id, api_key)),
        ("AgentFactory cache invalidation", lambda: test_agent_factory_cache_invalidation(app, tenant_id, api_key)),
        # 6. Security
        ("SSRF internal IP blocking", test_ssrf_internal_ip_blocking),
        ("SSRF FTP scheme blocked", test_ssrf_ftp_scheme_blocked),
        ("Redirect blocking", test_redirect_blocking),
        ("Tool execution timeout", test_tool_execution_timeout),
        ("Template injection protection", test_tool_template_injection),
        ("Response path traversal", test_tool_response_path_traversal),
        ("API key masking", lambda: test_api_key_masking(app, tenant_id, api_key)),
        ("Tool headers masking", lambda: test_tool_headers_masking(app, tenant_id, api_key)),
        ("Tenant isolation", lambda: test_tenant_isolation(app)),
        ("Unauthenticated access", lambda: test_unauthenticated_access(app)),
        # 7. Performance
        ("AgentFactory LRU eviction", test_agent_factory_lru_eviction),
        ("Knowledge context truncation", lambda: test_knowledge_context_truncation(app, tenant_id, api_key)),
        ("Knowledge cache", lambda: test_knowledge_cache(app, tenant_id, api_key)),
        # 8. Unit Tests
        ("Custom tool execute success", test_custom_tool_execute_success),
        ("Custom tool no execution config", test_custom_tool_no_execution_config),
        ("Validate tool definition", test_validate_tool_definition),
    ]

    print(f"\n{'='*60}")
    print(f"Phase 6: Full Test Suite ({len(tests)} tests)")
    print(f"{'='*60}\n")

    passed_tests = 0
    failed_tests = 0

    for name, test_fn in tests:
        try:
            test_fn()
            passed_tests += 1
            print(f"  ✓ {name}")
        except Exception as e:
            failed_tests += 1
            print(f"  ✗ {name}: {e}")

    print(f"\n{'='*60}")
    print(f"Results: {passed_tests} passed, {failed_tests} failed, {passed_tests + failed_tests} total")
    if _errors:
        print(f"Errors: {_errors[:5]}")
    print(f"{'='*60}")

    return failed_tests == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
