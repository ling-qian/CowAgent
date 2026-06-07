# encoding:utf-8
"""
计费系统 — 套餐定义、配额检查、账单生成

套餐层级：
- free: 免费版，基础配额
- pro: 专业版，提升配额
- enterprise: 企业版，无限配额

配额维度：
- llm_tokens: LLM Token 用量（按月）
- api_calls: API 调用次数（按月）
- memory_mb: 记忆存储空间（MB）
- api_keys: API Key 数量上限
"""

# ---------------------------------------------------------------------------
# 套餐定义
# ---------------------------------------------------------------------------

PLANS = {
    "free": {
        "name": "免费版",
        "price": 0,
        "quotas": {
            "llm_tokens": 100_000,      # 10万 tokens/月
            "api_calls": 1_000,          # 1000 次/月
            "memory_mb": 50,             # 50MB
            "api_keys": 2,               # 最多2个 Key
        },
    },
    "pro": {
        "name": "专业版",
        "price": 99,
        "quotas": {
            "llm_tokens": 2_000_000,     # 200万 tokens/月
            "api_calls": 50_000,          # 5万次/月
            "memory_mb": 500,             # 500MB
            "api_keys": 10,              # 最多10个 Key
        },
    },
    "enterprise": {
        "name": "企业版",
        "price": 499,
        "quotas": {
            "llm_tokens": -1,            # 无限
            "api_calls": -1,             # 无限
            "memory_mb": -1,             # 无限
            "api_keys": -1,              # 无限
        },
    },
}


def get_plan(plan_id):
    """获取套餐定义，未知套餐降级为 free"""
    return PLANS.get(plan_id, PLANS["free"])


def get_quota(plan_id, metric):
    """获取指定套餐的某项配额，-1 表示无限"""
    plan = get_plan(plan_id)
    return plan["quotas"].get(metric, 0)


# ---------------------------------------------------------------------------
# 配额检查
# ---------------------------------------------------------------------------

def check_quota(tenant_id, metric, increment=1):
    """检查租户是否还有配额余额

    Args:
        tenant_id: 租户 ID
        metric: 配额维度（llm_tokens / api_calls）
        increment: 本次增量

    Returns:
        (allowed: bool, remaining: int, limit: int)
        remaining=-1 表示无限配额
    """
    from saas.database import db, Tenant, UsageRecord

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return False, 0, 0

    quota_limit = get_quota(tenant.plan, metric)

    # -1 表示无限配额
    if quota_limit == -1:
        return True, -1, -1

    # 查询当月已用量
    from datetime import datetime, timezone
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")

    record = UsageRecord.query.filter_by(
        tenant_id=tenant_id,
        metric=metric,
        period=current_period,
    ).first()

    used = record.value if record else 0
    remaining = max(0, quota_limit - used)

    if used + increment > quota_limit:
        # 配额超限，发送告警邮件
        try:
            usage_pct = (used + increment) / quota_limit if quota_limit > 0 else 1.0
            if usage_pct >= 0.9:  # 90% 以上才发邮件
                from saas.email import send_quota_alert
                send_quota_alert(
                    tenant_id=tenant_id,
                    tenant_name=tenant.name,
                    usage_pct=usage_pct,
                    plan=tenant.plan,
                    monthly_calls=used + increment,
                    monthly_limit=quota_limit,
                )
        except Exception:
            pass  # 邮件发送失败不影响配额检查
        return False, remaining, quota_limit

    # 配额使用超过 80% 时发送预警
    try:
        usage_pct = (used + increment) / quota_limit if quota_limit > 0 else 0
        if 0.8 <= usage_pct < 0.9:
            from saas.email import send_quota_alert
            send_quota_alert(
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                usage_pct=usage_pct,
                plan=tenant.plan,
                monthly_calls=used + increment,
                monthly_limit=quota_limit,
            )
    except Exception:
        pass

    return True, remaining, quota_limit


def check_api_key_limit(tenant_id):
    """检查租户是否还能创建 API Key

    Returns:
        (allowed: bool, current: int, limit: int)
    """
    from saas.database import db, Tenant, ApiKey

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return False, 0, 0

    quota_limit = get_quota(tenant.plan, "api_keys")
    if quota_limit == -1:
        current = ApiKey.query.filter_by(tenant_id=tenant_id, is_active=True).count()
        return True, current, -1

    current = ApiKey.query.filter_by(tenant_id=tenant_id, is_active=True).count()
    if current >= quota_limit:
        return False, current, quota_limit

    return True, current, quota_limit


# ---------------------------------------------------------------------------
# 账单生成
# ---------------------------------------------------------------------------

def get_billing_summary(tenant_id):
    """获取租户当月账单摘要

    Returns:
        {
            "plan": "pro",
            "plan_name": "专业版",
            "period": "2026-06",
            "usage": {"llm_tokens": 15000, "api_calls": 230},
            "quotas": {"llm_tokens": 2000000, "api_calls": 50000},
            "overage": {},  # 超额部分（企业版不会有）
            "estimated_cost": 99,  # 套餐价格
        }
    """
    from saas.database import db, Tenant, UsageRecord
    from datetime import datetime, timezone

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return None

    plan = get_plan(tenant.plan)
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")

    # 查询当月所有用量
    records = UsageRecord.query.filter_by(
        tenant_id=tenant_id,
        period=current_period,
    ).all()

    usage = {}
    for r in records:
        usage[r.metric] = usage.get(r.metric, 0) + r.value

    # 计算超额
    overage = {}
    for metric, used in usage.items():
        limit = plan["quotas"].get(metric, 0)
        if limit > 0 and used > limit:
            overage[metric] = used - limit

    return {
        "plan": tenant.plan,
        "plan_name": plan["name"],
        "period": current_period,
        "usage": usage,
        "quotas": plan["quotas"],
        "overage": overage,
        "estimated_cost": plan["price"],
    }
