#!/usr/bin/env python3
"""
Hugging Face Spaces 入口脚本

HF Spaces 只暴露 7860 端口，此脚本：
1. 启动 SaaS Flask API 在 7860 端口
2. 在后台线程启动 CowAgent 主服务（web channel on 8080）

启动方式：
  - 开发：python hf_spaces_app.py
  - 生产：gunicorn hf_spaces_app:saas_app --bind 0.0.0.0:7860 --workers 2
"""

import os
import sys
import traceback

# 设置 HF Spaces 端口
os.environ.setdefault("SAAS_API_PORT", "7860")
os.environ.setdefault("SAAS_MODE", "true")

# 确保工作目录
WORKSPACE = os.environ.get("COW_WORKSPACE", "/data")
os.makedirs(WORKSPACE, exist_ok=True)

# 修复 Neon 连接串：channel_binding 不被 psycopg2 支持
_db_url = os.environ.get("DATABASE_URL", "")
if "channel_binding=" in _db_url:
    import re
    _db_url = re.sub(r'[&?]channel_binding=[^&]*', '', _db_url)
    os.environ["DATABASE_URL"] = _db_url


def _create_app():
    """创建并配置 Flask 应用"""
    from flask import Flask, jsonify, send_from_directory
    from flask_cors import CORS

    app = Flask(__name__)
    CORS(app)

    # 保存全局引用供 AgentFactory 等模块使用
    import saas as _saas_mod
    _saas_mod.set_flask_app(app)

    # 全局 JSON 错误处理器
    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(500)
    def json_error_handler(error):
        return jsonify({"error": error.description if hasattr(error, 'description') else str(error)}), error.code if hasattr(error, 'code') else 500

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("[HF] WARNING: DATABASE_URL not set — API will not work", file=sys.stderr)

    # 初始化数据库
    _db_error = None
    if database_url:
        try:
            from saas.database import init_db
            init_db(app=app, database_uri=database_url)
            print("[HF] Database initialized OK")
        except Exception as e:
            _db_error = str(e)
            print(f"[HF] Database init error: {e}", file=sys.stderr)
            traceback.print_exc()

    # 中间件
    try:
        from saas.middleware import TenantMiddleware
        from saas.rate_limit import RateLimitMiddleware
        TenantMiddleware(app)
        RateLimitMiddleware(app)
    except Exception as e:
        print(f"[HF] Middleware error: {e}", file=sys.stderr)

    # API 蓝图
    _blueprints_ok = True
    try:
        from saas.api import tenants_bp, keys_bp, usage_bp, health_bp, billing_bp, sso_bp
        from saas.api.audit import audit_bp
        from saas.api.webhooks import webhooks_bp
        from saas.api.plugins import plugins_bp
        from saas.api.gdpr import gdpr_bp
        from saas.api.im_channels import bp as im_channels_bp
        from saas.api.chat import chat_bp
        from saas.api.agent import agent_bp
        from saas.api.knowledge import knowledge_bp
        from saas.api.tools import tools_bp

        app.register_blueprint(tenants_bp, url_prefix="/api/tenants")
        app.register_blueprint(keys_bp, url_prefix="/api/keys")
        app.register_blueprint(usage_bp, url_prefix="/api/usage")
        app.register_blueprint(billing_bp, url_prefix="/api/billing")
        app.register_blueprint(sso_bp, url_prefix="/api/auth")
        app.register_blueprint(audit_bp, url_prefix="/api/audit")
        app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
        app.register_blueprint(plugins_bp, url_prefix="/api/plugins")
        app.register_blueprint(gdpr_bp, url_prefix="/api/gdpr")
        app.register_blueprint(im_channels_bp, url_prefix="/api/im-channels")
        app.register_blueprint(chat_bp, url_prefix="/api/chat")
        app.register_blueprint(health_bp)
        # Agent 配置蓝图
        app.register_blueprint(agent_bp, url_prefix="/api/agent")
        app.register_blueprint(knowledge_bp, url_prefix="/api/agent/knowledge")
        # 自定义工具蓝图
        app.register_blueprint(tools_bp, url_prefix="/api/agent/tools")
        print("[HF] All blueprints registered OK (including agent/knowledge/tools)")
    except Exception as e:
        _blueprints_ok = False
        print(f"[HF] Blueprint registration error: {e}", file=sys.stderr)
        traceback.print_exc()

    # Swagger
    try:
        from flasgger import Swagger
        from saas.api.docs import SWAGGER_CONFIG, SWAGGER_TEMPLATE
        Swagger(app, config=SWAGGER_CONFIG, template=SWAGGER_TEMPLATE)
    except Exception:
        pass

    # Prometheus
    try:
        from saas.metrics import metrics_middleware, create_metrics_blueprint
        app.register_blueprint(create_metrics_blueprint())
        metrics_middleware(app)
    except Exception:
        pass

    # ---- 前端静态页面 ----
    _web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel", "web")

    @app.route("/chat")
    @app.route("/agent-builder")
    def serve_chat_html():
        """Agent Builder / Chat 页面"""
        return send_from_directory(_web_dir, "chat.html")

    @app.route("/static/<path:filename>")
    def serve_static(filename):
        """静态资源（JS/CSS/图片）"""
        return send_from_directory(os.path.join(_web_dir, "static"), filename)

    # 根路径 — HTML 欢迎页
    @app.route("/")
    def index():
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CowAgent SaaS - AI Agent 多租户平台</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  .container { max-width: 720px; padding: 40px 24px; text-align: center; }
  h1 { font-size: 2.5rem; font-weight: 700; margin-bottom: 8px; background: linear-gradient(135deg, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .subtitle { font-size: 1.1rem; color: #94a3b8; margin-bottom: 40px; }
  .status { display: inline-flex; align-items: center; gap: 8px; background: #1e293b; padding: 8px 16px; border-radius: 999px; font-size: 0.85rem; margin-bottom: 32px; }
  .status .dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
  .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 40px; }
  .link-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; text-decoration: none; color: #e2e8f0; transition: all 0.2s; }
  .link-card:hover { border-color: #38bdf8; transform: translateY(-2px); }
  .link-card .icon { font-size: 1.5rem; margin-bottom: 8px; }
  .link-card .title { font-weight: 600; margin-bottom: 4px; }
  .link-card .desc { font-size: 0.8rem; color: #94a3b8; }
  .quick-start { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; text-align: left; margin-bottom: 32px; }
  .quick-start h3 { margin-bottom: 16px; color: #38bdf8; font-size: 1rem; }
  .quick-start pre { background: #0f172a; border-radius: 8px; padding: 16px; overflow-x: auto; font-size: 0.8rem; line-height: 1.6; color: #a5f3fc; }
  .quick-start pre .comment { color: #64748b; }
  .footer { font-size: 0.75rem; color: #475569; }
  .footer a { color: #64748b; text-decoration: none; }
  .footer a:hover { color: #94a3b8; }
</style>
</head>
<body>
<div class="container">
  <h1>CowAgent SaaS</h1>
  <p class="subtitle">AI Agent 多租户企业级平台</p>
  <div class="status"><span class="dot"></span> 服务运行中</div>
  <div class="links">
    <a class="link-card" href="/chat">
      <div class="icon">🤖</div>
      <div class="title">Agent Builder</div>
      <div class="desc">创建和配置你的 AI Agent</div>
    </a>
    <a class="link-card" href="/chat">
      <div class="icon">🎛️</div>
      <div class="title">管理控制台</div>
      <div class="desc">租户管理与 Agent 配置</div>
    </a>
    <a class="link-card" href="/apidocs">
      <div class="icon">📖</div>
      <div class="title">API 文档</div>
      <div class="desc">Swagger 交互式文档</div>
    </a>
    <a class="link-card" href="/health">
      <div class="icon">💚</div>
      <div class="title">健康检查</div>
      <div class="desc">服务状态与数据库连接</div>
    </a>
  </div>
  <div class="quick-start">
    <h3>快速开始</h3>
    <pre><span class="comment"># 1. 注册租户</span>
curl -X POST /api/tenants/register \\
  -H "Content-Type: application/json" \\
  -H "X-Admin-Secret: YOUR_SECRET" \\
  -d '{"name":"My Team","slug":"my-team","plan":"free","email":"me@example.com"}'

<span class="comment"># 2. 使用返回的 API Key 调用接口</span>
curl /api/tenants/me -H "X-API-Key: sk-xxx"

<span class="comment"># 3. 查看配额</span>
curl /api/billing/quotas -H "X-API-Key: sk-xxx"</pre>
  </div>
  <div class="footer">
    Powered by <a href="https://github.com/zhayujie/CowAgent">CowAgent</a> ·
    PostgreSQL + pgvector ·
    <a href="https://huggingface.co/spaces">HF Spaces</a>
  </div>
</div>
</body>
</html>"""

    # 调试路由：查看启动状态
    @app.route("/debug")
    def debug_info():
        rules = [str(rule) for rule in app.url_map.iter_rules()]
        return jsonify({
            "db_error": _db_error,
            "blueprints_ok": _blueprints_ok,
            "database_url_set": bool(database_url),
            "routes_count": len(rules),
            "routes": sorted(rules),
        })

    # 后台启动 CowAgent 主服务
    try:
        import threading
        def start_bg():
            try:
                from channel import channel_factory
                from config import load_config, conf
                import plugins
                load_config()
                channel = channel_factory.create_channel("web")
                channel.startup()
            except Exception as e:
                print(f"[HF] Background service: {e}", file=sys.stderr)

        bg = threading.Thread(target=start_bg, daemon=True)
        bg.start()
    except Exception:
        pass

    return app


# 模块级 Flask 实例 — gunicorn 通过 hf_spaces_app:saas_app 导入
saas_app = _create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    print(f"[HF] Starting SaaS API on port {port}")
    saas_app.run(host="0.0.0.0", port=port, debug=False)
