# encoding:utf-8
"""
多租户上下文管理 — 基于 contextvars 的租户ID透传

在单进程内通过 ContextVar 在线程/协程间透传 tenant_id，
解决 CowAgent 消息处理链跨越 HTTP 边界（Channel → 消息队列 → Bridge → Agent）的问题。

用法:
    from common.tenant import TenantContext, current_tenant_id

    # 设置当前租户
    token = TenantContext.set_tenant("tenant_abc")
    try:
        # 在当前上下文中，任何地方都可以获取 tenant_id
        tid = current_tenant_id()  # "tenant_abc"
    finally:
        TenantContext.reset(token)

    # Flask 中间件自动设置
    @app.before_request
    def set_tenant():
        tid = extract_tenant_from_request(request)
        if tid:
            TenantContext.set_tenant(tid)

    # saas_mode 关闭时，tenant_id 始终为 None
"""

from contextvars import ContextVar, Token
from typing import Optional


# ---------------------------------------------------------------------------
# ContextVar 定义
# ---------------------------------------------------------------------------
_tenant_id: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)


class TenantContext:
    """租户上下文管理器，封装 ContextVar 操作"""

    @staticmethod
    def set_tenant(tenant_id: str) -> Token:
        """设置当前上下文的 tenant_id，返回 token 用于后续 reset"""
        return _tenant_id.set(tenant_id)

    @staticmethod
    def reset(token: Token) -> None:
        """恢复之前的 tenant_id 上下文"""
        _tenant_id.reset(token)

    @staticmethod
    def get_tenant_id() -> Optional[str]:
        """获取当前上下文的 tenant_id"""
        return _tenant_id.get()

    @staticmethod
    def clear() -> None:
        """清除当前上下文的 tenant_id（设为 None）"""
        _tenant_id.set(None)


def current_tenant_id() -> Optional[str]:
    """快捷函数：获取当前租户ID"""
    return _tenant_id.get()


def require_tenant_id() -> str:
    """获取当前租户ID，如果未设置则抛出 ValueError"""
    tid = _tenant_id.get()
    if tid is None:
        raise ValueError("tenant_id is required but not set in current context")
    return tid
