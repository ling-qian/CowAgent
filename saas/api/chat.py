# encoding:utf-8
"""
Chat API — 对话端点（基于 CowAgent Bridge）

POST /api/chat/completions   — 发送消息
GET  /api/chat/sessions      — 列出当前租户的会话
DELETE /api/chat/sessions/:id — 清除会话历史
GET  /api/chat/models        — 列出可用模型
"""

import json
from flask import Blueprint, request, jsonify, g, Response
from saas.middleware import require_auth
from saas.rate_limit import rate_limit
from saas.chat_engine import chat, chat_stream, list_sessions, clear_session, get_available_models, _get_tenant_llm_config
from common.log import logger

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/completions", methods=["POST"])
@require_auth
@rate_limit("chat")
def completions(tenant_id):
    """发送消息并获取 AI 回复

    请求体:
    {
        "message": "你好",
        "session_id": "可选，不传则新建会话",
        "system_prompt": "可选，覆盖默认系统提示词",
        "stream": false
    }

    非流式响应:
    {
        "session_id": "xxx",
        "content": "你好！有什么可以帮你的？",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "mode": "agent"
    }
    """
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    session_id = data.get("session_id")
    system_prompt = data.get("system_prompt")
    stream = data.get("stream", False)

    try:
        if stream:
            # 流式 SSE 响应
            chunk_gen = chat_stream(
                tenant_id=tenant_id,
                message=message,
                session_id=session_id,
                system_prompt=system_prompt,
            )

            def generate():
                for chunk in chunk_gen:
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            result = chat(
                tenant_id=tenant_id,
                message=message,
                session_id=session_id,
                system_prompt=system_prompt,
            )
            return jsonify(result)

    except Exception as e:
        logger.error(f"[ChatAPI] Completion error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/sessions", methods=["GET"])
@require_auth
def get_sessions(tenant_id):
    """列出当前租户的活跃会话"""
    sessions = list_sessions(tenant_id)
    return jsonify({"sessions": sessions, "count": len(sessions)})


@chat_bp.route("/sessions/<session_id>", methods=["DELETE"])
@require_auth
def delete_session(tenant_id, session_id):
    """清除指定会话的历史"""
    clear_session(session_id)
    return jsonify({"message": "Session cleared", "session_id": session_id})


@chat_bp.route("/models", methods=["GET"])
@require_auth
def models(tenant_id):
    """列出可用模型"""
    config = _get_tenant_llm_config(tenant_id)
    available = get_available_models()
    return jsonify({
        "current_model": config["model"],
        "api_base": config["api_base"],
        "available_models": available,
    })
