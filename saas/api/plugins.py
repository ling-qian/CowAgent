# encoding:utf-8
"""
租户插件管理 API

GET    /api/plugins           — 列出所有插件及租户级配置
PUT    /api/plugins/<name>    — 设置租户级插件配置
DELETE /api/plugins/<name>    — 删除租户级插件配置（恢复全局默认）
"""

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.tenant_plugin import (
    get_tenant_plugin_config,
    set_tenant_plugin_config,
    list_tenant_plugins,
)
from saas.audit import audit_log

plugins_bp = Blueprint("plugins", __name__)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


@plugins_bp.route("", methods=["GET"])
def list_plugins():
    """列出所有插件及租户级配置

    合并全局插件列表和租户级覆盖配置
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    # 获取全局插件列表
    try:
        from plugins.plugin_manager import PluginManager
        pm = PluginManager()
        global_plugins = []
        for name, plugincls in pm.plugins.items():
            global_plugins.append({
                "name": name,
                "display_name": getattr(plugincls, "name", name),
                "desc": getattr(plugincls, "desc", ""),
                "enabled": plugincls.enabled,
                "priority": plugincls.priority,
                "version": getattr(plugincls, "version", "1.0"),
                "author": getattr(plugincls, "author", ""),
            })
    except Exception:
        global_plugins = []

    # 获取租户级配置
    tenant_configs = list_tenant_plugins(tenant_id)
    config_map = {c["plugin_name"]: c for c in tenant_configs}

    # 合并
    result = []
    for p in global_plugins:
        name = p["name"]
        tc = config_map.get(name)
        result.append({
            **p,
            "tenant_enabled": tc["enabled"] if tc else p["enabled"],
            "tenant_priority": tc["priority"] if tc else None,
            "has_override": tc is not None,
        })

    return jsonify({"plugins": result})


@plugins_bp.route("/<plugin_name>", methods=["PUT"])
def update_plugin_config(plugin_name):
    """设置租户级插件配置"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json() or {}
    enabled = data.get("enabled")
    priority = data.get("priority")
    config = data.get("config")

    success = set_tenant_plugin_config(
        tenant_id=tenant_id,
        plugin_name=plugin_name,
        enabled=enabled,
        priority=priority,
        config=config,
    )

    if not success:
        return jsonify({"error": "Failed to update plugin config"}), 500

    audit_log(
        action="plugin.config_update", resource_type="plugin",
        resource_id=plugin_name,
        detail=f"enabled={enabled}, priority={priority}",
        tenant_id=tenant_id, ip_address=_client_ip(),
    )

    return jsonify({"message": "Plugin config updated", "plugin": plugin_name})


@plugins_bp.route("/<plugin_name>", methods=["DELETE"])
def delete_plugin_config(plugin_name):
    """删除租户级插件配置（恢复全局默认）"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    try:
        from saas.tenant_plugin import _get_model
        from saas.database import db
        TenantPluginConfig = _get_model()

        config = TenantPluginConfig.query.filter_by(
            tenant_id=tenant_id,
            plugin_name=plugin_name.upper(),
        ).first()

        if config:
            db.session.delete(config)
            db.session.commit()

        audit_log(
            action="plugin.config_delete", resource_type="plugin",
            resource_id=plugin_name,
            detail="reverted to global default",
            tenant_id=tenant_id, ip_address=_client_ip(),
        )

        return jsonify({"message": "Plugin config reverted to global default"})
    except Exception as e:
        return jsonify({"error": f"Failed to delete plugin config: {e}"}), 500
