# encoding:utf-8
"""
API 限流 — 基于租户的滑动窗口限流

按租户 + 端点维度限制请求频率，防止滥用。
使用 Redis 或内存缓存作为计数器后端，与 saas.cache 共享基础设施。

限流维度：
- 全局限流：每租户每分钟最大请求数
- 端点限流：特定端点（如 /api/chat/completions）的独立限制

套餐限流配置：
- free:  20 req/min (全局), 10 req/min (chat)
- pro:   120 req/min (全局), 60 req/min (chat)
- enterprise: 600 req/min (全局), 300 req/min (chat)
"""

import time
import threading
from typing import Optional, Tuple

from common.log import logger


# ---------------------------------------------------------------------------
# 套餐限流配置
# ---------------------------------------------------------------------------

RATE_LIMITS = {
    "free": {
        "global": 20,       # 20 req/min
        "chat": 10,         # 10 req/min for /api/chat/completions
    },
    "pro": {
        "global": 120,
        "chat": 60,
    },
    "enterprise": {
        "global": 600,
        "chat": 300,
    },
}

# 固定窗口大小（秒）
WINDOW_SIZE = 60


def get_rate_limit(plan: str, endpoint_type: str = "global") -> int:
    """获取套餐对应的限流值，未知套餐降级为 free"""
    plan_limits = RATE_LIMITS.get(plan, RATE_LIMITS["free"])
    return plan_limits.get(endpoint_type, plan_limits["global"])


# ---------------------------------------------------------------------------
# 滑动窗口计数器
# ---------------------------------------------------------------------------

class RateLimiter:
    """基于缓存的滑动窗口限流器

    使用 saas.cache 后端存储计数器，Redis 不可用时自动降级为内存。
    线程安全，支持高并发。
    """

    PREFIX = "saas:ratelimit:"

    def __init__(self):
        self._local_counters = {}  # 内存回退计数器
        self._lock = threading.Lock()

    def check(self, tenant_id: str, endpoint_type: str = "global",
              plan: str = "free") -> Tuple[bool, int, int, int]:
        """检查请求是否被限流

        Args:
            tenant_id: 租户 ID
            endpoint_type: 端点类型 (global / chat)
            plan: 租户套餐

        Returns:
            (allowed: bool, remaining: int, limit: int, retry_after: int)
            retry_after 仅在 allowed=False 时有意义（秒）
        """
        limit = get_rate_limit(plan, endpoint_type)
        now = time.time()
        window_key = int(now // WINDOW_SIZE)  # 当前窗口编号
        cache_key = f"{self.PREFIX}{tenant_id}:{endpoint_type}:{window_key}"

        # 尝试使用缓存后端（Redis）
        try:
            from saas.cache import _get_backend
            backend = _get_backend()
            current = backend.get(cache_key)
            if current is None:
                current = 0
            else:
                current = int(current)

            if current >= limit:
                # 计算当前窗口剩余时间
                retry_after = WINDOW_SIZE - int(now % WINDOW_SIZE)
                return False, 0, limit, retry_after

            # 递增计数器
            new_count = current + 1
            # TTL 设为窗口大小的 2 倍，确保过期窗口自动清理
            backend.set(cache_key, str(new_count), ttl=WINDOW_SIZE * 2)

            remaining = max(0, limit - new_count)
            return True, remaining, limit, 0

        except Exception:
            pass

        # 降级为内存计数器
        return self._check_local(tenant_id, endpoint_type, limit, window_key, now)

    def _check_local(self, tenant_id: str, endpoint_type: str,
                     limit: int, window_key: int,
                     now: float) -> Tuple[bool, int, int, int]:
        """内存计数器（Redis 不可用时的降级方案）"""
        local_key = f"{tenant_id}:{endpoint_type}:{window_key}"

        with self._lock:
            # 清理过期窗口
            expired = [k for k, v in self._local_counters.items()
                       if v.get("expires_at", 0) < now]
            for k in expired:
                del self._local_counters[k]

            entry = self._local_counters.get(local_key)
            if entry is None:
                entry = {"count": 0, "expires_at": now + WINDOW_SIZE}
                self._local_counters[local_key] = entry

            if entry["count"] >= limit:
                retry_after = max(1, int(entry["expires_at"] - now))
                return False, 0, limit, retry_after

            entry["count"] += 1
            remaining = max(0, limit - entry["count"])
            return True, remaining, limit, 0


# 全局限流器实例
_limiter = RateLimiter()


def check_rate_limit(tenant_id: str, endpoint_type: str = "global") -> Tuple[bool, int, int, int]:
    """检查租户请求是否被限流

    自动获取租户套餐，返回限流结果。

    Args:
        tenant_id: 租户 ID
        endpoint_type: 端点类型 (global / chat)

    Returns:
        (allowed, remaining, limit, retry_after)
    """
    # 获取租户套餐
    plan = "free"
    try:
        from saas.database import Tenant
        tenant = Tenant.query.get(tenant_id)
        if tenant:
            plan = tenant.plan
    except Exception:
        pass

    return _limiter.check(tenant_id, endpoint_type, plan)


# ---------------------------------------------------------------------------
# Flask 限流装饰器
# ---------------------------------------------------------------------------

def rate_limit(endpoint_type: str = "global"):
    """Flask 蓝图限流装饰器

    用法:
        @chat_bp.route("/completions", methods=["POST"])
        @require_auth
        @rate_limit("chat")
        def completions(tenant_id):
            ...
    """
    from functools import wraps
    from flask import g, jsonify

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            tenant_id = getattr(g, "_tenant_id", None)
            if not tenant_id:
                from common.tenant import TenantContext
                tenant_id = TenantContext.get_tenant()

            if tenant_id:
                allowed, remaining, limit, retry_after = check_rate_limit(
                    tenant_id, endpoint_type)

                if not allowed:
                    response = jsonify({
                        "error": "Rate limit exceeded",
                        "retry_after": retry_after,
                    })
                    response.status_code = 429
                    response.headers["Retry-After"] = str(retry_after)
                    response.headers["X-RateLimit-Limit"] = str(limit)
                    response.headers["X-RateLimit-Remaining"] = "0"
                    return response

                # 在响应头中附加限流信息
                result = f(*args, **kwargs)
                if hasattr(result, 'headers'):
                    result.headers["X-RateLimit-Limit"] = str(limit)
                    result.headers["X-RateLimit-Remaining"] = str(remaining)
                return result

            return f(*args, **kwargs)
        return decorated
    return decorator
