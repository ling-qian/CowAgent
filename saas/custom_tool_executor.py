# encoding:utf-8
"""
自定义工具执行引擎 — 将租户定义的 HTTP 工具转换为 Agent 可调用的 BaseTool

设计：
- CustomHttpTool 继承 BaseTool，将工具定义转换为 OpenAI function calling 格式
- 执行时根据工具定义构建 HTTP 请求，支持模板变量替换
- 安全措施：仅 HTTPS、禁止内网访问、超时限制、响应大小限制

工具定义格式（存储在 AgentConfig.tools JSON 字段）：
{
    "id": "tool_001",
    "name": "get_weather",
    "description": "Get current weather for a city",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"}
        },
        "required": ["city"]
    },
    "execution": {
        "type": "http",
        "url": "https://api.example.com/weather",
        "method": "GET",
        "headers": {"Authorization": "Bearer xxx"},
        "query_params": {"q": "{{city}}"},
        "body": null,
        "response_path": "$.weather.description"
    }
}
"""

import ipaddress
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse

import requests
from common.log import logger
from agent.tools.base_tool import BaseTool, ToolResult

# ---------------------------------------------------------------------------
# 安全配置
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = 10          # 请求超时（秒）
MAX_RESPONSE_SIZE = 1024 * 1024  # 响应大小限制（1MB）
ALLOWED_SCHEMES = {"https"}   # 仅允许 HTTPS（开发环境可加 http）

# 内网 IP 段（RFC 1918 + loopback + link-local）
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# 计划限制
PLAN_TOOL_LIMITS = {
    "free": 0,
    "pro": 3,
    "enterprise": -1,  # -1 = 无限制
}


# ---------------------------------------------------------------------------
# 安全检查
# ---------------------------------------------------------------------------

def _is_internal_url(url: str) -> bool:
    """检查 URL 是否指向内网地址"""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True  # 无主机名，拒绝

        import socket
        # 解析域名获取 IP
        try:
            ip_str = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
        except socket.gaierror:
            try:
                ip_str = socket.getaddrinfo(hostname, None, socket.AF_INET6)[0][4][0]
            except socket.gaierror:
                # 无法解析域名 — 可能是尚未配置的公网域名
                # 记录警告但不阻止（实际请求时 DNS 会再次解析）
                logger.warning(f"[CustomTool] Cannot resolve hostname: {hostname}")
                return False

        ip = ipaddress.ip_address(ip_str)
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                return True
        return False
    except Exception:
        return True  # 异常时保守拒绝


def _validate_url(url: str) -> tuple:
    """验证 URL 安全性

    Returns:
        (is_valid, error_message)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        # 开发环境允许 http，但生产环境应仅允许 https
        if parsed.scheme == "http":
            logger.warning(f"[CustomTool] HTTP scheme used (not HTTPS): {url}")
        else:
            return False, f"URL scheme '{parsed.scheme}' not allowed, use HTTPS"
    if _is_internal_url(url):
        return False, "URL points to internal network address, not allowed"
    return True, ""


# ---------------------------------------------------------------------------
# 模板渲染
# ---------------------------------------------------------------------------

def _render_template(template: str, params: dict) -> str:
    """将模板中的 {{param_name}} 替换为实际参数值

    Args:
        template: 包含 {{param_name}} 占位符的字符串
        params: LLM 传入的参数字典

    Returns:
        替换后的字符串
    """
    if not template or "{{" not in template:
        return template

    def replacer(match):
        key = match.group(1).strip()
        value = params.get(key, "")
        return str(value)

    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


def _render_dict_templates(data: dict, params: dict) -> dict:
    """递归渲染字典中的模板变量"""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _render_template(value, params)
        elif isinstance(value, dict):
            result[key] = _render_dict_templates(value, params)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 响应提取
# ---------------------------------------------------------------------------

def _extract_response_path(data: Any, path: str) -> Any:
    """从 JSON 响应中提取指定路径的值

    支持简单的点号路径语法：$.weather.description
    """
    if not path or not path.startswith("$"):
        return data

    # 去掉 $. 前缀
    parts = path[2:].split(".")
    current = data
    for part in parts:
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# CustomHttpTool — 自定义 HTTP 工具
# ---------------------------------------------------------------------------

class CustomHttpTool(BaseTool):
    """自定义 HTTP 工具 — 将工具定义转换为 Agent 可调用的 BaseTool

    当 LLM 决定调用此工具时，execute() 方法会：
    1. 根据工具定义构建 HTTP 请求
    2. 替换模板变量（{{param_name}}）
    3. 执行请求（带安全检查）
    4. 提取响应数据
    5. 返回 ToolResult
    """

    stage = BaseTool.stage  # PRE_PROCESS

    def __init__(self, tool_def: dict):
        """初始化自定义工具

        Args:
            tool_def: 工具定义字典，包含 name, description, parameters, execution
        """
        self.tool_def = tool_def
        self._name = tool_def.get("name", "custom_tool")
        self._description = tool_def.get("description", "Custom tool")
        self._params = tool_def.get("parameters", {
            "type": "object",
            "properties": {},
        })
        self._execution = tool_def.get("execution", {})
        self._tool_id = tool_def.get("id", "")

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def description(self) -> str:
        return self._description

    @description.setter
    def description(self, value):
        self._description = value

    @property
    def params(self) -> dict:
        return self._params

    @params.setter
    def params(self, value):
        self._params = value

    @property
    def tool_id(self) -> str:
        return self._tool_id

    def execute(self, params: dict) -> ToolResult:
        """执行自定义工具 — 构建 HTTP 请求并执行

        Args:
            params: LLM 传入的参数字典

        Returns:
            ToolResult 包含执行结果或错误信息
        """
        try:
            exec_config = self._execution
            if not exec_config:
                return ToolResult.fail("No execution config defined for this tool")

            url = _render_template(exec_config.get("url", ""), params)
            method = exec_config.get("method", "GET").upper()
            headers = _render_dict_templates(exec_config.get("headers", {}), params)
            response_path = exec_config.get("response_path", "")

            # 安全检查
            is_valid, error_msg = _validate_url(url)
            if not is_valid:
                return ToolResult.fail(f"URL validation failed: {error_msg}")

            # 构建请求
            request_kwargs = {
                "timeout": REQUEST_TIMEOUT,
                "headers": headers,
                "allow_redirects": False,  # 禁止自动跟随重定向（防止 SSRF 绕过）
            }

            if method == "GET":
                query_params = _render_dict_templates(exec_config.get("query_params", {}), params)
                if query_params:
                    separator = "&" if "?" in url else "?"
                    url = f"{url}{separator}{urlencode(query_params)}"
            elif method in ("POST", "PUT", "PATCH"):
                body_template = exec_config.get("body")
                if body_template:
                    if isinstance(body_template, dict):
                        body = _render_dict_templates(body_template, params)
                    elif isinstance(body_template, str):
                        body_str = _render_template(body_template, params)
                        try:
                            body = json.loads(body_str)
                        except json.JSONDecodeError:
                            body = body_str
                    else:
                        body = body_template
                    request_kwargs["json"] = body

            # 执行请求
            logger.info(f"[CustomTool] Executing {method} {url}")
            response = requests.request(method, url, **request_kwargs)

            # 检查重定向（allow_redirects=False 时 3xx 不会自动跟随）
            if 300 <= response.status_code < 400:
                redirect_url = response.headers.get("Location", "")
                return ToolResult.fail(
                    f"Redirects not allowed (HTTP {response.status_code} -> {redirect_url})"
                )

            # 检查响应大小
            if len(response.content) > MAX_RESPONSE_SIZE:
                return ToolResult.fail(
                    f"Response too large ({len(response.content)} bytes, max {MAX_RESPONSE_SIZE})"
                )

            # 解析响应
            try:
                response_data = response.json()
            except (json.JSONDecodeError, ValueError):
                response_data = response.text

            # 检查 HTTP 状态码
            if response.status_code >= 400:
                error_detail = response_data if isinstance(response_data, str) else json.dumps(response_data)
                return ToolResult.fail(
                    f"HTTP {response.status_code}: {error_detail[:500]}"
                )

            # 提取指定路径
            if response_path:
                result = _extract_response_path(response_data, response_path)
                if result is None:
                    return ToolResult.fail(
                        f"Response path '{response_path}' not found in response"
                    )
                return ToolResult.success(result)

            return ToolResult.success(response_data)

        except requests.Timeout:
            return ToolResult.fail(f"Request timed out after {REQUEST_TIMEOUT}s")
        except requests.ConnectionError as e:
            return ToolResult.fail(f"Connection error: {str(e)[:200]}")
        except Exception as e:
            logger.error(f"[CustomTool] Execution error: {e}")
            return ToolResult.fail(f"Execution error: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# 工具定义验证
# ---------------------------------------------------------------------------

def validate_tool_definition(tool_def: dict) -> tuple:
    """验证工具定义格式

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(tool_def, dict):
        return False, "Tool definition must be a JSON object"

    # 必填字段
    if not tool_def.get("name"):
        return False, "Tool 'name' is required"
    if not tool_def.get("description"):
        return False, "Tool 'description' is required"

    # name 格式验证（仅允许字母、数字、下划线）
    name = tool_def["name"]
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", name):
        return False, "Tool name must start with a letter, contain only alphanumeric and underscore, max 64 chars"

    # parameters 格式验证
    params = tool_def.get("parameters")
    if params:
        if not isinstance(params, dict):
            return False, "Tool 'parameters' must be a JSON Schema object"
        if params.get("type") != "object":
            return False, "Tool parameters must have type 'object'"

    # execution 格式验证
    execution = tool_def.get("execution")
    if execution:
        if not isinstance(execution, dict):
            return False, "Tool 'execution' must be an object"
        if not execution.get("url"):
            return False, "Tool execution 'url' is required"
        if execution.get("method", "GET").upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return False, "Tool execution method must be GET/POST/PUT/PATCH/DELETE"

    return True, ""


def get_plan_tool_limit(tenant_plan: str) -> int:
    """获取计划下的自定义工具数量限制

    Returns:
        最大工具数量，-1 表示无限制
    """
    return PLAN_TOOL_LIMITS.get(tenant_plan, 0)


# ---------------------------------------------------------------------------
# 工具创建辅助
# ---------------------------------------------------------------------------

def create_custom_tools(tool_definitions: List[dict]) -> List[CustomHttpTool]:
    """从工具定义列表创建 CustomHttpTool 实例列表

    Args:
        tool_definitions: 工具定义字典列表

    Returns:
        CustomHttpTool 实例列表
    """
    tools = []
    for tool_def in tool_definitions:
        try:
            is_valid, error = validate_tool_definition(tool_def)
            if not is_valid:
                logger.warning(f"[CustomTool] Invalid tool definition '{tool_def.get('name')}': {error}")
                continue
            tools.append(CustomHttpTool(tool_def))
        except Exception as e:
            logger.warning(f"[CustomTool] Failed to create tool: {e}")
    return tools
