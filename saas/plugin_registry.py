# encoding:utf-8
"""
插件注册表 — 定义所有可用插件、分类、计划可用性

设计原则：
- 插件 = CowAgent 工具的 SaaS 化包装
- 每个插件对应一个或多个 BaseTool 子类
- 按计划（free/pro/enterprise）控制可用性
- 支持按租户启用/禁用
"""

from typing import Dict, List, Optional
from common.log import logger


# ---------------------------------------------------------------------------
# 插件注册表
# ---------------------------------------------------------------------------

PLUGIN_REGISTRY: Dict[str, dict] = {
    # ---- 文件操作 ----
    "file_read": {
        "name": "file_read",
        "display_name": "文件读取",
        "description": "读取文件内容，支持多种文件格式",
        "category": "file_ops",
        "icon": "📄",
        "tool_classes": ["read"],
        "plan_availability": ["free", "pro", "enterprise"],
        "enabled_by_default": True,
    },
    "file_write": {
        "name": "file_write",
        "display_name": "文件写入",
        "description": "创建或覆盖文件内容",
        "category": "file_ops",
        "icon": "✏️",
        "tool_classes": ["write"],
        "plan_availability": ["free", "pro", "enterprise"],
        "enabled_by_default": True,
    },
    "file_edit": {
        "name": "file_edit",
        "display_name": "文件编辑",
        "description": "精确编辑文件中的特定内容",
        "category": "file_ops",
        "icon": "🔧",
        "tool_classes": ["edit"],
        "plan_availability": ["pro", "enterprise"],
        "enabled_by_default": True,
    },
    "file_list": {
        "name": "file_list",
        "display_name": "文件列表",
        "description": "列出目录中的文件和子目录",
        "category": "file_ops",
        "icon": "📁",
        "tool_classes": ["ls"],
        "plan_availability": ["free", "pro", "enterprise"],
        "enabled_by_default": True,
    },

    # ---- 代码执行 ----
    "bash": {
        "name": "bash",
        "display_name": "命令执行",
        "description": "执行 Shell 命令，用于代码运行和系统操作",
        "category": "code",
        "icon": "💻",
        "tool_classes": ["bash"],
        "plan_availability": ["pro", "enterprise"],
        "enabled_by_default": True,
    },

    # ---- 网络搜索 ----
    "web_search": {
        "name": "web_search",
        "display_name": "网络搜索",
        "description": "搜索互联网获取最新信息",
        "category": "web",
        "icon": "🔍",
        "tool_classes": ["web_search"],
        "plan_availability": ["pro", "enterprise"],
        "enabled_by_default": True,
    },
    "web_fetch": {
        "name": "web_fetch",
        "display_name": "网页抓取",
        "description": "抓取网页内容并提取文本",
        "category": "web",
        "icon": "🌐",
        "tool_classes": ["web_fetch"],
        "plan_availability": ["pro", "enterprise"],
        "enabled_by_default": False,
    },

    # ---- 浏览器 ----
    "browser": {
        "name": "browser",
        "display_name": "浏览器",
        "description": "自动化浏览器操作，支持网页交互和截图",
        "category": "web",
        "icon": "🧭",
        "tool_classes": ["browser"],
        "plan_availability": ["enterprise"],
        "enabled_by_default": False,
    },

    # ---- 视觉 ----
    "vision": {
        "name": "vision",
        "display_name": "图像理解",
        "description": "分析和理解图像内容",
        "category": "ai",
        "icon": "👁️",
        "tool_classes": ["vision"],
        "plan_availability": ["pro", "enterprise"],
        "enabled_by_default": False,
    },

    # ---- 记忆 ----
    "memory": {
        "name": "memory",
        "display_name": "长期记忆",
        "description": "存储和检索长期记忆信息",
        "category": "ai",
        "icon": "🧠",
        "tool_classes": ["memory_search", "memory_get"],
        "plan_availability": ["pro", "enterprise"],
        "enabled_by_default": True,
    },

    # ---- 调度 ----
    "scheduler": {
        "name": "scheduler",
        "display_name": "定时任务",
        "description": "创建和管理定时执行的任务",
        "category": "automation",
        "icon": "⏰",
        "tool_classes": ["scheduler"],
        "plan_availability": ["enterprise"],
        "enabled_by_default": False,
    },

    # ---- MCP ----
    "mcp": {
        "name": "mcp",
        "display_name": "MCP 工具",
        "description": "Model Context Protocol 外部工具集成",
        "category": "integration",
        "icon": "🔗",
        "tool_classes": ["mcp"],
        "plan_availability": ["enterprise"],
        "enabled_by_default": False,
    },

    # ---- 发送消息 ----
    "send_message": {
        "name": "send_message",
        "display_name": "消息发送",
        "description": "向用户发送消息或通知",
        "category": "communication",
        "icon": "📤",
        "tool_classes": ["send"],
        "plan_availability": ["free", "pro", "enterprise"],
        "enabled_by_default": True,
    },

    # ---- 环境配置 ----
    "env_config": {
        "name": "env_config",
        "display_name": "环境配置",
        "description": "查看和管理环境变量配置",
        "category": "system",
        "icon": "⚙️",
        "tool_classes": ["env_config"],
        "plan_availability": ["enterprise"],
        "enabled_by_default": False,
    },
}

# 分类定义
PLUGIN_CATEGORIES = {
    "file_ops": {"display_name": "文件操作", "order": 1},
    "code": {"display_name": "代码执行", "order": 2},
    "web": {"display_name": "网络能力", "order": 3},
    "ai": {"display_name": "AI 能力", "order": 4},
    "automation": {"display_name": "自动化", "order": 5},
    "integration": {"display_name": "集成", "order": 6},
    "communication": {"display_name": "通信", "order": 7},
    "system": {"display_name": "系统", "order": 8},
}


# ---------------------------------------------------------------------------
# 查询函数
# ---------------------------------------------------------------------------

def get_available_plugins(tenant_plan: str) -> List[dict]:
    """获取租户计划下可用的所有插件

    Args:
        tenant_plan: 租户计划 (free/pro/enterprise)

    Returns:
        可用插件列表，每个元素包含 name, display_name, description,
        category, icon, enabled_by_default
    """
    result = []
    for plugin_name, plugin_def in PLUGIN_REGISTRY.items():
        if tenant_plan in plugin_def.get("plan_availability", []):
            result.append({
                "name": plugin_def["name"],
                "display_name": plugin_def["display_name"],
                "description": plugin_def["description"],
                "category": plugin_def["category"],
                "icon": plugin_def.get("icon", ""),
                "enabled_by_default": plugin_def.get("enabled_by_default", False),
                "plan_availability": plugin_def["plan_availability"],
            })
    # 按分类排序
    result.sort(key=lambda p: PLUGIN_CATEGORIES.get(p["category"], {}).get("order", 99))
    return result


def get_plugin(plugin_name: str) -> Optional[dict]:
    """获取单个插件定义"""
    return PLUGIN_REGISTRY.get(plugin_name)


def get_tool_classes_for_plugins(plugin_names: List[str]) -> List[str]:
    """获取插件列表对应的所有 tool class 名称

    Args:
        plugin_names: 启用的插件名称列表

    Returns:
        tool class 名称列表（去重）
    """
    tool_classes = []
    for name in plugin_names:
        plugin = PLUGIN_REGISTRY.get(name)
        if plugin:
            tool_classes.extend(plugin.get("tool_classes", []))
    return list(dict.fromkeys(tool_classes))  # 去重保序


def is_plugin_available(plugin_name: str, tenant_plan: str) -> bool:
    """检查插件是否在租户计划下可用"""
    plugin = PLUGIN_REGISTRY.get(plugin_name)
    if not plugin:
        return False
    return tenant_plan in plugin.get("plan_availability", [])


def get_default_plugins_for_plan(tenant_plan: str) -> List[str]:
    """获取计划下默认启用的插件列表"""
    defaults = []
    for plugin_name, plugin_def in PLUGIN_REGISTRY.items():
        if (tenant_plan in plugin_def.get("plan_availability", []) and
                plugin_def.get("enabled_by_default", False)):
            defaults.append(plugin_name)
    return defaults


def get_plugins_by_category(tenant_plan: str) -> Dict[str, List[dict]]:
    """按分类获取可用插件"""
    plugins = get_available_plugins(tenant_plan)
    result = {}
    for p in plugins:
        cat = p["category"]
        if cat not in result:
            cat_info = PLUGIN_CATEGORIES.get(cat, {"display_name": cat})
            result[cat] = {
                "display_name": cat_info["display_name"],
                "plugins": [],
            }
        result[cat]["plugins"].append(p)
    return result


def validate_plugins_for_plan(plugin_names: List[str], tenant_plan: str) -> List[str]:
    """验证插件列表是否在计划下可用，返回不可用的插件名"""
    unavailable = []
    for name in plugin_names:
        if not is_plugin_available(name, tenant_plan):
            unavailable.append(name)
    return unavailable
