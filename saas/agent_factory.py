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
        """加载工具：从 ToolManager 加载可用工具

        Phase 1 不加载工具，返回空列表。
        Phase 2 将加入按租户启用/禁用插件的能力。
        """
        return []

    @classmethod
    def _load_knowledge(cls, config) -> str:
        """加载知识库内容

        Phase 1 返回空字符串（知识库功能在 Phase 3 实现）。
        """
        return ""

    @classmethod
    def destroy(cls, tenant_id: str):
        """移除租户的缓存 Agent 实例"""
        with cls._lock:
            cls._instances.pop(tenant_id, None)
            cls._versions.pop(tenant_id, None)
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
