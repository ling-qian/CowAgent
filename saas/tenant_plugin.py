# encoding:utf-8
"""
租户级插件隔离

在 SaaS 模式下，不同租户可以启用/禁用不同的插件。
核心思路：
- PluginManager 仍然是全局单例（所有租户共享已加载的插件类）
- TenantPluginConfig 表记录每个租户的插件启用/禁用状态
- emit_event 时检查当前租户的插件配置，跳过未启用的插件
- saas_mode=False 时行为与原版完全一致
"""

import uuid
from datetime import datetime, timezone

from common.log import logger
from common.tenant import current_tenant_id


# ---------------------------------------------------------------------------
# 租户插件配置模型（在 database.py 中注册）
# ---------------------------------------------------------------------------

def _ensure_tenant_plugin_model():
    """确保 TenantPluginConfig 模型已注册（已在 database.py 中定义）"""
    pass


def _get_model():
    """获取 TenantPluginConfig 模型类"""
    from saas.database import TenantPluginConfig
    return TenantPluginConfig


# ---------------------------------------------------------------------------
# 租户插件配置 API
# ---------------------------------------------------------------------------

def get_tenant_plugin_config(tenant_id: str, plugin_name: str) -> dict:
    """获取租户级插件配置

    Returns:
        {"enabled": bool, "priority": int|None, "config": dict|None}
        如果没有租户级配置，返回 None
    """
    try:
        TenantPluginConfig = _get_model()
        config = TenantPluginConfig.query.filter_by(
            tenant_id=tenant_id,
            plugin_name=plugin_name.upper(),
        ).first()
        if config:
            import json
            return {
                "enabled": config.enabled,
                "priority": config.priority,
                "config": json.loads(config.config_json) if config.config_json else None,
            }
        return None
    except Exception as e:
        logger.debug(f"[TenantPlugin] get config failed: {e}")
        return None


def set_tenant_plugin_config(tenant_id: str, plugin_name: str, enabled: bool = None,
                              priority: int = None, config: dict = None) -> bool:
    """设置租户级插件配置"""
    try:
        TenantPluginConfig = _get_model()
        from saas.database import db

        existing = TenantPluginConfig.query.filter_by(
            tenant_id=tenant_id,
            plugin_name=plugin_name.upper(),
        ).first()

        if existing:
            if enabled is not None:
                existing.enabled = enabled
            if priority is not None:
                existing.priority = priority
            if config is not None:
                import json
                existing.config_json = json.dumps(config, ensure_ascii=False)
            existing.updated_at = datetime.now(timezone.utc)
        else:
            import json
            existing = TenantPluginConfig(
                tenant_id=tenant_id,
                plugin_name=plugin_name.upper(),
                enabled=enabled if enabled is not None else True,
                priority=priority,
                config_json=json.dumps(config, ensure_ascii=False) if config else None,
            )
            db.session.add(existing)

        db.session.commit()
        return True
    except Exception as e:
        logger.warning(f"[TenantPlugin] set config failed: {e}")
        return False


def is_plugin_enabled_for_tenant(tenant_id: str, plugin_name: str, default_enabled: bool = True) -> bool:
    """检查插件是否对指定租户启用

    Args:
        tenant_id: 租户 ID
        plugin_name: 插件名称（大写）
        default_enabled: 全局默认启用状态

    Returns:
        插件是否对该租户启用
    """
    from config import conf
    if not conf().get("saas_mode", False):
        return default_enabled

    config = get_tenant_plugin_config(tenant_id, plugin_name)
    if config is not None:
        return config["enabled"]
    return default_enabled


def list_tenant_plugins(tenant_id: str) -> list:
    """列出租户的所有插件配置

    Returns:
        [{"plugin_name": str, "enabled": bool, "priority": int|None, "config": dict|None}]
    """
    try:
        TenantPluginConfig = _get_model()
        configs = TenantPluginConfig.query.filter_by(tenant_id=tenant_id).all()
        import json
        return [
            {
                "plugin_name": c.plugin_name,
                "enabled": c.enabled,
                "priority": c.priority,
                "config": json.loads(c.config_json) if c.config_json else None,
            }
            for c in configs
        ]
    except Exception as e:
        logger.debug(f"[TenantPlugin] list failed: {e}")
        return []


# ---------------------------------------------------------------------------
# PluginManager 集成 — emit_event 钩子
# ---------------------------------------------------------------------------

def should_emit_plugin(plugin_name: str) -> bool:
    """在 PluginManager.emit_event 中调用，判断是否应该触发该插件

    用法：在 plugin_manager.py 的 emit_event 方法中，
    在调用 instance.handlers 之前调用此函数检查。
    """
    from config import conf
    if not conf().get("saas_mode", False):
        return True

    tenant_id = current_tenant_id()
    if not tenant_id:
        return True  # 无租户上下文时放行

    return is_plugin_enabled_for_tenant(tenant_id, plugin_name)
