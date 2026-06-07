# encoding:utf-8
"""
Webhook 通知系统

支持的事件类型：
- quota.exceeded: 配额超限
- api_key.revoked: API Key 被吊销
- api_key.created: API Key 创建
- rate_limit.exceeded: 限流触发
- tenant.updated: 租户配置更新

租户通过 POST /api/webhooks 注册 Webhook URL，
系统在事件触发时向该 URL 发送 HTTP POST 请求。

安全机制：
- HMAC-SHA256 签名验证（X-Webhook-Signature 头）
- 失败重试（最多 3 次，指数退避）
- 事件 ID 去重
"""

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from common.log import logger

# ---------------------------------------------------------------------------
# Webhook 模型（延迟导入避免循环依赖）
# ---------------------------------------------------------------------------

def _ensure_webhook_model():
    """确保 WebhookEndpoint 模型已注册到 SQLAlchemy（已在 database.py 中定义）"""
    from saas.database import db
    return db


# ---------------------------------------------------------------------------
# 签名与验证
# ---------------------------------------------------------------------------

def sign_payload(payload: str, secret: str) -> str:
    """使用 HMAC-SHA256 对 payload 签名"""
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(payload: str, secret: str, signature: str) -> bool:
    """验证 HMAC-SHA256 签名"""
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Webhook 投递
# ---------------------------------------------------------------------------

def _deliver_webhook(url: str, secret: str, event: dict, max_retries: int = 3):
    """投递 Webhook 事件（带重试）

    Args:
        url: 目标 URL
        secret: HMAC 签名密钥
        event: 事件数据
        max_retries: 最大重试次数
    """
    payload = json.dumps(event, ensure_ascii=False)
    signature = sign_payload(payload, secret)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Event": event.get("event", ""),
        "X-Webhook-ID": event.get("id", ""),
    }

    for attempt in range(max_retries):
        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=payload.encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    logger.debug(f"[Webhook] Delivered {event['event']} to {url} (attempt {attempt + 1})")
                    return True
        except Exception as e:
            logger.warning(f"[Webhook] Delivery failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避

    logger.error(f"[Webhook] All {max_retries} attempts failed for {url}")
    return False


def trigger_webhook(event_type: str, tenant_id: str, data: dict = None):
    """触发 Webhook 事件 — 查找该租户的所有活跃 Webhook 端点并投递

    Args:
        event_type: 事件类型（如 quota.exceeded, api_key.revoked）
        tenant_id: 租户 ID
        data: 事件附加数据
    """
    try:
        db = _ensure_webhook_model()
        from saas.database import WebhookEndpoint

        endpoints = WebhookEndpoint.query.filter_by(
            tenant_id=tenant_id,
            is_active=True,
        ).all()

        if not endpoints:
            return

        event = {
            "id": str(uuid.uuid4()),
            "event": event_type,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }

        for ep in endpoints:
            # 检查该端点是否订阅了此事件
            try:
                subscribed_events = json.loads(ep.events)
            except (json.JSONDecodeError, TypeError):
                subscribed_events = []

            # 空列表 = 订阅所有事件
            if subscribed_events and event_type not in subscribed_events:
                continue

            # 异步投递
            threading.Thread(
                target=_deliver_webhook,
                args=(ep.url, ep.secret, event),
                daemon=True,
            ).start()

            # 更新最后投递时间
            try:
                ep.last_delivery_at = datetime.now(timezone.utc)
                db.session.commit()
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"[Webhook] trigger_webhook failed: {e}")


# ---------------------------------------------------------------------------
# 便捷函数 — 在关键事件中调用
# ---------------------------------------------------------------------------

def notify_quota_exceeded(tenant_id: str, metric: str, current: int, limit: int):
    """配额超限通知"""
    trigger_webhook("quota.exceeded", tenant_id, {
        "metric": metric,
        "current": current,
        "limit": limit,
    })


def notify_api_key_revoked(tenant_id: str, key_id: str, key_name: str):
    """API Key 吊销通知"""
    trigger_webhook("api_key.revoked", tenant_id, {
        "key_id": key_id,
        "key_name": key_name,
    })


def notify_api_key_created(tenant_id: str, key_id: str, key_name: str):
    """API Key 创建通知"""
    trigger_webhook("api_key.created", tenant_id, {
        "key_id": key_id,
        "key_name": key_name,
    })


def notify_rate_limit_exceeded(tenant_id: str, limit: int, current: int):
    """限流触发通知"""
    trigger_webhook("rate_limit.exceeded", tenant_id, {
        "limit": limit,
        "current": current,
    })
