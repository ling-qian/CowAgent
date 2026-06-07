# encoding:utf-8
"""
审计日志查询 API

GET /api/audit — 查询当前租户的审计日志
"""

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.audit import query_audit_logs

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("", methods=["GET"])
def list_audit_logs():
    """查询审计日志

    Query params:
        action: 按操作类型过滤（如 api_key.create）
        resource_type: 按资源类型过滤（如 api_key, tenant）
        limit: 返回条数（默认 100，最大 500）
        offset: 偏移量
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    action = request.args.get("action")
    resource_type = request.args.get("resource_type")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (ValueError, TypeError):
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    logs = query_audit_logs(
        tenant_id=tenant_id,
        action=action,
        resource_type=resource_type,
        limit=limit,
        offset=offset,
    )

    return jsonify({
        "logs": [
            {
                "id": log.id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "detail": log.detail,
                "user_id": log.user_id,
                "ip_address": log.ip_address,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "count": len(logs),
        "limit": limit,
        "offset": offset,
    })
