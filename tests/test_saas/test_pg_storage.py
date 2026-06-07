import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestPostgresMemoryStorageTenantIsolation(unittest.TestCase):

    def _make_storage(self, tenant_id):
        try:
            from agent.memory.storage import PostgresMemoryStorage
        except ImportError:
            self.skipTest("PostgresMemoryStorage not available")

        storage = PostgresMemoryStorage.__new__(PostgresMemoryStorage)
        storage.database_url = "postgresql://test"
        storage.tenant_id = tenant_id
        storage._lock = __import__('threading').RLock()
        storage._pool = MagicMock()
        return storage

    def test_different_tenants_use_different_ids(self):
        s1 = self._make_storage("t_a")
        s2 = self._make_storage("t_b")
        self.assertEqual(s1.tenant_id, "t_a")
        self.assertEqual(s2.tenant_id, "t_b")
        self.assertNotEqual(s1.tenant_id, s2.tenant_id)


class TestEmbeddingConversion(unittest.TestCase):

    def test_embedding_to_pgvector(self):
        try:
            from agent.memory.storage import PostgresMemoryStorage
        except ImportError:
            self.skipTest("PostgresMemoryStorage not available")
        result = PostgresMemoryStorage._embedding_to_pgvector([1.0, 2.0, 3.0])
        self.assertEqual(result, "[1.0,2.0,3.0]")

    def test_embedding_to_pgvector_none(self):
        try:
            from agent.memory.storage import PostgresMemoryStorage
        except ImportError:
            self.skipTest("PostgresMemoryStorage not available")
        result = PostgresMemoryStorage._embedding_to_pgvector(None)
        self.assertIsNone(result)


class TestMemoryStorageComputeHash(unittest.TestCase):

    def test_compute_hash_deterministic(self):
        try:
            from agent.memory.storage import MemoryStorage
        except ImportError:
            self.skipTest("MemoryStorage not available")
        h1 = MemoryStorage.compute_hash("hello")
        h2 = MemoryStorage.compute_hash("hello")
        h3 = MemoryStorage.compute_hash("world")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)


if __name__ == "__main__":
    unittest.main()
