import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestTenantContextVar(unittest.TestCase):

    def test_default_is_none(self):
        from common.tenant import current_tenant_id, TenantContext
        TenantContext.clear()
        self.assertIsNone(current_tenant_id())

    def test_set_and_get(self):
        from common.tenant import TenantContext, current_tenant_id
        TenantContext.clear()
        token = TenantContext.set_tenant("t1")
        try:
            self.assertEqual(current_tenant_id(), "t1")
        finally:
            TenantContext.reset(token)

    def test_clear(self):
        from common.tenant import TenantContext, current_tenant_id
        token = TenantContext.set_tenant("t1")
        TenantContext.reset(token)
        TenantContext.clear()
        self.assertIsNone(current_tenant_id())

    def test_reset_restores_previous(self):
        from common.tenant import TenantContext, current_tenant_id
        token1 = TenantContext.set_tenant("outer")
        token2 = TenantContext.set_tenant("inner")
        try:
            self.assertEqual(current_tenant_id(), "inner")
        finally:
            TenantContext.reset(token2)
        try:
            self.assertEqual(current_tenant_id(), "outer")
        finally:
            TenantContext.reset(token1)

    def test_require_tenant_id_raises(self):
        from common.tenant import require_tenant_id, TenantContext
        TenantContext.clear()
        with self.assertRaises(ValueError):
            require_tenant_id()

    def test_require_tenant_id_returns(self):
        from common.tenant import require_tenant_id, TenantContext
        token = TenantContext.set_tenant("t_required")
        try:
            self.assertEqual(require_tenant_id(), "t_required")
        finally:
            TenantContext.reset(token)


class TestConfTenantSaasOff(unittest.TestCase):

    def test_conf_tenant_returns_global_when_saas_off(self):
        from config import conf, conf_tenant
        with patch("config.config", {"saas_mode": False}):
            global_conf = conf()
            result = conf_tenant()
            self.assertIs(result, global_conf)

    def test_conf_tenant_with_tenant_id_when_saas_off(self):
        from config import conf, conf_tenant
        with patch("config.config", {"saas_mode": False}):
            global_conf = conf()
            result = conf_tenant(tenant_id="any-id")
            self.assertIs(result, global_conf)


class TestContextTenantId(unittest.TestCase):

    def test_context_no_tenant_id_by_default(self):
        from bridge.context import Context
        from common.tenant import TenantContext
        TenantContext.clear()
        ctx = Context()
        self.assertIsNone(ctx.tenant_id)

    def test_context_explicit_tenant_id(self):
        from bridge.context import Context
        ctx = Context()
        ctx.tenant_id = "explicit"
        self.assertEqual(ctx.tenant_id, "explicit")


class TestBridgeSaasOff(unittest.TestCase):

    def test_bridge_singleton_still_works(self):
        from bridge.bridge import Bridge
        b1 = Bridge()
        b2 = Bridge()
        self.assertIs(b1, b2)


if __name__ == "__main__":
    unittest.main()
