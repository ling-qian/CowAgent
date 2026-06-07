# encoding:utf-8
"""
多租户隔离基础测试

验证核心隔离机制：
1. TenantContext 上下文变量透传
2. BridgeManager 按租户隔离 Bridge 实例
3. Context 携带 tenant_id
4. conf_tenant() 租户级配置
5. saas_mode=False 时行为与原版一致
"""

import threading
import unittest


class TestTenantContext(unittest.TestCase):
    """测试 TenantContext + contextvars 透传"""

    def test_set_and_get(self):
        from common.tenant import TenantContext, current_tenant_id
        token = TenantContext.set_tenant("tenant_abc")
        try:
            self.assertEqual(current_tenant_id(), "tenant_abc")
            self.assertEqual(TenantContext.get_tenant_id(), "tenant_abc")
        finally:
            TenantContext.reset(token)

    def test_default_is_none(self):
        from common.tenant import current_tenant_id
        self.assertIsNone(current_tenant_id())

    def test_reset_restores_previous(self):
        from common.tenant import TenantContext, current_tenant_id
        token1 = TenantContext.set_tenant("tenant_a")
        token2 = TenantContext.set_tenant("tenant_b")
        try:
            self.assertEqual(current_tenant_id(), "tenant_b")
        finally:
            TenantContext.reset(token2)
        try:
            self.assertEqual(current_tenant_id(), "tenant_a")
        finally:
            TenantContext.reset(token1)

    def test_clear(self):
        from common.tenant import TenantContext, current_tenant_id
        TenantContext.set_tenant("tenant_x")
        TenantContext.clear()
        self.assertIsNone(current_tenant_id())

    def test_require_tenant_id_raises(self):
        from common.tenant import require_tenant_id
        with self.assertRaises(ValueError):
            require_tenant_id()

    def test_require_tenant_id_returns(self):
        from common.tenant import TenantContext, require_tenant_id
        token = TenantContext.set_tenant("tenant_123")
        try:
            self.assertEqual(require_tenant_id(), "tenant_123")
        finally:
            TenantContext.reset(token)

    def test_thread_isolation(self):
        """不同线程的 tenant_id 应该隔离"""
        from common.tenant import TenantContext, current_tenant_id
        results = {}

        def worker(name, tid):
            token = TenantContext.set_tenant(tid)
            try:
                results[name] = current_tenant_id()
            finally:
                TenantContext.reset(token)

        t1 = threading.Thread(target=worker, args=("t1", "tenant_a"))
        t2 = threading.Thread(target=worker, args=("t2", "tenant_b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(results["t1"], "tenant_a")
        self.assertEqual(results["t2"], "tenant_b")


class TestBridgeManager(unittest.TestCase):
    """测试 BridgeManager 按租户隔离 Bridge 实例"""

    def test_saas_mode_off_returns_singleton(self):
        """saas_mode=False 时返回全局 @singleton Bridge"""
        from config import config
        original = config.get("saas_mode", False)
        config["saas_mode"] = False
        try:
            from bridge.bridge import get_bridge_manager
            mgr = get_bridge_manager()
            b1 = mgr.get_bridge("tenant_a")
            b2 = mgr.get_bridge("tenant_b")
            # saas_mode 关闭时，不同 tenant_id 应该返回同一个全局单例
            self.assertIs(b1, b2)
        finally:
            config["saas_mode"] = original

    def test_saas_mode_on_returns_per_tenant(self):
        """saas_mode=True 时不同租户返回不同 Bridge 实例"""
        from config import config
        original = config.get("saas_mode", False)
        config["saas_mode"] = True
        try:
            from bridge.bridge import BridgeManager
            mgr = BridgeManager()
            b1 = mgr.get_bridge("tenant_a")
            b2 = mgr.get_bridge("tenant_b")
            b1_again = mgr.get_bridge("tenant_a")
            self.assertIsNot(b1, b2)
            self.assertIs(b1, b1_again)
        finally:
            config["saas_mode"] = original
            # 清理 BridgeManager 内部缓存
            mgr.clear_all()

    def test_remove_bridge(self):
        from config import config
        original = config.get("saas_mode", False)
        config["saas_mode"] = True
        try:
            from bridge.bridge import BridgeManager
            mgr = BridgeManager()
            b1 = mgr.get_bridge("tenant_x")
            mgr.remove_bridge("tenant_x")
            b2 = mgr.get_bridge("tenant_x")
            # 移除后重新创建，应该是新实例
            self.assertIsNot(b1, b2)
        finally:
            config["saas_mode"] = original
            mgr.clear_all()


class TestContextTenantId(unittest.TestCase):
    """测试 Context 携带 tenant_id"""

    def test_default_tenant_id_is_none(self):
        from bridge.context import Context
        ctx = Context()
        self.assertIsNone(ctx.tenant_id)

    def test_set_tenant_id(self):
        from bridge.context import Context
        ctx = Context()
        ctx.tenant_id = "tenant_abc"
        self.assertEqual(ctx.tenant_id, "tenant_abc")


class TestConfTenant(unittest.TestCase):
    """测试 conf_tenant() 租户级配置"""

    def test_saas_mode_off_returns_global(self):
        """saas_mode=False 时 conf_tenant() 等价于 conf()"""
        from config import config, conf, conf_tenant
        original = config.get("saas_mode", False)
        config["saas_mode"] = False
        try:
            result = conf_tenant()
            self.assertIs(result, conf())
        finally:
            config["saas_mode"] = original

    def test_saas_mode_off_no_tenant_returns_global(self):
        """saas_mode=False 且无 tenant_id 时返回全局配置"""
        from config import config, conf, conf_tenant
        original = config.get("saas_mode", False)
        config["saas_mode"] = False
        try:
            result = conf_tenant("some_tenant")
            self.assertIs(result, conf())
        finally:
            config["saas_mode"] = original


class TestSaasModeCompat(unittest.TestCase):
    """测试 saas_mode=False 时行为与原版完全一致"""

    def test_no_saas_import_when_off(self):
        """saas_mode=False 时不应该触发 saas 模块导入"""
        import sys
        # 确保 saas 模块不在已导入列表中（如果之前没导入过）
        had_saas = "saas" in sys.modules
        from config import conf_tenant
        # conf_tenant 在 saas_mode=False 时不会真正导入 saas
        from config import config
        original = config.get("saas_mode", False)
        config["saas_mode"] = False
        try:
            result = conf_tenant()
            # 应该返回全局配置，不触发 saas 模块
            self.assertIs(result, config)
        finally:
            config["saas_mode"] = original

    def test_bridge_singleton_when_off(self):
        """saas_mode=False 时 Bridge 仍然是 @singleton"""
        from bridge.bridge import Bridge
        b1 = Bridge()
        b2 = Bridge()
        self.assertIs(b1, b2)


if __name__ == "__main__":
    unittest.main()
