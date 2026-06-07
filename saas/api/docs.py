# encoding:utf-8
"""
API 文档 — Swagger/OpenAPI 自动生成

访问 /api/docs 查看 Swagger UI
访问 /api/swagger.json 获取 OpenAPI 规范
"""

SWAGGER_CONFIG = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs/",
}

SWAGGER_TEMPLATE = {
    "openapi": "3.0.0",
    "info": {
        "title": "CowAgent SaaS 管理 API",
        "description": "CowAgent 多租户 SaaS 平台管理接口，支持租户管理、API Key 管理、用量统计、计费和 SSO 登录。",
        "version": "1.0.0",
        "contact": {
            "name": "CowAgent",
            "url": "https://github.com/cowagent/cowagent",
        },
    },
    "servers": [
        {"url": "http://localhost:8081", "description": "本地开发"},
    ],
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "通过 API Key 认证（格式: sk-xxx）",
            },
            "TenantIdAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Tenant-ID",
                "description": "直接指定租户 ID（管理端使用）",
            },
        },
        "schemas": {
            "Tenant": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "租户 ID"},
                    "name": {"type": "string", "description": "租户名称"},
                    "slug": {"type": "string", "description": "URL 标识"},
                    "plan": {"type": "string", "enum": ["free", "pro", "enterprise"], "description": "套餐"},
                    "config_json": {"type": "string", "description": "配置覆盖（JSON 字符串）"},
                    "is_active": {"type": "boolean"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
            "ApiKey": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "key_prefix": {"type": "string", "description": "Key 前缀（如 sk-abc1）"},
                    "is_active": {"type": "boolean"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "last_used_at": {"type": "string", "format": "date-time"},
                },
            },
            "UsageRecord": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "指标名（llm_tokens / api_calls）"},
                    "value": {"type": "integer", "description": "累计值"},
                    "period": {"type": "string", "description": "统计周期（YYYY-MM）"},
                },
            },
            "BillingSummary": {
                "type": "object",
                "properties": {
                    "plan": {"type": "string"},
                    "plan_name": {"type": "string"},
                    "period": {"type": "string"},
                    "usage": {"type": "object"},
                    "quotas": {"type": "object"},
                    "overage": {"type": "object"},
                    "estimated_cost": {"type": "integer"},
                },
            },
            "Error": {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                },
            },
        },
    },
    "security": [
        {"ApiKeyAuth": []},
    ],
}
