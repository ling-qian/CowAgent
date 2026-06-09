# encoding:utf-8
"""
租户管理 API

POST   /api/tenants/register     — 注册新租户
GET    /api/tenants/me           — 获取当前租户信息
PUT    /api/tenants/me           — 更新当前租户信息
GET    /api/tenants/me/stats     — 获取当前租户统计信息
"""

from flask import Blueprint, request, jsonify
import re

from common.tenant import current_tenant_id
from saas.database import db, Tenant, User, ApiKey, UsageRecord
from saas.audit import audit_log

tenants_bp = Blueprint("tenants", __name__)

# 邮箱格式验证
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
# 危险字符检测（防 SQL 注入）
_DANGEROUS_RE = re.compile(r"['\";\\]|(--)|(/\*)|(\*/)", re.IGNORECASE)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


@tenants_bp.route("/register", methods=["POST"])
def register_tenant():
    """注册新租户"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip()
    email = data.get("email", "").strip()
    plan = data.get("plan", "free")

    if not name or not slug or not email:
        return jsonify({"error": "name, slug, and email are required"}), 400

    # 验证邮箱格式
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email format"}), 400

    # 检测危险字符（防 SQL 注入）
    for field_name, field_val in [("name", name), ("slug", slug), ("email", email)]:
        if _DANGEROUS_RE.search(field_val):
            return jsonify({"error": f"Invalid characters in {field_name}"}), 400

    # 检查 slug 和 email 是否已存在
    if Tenant.query.filter_by(slug=slug).first():
        return jsonify({"error": f"Slug '{slug}' already taken"}), 409

    # 创建租户
    tenant = Tenant(name=name, slug=slug, plan=plan)
    db.session.add(tenant)
    db.session.flush()  # 获取 tenant.id

    # 创建 owner 用户
    user = User(tenant_id=tenant.id, email=email, role="owner")
    db.session.add(user)

    # 自动创建第一个 API Key
    from saas.middleware import generate_api_key
    raw_key, key_hash, key_prefix = generate_api_key()
    from saas.database import ApiKey
    api_key = ApiKey(
        tenant_id=tenant.id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="Default Key",
    )
    db.session.add(api_key)
    db.session.commit()

    audit_log(
        action="tenant.register", resource_type="tenant", resource_id=tenant.id,
        detail=f"slug={slug}, plan={plan}", tenant_id=tenant.id,
        ip_address=_client_ip(),
    )

    return jsonify({
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
        },
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
        },
        "api_key": raw_key,  # 只在创建时返回一次
    }), 201


@tenants_bp.route("/me", methods=["GET"])
def get_tenant():
    """获取当前租户信息"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify({
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "plan": tenant.plan,
        "is_active": tenant.is_active,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    })


@tenants_bp.route("/me", methods=["PUT"])
def update_tenant():
    """更新当前租户信息"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    if "name" in data:
        tenant.name = data["name"]
    if "config_json" in data:
        import json
        tenant.config_json = json.dumps(data["config_json"]) if isinstance(data["config_json"], dict) else data["config_json"]
        # 清除租户配置缓存
        from saas.config_loader import invalidate_tenant_config
        invalidate_tenant_config(tenant_id)

    db.session.commit()

    audit_log(
        action="tenant.update", resource_type="tenant", resource_id=tenant_id,
        detail=f"fields={list(data.keys())}", tenant_id=tenant_id,
        ip_address=_client_ip(),
    )

    return jsonify({
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "plan": tenant.plan,
    })


@tenants_bp.route("/me/stats", methods=["GET"])
def get_tenant_stats():
    """获取当前租户的统计信息"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    users_count = User.query.filter_by(tenant_id=tenant_id).count()
    active_keys = ApiKey.query.filter_by(tenant_id=tenant_id, is_active=True).count()

    # 本月用量
    from datetime import datetime, timezone
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")
    usage_records = UsageRecord.query.filter_by(
        tenant_id=tenant_id, period=current_period
    ).all()
    usage_summary = {}
    for r in usage_records:
        usage_summary[r.metric] = usage_summary.get(r.metric, 0) + r.value

    # Memory 统计（通过 SQLAlchemy 模型查询）
    memory_stats = {"chunks": 0, "files": 0, "embedded": 0}
    try:
        from saas.database import MemoryChunkRecord, MemoryFileRecord
        memory_stats["chunks"] = MemoryChunkRecord.query.filter_by(tenant_id=tenant_id).count()
        memory_stats["files"] = MemoryFileRecord.query.filter_by(tenant_id=tenant_id).count()
        # embedded count needs raw SQL since vector column isn't in SQLAlchemy model
        try:
            from sqlalchemy import text
            result = db.session.execute(text(
                "SELECT COUNT(*) as cnt FROM memory_chunks WHERE tenant_id = :tid AND embedding IS NOT NULL"
            ), {"tid": tenant_id}).fetchone()
            memory_stats["embedded"] = result.cnt if result else 0
        except Exception:
            pass
    except Exception:
        pass

    return jsonify({
        "tenant_id": tenant_id,
        "plan": tenant.plan,
        "users": users_count,
        "active_api_keys": active_keys,
        "usage_this_month": usage_summary,
        "memory": memory_stats,
    })
