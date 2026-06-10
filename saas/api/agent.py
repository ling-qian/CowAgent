# encoding:utf-8
"""
Agent 配置管理 API

GET    /api/agent/config          — 获取当前租户的 Agent 配置
PUT    /api/agent/config          — 更新 Agent 配置（支持部分更新）
POST   /api/agent/config/reset    — 重置为默认配置
GET    /api/agent/models          — 获取可用模型列表
GET    /api/agent/stats           — 获取 AgentFactory 统计信息
"""

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.database import db, AgentConfig
from saas.agent_factory import AgentFactory, get_or_create_default_config
from saas.audit import audit_log

agent_bp = Blueprint("agent", __name__)


def _serialize_config(config: AgentConfig) -> dict:
    """将 AgentConfig ORM 对象序列化为 API 响应字典"""
    return {
        "id": config.id,
        "tenant_id": config.tenant_id,
        "name": config.name,
        "avatar_url": config.avatar_url,
        "description": config.description,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "api_key": "***" if config.api_key else "",  # 脱敏
        "api_base": config.api_base,
        "plugins": config.get_plugins(),
        "tools": config.get_tools(),
        "knowledge_ids": config.get_knowledge_ids(),
        "max_steps": config.max_steps,
        "temperature": config.temperature,
        "enable_thinking": config.enable_thinking,
        "reasoning_effort": config.reasoning_effort,
        "config_version": config.config_version,
        "is_active": config.is_active,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


# 可更新的字段白名单
_UPDATABLE_FIELDS = {
    "name", "avatar_url", "description",
    "system_prompt", "model", "api_key", "api_base",
    "max_steps", "temperature", "enable_thinking", "reasoning_effort",
    "is_active",
}


@agent_bp.route("/config", methods=["GET"])
def get_config():
    """获取当前租户的 Agent 配置

    如果租户还没有配置，自动创建默认配置。
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    config = get_or_create_default_config(tenant_id)
    return jsonify(_serialize_config(config))


@agent_bp.route("/config", methods=["PUT"])
def update_config():
    """更新 Agent 配置（支持部分更新）

    每次更新自动递增 config_version，触发 AgentFactory 重建 Agent。
    plugins/tools/knowledge_ids 字段需传完整数组（整体替换）。
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        config = get_or_create_default_config(tenant_id)

    # 应用更新
    changed = False
    for field in _UPDATABLE_FIELDS:
        if field in data:
            setattr(config, field, data[field])
            changed = True

    # JSON 数组字段（整体替换）
    if "plugins" in data:
        if not isinstance(data["plugins"], list):
            return jsonify({"error": "plugins must be an array"}), 400
        config.set_plugins(data["plugins"])
        changed = True

    if "tools" in data:
        if not isinstance(data["tools"], list):
            return jsonify({"error": "tools must be an array"}), 400
        config.set_tools(data["tools"])
        changed = True

    if "knowledge_ids" in data:
        if not isinstance(data["knowledge_ids"], list):
            return jsonify({"error": "knowledge_ids must be an array"}), 400
        config.set_knowledge_ids(data["knowledge_ids"])
        changed = True

    if not changed:
        return jsonify({"error": "No valid fields to update"}), 400

    # 递增版本号 → 触发 AgentFactory 重建
    config.config_version += 1

    # 清除 AgentFactory 缓存，下次请求时重建
    AgentFactory.destroy(tenant_id)

    db.session.commit()

    # 审计日志
    audit_log(
        tenant_id=tenant_id,
        action="agent_config_update",
        resource_type="agent_config",
        resource_id=config.id,
        detail=f"Updated fields: {list(data.keys())}, version={config.config_version}",
    )

    return jsonify(_serialize_config(config))


@agent_bp.route("/config/reset", methods=["POST"])
def reset_config():
    """重置 Agent 配置为默认值"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        # 没有配置，创建默认配置即可
        config = get_or_create_default_config(tenant_id)
        return jsonify(_serialize_config(config))

    # 重置为默认值
    config.name = "My Agent"
    config.avatar_url = None
    config.description = None
    config.system_prompt = "You are a helpful assistant."
    config.model = AgentFactory._get_platform_model()
    config.api_key = ""
    config.api_base = ""
    config.set_plugins([])
    config.set_tools([])
    config.set_knowledge_ids([])
    config.max_steps = 15
    config.temperature = 0.7
    config.enable_thinking = True
    config.reasoning_effort = "high"
    config.is_active = True
    config.config_version += 1

    # 清除缓存
    AgentFactory.destroy(tenant_id)

    db.session.commit()

    audit_log(
        tenant_id=tenant_id,
        action="agent_config_reset",
        resource_type="agent_config",
        resource_id=config.id,
        detail=f"Reset to defaults, version={config.config_version}",
    )

    return jsonify(_serialize_config(config))


@agent_bp.route("/models", methods=["GET"])
def get_models():
    """获取可用模型列表"""
    from saas.chat_engine import get_available_models
    models = get_available_models()
    return jsonify({"models": models})


@agent_bp.route("/stats", methods=["GET"])
def get_stats():
    """获取 AgentFactory 统计信息（管理员调试用）"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    stats = AgentFactory.get_stats()
    return jsonify(stats)
