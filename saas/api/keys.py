# encoding:utf-8
"""
API Key 管理 API

POST   /api/keys                 — 创建新 API Key
GET    /api/keys                 — 列出当前租户的所有 API Key
DELETE /api/keys/<key_id>        — 吊销 API Key
"""

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.database import db, ApiKey
from saas.middleware import generate_api_key
from saas.audit import audit_log

keys_bp = Blueprint("keys", __name__)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


@keys_bp.route("", methods=["POST"])
def create_api_key():
    """创建新 API Key"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json() or {}
    name = data.get("name", "").strip() or "Unnamed Key"

    # 配额检查：API Key 数量上限
    from saas.billing import check_api_key_limit
    allowed, current, limit = check_api_key_limit(tenant_id)
    if not allowed:
        return jsonify({
            "error": "API Key 数量已达上限",
            "current": current,
            "limit": limit,
        }), 403

    raw_key, key_hash, key_prefix = generate_api_key()
    api_key = ApiKey(
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=name,
    )
    db.session.add(api_key)
    db.session.commit()

    audit_log(
        action="api_key.create", resource_type="api_key", resource_id=api_key.id,
        detail=f"name={name}", tenant_id=tenant_id, ip_address=_client_ip(),
    )

    from saas.webhook import notify_api_key_created
    notify_api_key_created(tenant_id, api_key.id, name)

    return jsonify({
        "id": api_key.id,
        "name": api_key.name,
        "key_prefix": key_prefix,
        "api_key": raw_key,  # 只在创建时返回一次
        "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
    }), 201


@keys_bp.route("", methods=["GET"])
def list_api_keys():
    """列出当前租户的所有 API Key"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    keys = ApiKey.query.filter_by(tenant_id=tenant_id).order_by(ApiKey.created_at.desc()).all()
    return jsonify({
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": k.key_prefix,
                "is_active": k.is_active,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in keys
        ]
    })


@keys_bp.route("/<key_id>", methods=["DELETE"])
def revoke_api_key(key_id):
    """吊销 API Key"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    api_key = ApiKey.query.filter_by(id=key_id, tenant_id=tenant_id).first()
    if not api_key:
        return jsonify({"error": "API Key not found"}), 404

    api_key.is_active = False
    db.session.commit()

    audit_log(
        action="api_key.revoke", resource_type="api_key", resource_id=key_id,
        detail=f"name={api_key.name}", tenant_id=tenant_id, ip_address=_client_ip(),
    )

    from saas.webhook import notify_api_key_revoked
    notify_api_key_revoked(tenant_id, key_id, api_key.name)

    return jsonify({"message": "API Key revoked"})
