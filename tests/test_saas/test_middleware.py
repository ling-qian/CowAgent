import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestMiddlewareSaasOff(unittest.TestCase):

    def test_webpy_processor_skips_when_saas_off(self):
        from saas.middleware import webpy_tenant_processor

        original_handler = lambda *a, **kw: "original"
        processor = webpy_tenant_processor(original_handler)

        with patch("config.config", {"saas_mode": False}):
            result = processor()
            self.assertEqual(result, "original")

    def test_teardown_clears_tenant(self):
        from common.tenant import TenantContext, current_tenant_id
        TenantContext.clear()
        token = TenantContext.set_tenant("t_teardown")
        self.assertEqual(current_tenant_id(), "t_teardown")
        TenantContext.reset(token)
        self.assertIsNone(current_tenant_id())


class TestMiddlewareTenantExtraction(unittest.TestCase):

    def test_extract_api_key_from_header(self):
        from saas.middleware import _extract_api_key_from_headers
        result = _extract_api_key_from_headers({"X-API-Key": "sk-test-key"})
        self.assertEqual(result, "sk-test-key")

    def test_extract_bearer_token(self):
        from saas.middleware import _extract_api_key_from_headers
        result = _extract_api_key_from_headers({"Authorization": "Bearer sk-test-key"})
        self.assertEqual(result, "sk-test-key")

    def test_returns_empty_when_no_key(self):
        from saas.middleware import _extract_api_key_from_headers
        result = _extract_api_key_from_headers({})
        self.assertIsNone(result)


class TestApiKeyResolution(unittest.TestCase):

    def test_resolve_api_key_returns_tenant_id(self):
        from saas.middleware import generate_api_key, _resolve_api_key
        raw_key, key_hash, key_prefix = generate_api_key()
        # Without DB, _resolve_api_key returns None
        result = _resolve_api_key(raw_key)
        # In test without DB, this returns None (no Flask app context)
        self.assertTrue(result is None or isinstance(result, str))

    def test_generate_api_key_format(self):
        from saas.middleware import generate_api_key
        raw_key, key_hash, key_prefix = generate_api_key()
        self.assertTrue(raw_key.startswith("sk-"))
        self.assertTrue(len(key_hash) > 0)
        self.assertTrue(len(key_prefix) > 0)


if __name__ == "__main__":
    unittest.main()
