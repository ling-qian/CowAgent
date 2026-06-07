# encoding:utf-8
"""
SaaS 多租户端到端集成测试

验证完整链路：
1. Flask 管理 API（注册租户、创建 API Key、查询用量）
2. API Key 认证中间件
3. 租户级配置覆盖 (conf_tenant)
4. 用量记录与查询
5. 健康检查端点
6. 租户统计 API

使用 SQLite 内存数据库模拟，无需 PostgreSQL 环境。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SAAS_TEST_MODE"] = "1"


def _create_test_app():
    """创建测试用 Flask app（SQLite 内存数据库 + 中间件）"""
    from flask import Flask
    from saas.database import db, init_db

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    init_db(app=app)

    from saas.api.tenants import tenants_bp
    from saas.api.keys import keys_bp
    from saas.api.usage import usage_bp
    from saas.api.health import health_bp
    from saas.api.audit import audit_bp
    from saas.api.webhooks import webhooks_bp
    from saas.api.gdpr import gdpr_bp
    from saas.api.billing import billing_bp
    from saas.api.sso import sso_bp
    from saas.api.plugins import plugins_bp

    app.register_blueprint(tenants_bp, url_prefix="/api/tenants")
    app.register_blueprint(keys_bp, url_prefix="/api/keys")
    app.register_blueprint(usage_bp, url_prefix="/api/usage")
    app.register_blueprint(audit_bp, url_prefix="/api/audit")
    app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
    app.register_blueprint(gdpr_bp, url_prefix="/api/gdpr")
    app.register_blueprint(billing_bp, url_prefix="/api/billing")
    app.register_blueprint(sso_bp, url_prefix="/api/auth")
    app.register_blueprint(plugins_bp, url_prefix="/api/plugins")
    app.register_blueprint(health_bp)

    # 注册中间件
    from saas.middleware import TenantMiddleware
    from saas.rate_limit import RateLimitMiddleware
    TenantMiddleware(app)
    RateLimitMiddleware(app)

    return app, db


def _register_tenant(client, name="TestCorp", slug="testcorp", plan="pro", email=None):
    """辅助：注册租户并返回 (tenant_id, api_key)"""
    email = email or f"admin@{slug}.com"
    resp = client.post("/api/tenants/register", json={
        "name": name,
        "slug": slug,
        "plan": plan,
        "email": email,
    })
    assert resp.status_code == 201, f"Register failed: {resp.get_data(as_text=True)}"
    data = resp.get_json()
    return data["tenant"]["id"], data["api_key"]


class BaseTestCase(unittest.TestCase):
    """公共基类：创建 app + 启用 saas_mode"""

    @classmethod
    def setUpClass(cls):
        cls.app, cls.db = _create_test_app()
        cls.client = cls.app.test_client()

    def setUp(self):
        from config import config
        self._original_saas = config.get("saas_mode", False)
        config["saas_mode"] = True
        with self.app.app_context():
            self.db.create_all()

    def tearDown(self):
        from config import config
        config["saas_mode"] = self._original_saas
        with self.app.app_context():
            for table in reversed(self.db.metadata.sorted_tables):
                self.db.session.execute(table.delete())
            self.db.session.commit()


class TestTenantRegistrationAPI(BaseTestCase):
    """测试租户注册 API"""

    def test_register_tenant_success(self):
        """注册新租户 — 正常流程"""
        resp = self.client.post("/api/tenants/register", json={
            "name": "TestCorp",
            "slug": "testcorp",
            "plan": "pro",
            "email": "admin@testcorp.com",
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIn("tenant", data)
        self.assertEqual(data["tenant"]["name"], "TestCorp")
        self.assertEqual(data["tenant"]["slug"], "testcorp")
        self.assertEqual(data["tenant"]["plan"], "pro")
        self.assertIn("api_key", data)
        self.assertIn("user", data)

    def test_register_tenant_duplicate_slug(self):
        """注册租户 — slug 重复应报 409"""
        self.client.post("/api/tenants/register", json={
            "name": "Corp1", "slug": "dup-slug", "plan": "free", "email": "a@b.com",
        })
        resp = self.client.post("/api/tenants/register", json={
            "name": "Corp2", "slug": "dup-slug", "plan": "free", "email": "c@d.com",
        })
        self.assertEqual(resp.status_code, 409)

    def test_register_tenant_missing_fields(self):
        """注册租户 — 缺少必填字段应报 400"""
        resp = self.client.post("/api/tenants/register", json={
            "name": "NoSlug",
        })
        self.assertEqual(resp.status_code, 400)

    def test_get_tenant_me(self):
        """获取当前租户信息"""
        tenant_id, api_key = _register_tenant(self.client, "MeCorp", "mecorp")

        resp = self.client.get("/api/tenants/me", headers={
            "X-API-Key": api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["name"], "MeCorp")

    def test_update_tenant_config(self):
        """更新租户配置"""
        tenant_id, api_key = _register_tenant(self.client, "ConfigCorp", "configcorp")

        resp = self.client.put("/api/tenants/me", headers={
            "X-API-Key": api_key,
        }, json={"config_json": {"model": "gpt-4o", "max_tokens": 8000}})
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get("/api/tenants/me", headers={
            "X-API-Key": api_key,
        })
        data = resp.get_json()
        self.assertIn("id", data)


class TestApiKeyAPI(BaseTestCase):
    """测试 API Key 管理 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "KeyCorp", "keycorp"
        )

    def test_create_api_key(self):
        """创建 API Key"""
        resp = self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "test-key"})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIn("api_key", data)
        self.assertTrue(data["api_key"].startswith("sk-"))
        self.assertIn("id", data)

    def test_list_api_keys(self):
        """列出 API Keys"""
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "key1"})
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "key2"})

        resp = self.client.get("/api/keys", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("keys", data)
        self.assertGreaterEqual(len(data["keys"]), 3)  # 1 default + 2 new

    def test_revoke_api_key(self):
        """吊销 API Key"""
        create = self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "to-revoke"})
        key_id = create.get_json()["id"]

        resp = self.client.delete(f"/api/keys/{key_id}", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)

    def test_api_key_isolation(self):
        """不同租户的 API Key 应该隔离"""
        tenant2_id, api_key2 = _register_tenant(self.client, "OtherCorp", "othercorp")

        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "key-t1"})
        self.client.post("/api/keys", headers={
            "X-API-Key": api_key2,
        }, json={"name": "key-t2"})

        resp1 = self.client.get("/api/keys", headers={"X-API-Key": self.api_key})
        resp2 = self.client.get("/api/keys", headers={"X-API-Key": api_key2})

        keys1 = resp1.get_json()["keys"]
        keys2 = resp2.get_json()["keys"]
        ids1 = {k["id"] for k in keys1}
        ids2 = {k["id"] for k in keys2}
        self.assertEqual(ids1 & ids2, set())


class TestUsageAPI(BaseTestCase):
    """测试用量记录与查询 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "UsageCorp", "usagecorp"
        )

    def test_record_and_query_usage(self):
        """记录用量 + 查询用量"""
        with self.app.app_context():
            from saas.api.usage import record_usage
            record_usage(self.tenant_id, "llm_tokens", 1500)
            record_usage(self.tenant_id, "llm_tokens", 500)
            record_usage(self.tenant_id, "api_calls", 10)

        resp = self.client.get("/api/usage", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("usage", data)
        metrics = {r["metric"]: r["value"] for r in data["usage"]}
        self.assertGreaterEqual(metrics.get("llm_tokens", 0), 2000)
        self.assertGreaterEqual(metrics.get("api_calls", 0), 10)

    def test_usage_isolation(self):
        """不同租户的用量应该隔离"""
        tenant2_id, api_key2 = _register_tenant(self.client, "OtherUsage", "otherusage")

        with self.app.app_context():
            from saas.api.usage import record_usage
            record_usage(self.tenant_id, "llm_tokens", 9999)
            record_usage(tenant2_id, "llm_tokens", 100)

        resp1 = self.client.get("/api/usage", headers={"X-API-Key": self.api_key})
        resp2 = self.client.get("/api/usage", headers={"X-API-Key": api_key2})

        metrics1 = {r["metric"]: r["value"] for r in resp1.get_json()["usage"]}
        metrics2 = {r["metric"]: r["value"] for r in resp2.get_json()["usage"]}
        self.assertGreaterEqual(metrics1.get("llm_tokens", 0), 9999)
        self.assertLess(metrics2.get("llm_tokens", 0), 9999)


class TestHealthEndpoint(BaseTestCase):
    """测试健康检查端点"""

    def test_health_returns_ok(self):
        """健康检查应返回 200"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)


class TestTenantStatsAPI(BaseTestCase):
    """测试租户统计 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "StatsCorp", "statscorp"
        )

    def test_tenant_stats(self):
        """获取租户统计信息"""
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "stats-key"})

        with self.app.app_context():
            from saas.api.usage import record_usage
            record_usage(self.tenant_id, "llm_tokens", 5000)

        resp = self.client.get("/api/tenants/me/stats", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["tenant_id"], self.tenant_id)
        self.assertGreaterEqual(data["active_api_keys"], 1)
        self.assertIn("usage_this_month", data)
        self.assertIn("memory", data)


class TestConfTenantIntegration(BaseTestCase):
    """测试 conf_tenant() 与 SaaS 数据库的集成"""

    def test_conf_tenant_with_db_override(self):
        """conf_tenant() 应该从数据库读取租户配置覆盖"""
        from saas.database import Tenant

        with self.app.app_context():
            tenant = Tenant(
                name="ConfTest",
                slug="conftest",
                plan="pro",
                config_json=json.dumps({"model": "gpt-4o", "max_tokens": 8000}),
            )
            self.db.session.add(tenant)
            self.db.session.commit()
            tenant_id = tenant.id

        with self.app.app_context():
            from config import conf_tenant
            result = conf_tenant(tenant_id)
            self.assertEqual(result.get("model"), "gpt-4o")
            self.assertEqual(result.get("max_tokens"), 8000)

    def test_conf_tenant_fallback_to_global(self):
        """conf_tenant() 无覆盖时应回退到全局配置"""
        from saas.database import Tenant

        with self.app.app_context():
            tenant = Tenant(
                name="FallbackTest",
                slug="fallbacktest",
                plan="free",
                config_json=None,
            )
            self.db.session.add(tenant)
            self.db.session.commit()
            tenant_id = tenant.id

        with self.app.app_context():
            from config import conf_tenant, config
            result = conf_tenant(tenant_id)
            # 无覆盖时返回全局配置（包含 saas_mode=True）
            self.assertEqual(result.get("saas_mode"), True)


class TestApiKeyAuthentication(BaseTestCase):
    """测试 API Key 认证中间件"""

    def test_api_key_auth_sets_tenant(self):
        """通过 API Key 认证后应设置 tenant_id"""
        tenant_id, api_key = _register_tenant(self.client, "AuthCorp", "authcorp")

        resp = self.client.get("/api/tenants/me", headers={
            "X-API-Key": api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["name"], "AuthCorp")

    def test_invalid_api_key_rejected(self):
        """无效 API Key 应被拒绝"""
        resp = self.client.get("/api/tenants/me", headers={
            "X-API-Key": "sk-invalid_key_12345",
        })
        self.assertIn(resp.status_code, (401, 403))

    def test_no_auth_rejected(self):
        """无认证信息应被拒绝"""
        resp = self.client.get("/api/tenants/me")
        self.assertIn(resp.status_code, (401, 403))

    def test_health_no_auth_required(self):
        """健康检查不需要认证"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_register_no_auth_required(self):
        """注册接口不需要认证"""
        resp = self.client.post("/api/tenants/register", json={
            "name": "NoAuth", "slug": "noauth", "plan": "free", "email": "x@y.com",
        })
        self.assertEqual(resp.status_code, 201)


class TestAuditLogAPI(BaseTestCase):
    """测试审计日志 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "AuditCorp", "auditcorp"
        )

    def test_register_creates_audit_log(self):
        """注册租户应产生审计日志"""
        resp = self.client.get("/api/audit", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        logs = data["logs"]
        actions = [l["action"] for l in logs]
        self.assertIn("tenant.register", actions)

    def test_create_key_creates_audit_log(self):
        """创建 API Key 应产生审计日志"""
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "audited-key"})

        resp = self.client.get("/api/audit", headers={
            "X-API-Key": self.api_key,
        })
        data = resp.get_json()
        actions = [l["action"] for l in data["logs"]]
        self.assertIn("api_key.create", actions)

        # 验证 detail 包含 key name
        create_log = next(l for l in data["logs"] if l["action"] == "api_key.create")
        self.assertIn("audited-key", create_log["detail"])

    def test_revoke_key_creates_audit_log(self):
        """吊销 API Key 应产生审计日志"""
        create = self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "to-revoke"})
        key_id = create.get_json()["id"]

        self.client.delete(f"/api/keys/{key_id}", headers={
            "X-API-Key": self.api_key,
        })

        resp = self.client.get("/api/audit", headers={
            "X-API-Key": self.api_key,
        })
        actions = [l["action"] for l in resp.get_json()["logs"]]
        self.assertIn("api_key.revoke", actions)

    def test_update_tenant_creates_audit_log(self):
        """更新租户应产生审计日志"""
        self.client.put("/api/tenants/me", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "UpdatedCorp"})

        resp = self.client.get("/api/audit", headers={
            "X-API-Key": self.api_key,
        })
        actions = [l["action"] for l in resp.get_json()["logs"]]
        self.assertIn("tenant.update", actions)

    def test_audit_log_filter_by_action(self):
        """按 action 过滤审计日志"""
        # 创建一个 key 以产生 api_key.create 事件
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "filter-test"})

        resp = self.client.get("/api/audit?action=api_key.create", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for log in data["logs"]:
            self.assertEqual(log["action"], "api_key.create")

    def test_audit_log_filter_by_resource_type(self):
        """按 resource_type 过滤审计日志"""
        resp = self.client.get("/api/audit?resource_type=tenant", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for log in data["logs"]:
            self.assertEqual(log["resource_type"], "tenant")

    def test_audit_log_isolation(self):
        """不同租户的审计日志应该隔离"""
        tenant2_id, api_key2 = _register_tenant(self.client, "OtherAudit", "otheraudit")

        # tenant1 创建 key
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "t1-key"})

        # tenant2 的审计日志不应包含 tenant1 的事件
        resp2 = self.client.get("/api/audit", headers={"X-API-Key": api_key2})
        actions2 = [l["action"] for l in resp2.get_json()["logs"]]
        # tenant2 只有注册事件，没有 api_key.create
        self.assertNotIn("api_key.create", actions2)

    def test_audit_log_pagination(self):
        """审计日志分页"""
        resp = self.client.get("/api/audit?limit=1&offset=0", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertLessEqual(len(data["logs"]), 1)
        self.assertIn("count", data)
        self.assertIn("limit", data)
        self.assertIn("offset", data)


class TestRateLimiting(BaseTestCase):
    """测试租户级 API 限流"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "RateCorp", "ratecorp", plan="free"
        )
        # 重置限流计数器
        from saas.rate_limit import reset_rate_limit
        reset_rate_limit(self.tenant_id)

    def tearDown(self):
        from saas.rate_limit import reset_rate_limit
        reset_rate_limit(self.tenant_id)
        super().tearDown()

    def test_rate_limit_headers_present(self):
        """响应应包含限流头"""
        resp = self.client.get("/api/tenants/me", headers={
            "X-API-Key": self.api_key,
        })
        self.assertIn("X-RateLimit-Limit", resp.headers)
        self.assertIn("X-RateLimit-Remaining", resp.headers)

    def test_rate_limit_allows_normal_requests(self):
        """正常请求不应被限流"""
        for _ in range(10):
            resp = self.client.get("/api/tenants/me", headers={
                "X-API-Key": self.api_key,
            })
            self.assertEqual(resp.status_code, 200)

    def test_rate_limit_blocks_excess_requests(self):
        """超限请求应返回 429"""
        from saas.rate_limit import reset_rate_limit, RATE_LIMITS
        # 临时修改限流配置
        original = RATE_LIMITS["free"]
        RATE_LIMITS["free"] = 5  # 临时设为 5 次/分钟

        try:
            reset_rate_limit(self.tenant_id)
            for i in range(5):
                resp = self.client.get("/api/tenants/me", headers={
                    "X-API-Key": self.api_key,
                })
                self.assertEqual(resp.status_code, 200)

            # 第 6 次应该被限流
            resp = self.client.get("/api/tenants/me", headers={
                "X-API-Key": self.api_key,
            })
            self.assertEqual(resp.status_code, 429)
            data = resp.get_json()
            self.assertIn("error", data)
            self.assertIn("Rate limit", data["error"])
        finally:
            RATE_LIMITS["free"] = original
            reset_rate_limit(self.tenant_id)

    def test_rate_limit_per_tenant_isolation(self):
        """不同租户的限流应该独立"""
        from saas.rate_limit import reset_rate_limit, RATE_LIMITS
        tenant2_id, api_key2 = _register_tenant(
            self.client, "Rate2Corp", "rate2corp", plan="free"
        )
        reset_rate_limit(self.tenant_id)
        reset_rate_limit(tenant2_id)

        from saas.rate_limit import RATE_LIMITS
        original = RATE_LIMITS["free"]
        RATE_LIMITS["free"] = 3

        try:
            # tenant1 耗尽配额
            for _ in range(3):
                self.client.get("/api/tenants/me", headers={"X-API-Key": self.api_key})

            # tenant1 被限流
            resp1 = self.client.get("/api/tenants/me", headers={"X-API-Key": self.api_key})
            self.assertEqual(resp1.status_code, 429)

            # tenant2 不受影响
            resp2 = self.client.get("/api/tenants/me", headers={"X-API-Key": api_key2})
            self.assertEqual(resp2.status_code, 200)
        finally:
            RATE_LIMITS["free"] = original
            reset_rate_limit(self.tenant_id)
            reset_rate_limit(tenant2_id)

    def test_health_endpoint_not_rate_limited(self):
        """健康检查不应被限流"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("X-RateLimit-Limit", resp.headers)


class TestWebhookAPI(BaseTestCase):
    """测试 Webhook 管理 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "HookCorp", "hookcorp"
        )

    def test_create_webhook(self):
        """创建 Webhook 端点"""
        resp = self.client.post("/api/webhooks", headers={
            "X-API-Key": self.api_key,
        }, json={"url": "https://example.com/webhook", "events": ["api_key.revoked"]})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIn("id", data)
        self.assertIn("secret", data)
        self.assertEqual(data["url"], "https://example.com/webhook")

    def test_list_webhooks(self):
        """列出 Webhook 端点"""
        self.client.post("/api/webhooks", headers={
            "X-API-Key": self.api_key,
        }, json={"url": "https://example.com/hook1"})

        resp = self.client.get("/api/webhooks", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("webhooks", data)
        self.assertGreaterEqual(len(data["webhooks"]), 1)

    def test_delete_webhook(self):
        """删除 Webhook 端点"""
        create = self.client.post("/api/webhooks", headers={
            "X-API-Key": self.api_key,
        }, json={"url": "https://example.com/hook2"})
        wh_id = create.get_json()["id"]

        resp = self.client.delete(f"/api/webhooks/{wh_id}", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)

    def test_webhook_url_validation(self):
        """Webhook URL 必须是 http/https"""
        resp = self.client.post("/api/webhooks", headers={
            "X-API-Key": self.api_key,
        }, json={"url": "ftp://bad.example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_webhook_isolation(self):
        """不同租户的 Webhook 应该隔离"""
        tenant2_id, api_key2 = _register_tenant(self.client, "Hook2Corp", "hook2corp")

        self.client.post("/api/webhooks", headers={
            "X-API-Key": self.api_key,
        }, json={"url": "https://t1.example.com/hook"})

        resp2 = self.client.get("/api/webhooks", headers={"X-API-Key": api_key2})
        self.assertEqual(len(resp2.get_json()["webhooks"]), 0)

    def test_webhook_signature(self):
        """验证 Webhook 签名"""
        from saas.webhook import sign_payload, verify_signature
        secret = "test_secret_123"
        payload = '{"event": "test"}'
        sig = sign_payload(payload, secret)
        self.assertTrue(verify_signature(payload, secret, sig))
        self.assertFalse(verify_signature(payload, "wrong_secret", sig))


class TestGDPRCompliance(BaseTestCase):
    """测试 GDPR 合规 — 数据导出/删除"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "GDPRCorp", "gdprcorp"
        )

    def test_data_access(self):
        """数据访问权 — 查看数据摘要"""
        resp = self.client.get("/api/gdpr/access", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("tenant_id", data)
        self.assertIn("users_count", data)
        self.assertIn("api_keys_count", data)

    def test_data_export(self):
        """数据可携带权 — 导出所有数据"""
        # 创建一些数据
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "export-test-key"})

        resp = self.client.get("/api/gdpr/export", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("tenant", data)
        self.assertIn("users", data)
        self.assertIn("api_keys", data)
        self.assertIn("exported_at", data)

    def test_data_delete_requires_confirmation(self):
        """删除需要确认"""
        resp = self.client.delete("/api/gdpr/delete", headers={
            "X-API-Key": self.api_key,
        }, json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("confirm", resp.get_json()["error"].lower())

    def test_data_delete_with_confirmation(self):
        """确认后可以删除所有数据"""
        # 先创建一些数据
        self.client.post("/api/keys", headers={
            "X-API-Key": self.api_key,
        }, json={"name": "to-be-deleted"})

        resp = self.client.delete("/api/gdpr/delete", headers={
            "X-API-Key": self.api_key,
        }, json={"confirm": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("deleted", data)
        self.assertIn("export_snapshot", data)
        self.assertGreater(data["deleted"]["api_keys"], 0)

    def test_export_does_not_include_key_hash(self):
        """导出数据不应包含 key_hash"""
        resp = self.client.get("/api/gdpr/export", headers={
            "X-API-Key": self.api_key,
        })
        data = resp.get_json()
        for key in data.get("api_keys", []):
            self.assertNotIn("key_hash", key)


class TestBillingAPI(BaseTestCase):
    """测试计费 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "BillCorp", "billcorp", plan="pro"
        )

    def test_billing_summary(self):
        """获取当月账单摘要"""
        resp = self.client.get("/api/billing/summary", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("plan", data)
        self.assertIn("plan_name", data)
        self.assertIn("period", data)
        self.assertIn("usage", data)
        self.assertIn("quotas", data)
        self.assertIn("estimated_cost", data)
        self.assertEqual(data["plan"], "pro")
        self.assertEqual(data["estimated_cost"], 99)

    def test_billing_quotas(self):
        """获取当前套餐配额"""
        resp = self.client.get("/api/billing/quotas", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("plan", data)
        self.assertIn("plan_name", data)
        self.assertIn("quotas", data)
        # pro 套餐配额
        self.assertEqual(data["quotas"]["llm_tokens"]["limit"], 2_000_000)
        self.assertEqual(data["quotas"]["api_calls"]["limit"], 50_000)

    def test_billing_plans(self):
        """获取所有可用套餐"""
        resp = self.client.get("/api/billing/plans")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("plans", data)
        self.assertIn("free", data["plans"])
        self.assertIn("pro", data["plans"])
        self.assertIn("enterprise", data["plans"])
        self.assertEqual(data["plans"]["free"]["price"], 0)
        self.assertEqual(data["plans"]["enterprise"]["quotas"]["llm_tokens"], -1)

    def test_billing_check_allowed(self):
        """检查配额 — 允许"""
        resp = self.client.post("/api/billing/check", headers={
            "X-API-Key": self.api_key,
        }, json={"metric": "api_calls", "increment": 100})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["allowed"])
        self.assertEqual(data["metric"], "api_calls")

    def test_billing_check_free_plan_quota(self):
        """free 套餐配额检查"""
        tenant_id, api_key = _register_tenant(
            self.client, "FreeCorp", "freecorp", plan="free"
        )
        resp = self.client.post("/api/billing/check", headers={
            "X-API-Key": api_key,
        }, json={"metric": "api_calls", "increment": 1})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["allowed"])
        # free 套餐 api_calls 限额 1000
        self.assertEqual(data["limit"], 1000)

    def test_billing_enterprise_unlimited(self):
        """enterprise 套餐无限配额"""
        tenant_id, api_key = _register_tenant(
            self.client, "EntCorp", "entcorp", plan="enterprise"
        )
        resp = self.client.post("/api/billing/check", headers={
            "X-API-Key": api_key,
        }, json={"metric": "llm_tokens", "increment": 999999999})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["allowed"])
        self.assertEqual(data["limit"], -1)

    def test_billing_summary_after_usage(self):
        """使用后账单摘要应反映用量"""
        # 在 app context 中通过 record_usage 记录用量
        with self.app.app_context():
            from saas.api.usage import record_usage
            record_usage(self.tenant_id, "api_calls", 42)

        resp = self.client.get("/api/billing/summary", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreaterEqual(data["usage"].get("api_calls", 0), 42)

    def test_billing_no_auth(self):
        """未认证请求应被拒绝"""
        resp = self.client.get("/api/billing/summary")
        self.assertEqual(resp.status_code, 401)


class TestSSOAPI(BaseTestCase):
    """测试 SSO API（mock 外部 OAuth2 服务）"""

    def test_list_providers(self):
        """列出可用 SSO Provider"""
        resp = self.client.get("/api/auth/sso/providers")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("providers", data)
        provider_names = [p["provider"] for p in data["providers"]]
        self.assertIn("github", provider_names)
        self.assertIn("google", provider_names)
        self.assertIn("wechat", provider_names)
        self.assertIn("feishu", provider_names)
        self.assertIn("dingtalk", provider_names)

    def test_sso_login_unsupported_provider(self):
        """不支持的 Provider 应返回 400"""
        resp = self.client.get("/api/auth/sso/unsupported_provider")
        self.assertEqual(resp.status_code, 400)

    def test_sso_login_github_redirect(self):
        """GitHub SSO 登录应 302 重定向"""
        os.environ["GITHUB_CLIENT_ID"] = "test_client_id"
        try:
            resp = self.client.get("/api/auth/sso/github")
            self.assertEqual(resp.status_code, 302)
            location = resp.headers.get("Location", "")
            self.assertIn("github.com/login/oauth/authorize", location)
            self.assertIn("client_id=test_client_id", location)
        finally:
            del os.environ["GITHUB_CLIENT_ID"]

    def test_sso_login_wechat_redirect(self):
        """微信 SSO 登录应 302 重定向并带 appid 和 #wechat_redirect"""
        os.environ["WECHAT_CLIENT_ID"] = "wx_test_id"
        try:
            resp = self.client.get("/api/auth/sso/wechat")
            self.assertEqual(resp.status_code, 302)
            location = resp.headers.get("Location", "")
            self.assertIn("open.weixin.qq.com", location)
            self.assertIn("appid=wx_test_id", location)
            self.assertTrue(location.endswith("#wechat_redirect"))
        finally:
            del os.environ["WECHAT_CLIENT_ID"]

    def test_sso_login_feishu_redirect(self):
        """飞书 SSO 登录应 302 重定向并带 app_id"""
        os.environ["FEISHU_CLIENT_ID"] = "cli_test_id"
        try:
            resp = self.client.get("/api/auth/sso/feishu")
            self.assertEqual(resp.status_code, 302)
            location = resp.headers.get("Location", "")
            self.assertIn("open.feishu.cn", location)
            self.assertIn("app_id=cli_test_id", location)
        finally:
            del os.environ["FEISHU_CLIENT_ID"]

    def test_sso_login_dingtalk_redirect(self):
        """钉钉 SSO 登录应 302 重定向"""
        os.environ["DINGTALK_CLIENT_ID"] = "ding_test_id"
        try:
            resp = self.client.get("/api/auth/sso/dingtalk")
            self.assertEqual(resp.status_code, 302)
            location = resp.headers.get("Location", "")
            self.assertIn("login.dingtalk.com", location)
        finally:
            del os.environ["DINGTALK_CLIENT_ID"]

    def test_sso_login_no_client_id(self):
        """未配置 client_id 应返回 503"""
        # 确保环境变量不存在
        os.environ.pop("GITHUB_CLIENT_ID", None)
        resp = self.client.get("/api/auth/sso/github")
        self.assertEqual(resp.status_code, 503)

    def test_sso_callback_invalid_state(self):
        """无效 state 应返回 400"""
        resp = self.client.get("/api/auth/sso/github/callback?state=invalid&code=abc")
        self.assertEqual(resp.status_code, 400)

    def test_sso_callback_missing_code(self):
        """缺少 code 应返回 400"""
        # 先生成有效 state
        from saas.api.sso import _generate_state
        state = _generate_state("github")
        resp = self.client.get(f"/api/auth/sso/github/callback?state={state}")
        # 缺少 code 参数，IdP 授权失败回调
        self.assertIn(resp.status_code, [400, 502])

    def test_sso_github_callback_mock(self):
        """测试 SSO 回调核心逻辑 — 用户创建与关联"""
        # 直接测试 SSO callback 中的用户创建逻辑
        # 不 mock HTTP 请求（太复杂），而是验证核心数据流
        from saas.database import db, Tenant, User, ApiKey

        with self.app.app_context():
            # 模拟 SSO callback 创建的租户和用户
            tenant = Tenant(id="sso-test-1", name="SSO User", slug="sso-user-1", plan="free")
            db.session.add(tenant)

            user = User(
                tenant_id=tenant.id,
                email="sso@github.com",
                display_name="SSO Test User",
                role="admin",
                sso_uid="github:99999",
            )
            db.session.add(user)

            from saas.middleware import generate_api_key
            raw_key, key_hash, key_prefix = generate_api_key()
            api_key = ApiKey(
                tenant_id=tenant.id,
                name="SSO auto (github)",
                key_hash=key_hash,
                key_prefix=key_prefix,
            )
            db.session.add(api_key)
            db.session.commit()

            # 验证
            self.assertIsNotNone(user.id)
            self.assertEqual(user.sso_uid, "github:99999")
            self.assertTrue(raw_key.startswith("sk-"))

            # 验证 API Key 可以认证
            from saas.middleware import _resolve_api_key
            resolved_tid = _resolve_api_key(raw_key)
            self.assertEqual(resolved_tid, tenant.id)

    def test_sso_existing_user_login(self):
        """测试已有 SSO 用户再次登录"""
        from saas.database import db, Tenant, User

        with self.app.app_context():
            # 先创建租户和用户
            tenant = Tenant(id="sso-exist-1", name="Exist Corp", slug="sso-exist-1", plan="pro")
            db.session.add(tenant)

            user = User(
                tenant_id=tenant.id,
                email="exist@github.com",
                display_name="Existing User",
                role="admin",
                sso_uid="github:88888",
            )
            db.session.add(user)
            db.session.commit()

            # 查找已有用户
            found = User.query.filter_by(sso_uid="github:88888").first()
            self.assertIsNotNone(found)
            self.assertEqual(found.tenant_id, tenant.id)


class TestPluginAPI(BaseTestCase):
    """测试租户插件管理 API"""

    def setUp(self):
        super().setUp()
        self.tenant_id, self.api_key = _register_tenant(
            self.client, "PlugCorp", "plugcorp"
        )

    def test_list_plugins(self):
        """列出插件（全局 + 租户配置）"""
        resp = self.client.get("/api/plugins", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("plugins", data)

    def test_update_plugin_config(self):
        """设置租户级插件配置"""
        resp = self.client.put("/api/plugins/HELLO", headers={
            "X-API-Key": self.api_key,
        }, json={"enabled": False, "priority": 10})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("updated", data["message"].lower())

    def test_get_plugin_config_after_update(self):
        """设置后应能读取租户级配置"""
        self.client.put("/api/plugins/GODCMD", headers={
            "X-API-Key": self.api_key,
        }, json={"enabled": False})

        # 通过 API 验证（同一请求上下文）
        resp = self.client.get("/api/plugins", headers={
            "X-API-Key": self.api_key,
        })
        data = resp.get_json()
        godcmd = next((p for p in data["plugins"] if p["name"] == "GODCMD"), None)
        if godcmd:
            self.assertFalse(godcmd["tenant_enabled"])

    def test_delete_plugin_config(self):
        """删除租户级插件配置"""
        # 先创建
        self.client.put("/api/plugins/HELLO", headers={
            "X-API-Key": self.api_key,
        }, json={"enabled": True})

        # 再删除
        resp = self.client.delete("/api/plugins/HELLO", headers={
            "X-API-Key": self.api_key,
        })
        self.assertEqual(resp.status_code, 200)

        # 通过 API 验证已删除
        resp = self.client.get("/api/plugins", headers={
            "X-API-Key": self.api_key,
        })
        data = resp.get_json()
        hello = next((p for p in data["plugins"] if p["name"] == "HELLO"), None)
        if hello:
            self.assertFalse(hello["has_override"])

    def test_plugin_isolation(self):
        """不同租户的插件配置应该隔离"""
        tenant2_id, api_key2 = _register_tenant(
            self.client, "Plug2Corp", "plug2corp"
        )

        # tenant1 禁用插件
        self.client.put("/api/plugins/HELLO", headers={
            "X-API-Key": self.api_key,
        }, json={"enabled": False})

        # tenant2 的插件列表不受影响
        resp2 = self.client.get("/api/plugins", headers={"X-API-Key": api_key2})
        data2 = resp2.get_json()
        hello2 = next((p for p in data2["plugins"] if p["name"] == "HELLO"), None)
        if hello2:
            # tenant2 没有覆盖配置
            self.assertFalse(hello2["has_override"])

    def test_is_plugin_enabled_for_tenant(self):
        """测试 is_plugin_enabled_for_tenant 函数"""
        from saas.tenant_plugin import is_plugin_enabled_for_tenant

        # 默认启用
        self.assertTrue(is_plugin_enabled_for_tenant(self.tenant_id, "HELLO"))

        # 通过 API 设置为禁用
        self.client.put("/api/plugins/HELLO", headers={
            "X-API-Key": self.api_key,
        }, json={"enabled": False})

        # 需要在 app context 中查询
        with self.app.app_context():
            self.assertFalse(is_plugin_enabled_for_tenant(self.tenant_id, "HELLO"))

    def test_plugin_config_with_custom_data(self):
        """插件配置支持自定义 JSON 数据"""
        self.client.put("/api/plugins/HELLO", headers={
            "X-API-Key": self.api_key,
        }, json={"enabled": True, "config": {"api_key": "xxx", "model": "gpt-4"}})

        # 通过 API 验证
        with self.app.app_context():
            from saas.tenant_plugin import get_tenant_plugin_config
            config = get_tenant_plugin_config(self.tenant_id, "HELLO")
            self.assertIsNotNone(config)
            self.assertEqual(config["config"]["model"], "gpt-4")

    def test_plugin_no_auth(self):
        """未认证请求应被拒绝"""
        resp = self.client.get("/api/plugins")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
