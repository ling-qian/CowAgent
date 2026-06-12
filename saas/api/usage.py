# encoding:utf-8
"""
用量查询 API

GET    /api/usage                — 查询当前租户的用量统计
GET    /api/usage/summary        — 查询当前租户的用量汇总（含配额信息）
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


@usage_bp.route("/summary", methods=["GET"])
def get_usage_summary():
    """查询当前租户的用量汇总（含配额、限流信息）

    返回:
        {
            "period": "2026-06",
            "metrics": {
                "llm_tokens": {"used": 12345, "limit": 100000, "remaining": 87655},
                "api_calls": {"used": 150, "limit": 1000, "remaining": 850}
            },
            "rate_limits": {
                "global": {"limit": 20, "window": 60},
                "chat": {"limit": 10, "window": 60}
            },
            "plan": "free"
        }
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    from datetime import datetime, timezone
    from saas.database import Tenant
    from saas.billing import get_quota as get_quota_limit
    from saas.rate_limit import RATE_LIMITS, WINDOW_SIZE

    period = request.args.get("period", datetime.now(timezone.utc).strftime("%Y-%m"))

    # 获取租户套餐
    tenant = Tenant.query.get(tenant_id)
    plan = tenant.plan if tenant else "free"

    # 获取当月用量
    records = UsageRecord.query.filter_by(
        tenant_id=tenant_id, period=period
    ).all()

    metrics = {}
    for r in records:
        limit = get_quota_limit(plan, r.metric)
        metrics[r.metric] = {
            "used": r.value,
            "limit": limit,
            "remaining": max(0, limit - r.value),
        }

    # 确保关键指标都有条目
    for metric in ("llm_tokens", "api_calls"):
        if metric not in metrics:
            limit = get_quota_limit(plan, metric)
            metrics[metric] = {"used": 0, "limit": limit, "remaining": limit}

    # 限流信息
    plan_limits = RATE_LIMITS.get(plan, RATE_LIMITS["free"])
    rate_limits = {
        key: {"limit": val, "window": WINDOW_SIZE}
        for key, val in plan_limits.items()
    }

    return jsonify({
        "period": period,
        "plan": plan,
        "metrics": metrics,
        "rate_limits": rate_limits,
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
