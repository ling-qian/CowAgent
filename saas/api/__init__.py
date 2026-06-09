# encoding:utf-8
"""SaaS 管理 API 路由"""

from saas.api.tenants import tenants_bp
from saas.api.keys import keys_bp
from saas.api.usage import usage_bp
from saas.api.health import health_bp
from saas.api.billing import billing_bp
from saas.api.sso import sso_bp
from saas.api.audit import audit_bp
from saas.api.webhooks import webhooks_bp
from saas.api.gdpr import gdpr_bp
from saas.api.plugins import plugins_bp
from saas.api.im_channels import bp as im_channels_bp
from saas.api.chat import chat_bp

__all__ = [
    "tenants_bp", "keys_bp", "usage_bp", "health_bp", "billing_bp", "sso_bp",
    "audit_bp", "webhooks_bp", "gdpr_bp", "plugins_bp", "im_channels_bp",
    "chat_bp",
]
