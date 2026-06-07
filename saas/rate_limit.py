# encoding:utf-8
"""
租户级 API 限流

基于套餐配额的滑动窗口限流：
- free: 60 次/分钟
- pro: 300 次/分钟
- enterprise: 不限流

实现方式：
- 内存滑动窗口计数器（生产环境可替换为 Redis）
- 集成到 Flask before_request 中间件
- 超限返回 429 Too Many Requests
"""

import time
import threading
from collections import defaultdict

from common.log import logger

# ---------------------------------------------------------------------------
# 套餐限流配置
# ---------------------------------------------------------------------------

RATE_LIMITS = {
    "free": 60,        # 60 次/分钟
    "pro": 300,        # 300 次/分钟
    "enterprise": -1,  # 不限流
}

DEFAULT_RATE_LIMIT = 60  # 未知套餐默认限流


def get_rate_limit(plan_id: str) -> int:
    """获取套餐对应的每分钟请求上限，-1 表示不限流"""
    return RATE_LIMITS.get(plan_id, DEFAULT_RATE_LIMIT)


# ---------------------------------------------------------------------------
# 滑动窗口计数器
# ---------------------------------------------------------------------------

class SlidingWindowCounter:
    """线程安全的滑动窗口计数器

    使用分钟级时间桶实现近似滑动窗口：
    - 每个桶记录 1 分钟内的请求数
    - 窗口大小 = 1 分钟
    - 自动清理过期桶
    """

    def __init__(self):
        self._buckets = defaultdict(dict)  # tenant_id -> {minute_ts: count}
        self._lock = threading.Lock()

    def increment(self, tenant_id: str, window_seconds: int = 60) -> int:
        """递增计数并返回当前窗口内的总请求数

        Args:
            tenant_id: 租户 ID
            window_seconds: 窗口大小（秒），默认 60

        Returns:
            当前窗口内的总请求数
        """
        now = time.time()
        current_minute = int(now // window_seconds)

        with self._lock:
            buckets = self._buckets[tenant_id]

            # 清理过期桶（2 分钟前的）
            expired = [ts for ts in buckets if ts < current_minute - 1]
            for ts in expired:
                del buckets[ts]

            # 递增当前桶
            buckets[current_minute] = buckets.get(current_minute, 0) + 1

            # 计算窗口内总请求数
            total = sum(
                count for ts, count in buckets.items()
                if ts >= current_minute - 1
            )
            return total

    def get_count(self, tenant_id: str, window_seconds: int = 60) -> int:
        """获取当前窗口内的请求数（不递增）"""
        now = time.time()
        current_minute = int(now // window_seconds)

        with self._lock:
            buckets = self._buckets.get(tenant_id, {})
            return sum(
                count for ts, count in buckets.items()
                if ts >= current_minute - 1
            )

    def reset(self, tenant_id: str = None):
        """重置计数器"""
        with self._lock:
            if tenant_id:
                self._buckets.pop(tenant_id, None)
            else:
                self._buckets.clear()


# 全局计数器实例
_rate_counter = SlidingWindowCounter()


# ---------------------------------------------------------------------------
# Flask 限流中间件
# ---------------------------------------------------------------------------

class RateLimitMiddleware:
    """Flask 限流中间件 — 在 TenantMiddleware 之后执行

    用法：
        RateLimitMiddleware(app)

    依赖：
        TenantMiddleware 必须先注册（设置 g._tenant_id）
    """

    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        app.before_request(self._check_rate_limit)
        app.after_request(self._add_rate_limit_headers)

    def _check_rate_limit(self):
        from flask import request, g, jsonify
        from config import conf

        if not conf().get("saas_mode", False):
            return None

        # 健康检查和静态资源不限流
        if request.path.startswith("/health") or request.path.startswith("/static"):
            return None

        # 获取 tenant_id（由 TenantMiddleware 设置）
        from common.tenant import current_tenant_id
        tenant_id = current_tenant_id()
        if not tenant_id:
            return None  # 未认证的请求由 TenantMiddleware 处理

        # 获取套餐限流配置
        try:
            from saas.database import db, Tenant
            tenant = db.session.get(Tenant, tenant_id)
            if not tenant:
                return None
            limit = get_rate_limit(tenant.plan)
        except Exception:
            limit = DEFAULT_RATE_LIMIT

        # 不限流
        if limit == -1:
            g._rate_limit = -1
            g._rate_remaining = -1
            return None

        # 滑动窗口计数
        current_count = _rate_counter.increment(tenant_id)
        remaining = max(0, limit - current_count)

        g._rate_limit = limit
        g._rate_remaining = remaining

        if current_count > limit:
            from saas.audit import audit_log
            audit_log(
                action="rate_limit.exceeded",
                resource_type="tenant",
                resource_id=tenant_id,
                detail=f"count={current_count}, limit={limit}",
                tenant_id=tenant_id,
            )
            from saas.webhook import notify_rate_limit_exceeded
            notify_rate_limit_exceeded(tenant_id, limit, current_count)
            response = jsonify({
                "error": "Rate limit exceeded",
                "retry_after": 60,
                "limit": limit,
            })
            response.status_code = 429
            response.headers["Retry-After"] = "60"
            return response

        return None

    def _add_rate_limit_headers(self, response):
        from flask import g

        limit = getattr(g, "_rate_limit", None)
        remaining = getattr(g, "_rate_remaining", None)

        if limit is not None:
            response.headers["X-RateLimit-Limit"] = str(limit)
            if remaining is not None:
                response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def reset_rate_limit(tenant_id: str = None):
    """重置限流计数器（用于测试或管理操作）"""
    _rate_counter.reset(tenant_id)


def get_rate_limit_status(tenant_id: str) -> dict:
    """获取租户当前限流状态

    Returns:
        {"limit": int, "current": int, "remaining": int}
    """
    try:
        from saas.database import db, Tenant
        tenant = db.session.get(Tenant, tenant_id)
        limit = get_rate_limit(tenant.plan) if tenant else DEFAULT_RATE_LIMIT
    except Exception:
        limit = DEFAULT_RATE_LIMIT

    current = _rate_counter.get_count(tenant_id)
    remaining = max(0, limit - current) if limit > 0 else -1

    return {
        "limit": limit,
        "current": current,
        "remaining": remaining,
    }
