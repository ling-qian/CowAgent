# encoding:utf-8
"""
Redis 路由缓存

缓存两类数据：
1. API Key → tenant_id 映射（避免每次请求都查数据库）
2. 租户配置（conf_tenant 缓存，避免每次请求都查 tenants.config_json）

当 Redis 不可用时，自动降级为内存缓存（dict），不影响服务运行。

配置：
    REDIS_URL=redis://localhost:6379/0
    如果未设置，使用内存缓存。
"""

import json
import os
import threading
import time
from typing import Optional

from common.log import logger

# ---------------------------------------------------------------------------
# 缓存后端抽象
# ---------------------------------------------------------------------------

class CacheBackend:
    """缓存后端接口"""

    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def set(self, key: str, value: str, ttl: int = 300):
        raise NotImplementedError

    def delete(self, key: str):
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class MemoryCacheBackend(CacheBackend):
    """内存缓存后端（默认，无需 Redis）"""

    def __init__(self):
        self._store: dict[str, tuple[str, float]] = {}  # key -> (value, expire_at)
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if expire_at and time.time() > expire_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: str, ttl: int = 300):
        with self._lock:
            self._store[key] = (value, time.time() + ttl if ttl > 0 else 0)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None


class RedisCacheBackend(CacheBackend):
    """Redis 缓存后端"""

    def __init__(self, redis_url: str):
        import redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        # 测试连接
        self._client.ping()
        logger.info(f"[RedisCache] Connected to {redis_url.split('@')[-1]}")

    def get(self, key: str) -> Optional[str]:
        try:
            return self._client.get(key)
        except Exception as e:
            logger.warning(f"[RedisCache] GET failed: {e}")
            return None

    def set(self, key: str, value: str, ttl: int = 300):
        try:
            self._client.setex(key, ttl, value)
        except Exception as e:
            logger.warning(f"[RedisCache] SET failed: {e}")

    def delete(self, key: str):
        try:
            self._client.delete(key)
        except Exception as e:
            logger.warning(f"[RedisCache] DELETE failed: {e}")

    def exists(self, key: str) -> bool:
        try:
            return bool(self._client.exists(key))
        except Exception as e:
            logger.warning(f"[RedisCache] EXISTS failed: {e}")
            return False


# ---------------------------------------------------------------------------
# 全局缓存实例
# ---------------------------------------------------------------------------

_backend: Optional[CacheBackend] = None
_backend_lock = threading.Lock()


def _get_backend() -> CacheBackend:
    """获取缓存后端实例（懒初始化）"""
    global _backend
    if _backend is not None:
        return _backend

    with _backend_lock:
        if _backend is not None:
            return _backend

        redis_url = os.environ.get("REDIS_URL") or ""
        if redis_url:
            try:
                _backend = RedisCacheBackend(redis_url)
                return _backend
            except Exception as e:
                logger.warning(f"[RedisCache] Failed to connect Redis, falling back to memory: {e}")

        _backend = MemoryCacheBackend()
        logger.info("[RedisCache] Using memory cache backend")
        return _backend


# ---------------------------------------------------------------------------
# API Key 缓存
# ---------------------------------------------------------------------------

API_KEY_CACHE_PREFIX = "saas:apikey:"
API_KEY_CACHE_TTL = 600  # 10 分钟


def cache_api_key(raw_key: str, tenant_id: str):
    """缓存 API Key → tenant_id 映射"""
    import hashlib
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    cache_key = f"{API_KEY_CACHE_PREFIX}{key_hash}"
    _get_backend().set(cache_key, tenant_id, API_KEY_CACHE_TTL)


def lookup_api_key_cache(raw_key: str) -> Optional[str]:
    """从缓存查找 API Key → tenant_id"""
    import hashlib
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    cache_key = f"{API_KEY_CACHE_PREFIX}{key_hash}"
    result = _get_backend().get(cache_key)
    # 记录缓存指标
    try:
        from saas.metrics import record_cache_hit, record_cache_miss
        if result:
            record_cache_hit("api_key")
        else:
            record_cache_miss("api_key")
    except Exception:
        pass
    return result


def invalidate_api_key(raw_key: str):
    """使 API Key 缓存失效（传入原始 key，会自动哈希）"""
    import hashlib
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    cache_key = f"{API_KEY_CACHE_PREFIX}{key_hash}"
    _get_backend().delete(cache_key)


def invalidate_api_key_cache(key_hash_or_raw: str):
    """使 API Key 缓存失效（兼容接口：接受原始 key 或已哈希的 key）"""
    # 如果已经是 64 字符的 SHA256 哈希，直接用
    if len(key_hash_or_raw) == 64 and all(c in '0123456789abcdef' for c in key_hash_or_raw):
        cache_key = f"{API_KEY_CACHE_PREFIX}{key_hash_or_raw}"
    else:
        import hashlib
        key_hash = hashlib.sha256(key_hash_or_raw.encode("utf-8")).hexdigest()
        cache_key = f"{API_KEY_CACHE_PREFIX}{key_hash}"
    _get_backend().delete(cache_key)


# ---------------------------------------------------------------------------
# 租户配置缓存
# ---------------------------------------------------------------------------

TENANT_CONFIG_CACHE_PREFIX = "saas:tenant_config:"
TENANT_CONFIG_CACHE_TTL = 300  # 5 分钟


def cache_tenant_config(tenant_id: str, config: dict):
    """缓存租户配置"""
    cache_key = f"{TENANT_CONFIG_CACHE_PREFIX}{tenant_id}"
    _get_backend().set(cache_key, json.dumps(config, ensure_ascii=False), TENANT_CONFIG_CACHE_TTL)


def lookup_tenant_config(tenant_id: str) -> Optional[dict]:
    """从缓存查找租户配置"""
    cache_key = f"{TENANT_CONFIG_CACHE_PREFIX}{tenant_id}"
    value = _get_backend().get(cache_key)
    # 记录缓存指标
    try:
        from saas.metrics import record_cache_hit, record_cache_miss
        if value:
            record_cache_hit("tenant_config")
        else:
            record_cache_miss("tenant_config")
    except Exception:
        pass
    if value:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def invalidate_tenant_config(tenant_id: str):
    """使租户配置缓存失效"""
    cache_key = f"{TENANT_CONFIG_CACHE_PREFIX}{tenant_id}"
    _get_backend().delete(cache_key)


# ---------------------------------------------------------------------------
# IM 渠道映射缓存
# ---------------------------------------------------------------------------

IM_CHANNEL_CACHE_PREFIX = "saas:im_channel:"
IM_CHANNEL_CACHE_TTL = 300  # 5 分钟


def cache_im_channel_mapping(channel_type: str, app_id: str, tenant_id: str):
    """缓存 IM 渠道映射"""
    cache_key = f"{IM_CHANNEL_CACHE_PREFIX}{channel_type}:{app_id}"
    _get_backend().set(cache_key, tenant_id, IM_CHANNEL_CACHE_TTL)


def lookup_im_channel_mapping(channel_type: str, app_id: str) -> Optional[str]:
    """从缓存查找 IM 渠道映射"""
    cache_key = f"{IM_CHANNEL_CACHE_PREFIX}{channel_type}:{app_id}"
    result = _get_backend().get(cache_key)
    # 记录缓存指标
    try:
        from saas.metrics import record_cache_hit, record_cache_miss
        if result:
            record_cache_hit("im_channel")
        else:
            record_cache_miss("im_channel")
    except Exception:
        pass
    return result


def invalidate_im_channel_mapping(channel_type: str, app_id: str):
    """使 IM 渠道映射缓存失效"""
    cache_key = f"{IM_CHANNEL_CACHE_PREFIX}{channel_type}:{app_id}"
    _get_backend().delete(cache_key)
