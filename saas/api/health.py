# encoding:utf-8
"""
健康检查 API

GET /health — 服务健康检查（无需认证）
"""

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health_check():
    """服务健康检查"""
    checks = {"status": "ok", "saas_mode": True}

    # 检查 PostgreSQL 连接
    try:
        from saas.database import db
        from sqlalchemy import text
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        checks["status"] = "degraded"

    # 检查 pgvector 扩展
    try:
        from saas.database import db
        from sqlalchemy import text
        result = db.session.execute(text(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        )).fetchone()
        checks["pgvector"] = "ok" if result else "not_installed"
    except Exception:
        checks["pgvector"] = "unknown"

    status_code = 200 if checks["status"] == "ok" else 503
    return jsonify(checks), status_code
