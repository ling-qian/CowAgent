# encoding:utf-8
"""
Agent Factory — 根据租户 AgentConfig 构造独立 Agent 实例

核心设计：
- 每个租户一个 Agent 实例，通过 config_version 缓存
- 绕过全局 config 和 Bridge 单例，直接构造 Agent
- 使用 OpenAI 兼容 API 直接调用 LLM（不依赖 Bridge 的 bot 基础设施）
- 配置变更时自动重建 Agent（版本号递增机制）
"""

import json
import os
import threading
from typing import Dict, Optional

from common.log import logger


class DirectLLMModel:
    """
    独立的 LLM 客户端 — 直接调用 OpenAI 兼容 API

    不依赖 Bridge/Bot 基础设施，根据 AgentConfig 中的
    model/api_key/api_base 独立创建 OpenAI 客户端。
    兼容 Agent.protocol.models.LLMModel 接口（call/call_stream）。
    """

    def __init__(self, model: str, api_key: str, api_base: str,
                 temperature: float = 0.7, enable_thinking: bool = True,
                 reasoning_effort: str = "high"):
        # 兼容 LLMModel 基类属性
        self.model = model
        self.config = {
            "api_key": api_key,
            "api_base": api_base,
            "temperature": temperature,
        }
        self.api_key = api_key
        self.api_base = api_base.rstrip("/") if api_base else ""
        self.temperature = temperature
        self.enable_thinking = enable_thinking
        self.reasoning_effort = reasoning_effort
        self._client = None

    def _get_client(self):
        """懒加载 OpenAI 客户端"""
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=f"{self.api_base}/v1" if self.api_base and not self.api_base.endswith("/v1") else self.api_base,
            )
            return self._client
        except ImportError:
            raise RuntimeError("openai package is required for DirectLLMModel")

    def call(self, request):
        """非流式调用"""
        client = self._get_client()
        kwargs = {
            "model": self.model,
            "messages": request.messages,
            "temperature": self.temperature,
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        formatted_tools = self._format_tools(request.tools) if request.tools else []
        if formatted_tools:
            kwargs["tools"] = formatted_tools
            kwargs["tool_choice"] = "auto"

        # Thinking mode — 仅推理模型支持（deepseek-reasoner, o1, o3 等）
        if self.enable_thinking and self._supports_thinking():
            kwargs["thinking"] = {"type": "enabled"}
            if self.reasoning_effort in ("high", "max"):
                kwargs["reasoning_effort"] = self.reasoning_effort

        response = client.chat.completions.create(**kwargs)
        return self._format_response(response)

    def call_stream(self, request):
        """流式调用 — 返回生成器"""
        client = self._get_client()
        kwargs = {
            "model": self.model,
            "messages": request.messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        formatted_tools = self._format_tools(request.tools) if request.tools else []
        if formatted_tools:
            kwargs["tools"] = formatted_tools
            kwargs["tool_choice"] = "auto"

        # Thinking mode — 仅推理模型支持
        if self.enable_thinking and self._supports_thinking():
            kwargs["thinking"] = {"type": "enabled"}
            if self.reasoning_effort in ("high", "max"):
                kwargs["reasoning_effort"] = self.reasoning_effort

        stream = client.chat.completions.create(**kwargs)
        return self._iter_stream(stream)

    def _format_tools(self, tools):
        """将 Agent BaseTool 列表转为 OpenAI function calling 格式"""
        formatted = []
        for tool in tools:
            if hasattr(tool, 'to_openai_tool'):
                formatted.append(tool.to_openai_tool())
            elif hasattr(tool, 'name') and hasattr(tool, 'description'):
                params = getattr(tool, 'parameters', {
                    "type": "object",
                    "properties": {},
                })
                formatted.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": params,
                    }
                })
        return formatted

    def _format_response(self, response):
        """将 OpenAI 响应转为统一格式"""
        choice = response.choices[0] if response.choices else None
        if not choice:
            return {"content": "", "tool_calls": []}

        message = choice.message
        result = {
            "content": message.content or "",
            "tool_calls": [],
        }
        if hasattr(message, 'tool_calls') and message.tool_calls:
            for tc in message.tool_calls:
                result["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                })
        return result

    def _iter_stream(self, stream):
        """迭代流式响应，返回 OpenAI 原始格式 chunk

        AgentStreamExecutor 期望每个 chunk 是包含 "choices" 字段的 dict，
        格式与 OpenAI API 原始响应一致。
        """
        for chunk in stream:
            if not chunk.choices:
                continue
            # 直接返回 OpenAI 原始 chunk 的 dict 表示
            yield chunk.model_dump() if hasattr(chunk, 'model_dump') else {
                "choices": [
                    {
                        "delta": {
                            "content": chunk.choices[0].delta.content or None,
                            "tool_calls": self._format_stream_tool_calls(chunk.choices[0].delta),
                            "reasoning_content": getattr(chunk.choices[0].delta, 'reasoning_content', None),
                        },
                        "finish_reason": chunk.choices[0].finish_reason,
                    }
                ]
            }

    @staticmethod
    def _format_stream_tool_calls(delta):
        """格式化流式 tool_calls delta"""
        if not hasattr(delta, 'tool_calls') or not delta.tool_calls:
            return []
        result = []
        for tc in delta.tool_calls:
            entry = {
                "index": tc.index,
                "id": getattr(tc, 'id', None) or "",
                "type": "function",
                "function": {
                    "name": getattr(tc.function, 'name', None) or "",
                    "arguments": getattr(tc.function, 'arguments', None) or "",
                }
            }
            result.append(entry)
        return result

    def _supports_thinking(self) -> bool:
        """判断当前模型是否支持 thinking/reasoning 参数

        仅推理模型（deepseek-reasoner, o1, o3 等）支持此参数。
        普通聊天模型不支持，传入会导致 API 报错。
        """
        thinking_models = ("deepseek-reasoner", "o1", "o1-mini", "o3", "o3-mini",
                           "o4-mini", "deepseek-r1")
        model_lower = self.model.lower()
        return any(m in model_lower for m in thinking_models)


class AgentFactory:
    """创建和管理按租户隔离的 Agent 实例"""

    _instances: Dict[str, object] = {}      # tenant_id → Agent
    _versions: Dict[str, int] = {}           # tenant_id → config_version
    _knowledge_cache: Dict[str, tuple] = {}  # tenant_id → (config_version, knowledge_text)
    _lock = threading.Lock()

    # 最大缓存 Agent 实例数，超出时 LRU 淘汰
    MAX_INSTANCES = 500

    @classmethod
    def get_or_create(cls, tenant_id: str, config) -> object:
        """获取缓存的 Agent 或根据 config 创建新实例

        Args:
            tenant_id: 租户 ID
            config: AgentConfig ORM 对象

        Returns:
            Agent 实例
        """
        with cls._lock:
            cached_version = cls._versions.get(tenant_id)
            if cached_version == config.config_version and tenant_id in cls._instances:
                return cls._instances[tenant_id]

            # 配置变更或未缓存 — 创建新 Agent
            agent = cls._create_agent(config)
            cls._instances[tenant_id] = agent
            cls._versions[tenant_id] = config.config_version

            # LRU 淘汰：超出上限时移除最早的实例
            if len(cls._instances) > cls.MAX_INSTANCES:
                oldest_key = next(iter(cls._instances))
                cls._instances.pop(oldest_key, None)
                cls._versions.pop(oldest_key, None)
                logger.info(f"[AgentFactory] LRU evicted agent for tenant {oldest_key}")

            logger.info(f"[AgentFactory] Created agent for tenant {tenant_id}, "
                        f"version={config.config_version}, model={config.model}")
            return agent

    @classmethod
    def _create_agent(cls, config) -> object:
        """根据 AgentConfig 构造 Agent 实例

        步骤：
        1. 创建 LLM 客户端（DirectLLMModel）
        2. 加载工具（从 ToolManager + 自定义工具）
        3. 加载知识库上下文
        4. 构建完整 system prompt
        5. 创建 Agent 实例
        """
        from agent.protocol.agent import Agent

        # 1. 创建 LLM 客户端
        llm = cls._create_llm(config)

        # 2. 加载工具
        tools = cls._load_tools(config)

        # 3. 加载知识库上下文
        knowledge_context = cls._load_knowledge(config)

        # 4. 构建完整 system prompt
        full_prompt = config.system_prompt or "You are a helpful assistant."
        if knowledge_context:
            full_prompt += f"\n\n## Knowledge Base\n{knowledge_context}"

        # 5. 创建 Agent 实例
        agent = Agent(
            system_prompt=full_prompt,
            description=config.description or config.name,
            model=llm,
            tools=tools if tools else None,
            max_steps=config.max_steps or 15,
            enable_skills=False,  # SaaS 模式暂不启用 skill
            output_mode="logger",
        )
        return agent

    @classmethod
    def _create_llm(cls, config) -> DirectLLMModel:
        """根据 AgentConfig 创建 LLM 客户端

        优先使用租户自己的 api_key/api_base，
        否则使用平台默认配置。
        """
        api_key = config.api_key or cls._get_platform_api_key()
        api_base = config.api_base or cls._get_platform_api_base()
        model = config.model or cls._get_platform_model()

        if not api_key:
            raise ValueError(f"No API key available for tenant {config.tenant_id}")

        return DirectLLMModel(
            model=model,
            api_key=api_key,
            api_base=api_base,
            temperature=config.temperature or 0.7,
            enable_thinking=config.enable_thinking if config.enable_thinking is not None else True,
            reasoning_effort=config.reasoning_effort or "high",
        )

    @classmethod
    def _load_tools(cls, config) -> list:
        """加载工具：根据租户启用的插件从 ToolManager 加载对应工具

        流程：
        1. 从 AgentConfig.plugins 读取启用的插件列表
        2. 通过 plugin_registry 获取对应的 tool class 名称
        3. 从 ToolManager 加载这些工具
        4. 验证插件是否在租户计划下可用
        """
        from saas.plugin_registry import (
            get_tool_classes_for_plugins,
            is_plugin_available,
            get_default_plugins_for_plan,
        )

        # 获取租户计划
        tenant_plan = cls._get_tenant_plan(config.tenant_id)

        # 获取启用的插件列表
        enabled_plugins = config.get_plugins()

        # 如果没有配置任何插件（None 或空列表），使用计划默认值
        # 注意：用户明确设置空列表 [] 表示不需要任何插件
        if enabled_plugins is None:
            enabled_plugins = get_default_plugins_for_plan(tenant_plan)

        # 过滤掉计划不可用的插件
        valid_plugins = [p for p in enabled_plugins if is_plugin_available(p, tenant_plan)]
        if len(valid_plugins) != len(enabled_plugins):
            skipped = set(enabled_plugins) - set(valid_plugins)
            logger.warning(f"[AgentFactory] Skipped unavailable plugins for tenant "
                           f"{config.tenant_id} (plan={tenant_plan}): {skipped}")

        # 获取对应的 tool class 名称
        tool_class_names = get_tool_classes_for_plugins(valid_plugins)

        tools = []
        # 从 ToolManager 加载插件工具实例
        try:
            from agent.tools import ToolManager
            tm = ToolManager()
            tm.load_tools()
            for tool_name in tool_class_names:
                if tool_name in tm.tool_classes:
                    try:
                        tool = tm.create_tool(tool_name)
                        if tool:
                            tools.append(tool)
                    except Exception as e:
                        logger.warning(f"[AgentFactory] Failed to load tool {tool_name}: {e}")
                else:
                    logger.debug(f"[AgentFactory] Tool {tool_name} not found in ToolManager")
        except Exception as e:
            logger.warning(f"[AgentFactory] ToolManager load failed: {e}")

        # 加载自定义工具（Custom Tools）
        custom_tool_defs = config.get_tools()
        if custom_tool_defs:
            # 计划限制检查
            from saas.custom_tool_executor import get_plan_tool_limit, create_custom_tools
            tool_limit = get_plan_tool_limit(tenant_plan)
            if tool_limit == 0:
                logger.debug(f"[AgentFactory] Custom tools disabled for plan {tenant_plan}")
            else:
                if tool_limit > 0:
                    custom_tool_defs = custom_tool_defs[:tool_limit]
                custom_tools = create_custom_tools(custom_tool_defs)
                tools.extend(custom_tools)
                if custom_tools:
                    logger.info(f"[AgentFactory] Loaded {len(custom_tools)} custom tools for "
                                f"tenant {config.tenant_id}")

        return tools

    @staticmethod
    def _get_tenant_plan(tenant_id: str) -> str:
        """获取租户的计划"""
        try:
            from saas.database import Tenant
            tenant = Tenant.query.get(tenant_id)
            return tenant.plan if tenant else "free"
        except Exception:
            return "free"

    @classmethod
    def _load_knowledge(cls, config) -> str:
        """加载知识库内容注入 Agent 上下文

        从 AgentConfig.knowledge_ids 读取知识文件 ID 列表，
        通过 knowledge_processor 加载已处理的 chunks 内容，
        拼接为一段文本用于 system prompt 注入。

        v1 策略：System Prompt Injection（简单可靠）
        - 将知识文本拼接到 system prompt 末尾
        - 限制总量以适配上下文窗口
        - 未来可升级为 RAG 检索方案
        - 带 config_version 缓存，避免重复磁盘 I/O
        """
        knowledge_ids = config.get_knowledge_ids()
        if not knowledge_ids:
            return ""

        # 检查知识缓存
        cached = cls._knowledge_cache.get(config.tenant_id)
        if cached and cached[0] == config.config_version:
            return cached[1]

        # 获取计划限制
        tenant_plan = cls._get_tenant_plan(config.tenant_id)
        from saas.knowledge_processor import load_knowledge_context, get_plan_limits
        limits = get_plan_limits(tenant_plan)
        max_chars = limits.get("max_context_chars", 50000)

        if max_chars <= 0:
            logger.debug(f"[AgentFactory] Knowledge disabled for plan {tenant_plan}")
            return ""

        try:
            context = load_knowledge_context(
                tenant_id=config.tenant_id,
                knowledge_ids=knowledge_ids,
                max_chars=max_chars,
            )
            if context:
                logger.info(f"[AgentFactory] Loaded knowledge context for tenant "
                            f"{config.tenant_id}: {len(context)} chars")
            # 缓存知识上下文
            cls._knowledge_cache[config.tenant_id] = (config.config_version, context)
            return context
        except Exception as e:
            logger.warning(f"[AgentFactory] Failed to load knowledge: {e}")
            return ""

    @classmethod
    def destroy(cls, tenant_id: str):
        """移除租户的缓存 Agent 实例"""
        with cls._lock:
            cls._instances.pop(tenant_id, None)
            cls._versions.pop(tenant_id, None)
            cls._knowledge_cache.pop(tenant_id, None)
            logger.info(f"[AgentFactory] Destroyed agent for tenant {tenant_id}")

    @classmethod
    def list_active(cls) -> list:
        """列出所有活跃的 Agent 租户 ID"""
        with cls._lock:
            return list(cls._instances.keys())

    @classmethod
    def get_stats(cls) -> dict:
        """获取 AgentFactory 统计信息"""
        with cls._lock:
            return {
                "active_count": len(cls._instances),
                "max_instances": cls.MAX_INSTANCES,
                "tenant_ids": list(cls._instances.keys()),
            }

    # -----------------------------------------------------------------------
    # 平台默认配置获取
    # -----------------------------------------------------------------------

    @staticmethod
    def _get_platform_api_key() -> str:
        """获取平台默认 API Key"""
        # 环境变量优先
        key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if key:
            return key
        # 从 config.json 读取
        try:
            from config import conf
            c = conf()
            return (c.get("open_ai_api_key") or c.get("deepseek_api_key") or "")
        except Exception:
            return ""

    @staticmethod
    def _get_platform_api_base() -> str:
        """获取平台默认 API Base"""
        base = os.environ.get("LLM_API_BASE") or os.environ.get("OPENAI_API_BASE")
        if base:
            return base
        try:
            from config import conf
            c = conf()
            return (c.get("open_ai_api_base") or c.get("deepseek_api_base") or
                    "https://api.openai.com/v1")
        except Exception:
            return "https://api.openai.com/v1"

    @staticmethod
    def _get_platform_model() -> str:
        """获取平台默认模型"""
        model = os.environ.get("LLM_MODEL")
        if model:
            return model
        try:
            from config import conf
            return conf().get("model", "deepseek-chat")
        except Exception:
            return "deepseek-chat"


def get_or_create_default_config(tenant_id: str) -> object:
    """获取或创建租户的默认 AgentConfig

    如果租户还没有 AgentConfig，自动创建一个默认配置。
    """
    from saas.database import AgentConfig, db

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if config:
        return config

    # 创建默认配置
    config = AgentConfig(
        tenant_id=tenant_id,
        name="My Agent",
        system_prompt="You are a helpful assistant.",
        model=AgentFactory._get_platform_model(),
        api_key="",  # 使用平台默认
        api_base="",
        max_steps=15,
        temperature=0.7,
        enable_thinking=True,
        reasoning_effort="high",
    )
    db.session.add(config)
    db.session.commit()
    logger.info(f"[AgentFactory] Created default AgentConfig for tenant {tenant_id}")
    return config
