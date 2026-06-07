# encoding:utf-8
"""
Webhook 管理 API

POST   /api/webhooks           — 注册 Webhook 端点
GET    /api/webhooks           — 列出当前租户的 Webhook 端点
DELETE /api/webhooks/<id>      — 删除 Webhook 端点
POST   /api/webhooks/<id>/test — 测试 Webhook 端点
"""

import json
import secrets
import uuid

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.webhook import _ensure_webhook_model, trigger_webhook

webhooks_bp = Blueprint("webhooks", __name__)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


@webhooks_bp.route("", methods=["POST"])
def create_webhook():
    """注册 Webhook 端点"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json() or {}
    url = data.get("url", "").strip()
    events = data.get("events", [])  # 空列表 = 订阅所有事件

    if not url:
        return jsonify({"error": "url is required"}), 400

    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must start with http:// or https://"}), 400

    db = _ensure_webhook_model()
    from saas.database import WebhookEndpoint

    # 限制每个租户最多 10 个 Webhook 端点
    existing = WebhookEndpoint.query.filter_by(tenant_id=tenant_id, is_active=True).count()
    if existing >= 10:
        return jsonify({"error": "Maximum 10 webhook endpoints per tenant"}), 403

    secret = secrets.token_hex(32)
    endpoint = WebhookEndpoint(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        url=url,
        secret=secret,
        events=json.dumps(events),
    )
    db.session.add(endpoint)
    db.session.commit()

    from saas.audit import audit_log
    audit_log(
        action="webhook.create", resource_type="webhook", resource_id=endpoint.id,
        detail=f"url={url}", tenant_id=tenant_id, ip_address=_client_ip(),
    )

    return jsonify({
        "id": endpoint.id,
        "url": endpoint.url,
        "secret": secret,  # 只在创建时返回一次
        "events": events,
        "created_at": endpoint.created_at.isoformat() if endpoint.created_at else None,
    }), 201


@webhooks_bp.route("", methods=["GET"])
def list_webhooks():
    """列出当前租户的 Webhook 端点"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    db = _ensure_webhook_model()
    from saas.database import WebhookEndpoint

    endpoints = WebhookEndpoint.query.filter_by(
        tenant_id=tenant_id, is_active=True
    ).order_by(WebhookEndpoint.created_at.desc()).all()

    return jsonify({
        "webhooks": [
            {
                "id": ep.id,
                "url": ep.url,
                "events": json.loads(ep.events) if ep.events else [],
                "last_delivery_at": ep.last_delivery_at.isoformat() if ep.last_delivery_at else None,
                "last_delivery_status": ep.last_delivery_status,
                "created_at": ep.created_at.isoformat() if ep.created_at else None,
            }
            for ep in endpoints
        ]
    })


@webhooks_bp.route("/<endpoint_id>", methods=["DELETE"])
def delete_webhook(endpoint_id):
    """删除 Webhook 端点"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    db = _ensure_webhook_model()
    from saas.database import WebhookEndpoint

    endpoint = WebhookEndpoint.query.filter_by(
        id=endpoint_id, tenant_id=tenant_id
    ).first()
    if not endpoint:
        return jsonify({"error": "Webhook endpoint not found"}), 404

    endpoint.is_active = False
    db.session.commit()

    from saas.audit import audit_log
    audit_log(
        action="webhook.delete", resource_type="webhook", resource_id=endpoint_id,
        detail=f"url={endpoint.url}", tenant_id=tenant_id, ip_address=_client_ip(),
    )

    return jsonify({"message": "Webhook endpoint deleted"})


@webhooks_bp.route("/<endpoint_id>/test", methods=["POST"])
def test_webhook(endpoint_id):
    """测试 Webhook 端点 — 发送一个测试事件"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    db = _ensure_webhook_model()
    from saas.database import WebhookEndpoint

    endpoint = WebhookEndpoint.query.filter_by(
        id=endpoint_id, tenant_id=tenant_id, is_active=True
    ).first()
    if not endpoint:
        return jsonify({"error": "Webhook endpoint not found"}), 404

    # 触发一个测试事件
    trigger_webhook("webhook.test", tenant_id, {
        "message": "This is a test event from CowAgent SaaS",
        "endpoint_id": endpoint_id,
    })

    return jsonify({"message": "Test event sent", "event": "webhook.test"})
