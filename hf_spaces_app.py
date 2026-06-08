#!/usr/bin/env python3
"""
Hugging Face Spaces 入口脚本

HF Spaces 只暴露 7860 端口，此脚本：
1. 启动 SaaS Flask API 在 7860 端口
2. 在后台线程启动 CowAgent 主服务（web channel on 8080）
"""

import os
import sys
import threading
import signal

# 设置 HF Spaces 端口
os.environ["SAAS_API_PORT"] = "7860"
os.environ.setdefault("SAAS_MODE", "true")

# 确保工作目录
WORKSPACE = os.environ.get("COW_WORKSPACE", "/data")
os.makedirs(WORKSPACE, exist_ok=True)


def start_cowagent_background():
    """在后台线程启动 CowAgent 主服务"""
    try:
        from channel import channel_factory
        from common import const
        from common.log import logger
        from config import load_config, conf
        from plugins import *  # noqa: F401 F403

        load_config()

        # 启动 web channel（控制台）
        channel_type = conf().get("channel_type", "web")
        if channel_type == "web" or not channel_type:
            channel = channel_factory.create_channel("web")
            channel.startup()
            logger.info("[HF] CowAgent web console started on port 8080")
    except Exception as e:
        print(f"[HF] CowAgent background service error: {e}", file=sys.stderr)


def main():
    """启动 SaaS API 服务"""
    from flask import Flask, jsonify
    from flask_cors import CORS
    from saas.database import init_db
    from saas.middleware import TenantMiddleware
    from saas.rate_limit import RateLimitMiddleware
    from saas.api import tenants_bp, keys_bp, usage_bp, health_bp, billing_bp, sso_bp
    from saas.api.audit import audit_bp
    from saas.api.webhooks import webhooks_bp
    from saas.api.plugins import plugins_bp
    from saas.api.gdpr import gdpr_bp
    from saas.api.im_channels import bp as im_channels_bp

    saas_app = Flask(__name__)
    CORS(saas_app)

    # Swagger
    try:
        from flasgger import Swagger
        from saas.api.docs import SWAGGER_CONFIG, SWAGGER_TEMPLATE
        Swagger(saas_app, config=SWAGGER_CONFIG, template=SWAGGER_TEMPLATE)
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("[HF] ERROR: DATABASE_URL not set!", file=sys.stderr)
        sys.exit(1)

    # 初始化数据库
    init_db(app=saas_app, database_uri=database_url)

    # 中间件
    TenantMiddleware(saas_app)
    RateLimitMiddleware(saas_app)

    # API 蓝图
    saas_app.register_blueprint(tenants_bp, url_prefix="/api/tenants")
    saas_app.register_blueprint(keys_bp, url_prefix="/api/keys")
    saas_app.register_blueprint(usage_bp, url_prefix="/api/usage")
    saas_app.register_blueprint(billing_bp, url_prefix="/api/billing")
    saas_app.register_blueprint(sso_bp, url_prefix="/api/auth")
    saas_app.register_blueprint(audit_bp, url_prefix="/api/audit")
    saas_app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
    saas_app.register_blueprint(plugins_bp, url_prefix="/api/plugins")
    saas_app.register_blueprint(gdpr_bp, url_prefix="/api/gdpr")
    saas_app.register_blueprint(im_channels_bp, url_prefix="/api/im-channels")
    saas_app.register_blueprint(health_bp)

    # Prometheus
    try:
        from saas.metrics import metrics_middleware, create_metrics_blueprint
        saas_app.register_blueprint(create_metrics_blueprint())
        metrics_middleware(saas_app)
    except Exception:
        pass

    # 根路径重定向到 health
    @saas_app.route("/")
    def index():
        return jsonify({
            "service": "CowAgent SaaS",
            "status": "running",
            "docs": "/apidocs",
            "health": "/health",
        })

    # 后台启动 CowAgent 主服务
    bg = threading.Thread(target=start_cowagent_background, daemon=True)
    bg.start()

    port = int(os.environ.get("PORT", 7860))
    print(f"[HF] Starting SaaS API on port {port}")
    saas_app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
