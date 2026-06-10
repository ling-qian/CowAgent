# encoding:utf-8
"""
CowAgent 核心对话引擎

支持两种模式：
1. AgentFactory 模式（新）— 根据租户 AgentConfig 直接构造独立 Agent 实例
2. Bridge 模式（旧）— 通过全局 config 覆盖 + Bridge 单例

优先使用 AgentFactory 模式，Bridge 作为 fallback。
"""

import json
import os
import uuid
import threading

from common.log import logger


# ---------------------------------------------------------------------------
# 模式选择
# ---------------------------------------------------------------------------

def _use_agent_factory(tenant_id: str) -> bool:
    """判断是否使用 AgentFactory 模式

    当租户有 AgentConfig 记录时使用 Factory，否则 fallback 到 Bridge。
    """
    try:
        from saas.database import AgentConfig
        config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
        return config is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# AgentFactory 模式对话
# ---------------------------------------------------------------------------

def _chat_via_factory(tenant_id: str, message: str, session_id: str,
                      system_prompt: str = None) -> dict:
    """通过 AgentFactory 执行对话

    流程：
    1. 获取/创建租户 AgentConfig
    2. AgentFactory.get_or_create() 获取 Agent 实例
    3. Agent.run_stream() 执行对话
    4. 记录用量
    """
    from saas.agent_factory import AgentFactory, get_or_create_default_config

    # 获取 AgentConfig（不存在则创建默认配置）
    config = get_or_create_default_config(tenant_id)

    # 如果请求中指定了 system_prompt，临时覆盖（不修改数据库）
    # 通过 expunge 使 config 脱离 SQLAlchemy session，避免 auto-flush 持久化
    from saas.database import db
    db.session.expunge(config)
    if system_prompt:
        config.system_prompt = system_prompt

    # 获取或创建 Agent 实例
    agent = AgentFactory.get_or_create(tenant_id, config)

    # 执行对话
    try:
        content = agent.run_stream(message, clear_history=False)
    except Exception as e:
        logger.error(f"[ChatEngine] AgentFactory run_stream error: {e}", exc_info=True)
        content = "对话引擎错误，请稍后重试"

    # 估算 token 用量
    prompt_tokens = int(len(message) * 1.5)
    completion_tokens = int(len(content) * 1.5) if content else 0
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }

    # 记录用量
    _record_usage(tenant_id, prompt_tokens, completion_tokens)

    return {
        "session_id": session_id,
        "content": content or "",
        "usage": usage,
        "mode": "agent_factory",
    }


# ---------------------------------------------------------------------------
# Bridge 模式对话（旧路径，作为 fallback）
# ---------------------------------------------------------------------------

_config_lock = threading.Lock()


def _get_tenant_llm_config(tenant_id: str) -> dict:
    """获取租户级 LLM 配置

    优先级：租户 config_json > 环境变量 > config.json
    """
    config = {
        "model": "deepseek-chat",
        "api_key": "",
        "api_base": "https://api.deepseek.com/v1",
        "max_tokens": 4096,
        "temperature": 0.7,
        "system_prompt": "你是一个智能助手，基于 CowAgent SaaS 平台运行。",
    }

    # 从环境变量 / config.json 读取默认值
    try:
        from config import conf
        c = conf()
        if c.get("model"):
            config["model"] = c["model"]
        if c.get("deepseek_api_key"):
            config["api_key"] = c["deepseek_api_key"]
            config["api_base"] = c.get("deepseek_api_base", "https://api.deepseek.com/v1")
        if c.get("open_ai_api_key"):
            config["api_key"] = c["open_ai_api_key"]
            config["api_base"] = c.get("open_ai_api_base", "https://api.openai.com/v1")
    except Exception:
        pass

    # 环境变量覆盖
    env_key = os.environ.get("LLM_API_KEY")
    if env_key:
        config["api_key"] = env_key
    env_base = os.environ.get("LLM_API_BASE")
    if env_base:
        config["api_base"] = env_base
    env_model = os.environ.get("LLM_MODEL")
    if env_model:
        config["model"] = env_model

    # 租户级配置覆盖（从数据库 tenants.config_json 读取）
    try:
        from saas.database import Tenant
        tenant = Tenant.query.get(tenant_id)
        if tenant and tenant.config_json:
            overrides = json.loads(tenant.config_json)
            for k in ("model", "api_key", "api_base", "max_tokens", "temperature", "system_prompt"):
                if k in overrides:
                    config[k] = overrides[k]
    except Exception:
        pass

    return config


def _apply_tenant_config(tenant_id: str) -> dict:
    """将租户配置临时应用到 CowAgent 全局 config，返回原始值用于恢复"""
    from config import conf

    tenant_config = _get_tenant_llm_config(tenant_id)

    # 保存原始值
    original = {}
    key_map = {
        "model": "model",
        "api_key": "deepseek_api_key",
        "api_base": "deepseek_api_base",
        "temperature": "temperature",
        "max_tokens": "conversation_max_tokens",
    }

    with _config_lock:
        for config_key, cow_key in key_map.items():
            if config_key in tenant_config and tenant_config[config_key]:
                original[cow_key] = conf().get(cow_key)
                conf()[cow_key] = tenant_config[config_key]

        # 应用租户级系统提示词（character_desc）
        tenant_system_prompt = tenant_config.get("system_prompt")
        if tenant_system_prompt:
            original["character_desc"] = conf().get("character_desc")
            conf()["character_desc"] = tenant_system_prompt

        # 根据模型名推断 bot_type 并设置对应的 API key/base
        model = tenant_config.get("model", "")
        api_key = tenant_config.get("api_key", "")
        api_base = tenant_config.get("api_base", "")

        if model.startswith("deepseek"):
            original["deepseek_api_key"] = conf().get("deepseek_api_key")
            original["deepseek_api_base"] = conf().get("deepseek_api_base")
            if api_key:
                conf()["deepseek_api_key"] = api_key
            if api_base:
                conf()["deepseek_api_base"] = api_base
        elif model.startswith("gpt") or model.startswith("o1"):
            original["open_ai_api_key"] = conf().get("open_ai_api_key")
            original["open_ai_api_base"] = conf().get("open_ai_api_base")
            if api_key:
                conf()["open_ai_api_key"] = api_key
            if api_base:
                conf()["open_ai_api_base"] = api_base
        else:
            original["open_ai_api_key"] = conf().get("open_ai_api_key")
            original["open_ai_api_base"] = conf().get("open_ai_api_base")
            if api_key:
                conf()["open_ai_api_key"] = api_key
            if api_base:
                conf()["open_ai_api_base"] = api_base

        # 同步到环境变量
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
            os.environ["DEEPSEEK_API_KEY"] = api_key
        if api_base:
            os.environ["OPENAI_API_BASE"] = api_base
            os.environ["DEEPSEEK_API_BASE"] = api_base

    return original


def _restore_config(original: dict):
    """恢复 CowAgent 全局 config 到原始值"""
    from config import conf

    with _config_lock:
        for key, value in original.items():
            if value is not None:
                conf()[key] = value
            else:
                conf().pop(key, None)


_bridge_instance = None
_bridge_lock = threading.Lock()


def _get_bridge():
    """获取或初始化 CowAgent Bridge 单例"""
    global _bridge_instance
    if _bridge_instance is not None:
        return _bridge_instance

    with _bridge_lock:
        if _bridge_instance is not None:
            return _bridge_instance

        try:
            from bridge.bridge import Bridge
            _bridge_instance = Bridge()
            logger.info("[ChatEngine] CowAgent Bridge initialized")
        except Exception as e:
            logger.error(f"[ChatEngine] Failed to init Bridge: {e}")
            raise

    return _bridge_instance


def _ensure_config_loaded():
    """确保 CowAgent 配置已加载"""
    try:
        from config import conf
        if not conf():
            from config import load_config
            load_config()
    except Exception as e:
        logger.warning(f"[ChatEngine] Config load check: {e}")


def _chat_via_bridge(tenant_id: str, message: str, session_id: str,
                     system_prompt: str = None) -> dict:
    """通过 Bridge 执行对话（旧路径）"""
    _ensure_config_loaded()
    original_config = _apply_tenant_config(tenant_id)

    try:
        bridge = _get_bridge()
        bridge.reset_bot()

        from bridge.context import Context, ContextType
        context = Context(ContextType.TEXT, content=message)
        context.kwargs["session_id"] = session_id
        context.kwargs["channel_type"] = "saas_api"

        if system_prompt:
            from config import conf
            conf()["character_desc"] = system_prompt

        try:
            from config import conf
            use_agent = conf().get("agent", True)

            if use_agent:
                reply = bridge.fetch_agent_reply(
                    query=message,
                    context=context,
                    on_event=None,
                    clear_history=False,
                )
            else:
                reply = bridge.fetch_reply_content(query=message, context=context)

            from bridge.reply import ReplyType
            if reply.type == ReplyType.ERROR:
                content = f"Error: {reply.content}"
            elif reply.type in (ReplyType.TEXT, ReplyType.TEXT_):
                content = reply.content
            elif reply.type == ReplyType.IMAGE_URL:
                content = f"[Image: {reply.content}]"
            elif reply.type == ReplyType.FILE:
                content = f"[File: {reply.content}]"
            else:
                content = str(reply.content) if reply.content else ""

            prompt_tokens = int(len(message) * 1.5)
            completion_tokens = int(len(content) * 1.5)
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }

            _record_usage(tenant_id, prompt_tokens, completion_tokens)

            return {
                "session_id": session_id,
                "content": content,
                "usage": usage,
                "mode": "agent" if use_agent else "chat",
            }

        except Exception as inner_e:
            logger.error(f"[ChatEngine] Bridge call error: {inner_e}", exc_info=True)
            raise

    except Exception as e:
        logger.error(f"[ChatEngine] CowAgent Bridge error: {e}", exc_info=True)
        return {
            "session_id": session_id,
            "content": f"对话引擎错误: {str(e)}",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "mode": "error",
        }
    finally:
        _restore_config(original_config)


# ---------------------------------------------------------------------------
# 用量记录
# ---------------------------------------------------------------------------

def _record_usage(tenant_id: str, prompt_tokens: int, completion_tokens: int):
    """记录 Token 用量到数据库（通过 usage.py 的 record_usage，含配额检查）"""
    try:
        from saas.api.usage import record_usage
        total = prompt_tokens + completion_tokens
        allowed, remaining = record_usage(
            tenant_id=tenant_id,
            metric="llm_tokens",
            value=total,
            api_key_id=None,
        )
        if not allowed:
            logger.warning(f"[ChatEngine] Quota exceeded for tenant {tenant_id}, tokens={total}")
    except Exception as e:
        logger.warning(f"[ChatEngine] Failed to record usage: {e}")


# ---------------------------------------------------------------------------
# 统一对话入口
# ---------------------------------------------------------------------------

def chat(
    tenant_id: str,
    message: str,
    session_id: str = None,
    system_prompt: str = None,
    stream: bool = False,
) -> dict:
    """执行对话 — 自动选择 AgentFactory 或 Bridge 模式

    优先使用 AgentFactory（租户有 AgentConfig 时），
    否则 fallback 到 Bridge 模式。

    Args:
        tenant_id: 租户 ID
        message: 用户消息
        session_id: 会话 ID（为空则新建）
        system_prompt: 系统提示词（覆盖租户默认）
        stream: 是否流式

    Returns:
        {"session_id": str, "content": str, "usage": {...}, "mode": str}
    """
    if not session_id:
        session_id = f"tenant_{tenant_id}_{uuid.uuid4().hex[:8]}"

    # 追踪租户会话
    _track_session(tenant_id, session_id)

    # 尝试 AgentFactory 模式
    if _use_agent_factory(tenant_id):
        try:
            return _chat_via_factory(tenant_id, message, session_id, system_prompt)
        except Exception as e:
            logger.warning(f"[ChatEngine] AgentFactory failed, falling back to Bridge: {e}")
            # Fallback 到 Bridge

    # Bridge 模式
    return _chat_via_bridge(tenant_id, message, session_id, system_prompt)


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------

_tenant_sessions = {}
_tenant_sessions_lock = threading.Lock()


def _track_session(tenant_id: str, session_id: str):
    """追踪租户的会话"""
    with _tenant_sessions_lock:
        if tenant_id not in _tenant_sessions:
            _tenant_sessions[tenant_id] = set()
        _tenant_sessions[tenant_id].add(session_id)


def list_sessions(tenant_id: str) -> list:
    """列出租户的活跃会话"""
    sessions = []
    with _tenant_sessions_lock:
        tracked = _tenant_sessions.get(tenant_id, set())
    for sid in tracked:
        try:
            bridge = _get_bridge()
            agent_bridge = bridge.get_agent_bridge()
            if sid in agent_bridge.agents:
                sessions.append(sid)
        except Exception:
            sessions.append(sid)
    return sessions


def clear_session(session_id: str):
    """清除指定会话"""
    try:
        bridge = _get_bridge()
        agent_bridge = bridge.get_agent_bridge()
        agent_bridge.clear_session(session_id)
    except Exception as e:
        logger.warning(f"[ChatEngine] Failed to clear session {session_id}: {e}")


def get_available_models() -> list:
    """获取 CowAgent 支持的模型列表"""
    try:
        from common import const
        return const.MODEL_LIST
    except Exception:
        return [
            "deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash",
            "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo",
            "claude-3-5-sonnet", "gemini-2.0-flash",
        ]
