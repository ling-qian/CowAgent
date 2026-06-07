# encoding:utf-8
"""
SaaS 多租户支持模块

当 saas_mode=True 时启用多租户功能，包括：
- PostgreSQL 数据库模型（tenants, users, api_keys, usage_records）
- Flask 中间件（API Key 认证 + tenant_id 注入）
- 租户级配置加载

注意：此模块使用延迟导入，避免在 saas_mode=False 或依赖未安装时触发导入错误。
"""


def __getattr__(name):
    """延迟导入，仅在访问时才加载"""
    if name == "db":
        from saas.database import db
        return db
    if name == "init_db":
        from saas.database import init_db
        return init_db
    if name == "TenantMiddleware":
        from saas.middleware import TenantMiddleware
        return TenantMiddleware
    if name == "get_tenant_config":
        from saas.config_loader import get_tenant_config
        return get_tenant_config
    raise AttributeError(f"module 'saas' has no attribute {name}")
