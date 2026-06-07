# encoding:utf-8
"""
审计日志 — 记录所有管理操作

使用方式：
    from saas.audit import audit_log
    audit_log(action="create_api_key", resource_type="api_key", resource_id=key_id, detail="name=test-key")

审计事件类型：
- tenant.register / tenant.update / tenant.delete
- api_key.create / api_key.revoke
- user.login / user.sso_login
- billing.quota_exceeded
- config.update
"""

import json
import uuid
from datetime import datetime, timezone

from common.tenant import current_tenant_id


def audit_log(action: str, resource_type: str, resource_id: str = None,
              detail: str = None, tenant_id: str = None, user_id: str = None,
              ip_address: str = None):
    """记录审计日志

    Args:
        action: 操作类型（如 create_api_key, revoke_api_key）
        resource_type: 资源类型（如 api_key, tenant, user）
        resource_id: 资源 ID
        detail: 详细描述
        tenant_id: 租户 ID（不传则从 current_tenant_id 获取）
        user_id: 操作用户 ID
        ip_address: 请求来源 IP
    """
    try:
        from saas.database import db, AuditLog

        tid = tenant_id or current_tenant_id()
        if not tid:
            return  # 无租户上下文，跳过

        entry = AuditLog(
            id=str(uuid.uuid4()),
            tenant_id=tid,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=ip_address,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        # 审计日志不应阻塞业务流程
        import logging
        logging.getLogger(__name__).debug(f"[AuditLog] Failed to write: {e}")


def query_audit_logs(tenant_id: str, action: str = None, resource_type: str = None,
                     limit: int = 100, offset: int = 0):
    """查询审计日志

    Args:
        tenant_id: 租户 ID
        action: 按操作类型过滤
        resource_type: 按资源类型过滤
        limit: 返回条数
        offset: 偏移量

    Returns:
        list[AuditLog]
    """
    from saas.database import db, AuditLog

    query = AuditLog.query.filter_by(tenant_id=tenant_id)

    if action:
        query = query.filter_by(action=action)
    if resource_type:
        query = query.filter_by(resource_type=resource_type)

    query = query.order_by(AuditLog.created_at.desc())
    return query.offset(offset).limit(limit).all()
