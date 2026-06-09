# encoding:utf-8
"""
多租户中间件

在 saas_mode=True 时，从请求中提取 tenant_id 并注入到 TenantContext。
支持两种 Web 框架：
1. web.py（CowAgent 默认 Web 框架）— 通过 add_processor 钩子
2. Flask（SaaS 管理 API）— 通过 before_request/after_request 钩子

认证方式：
1. API Key（Header: X-API-Key 或 Authorization: Bearer sk-xxx）
2. 请求头直接传递（X-Tenant-ID，仅用于内部服务间调用）
"""

import hashlib

from common.log import logger
from common.tenant import TenantContext


def _hash_api_key(raw_key: str) -> str:
    """计算 API Key 的 SHA256 哈希"""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _extract_api_key_from_headers(headers: dict) -> str:
    """从请求头中提取 API Key

    Args:
        headers: 请求头字典（不区分大小写的 key）
    """
    # 方式1: X-API-Key 头
    api_key = headers.get("X-API-Key") or headers.get("x-api-key")
    if api_key:
        return api_key.strip()

    # 方式2: Authorization: Bearer sk-xxx
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token.startswith("sk-"):
            return token

    return None


def _resolve_api_key(raw_key: str) -> str:
    """根据 API Key 哈希查找对应的 tenant_id

    查找顺序：
    1. 缓存（Redis/内存）
    2. 数据库
    """
    # 1. 检查缓存
    try:
        from saas.cache import lookup_api_key_cache, cache_api_key
        cached = lookup_api_key_cache(raw_key)
        if cached:
            return cached
    except Exception:
        pass

    # 2. 查询数据库
    try:
        from saas.database import db, ApiKey
        key_hash = _hash_api_key(raw_key)
        api_key_record = ApiKey.query.filter_by(
            key_hash=key_hash, is_active=True
        ).first()
        if api_key_record:
            from datetime import datetime, timezone
            api_key_record.last_used_at = datetime.now(timezone.utc)
            db.session.commit()
            # 写入缓存
            try:
                cache_api_key(raw_key, api_key_record.tenant_id)
            except Exception:
                pass
            return api_key_record.tenant_id
    except Exception as e:
        logger.warning(f"[TenantMiddleware] Failed to resolve API key: {e}")
    return None


# ---------------------------------------------------------------------------
# web.py 中间件
# ---------------------------------------------------------------------------

# 允许未认证的路径前缀
_WEBPY_ALLOWED_PREFIXES = ("/auth/", "/api/auth/", "/api/tenants/register", "/assets/")


def webpy_tenant_processor(handler):
    """web.py application processor，在每个请求前后注入/清理 tenant_id。

    用法:
        app = web.application(urls, globals())
        app.add_processor(webpy_tenant_processor)
    """
    def processor(handler_func):
        def wrapper():
            from config import conf
            if not conf().get("saas_mode", False):
                return handler_func()

            import web
            path = web.ctx.path or "/"

            # 白名单路径跳过认证
            if any(path.startswith(p) for p in _WEBPY_ALLOWED_PREFIXES):
                return handler_func()

            tenant_id = None

            # 1. 尝试从 API Key 认证
            headers = web.ctx.env or {}
            # web.py 把 HTTP 头放在 environ 中，格式如 HTTP_X_API_KEY
            header_dict = {}
            for key, value in headers.items():
                if isinstance(key, str) and isinstance(value, str):
                    # HTTP_X_API_KEY -> X-Api-Key
                    if key.startswith("HTTP_"):
                        header_key = key[5:].replace("_", "-").title()
                        header_dict[header_key] = value
                    elif key.startswith("CONTENT_") or key == "CONTENT_TYPE":
                        header_dict[key.replace("_", "-").title()] = value
            # 也保留原始 key 供直接查找
            for key, value in headers.items():
                if isinstance(key, str) and isinstance(value, str):
                    header_dict[key] = value

            api_key = _extract_api_key_from_headers(header_dict)
            if api_key:
                tenant_id = _resolve_api_key(api_key)

            # 2. 尝试从 X-Tenant-ID 头获取
            if tenant_id is None:
                tenant_id = header_dict.get("X-Tenant-ID") or header_dict.get("x-tenant-id")

            # 3. 未获取到 tenant_id
            if tenant_id is None:
                # 静态资源、健康检查等跳过
                if path.startswith("/assets/") or path == "/" or path == "/health":
                    return handler_func()
                # 其他路径需要认证
                web.ctx.status = "401 Unauthorized"
                web.header("Content-Type", "application/json")
                return '{"error": "Missing tenant authentication"}'

            # 注入 tenant_id 到 ContextVar
            token = TenantContext.set_tenant(tenant_id)
            try:
                return handler_func()
            finally:
                TenantContext.reset(token)

        return wrapper

    return processor(handler)


# ---------------------------------------------------------------------------
# Flask 中间件
# ---------------------------------------------------------------------------

class TenantMiddleware:
    """Flask before_request 中间件，注入 tenant_id 到上下文"""

    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """注册 before_request / after_request 钩子"""
        app.before_request(self._before_request)
        app.after_request(self._after_request)

    def _before_request(self):
        from flask import request, g
        import os
        saas_mode = os.environ.get("SAAS_MODE", "").lower() in ("true", "1", "yes")
        if not saas_mode:
            try:
                from config import conf
                saas_mode = conf().get("saas_mode", False)
            except Exception:
                pass
        if not saas_mode:
            return None

        if request.path.startswith("/health") or request.path.startswith("/static") or request.path.startswith("/debug") or request.path == "/" or request.path.startswith("/apidocs") or request.path.startswith("/flasgger") or request.path.startswith("/apispec") or request.path.startswith("/oauth2-redirect"):
            return None

        tenant_id = None

        api_key = _extract_api_key_from_headers(request.headers)
        if api_key:
            tenant_id = _resolve_api_key(api_key)

        if tenant_id is None:
            tenant_id = request.headers.get("X-Tenant-ID")

        if tenant_id is None:
            allowed_prefixes = ("/api/auth/", "/api/tenants/register", "/api/billing/plans", "/api/im-channels/lookup", "/health")
            if any(request.path.startswith(p) for p in allowed_prefixes):
                return None
            from flask import jsonify
            return jsonify({"error": "Missing tenant authentication"}), 401

        token = TenantContext.set_tenant(tenant_id)
        g._tenant_token = token
        g._tenant_id = tenant_id
        return None

    def _after_request(self, response):
        from flask import g
        token = g.pop("_tenant_token", None)
        if token is not None:
            TenantContext.reset(token)
        return response


# ---------------------------------------------------------------------------
# API Key 生成
# ---------------------------------------------------------------------------

def generate_api_key() -> tuple:
    """生成 API Key，返回 (raw_key, key_hash, key_prefix)

    raw_key 只在创建时返回一次，后续只存储哈希。
    """
    import secrets
    raw = "sk-" + secrets.token_hex(24)
    key_hash = _hash_api_key(raw)
    key_prefix = raw[:8]
    return raw, key_hash, key_prefix


# ---------------------------------------------------------------------------
# Flask 蓝图认证装饰器
# ---------------------------------------------------------------------------

def require_auth(f):
    """Flask 蓝图认证装饰器，从 g 对象获取 tenant_id 传入视图函数。

    用法:
        @bp.route("/my-resource", methods=["GET"])
        @require_auth
        def list_resources(tenant_id):
            ...
    """
    from functools import wraps
    from flask import g, jsonify

    @wraps(f)
    def decorated(*args, **kwargs):
        tenant_id = getattr(g, "_tenant_id", None)
        if not tenant_id:
            # 尝试从 TenantContext 获取（中间件已设置）
            from common.tenant import TenantContext
            tenant_id = TenantContext.get_tenant()
        if not tenant_id:
            return jsonify({"error": "Authentication required"}), 401
        return f(tenant_id, *args, **kwargs)
    return decorated
