# encoding:utf-8
"""
GDPR 合规 — 租户数据导出与删除

GDPR 核心权利：
- 数据可携带权（Right to Data Portability）：GET /api/gdpr/export
- 删除权（Right to Erasure）：DELETE /api/gdpr/delete
- 数据访问权（Right to Access）：GET /api/gdpr/access

导出格式：JSON（包含租户所有关联数据）
删除流程：
1. 验证租户身份（需二次确认）
2. 删除所有关联数据（级联删除）
3. 记录审计日志
4. 触发 Webhook 通知
"""

import json
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.audit import audit_log
from saas.webhook import trigger_webhook

gdpr_bp = Blueprint("gdpr", __name__)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


# ---------------------------------------------------------------------------
# 数据导出
# ---------------------------------------------------------------------------

def _export_tenant_data(tenant_id: str) -> dict:
    """导出租户所有关联数据

    Returns:
        包含所有租户数据的字典
    """
    from saas.database import (
        db, Tenant, User, ApiKey, UsageRecord,
        AuditLog, WebhookEndpoint, TenantPluginConfig,
    )

    data = {"exported_at": datetime.now(timezone.utc).isoformat()}

    # 租户信息
    tenant = db.session.get(Tenant, tenant_id)
    if tenant:
        data["tenant"] = {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "config_json": tenant.config_json,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        }

    # 用户
    users = User.query.filter_by(tenant_id=tenant_id).all()
    data["users"] = [
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "role": u.role,
            "sso_uid": u.sso_uid,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]

    # API Keys（不导出 key_hash）
    api_keys = ApiKey.query.filter_by(tenant_id=tenant_id).all()
    data["api_keys"] = [
        {
            "id": k.id,
            "name": k.name,
            "key_prefix": k.key_prefix,
            "is_active": k.is_active,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        }
        for k in api_keys
    ]

    # 用量记录
    usage_records = UsageRecord.query.filter_by(tenant_id=tenant_id).all()
    data["usage_records"] = [
        {
            "id": r.id,
            "metric": r.metric,
            "value": r.value,
            "api_key_id": r.api_key_id,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in usage_records
    ]

    # 审计日志
    audit_logs = AuditLog.query.filter_by(tenant_id=tenant_id).all()
    data["audit_logs"] = [
        {
            "id": l.id,
            "action": l.action,
            "resource_type": l.resource_type,
            "resource_id": l.resource_id,
            "detail": l.detail,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in audit_logs
    ]

    # Webhook 端点
    webhooks = WebhookEndpoint.query.filter_by(tenant_id=tenant_id).all()
    data["webhooks"] = [
        {
            "id": w.id,
            "url": w.url,
            "events": json.loads(w.events) if w.events else [],
            "is_active": w.is_active,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in webhooks
    ]

    # 插件配置
    plugin_configs = TenantPluginConfig.query.filter_by(tenant_id=tenant_id).all()
    data["plugin_configs"] = [
        {
            "id": c.id,
            "plugin_name": c.plugin_name,
            "enabled": c.enabled,
            "priority": c.priority,
            "config_json": c.config_json,
        }
        for c in plugin_configs
    ]

    return data


# ---------------------------------------------------------------------------
# 数据删除
# ---------------------------------------------------------------------------

def _delete_tenant_data(tenant_id: str) -> dict:
    """删除租户所有关联数据

    按外键依赖顺序删除，避免约束冲突。

    Returns:
        {"deleted": {table_name: count}}
    """
    from saas.database import (
        db, Tenant, User, ApiKey, UsageRecord,
        AuditLog, WebhookEndpoint, TenantPluginConfig,
    )

    deleted = {}

    # 按依赖顺序删除
    # 1. 审计日志
    count = AuditLog.query.filter_by(tenant_id=tenant_id).delete()
    deleted["audit_logs"] = count

    # 2. 用量记录
    count = UsageRecord.query.filter_by(tenant_id=tenant_id).delete()
    deleted["usage_records"] = count

    # 3. API Keys
    count = ApiKey.query.filter_by(tenant_id=tenant_id).delete()
    deleted["api_keys"] = count

    # 4. Webhook 端点
    count = WebhookEndpoint.query.filter_by(tenant_id=tenant_id).delete()
    deleted["webhooks"] = count

    # 5. 插件配置
    count = TenantPluginConfig.query.filter_by(tenant_id=tenant_id).delete()
    deleted["plugin_configs"] = count

    # 6. 用户
    count = User.query.filter_by(tenant_id=tenant_id).delete()
    deleted["users"] = count

    # 7. 租户本身
    count = Tenant.query.filter_by(id=tenant_id).delete()
    deleted["tenant"] = count

    db.session.commit()
    return deleted


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------

@gdpr_bp.route("/access", methods=["GET"])
def data_access():
    """数据访问权 — 查看租户持有的数据摘要"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    from saas.database import db, User, ApiKey, UsageRecord, AuditLog, WebhookEndpoint

    summary = {
        "tenant_id": tenant_id,
        "users_count": User.query.filter_by(tenant_id=tenant_id).count(),
        "api_keys_count": ApiKey.query.filter_by(tenant_id=tenant_id).count(),
        "usage_records_count": UsageRecord.query.filter_by(tenant_id=tenant_id).count(),
        "audit_logs_count": AuditLog.query.filter_by(tenant_id=tenant_id).count(),
        "webhooks_count": WebhookEndpoint.query.filter_by(tenant_id=tenant_id).count(),
    }

    return jsonify(summary)


@gdpr_bp.route("/export", methods=["GET"])
def data_export():
    """数据可携带权 — 导出租户所有数据为 JSON"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = _export_tenant_data(tenant_id)

    audit_log(
        action="gdpr.export", resource_type="tenant", resource_id=tenant_id,
        detail="Data export requested", tenant_id=tenant_id, ip_address=_client_ip(),
    )

    return jsonify(data)


@gdpr_bp.route("/delete", methods=["DELETE"])
def data_delete():
    """删除权 — 删除租户所有数据

    需要在请求体中提供 confirm=true 以确认删除操作。
    此操作不可逆！
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json() or {}
    if not data.get("confirm"):
        return jsonify({
            "error": "Deletion requires confirmation",
            "message": "Set 'confirm': true in request body to proceed. This action is irreversible.",
        }), 400

    # 先导出数据（用于备份/审计）
    export_data = _export_tenant_data(tenant_id)

    # 记录审计日志（在删除前，因为删除后 tenant_id 就不存在了）
    audit_log(
        action="gdpr.delete", resource_type="tenant", resource_id=tenant_id,
        detail="Data deletion requested and confirmed",
        tenant_id=tenant_id, ip_address=_client_ip(),
    )

    # 触发 Webhook（在删除前）
    trigger_webhook("tenant.deleted", tenant_id, {
        "reason": "GDPR deletion request",
    })

    # 执行删除
    deleted = _delete_tenant_data(tenant_id)

    return jsonify({
        "message": "All tenant data has been deleted",
        "deleted": deleted,
        "export_snapshot": export_data,  # 返回已删除数据的快照
    })
