# encoding:utf-8
"""
Agent 模板 — 预定义的 Agent 配置模板

租户可以从模板快速创建 Agent，也可以自定义。
模板定义了 system_prompt、plugins、tools、model 等推荐配置。
"""

from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# 模板定义
# ---------------------------------------------------------------------------

AGENT_TEMPLATES: Dict[str, dict] = {
    "general_assistant": {
        "name": "general_assistant",
        "display_name": "通用助手",
        "description": "通用对话助手，适合日常问答和文本生成",
        "icon": "🤖",
        "category": "general",
        "config": {
            "system_prompt": "You are a helpful assistant. Be concise and accurate.",
            "model": "deepseek-chat",
            "plugins": ["web_search"],
            "tools": [],
            "max_steps": 15,
            "temperature": 0.7,
            "enable_thinking": True,
            "reasoning_effort": "high",
        },
        "plan_availability": ["free", "pro", "enterprise"],
    },
    "code_assistant": {
        "name": "code_assistant",
        "display_name": "代码助手",
        "description": "编程辅助，支持代码生成、调试和解释",
        "icon": "💻",
        "category": "developer",
        "config": {
            "system_prompt": (
                "You are an expert programming assistant. "
                "Write clean, efficient, and well-documented code. "
                "When debugging, explain the root cause and fix. "
                "Use best practices and design patterns."
            ),
            "model": "deepseek-chat",
            "plugins": ["code_interpreter", "web_search"],
            "tools": [],
            "max_steps": 20,
            "temperature": 0.3,
            "enable_thinking": True,
            "reasoning_effort": "high",
        },
        "plan_availability": ["free", "pro", "enterprise"],
    },
    "data_analyst": {
        "name": "data_analyst",
        "display_name": "数据分析师",
        "description": "数据分析助手，支持数据处理、可视化和报告生成",
        "icon": "📊",
        "category": "business",
        "config": {
            "system_prompt": (
                "You are a data analysis expert. "
                "Help users analyze data, create visualizations, and generate insights. "
                "Use code interpreter for data processing. "
                "Present findings clearly with charts and summaries."
            ),
            "model": "deepseek-chat",
            "plugins": ["code_interpreter", "web_search"],
            "tools": [],
            "max_steps": 25,
            "temperature": 0.4,
            "enable_thinking": True,
            "reasoning_effort": "high",
        },
        "plan_availability": ["pro", "enterprise"],
    },
    "customer_service": {
        "name": "customer_service",
        "display_name": "客服助手",
        "description": "智能客服，自动回答用户问题，支持知识库检索",
        "icon": "🎧",
        "category": "business",
        "config": {
            "system_prompt": (
                "You are a professional customer service agent. "
                "Be polite, patient, and helpful. "
                "Answer questions based on the knowledge base. "
                "If unsure, say so and offer to escalate. "
                "Never make up information."
            ),
            "model": "deepseek-chat",
            "plugins": ["web_search"],
            "tools": [],
            "max_steps": 10,
            "temperature": 0.5,
            "enable_thinking": False,
            "reasoning_effort": "medium",
        },
        "plan_availability": ["pro", "enterprise"],
    },
    "research_assistant": {
        "name": "research_assistant",
        "display_name": "研究助手",
        "description": "学术研究助手，支持文献检索、总结和论文写作",
        "icon": "🔬",
        "category": "professional",
        "config": {
            "system_prompt": (
                "You are a research assistant. "
                "Help with literature review, data analysis, and academic writing. "
                "Always cite sources. "
                "Be thorough and methodical in your analysis."
            ),
            "model": "deepseek-chat",
            "plugins": ["web_search", "code_interpreter"],
            "tools": [],
            "max_steps": 25,
            "temperature": 0.3,
            "enable_thinking": True,
            "reasoning_effort": "high",
        },
        "plan_availability": ["pro", "enterprise"],
    },
    "creative_writer": {
        "name": "creative_writer",
        "display_name": "创意写作",
        "description": "创意写作助手，支持文案、故事、营销内容创作",
        "icon": "✍️",
        "category": "creative",
        "config": {
            "system_prompt": (
                "You are a creative writing assistant. "
                "Help with copywriting, storytelling, and content creation. "
                "Be imaginative and engaging. "
                "Adapt your tone to the target audience."
            ),
            "model": "deepseek-chat",
            "plugins": [],
            "tools": [],
            "max_steps": 10,
            "temperature": 1.0,
            "enable_thinking": False,
            "reasoning_effort": "medium",
        },
        "plan_availability": ["free", "pro", "enterprise"],
    },
}


# ---------------------------------------------------------------------------
# 模板查询 API
# ---------------------------------------------------------------------------

def get_all_templates() -> List[dict]:
    """获取所有模板列表"""
    return [
        {
            "name": t["name"],
            "display_name": t["display_name"],
            "description": t["description"],
            "icon": t["icon"],
            "category": t["category"],
            "plan_availability": t["plan_availability"],
        }
        for t in AGENT_TEMPLATES.values()
    ]


def get_template(template_name: str) -> Optional[dict]:
    """获取模板详情（含配置）"""
    return AGENT_TEMPLATES.get(template_name)


def get_templates_by_category() -> Dict[str, dict]:
    """按分类获取模板"""
    categories = {}
    for t in AGENT_TEMPLATES.values():
        cat = t["category"]
        if cat not in categories:
            categories[cat] = {
                "display_name": _category_display_name(cat),
                "templates": [],
            }
        categories[cat]["templates"].append({
            "name": t["name"],
            "display_name": t["display_name"],
            "description": t["description"],
            "icon": t["icon"],
            "plan_availability": t["plan_availability"],
        })
    return categories


def is_template_available(template_name: str, plan: str) -> bool:
    """检查模板是否在指定计划下可用"""
    template = AGENT_TEMPLATES.get(template_name)
    if not template:
        return False
    return plan in template["plan_availability"]


def apply_template(template_name: str, tenant_id: str) -> Optional[dict]:
    """将模板应用到租户的 AgentConfig

    Args:
        template_name: 模板名称
        tenant_id: 租户 ID

    Returns:
        更新后的配置字典，模板不存在返回 None
    """
    template = AGENT_TEMPLATES.get(template_name)
    if not template:
        return None

    from saas.database import db, AgentConfig
    from saas.agent_factory import AgentFactory

    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        from saas.agent_factory import get_or_create_default_config
        config = get_or_create_default_config(tenant_id)

    # 应用模板配置
    tmpl_config = template["config"]
    config.system_prompt = tmpl_config.get("system_prompt", config.system_prompt)
    config.model = tmpl_config.get("model", config.model)
    config.set_plugins(tmpl_config.get("plugins", []))
    config.set_tools(tmpl_config.get("tools", []))
    config.max_steps = tmpl_config.get("max_steps", config.max_steps)
    config.temperature = tmpl_config.get("temperature", config.temperature)
    config.enable_thinking = tmpl_config.get("enable_thinking", config.enable_thinking)
    config.reasoning_effort = tmpl_config.get("reasoning_effort", config.reasoning_effort)
    config.config_version += 1

    # 清除缓存
    AgentFactory.destroy(tenant_id)

    db.session.commit()
    return {
        "name": config.name,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "plugins": config.get_plugins() or [],
        "tools": config.get_tools(),
        "max_steps": config.max_steps,
        "temperature": config.temperature,
        "enable_thinking": config.enable_thinking,
        "reasoning_effort": config.reasoning_effort,
        "config_version": config.config_version,
    }


def _category_display_name(category: str) -> str:
    """分类中文名"""
    return {
        "general": "通用",
        "developer": "开发者",
        "business": "商业",
        "professional": "专业",
        "creative": "创意",
    }.get(category, category)
