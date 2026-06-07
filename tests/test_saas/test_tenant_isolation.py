import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestTenantContextVarIsolation(unittest.TestCase):

    def test_contextvar_isolation_between_threads(self):
        import threading
        from common.tenant import TenantContext, current_tenant_id

        TenantContext.clear()
        results = {}

        def thread_fn(tid):
            token = TenantContext.set_tenant(tid)
            try:
                import time
                time.sleep(0.05)
                results[tid] = current_tenant_id()
            finally:
                TenantContext.reset(token)

        t1 = threading.Thread(target=thread_fn, args=("t1",))
        t2 = threading.Thread(target=thread_fn, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results["t1"], "t1")
        self.assertEqual(results["t2"], "t2")
        TenantContext.clear()


class TestConfTenantIntegration(unittest.TestCase):

    def test_conf_tenant_falls_back_to_global(self):
        from saas.config_loader import conf_tenant

        with patch("config.config", {"saas_mode": True, "model": "default-model", "temperature": 0.9}):
            with patch("config.conf") as mock_conf:
                mock_conf.return_value = {"model": "default-model", "temperature": 0.9}
                result = conf_tenant("t_fallback")
                self.assertEqual(result.get("model"), "default-model")
                self.assertEqual(result.get("temperature"), 0.9)

    def test_conf_tenant_with_db_override(self):
        from saas.config_loader import conf_tenant

        with patch("config.config", {"saas_mode": True, "model": "default-model", "temperature": 0.9, "top_p": 1.0}):
            with patch("saas.config_loader._load_tenant_overrides") as mock_load:
                mock_load.return_value = {"model": "custom-model", "temperature": 0.5}
                result = conf_tenant("t_merge")
                self.assertEqual(result.get("model"), "custom-model")
                self.assertEqual(result.get("temperature"), 0.5)
                self.assertEqual(result.get("top_p"), 1.0)

    def test_invalidate_tenant_config(self):
        from saas.config_loader import invalidate_tenant_config

        invalidate_tenant_config("some_tenant")
        invalidate_tenant_config(None)


class TestBridgeManagerMultiTenant(unittest.TestCase):

    def setUp(self):
        from bridge.bridge import BridgeManager
        self.mgr = BridgeManager()
        self.mgr.clear_all()

    def test_different_tenants_get_different_bridges(self):
        from bridge.bridge import BridgeManager

        with patch("bridge.bridge.conf") as mock_conf:
            mock_conf.return_value = {"saas_mode": True}
            mgr = BridgeManager()
            b1 = mgr.get_bridge("tenant_a")
            b2 = mgr.get_bridge("tenant_b")
            self.assertIsNot(b1, b2)

    def test_same_tenant_gets_same_bridge(self):
        from bridge.bridge import BridgeManager

        with patch("bridge.bridge.conf") as mock_conf:
            mock_conf.return_value = {"saas_mode": True}
            mgr = BridgeManager()
            b1 = mgr.get_bridge("tenant_x")
            b2 = mgr.get_bridge("tenant_x")
            self.assertIs(b1, b2)

    def test_remove_bridge(self):
        from bridge.bridge import BridgeManager

        with patch("bridge.bridge.conf") as mock_conf:
            mock_conf.return_value = {"saas_mode": True}
            mgr = BridgeManager()
            b1 = mgr.get_bridge("tenant_rm")
            mgr.remove_bridge("tenant_rm")
            b2 = mgr.get_bridge("tenant_rm")
            self.assertIsNot(b1, b2)


class TestBridgeTenantConf(unittest.TestCase):

    def test_bridge_uses_tenant_conf(self):
        from bridge.bridge import Bridge

        with patch("config.conf_tenant") as mock_ct:
            mock_ct.return_value = {
                "model": "tenant-model",
                "bot_type": "openai",
                "open_ai_api_key": "key",
                "open_ai_api_base": "",
                "voice_to_text": "",
                "text_to_voice": "google",
                "translate": "baidu",
                "saas_mode": True,
            }
            bridge = Bridge()
            self.assertEqual(bridge.btype["chat"], "openai")

    def test_bridge_reset_preserves_config(self):
        from bridge.bridge import Bridge

        with patch("config.conf_tenant") as mock_ct:
            mock_ct.return_value = {
                "model": "gpt-4",
                "bot_type": "openai",
                "open_ai_api_key": "key",
                "open_ai_api_base": "",
                "voice_to_text": "",
                "text_to_voice": "google",
                "translate": "baidu",
                "saas_mode": True,
            }
            bridge = Bridge()
            bridge.reset_bot()
            self.assertEqual(bridge.btype["chat"], "openai")


if __name__ == "__main__":
    unittest.main()
