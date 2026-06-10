# encoding:utf-8
"""
自定义工具管理 API

GET    /api/agent/tools           — 列出自定义工具
POST   /api/agent/tools           — 添加自定义工具
PUT    /api/agent/tools/<tool_id> — 更新自定义工具
DELETE /api/agent/tools/<tool_id> — 删除自定义工具
"""

import uuid

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.database import db, AgentConfig
from saas.agent_factory import AgentFactory, get_or_create_default_config
from saas.custom_tool_executor import (
    validate_tool_definition,
    get_plan_tool_limit,
)
from saas.audit import audit_log

tools_bp = Blueprint("agent_tools", __name__)


def _get_tenant_plan(tenant_id: str) -> str:
    """获取租户计划"""
    from saas.database import Tenant
    tenant = Tenant.query.get(tenant_id)
    return tenant.plan if tenant else "free"


def _serialize_tool(tool_def: dict) -> dict:
    """序列化工具定义为 API 响应格式"""
    execution = tool_def.get("execution", {})
    # 脱敏 headers 中的 API Key
    safe_headers = {}
    for key, value in execution.get("headers", {}).items():
        key_lower = key.lower()
        if any(kw in key_lower for kw in ("authorization", "key", "token", "secret")):
            safe_headers[key] = "***"
        else:
            safe_headers[key] = value

    return {
        "id": tool_def.get("id", ""),
        "name": tool_def.get("name", ""),
        "description": tool_def.get("description", ""),
        "parameters": tool_def.get("parameters", {}),
        "execution": {
            "type": execution.get("type", "http"),
            "url": execution.get("url", ""),
            "method": execution.get("method", "GET"),
            "headers": safe_headers,
            "query_params": execution.get("query_params", {}),
            "body": execution.get("body"),
            "response_path": execution.get("response_path", ""),
        },
    }


@tools_bp.route("", methods=["GET"])
def list_tools():
    """列出自定义工具

    返回当前租户的所有自定义工具定义，以及计划限制信息。
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    tools = config.get_tools() if config else []

    tenant_plan = _get_tenant_plan(tenant_id)
    tool_limit = get_plan_tool_limit(tenant_plan)

    return jsonify({
        "tools": [_serialize_tool(t) for t in tools],
        "plan": tenant_plan,
        "limit": tool_limit,
        "count": len(tools),
    })


@tools_bp.route("", methods=["POST"])
def add_tool():
    """添加自定义工具

    请求体为工具定义 JSON，包含 name, description, parameters, execution。
    自动生成 tool ID，验证格式和计划限制。
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # 验证工具定义格式
    is_valid, error_msg = validate_tool_definition(data)
    if not is_valid:
        return jsonify({"error": f"Invalid tool definition: {error_msg}"}), 400

    # 检查计划限制
    tenant_plan = _get_tenant_plan(tenant_id)
    tool_limit = get_plan_tool_limit(tenant_plan)
    if tool_limit == 0:
        return jsonify({"error": f"Custom tools not available on {tenant_plan} plan"}), 403

    # 获取或创建配置
    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        config = get_or_create_default_config(tenant_id)

    current_tools = config.get_tools()

    if tool_limit > 0 and len(current_tools) >= tool_limit:
        return jsonify({
            "error": f"Custom tool limit reached ({tool_limit} tools on {tenant_plan} plan)",
            "limit": tool_limit,
        }), 403

    # 检查工具名是否重复
    existing_names = [t.get("name") for t in current_tools]
    if data["name"] in existing_names:
        return jsonify({"error": f"Tool with name '{data['name']}' already exists"}), 409

    # 生成 ID 并添加
    data["id"] = data.get("id") or uuid.uuid4().hex[:12]
    current_tools.append(data)
    config.set_tools(current_tools)
    config.config_version += 1
    AgentFactory.destroy(tenant_id)

    db.session.commit()

    audit_log(
        tenant_id=tenant_id,
        action="custom_tool_add",
        resource_type="custom_tool",
        resource_id=data["id"],
        detail=f"Added tool '{data['name']}'",
    )

    return jsonify(_serialize_tool(data)), 201


@tools_bp.route("/<tool_id>", methods=["PUT"])
def update_tool(tool_id: str):
    """更新自定义工具

    请求体为更新后的工具定义，整体替换指定 ID 的工具。
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # 验证工具定义格式
    is_valid, error_msg = validate_tool_definition(data)
    if not is_valid:
        return jsonify({"error": f"Invalid tool definition: {error_msg}"}), 400

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        return jsonify({"error": "No agent config found"}), 404

    current_tools = config.get_tools()

    # 查找要更新的工具
    tool_index = None
    for i, t in enumerate(current_tools):
        if t.get("id") == tool_id:
            tool_index = i
            break

    if tool_index is None:
        return jsonify({"error": f"Tool '{tool_id}' not found"}), 404

    # 检查名称冲突（排除自身）
    existing_names = [t.get("name") for i, t in enumerate(current_tools) if i != tool_index]
    if data["name"] in existing_names:
        return jsonify({"error": f"Tool with name '{data['name']}' already exists"}), 409

    # 强制使用路径参数中的 ID，忽略客户端传入的 id
    data["id"] = tool_id
    current_tools[tool_index] = data
    config.set_tools(current_tools)
    config.config_version += 1
    AgentFactory.destroy(tenant_id)

    db.session.commit()

    audit_log(
        tenant_id=tenant_id,
        action="custom_tool_update",
        resource_type="custom_tool",
        resource_id=tool_id,
        detail=f"Updated tool '{data['name']}'",
    )

    return jsonify(_serialize_tool(data))


@tools_bp.route("/<tool_id>", methods=["DELETE"])
def delete_tool(tool_id: str):
    """删除自定义工具"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        return jsonify({"error": "No agent config found"}), 404

    current_tools = config.get_tools()

    # 查找并删除
    tool_index = None
    tool_name = ""
    for i, t in enumerate(current_tools):
        if t.get("id") == tool_id:
            tool_index = i
            tool_name = t.get("name", "")
            break

    if tool_index is None:
        return jsonify({"error": f"Tool '{tool_id}' not found"}), 404

    current_tools.pop(tool_index)
    config.set_tools(current_tools)
    config.config_version += 1
    AgentFactory.destroy(tenant_id)

    db.session.commit()

    audit_log(
        tenant_id=tenant_id,
        action="custom_tool_delete",
        resource_type="custom_tool",
        resource_id=tool_id,
        detail=f"Deleted tool '{tool_name}'",
    )

    return jsonify({"deleted": True, "id": tool_id, "name": tool_name})
