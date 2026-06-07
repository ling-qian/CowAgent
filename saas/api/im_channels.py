# encoding:utf-8
"""
IM 渠道多租户映射 API

管理 IM 渠道（飞书/钉钉/微信等）的 AppID → tenant_id 映射，
使 IM 回调请求能自动识别所属租户。
"""

from flask import Blueprint, request, jsonify

from saas.database import db, IMChannelMapping
from saas.middleware import require_auth

bp = Blueprint("im_channels", __name__, url_prefix="/api/im-channels")

SUPPORTED_CHANNEL_TYPES = [
    "feishu", "dingtalk", "wechat_mp", "wechat_com", "wechat_kf", "wecom_bot",
]


@bp.route("", methods=["GET"])
@require_auth
def list_mappings(tenant_id):
    """列出当前租户的所有 IM 渠道映射"""
    mappings = IMChannelMapping.query.filter_by(
        tenant_id=tenant_id, is_active=True
    ).all()
    return jsonify({
        "mappings": [
            {
                "id": m.id,
                "channel_type": m.channel_type,
                "app_id": m.app_id,
                "extra_config": m.extra_config,
                "is_active": m.is_active,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in mappings
        ]
    })


@bp.route("", methods=["POST"])
@require_auth
def create_mapping(tenant_id):
    """创建 IM 渠道映射"""
    data = request.get_json(force=True)
    channel_type = data.get("channel_type", "").strip()
    app_id = data.get("app_id", "").strip()
    app_secret = data.get("app_secret", "").strip()
    extra_config = data.get("extra_config")

    if not channel_type or not app_id:
        return jsonify({"error": "channel_type and app_id are required"}), 400

    if channel_type not in SUPPORTED_CHANNEL_TYPES:
        return jsonify({"error": f"Unsupported channel_type. Supported: {SUPPORTED_CHANNEL_TYPES}"}), 400

    # 检查 app_id 是否已被其他租户占用
    existing = IMChannelMapping.query.filter_by(
        channel_type=channel_type, app_id=app_id, is_active=True
    ).first()
    if existing and existing.tenant_id != tenant_id:
        return jsonify({"error": f"app_id '{app_id}' is already mapped to another tenant"}), 409

    # 如果同一租户已有相同映射，返回已有记录
    if existing and existing.tenant_id == tenant_id:
        return jsonify({
            "id": existing.id,
            "channel_type": existing.channel_type,
            "app_id": existing.app_id,
            "message": "Mapping already exists",
        }), 200

    mapping = IMChannelMapping(
        tenant_id=tenant_id,
        channel_type=channel_type,
        app_id=app_id,
        app_secret=app_secret or None,
        extra_config=extra_config or None,
    )
    db.session.add(mapping)
    db.session.commit()

    return jsonify({
        "id": mapping.id,
        "channel_type": mapping.channel_type,
        "app_id": mapping.app_id,
        "message": "IM channel mapping created",
    }), 201


@bp.route("/<mapping_id>", methods=["DELETE"])
@require_auth
def delete_mapping(tenant_id, mapping_id):
    """删除 IM 渠道映射（软删除）"""
    mapping = db.session.get(IMChannelMapping, mapping_id)
    if not mapping or mapping.tenant_id != tenant_id:
        return jsonify({"error": "Mapping not found"}), 404

    mapping.is_active = False
    db.session.commit()
    return jsonify({"message": "Mapping deleted"})


@bp.route("/lookup", methods=["GET"])
def lookup_mapping():
    """根据 channel_type + app_id 查找 tenant_id（内部使用，无需认证）"""
    channel_type = request.args.get("channel_type", "").strip()
    app_id = request.args.get("app_id", "").strip()

    if not channel_type or not app_id:
        return jsonify({"error": "channel_type and app_id are required"}), 400

    mapping = IMChannelMapping.query.filter_by(
        channel_type=channel_type, app_id=app_id, is_active=True
    ).first()

    if not mapping:
        return jsonify({"tenant_id": None}), 404

    return jsonify({"tenant_id": mapping.tenant_id})
