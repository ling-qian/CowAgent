# encoding:utf-8
"""
计费 API

GET /api/billing/summary   — 获取当月账单摘要
GET /api/billing/quotas    — 获取当前套餐配额
GET /api/billing/plans     — 获取所有可用套餐
POST /api/billing/check    — 检查某项配额是否充足
"""

from flask import Blueprint, jsonify

from common.tenant import current_tenant_id
from saas.database import db, Tenant
from saas.billing import PLANS, get_plan, check_quota, check_api_key_limit, get_billing_summary

billing_bp = Blueprint("billing", __name__)


@billing_bp.route("/summary", methods=["GET"])
def billing_summary():
    """获取当月账单摘要"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    summary = get_billing_summary(tenant_id)
    if not summary:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(summary)


@billing_bp.route("/quotas", methods=["GET"])
def get_quotas():
    """获取当前套餐配额及已用量"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    plan = get_plan(tenant.plan)

    # 查询当月已用量
    from saas.database import UsageRecord
    from datetime import datetime, timezone
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")

    records = UsageRecord.query.filter_by(
        tenant_id=tenant_id,
        period=current_period,
    ).all()

    usage = {}
    for r in records:
        usage[r.metric] = usage.get(r.metric, 0) + r.value

    # 组装配额信息
    quotas = {}
    for metric, limit in plan["quotas"].items():
        used = usage.get(metric, 0)
        remaining = -1 if limit == -1 else max(0, limit - used)
        quotas[metric] = {
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "exceeded": limit > 0 and used >= limit,
        }

    # API Key 数量
    allowed, current_keys, key_limit = check_api_key_limit(tenant_id)
    quotas["api_keys"] = {
        "limit": key_limit,
        "used": current_keys,
        "remaining": -1 if key_limit == -1 else max(0, key_limit - current_keys),
        "exceeded": not allowed,
    }

    return jsonify({
        "plan": tenant.plan,
        "plan_name": plan["name"],
        "quotas": quotas,
    })


@billing_bp.route("/plans", methods=["GET"])
def list_plans():
    """获取所有可用套餐"""
    return jsonify({
        "plans": {
            plan_id: {
                "name": plan["name"],
                "price": plan["price"],
                "quotas": plan["quotas"],
            }
            for plan_id, plan in PLANS.items()
        }
    })


@billing_bp.route("/check", methods=["POST"])
def check_quota_endpoint():
    """检查某项配额是否充足

    Body: {"metric": "llm_tokens", "increment": 1000}
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    metric = data.get("metric", "api_calls")
    increment = data.get("increment", 1)

    allowed, remaining, limit = check_quota(tenant_id, metric, increment)

    return jsonify({
        "metric": metric,
        "increment": increment,
        "allowed": allowed,
        "remaining": remaining,
        "limit": limit,
    })


# 延迟导入避免循环依赖
from flask import request
