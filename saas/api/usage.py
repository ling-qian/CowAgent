# encoding:utf-8
"""
用量查询 API

GET    /api/usage                — 查询当前租户的用量统计
"""

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.database import db, UsageRecord

usage_bp = Blueprint("usage", __name__)


@usage_bp.route("", methods=["GET"])
def get_usage():
    """查询当前租户的用量统计"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    period = request.args.get("period")  # 如 "2026-06"

    query = UsageRecord.query.filter_by(tenant_id=tenant_id)
    if period:
        query = query.filter_by(period=period)

    records = query.order_by(UsageRecord.period.desc(), UsageRecord.metric).all()

    return jsonify({
        "usage": [
            {
                "id": r.id,
                "metric": r.metric,
                "value": r.value,
                "period": r.period,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    })


def record_usage(tenant_id: str, metric: str, value: int = 1, api_key_id: str = None):
    """记录用量（供业务层调用），含配额检查

    Args:
        tenant_id: 租户ID
        metric: 指标名（如 "llm_tokens", "api_calls"）
        value: 增量值
        api_key_id: 关联的 API Key ID

    Returns:
        (allowed: bool, remaining: int) — allowed=False 表示配额不足
    """
    from datetime import datetime, timezone
    from saas.billing import check_quota

    # 配额检查
    allowed, remaining, limit = check_quota(tenant_id, metric, value)
    if not allowed:
        from saas.webhook import notify_quota_exceeded
        notify_quota_exceeded(tenant_id, metric, remaining + value, limit)
        return False, remaining

    period = datetime.now(timezone.utc).strftime("%Y-%m")

    # 尝试 upsert
    existing = UsageRecord.query.filter_by(
        tenant_id=tenant_id,
        api_key_id=api_key_id,
        metric=metric,
        period=period,
    ).first()

    if existing:
        existing.value += value
    else:
        record = UsageRecord(
            tenant_id=tenant_id,
            api_key_id=api_key_id,
            metric=metric,
            value=value,
            period=period,
        )
        db.session.add(record)

    db.session.commit()
    return True, remaining
