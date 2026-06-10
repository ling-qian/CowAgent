# encoding:utf-8
"""
Knowledge Base API — 知识库文件管理

GET    /api/agent/knowledge              — 列出知识文件
POST   /api/agent/knowledge/upload       — 上传知识文件
DELETE /api/agent/knowledge/<file_id>     — 删除知识文件
POST   /api/agent/knowledge/<file_id>/reprocess — 重新处理失败文件
"""

import os

from flask import Blueprint, request, jsonify

from common.tenant import current_tenant_id
from saas.database import db, AgentConfig, KnowledgeFile
from saas.audit import audit_log

knowledge_bp = Blueprint("knowledge", __name__)


def _get_tenant_plan(tenant_id: str) -> str:
    """获取租户计划"""
    from saas.database import Tenant
    tenant = Tenant.query.get(tenant_id)
    return tenant.plan if tenant else "free"


def _serialize_knowledge_file(kf: KnowledgeFile) -> dict:
    """序列化知识文件记录"""
    return {
        "id": kf.id,
        "filename": kf.filename,
        "file_size": kf.file_size,
        "file_type": kf.file_type,
        "chunk_count": kf.chunk_count,
        "status": kf.status,
        "error_msg": kf.error_msg,
        "created_at": kf.created_at.isoformat() if kf.created_at else None,
    }


@knowledge_bp.route("", methods=["GET"])
def list_knowledge():
    """列出当前租户的所有知识文件"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    files = KnowledgeFile.query.filter_by(tenant_id=tenant_id).order_by(
        KnowledgeFile.created_at.desc()
    ).all()

    # 获取计划限制
    from saas.knowledge_processor import get_plan_limits
    plan = _get_tenant_plan(tenant_id)
    limits = get_plan_limits(plan)

    # 获取当前 AgentConfig 中的 knowledge_ids
    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    knowledge_ids = config.get_knowledge_ids() if config else []

    return jsonify({
        "files": [_serialize_knowledge_file(kf) for kf in files],
        "knowledge_ids": knowledge_ids,
        "limits": limits,
        "plan": plan,
    })


@knowledge_bp.route("/upload", methods=["POST"])
def upload_knowledge():
    """上传知识文件

    接收 multipart/form-data，字段名: file
    支持: .txt, .md, .pdf, .json, .csv
    """
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    # 检查是否有文件
    if "file" not in request.files:
        return jsonify({"error": "No file provided. Use 'file' field."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = file.filename
    file_content = file.read()
    file_size = len(file_content)

    # 验证文件
    plan = _get_tenant_plan(tenant_id)
    from saas.knowledge_processor import validate_file, get_plan_limits

    valid, error_msg = validate_file(filename, file_size, plan)
    if not valid:
        return jsonify({"error": error_msg}), 403

    # 检查文件数量限制
    limits = get_plan_limits(plan)
    current_count = KnowledgeFile.query.filter_by(tenant_id=tenant_id).count()
    if current_count >= limits["max_files"]:
        return jsonify({
            "error": f"Knowledge file limit reached ({limits['max_files']} files on {plan} plan)",
        }), 403

    # 保存文件
    from saas.knowledge_processor import save_uploaded_file, start_processing

    file_path = save_uploaded_file(tenant_id, filename, file_content)
    file_type = os.path.splitext(filename)[1].lower().lstrip(".")

    # 创建 DB 记录
    kf = KnowledgeFile(
        tenant_id=tenant_id,
        filename=filename,
        file_path=file_path,
        file_size=file_size,
        file_type=file_type,
        status="pending",
    )
    db.session.add(kf)
    db.session.commit()

    # 自动添加到 AgentConfig.knowledge_ids
    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if not config:
        # 自动创建默认 AgentConfig
        from saas.agent_factory import get_or_create_default_config
        config = get_or_create_default_config(tenant_id)
    ids = config.get_knowledge_ids()
    if ids is None:
        ids = []
    if kf.id not in ids:
        ids.append(kf.id)
        config.set_knowledge_ids(ids)
        config.config_version += 1
        from saas.agent_factory import AgentFactory
        AgentFactory.destroy(tenant_id)
        db.session.commit()

    # 启动后台处理
    start_processing(tenant_id, kf.id)

    # 审计日志
    audit_log(
        tenant_id=tenant_id,
        action="knowledge_upload",
        resource_type="knowledge_file",
        resource_id=kf.id,
        detail=f"Uploaded {filename} ({file_size} bytes)",
    )

    return jsonify({
        "message": "File uploaded and processing started",
        "file": _serialize_knowledge_file(kf),
    }), 201


@knowledge_bp.route("/<file_id>", methods=["DELETE"])
def delete_knowledge(file_id: str):
    """删除知识文件"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    # 验证文件归属
    kf = KnowledgeFile.query.get(file_id)
    if not kf or kf.tenant_id != tenant_id:
        return jsonify({"error": "Knowledge file not found"}), 404

    filename = kf.filename

    # 从 AgentConfig.knowledge_ids 中移除
    config = AgentConfig.query.filter_by(tenant_id=tenant_id).first()
    if config:
        ids = config.get_knowledge_ids()
        if ids and file_id in ids:
            ids.remove(file_id)
            config.set_knowledge_ids(ids)
            config.config_version += 1
            from saas.agent_factory import AgentFactory
            AgentFactory.destroy(tenant_id)

    # 删除文件和记录
    from saas.knowledge_processor import delete_knowledge_file
    delete_knowledge_file(tenant_id, file_id)

    if config:
        db.session.commit()

    # 审计日志
    audit_log(
        tenant_id=tenant_id,
        action="knowledge_delete",
        resource_type="knowledge_file",
        resource_id=file_id,
        detail=f"Deleted {filename}",
    )

    return jsonify({"message": "Knowledge file deleted"})


@knowledge_bp.route("/<file_id>/reprocess", methods=["POST"])
def reprocess_knowledge(file_id: str):
    """重新处理失败的知识文件"""
    tenant_id = current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not authenticated"}), 401

    kf = KnowledgeFile.query.get(file_id)
    if not kf or kf.tenant_id != tenant_id:
        return jsonify({"error": "Knowledge file not found"}), 404

    if kf.status not in ("error", "pending"):
        return jsonify({"error": f"File status is '{kf.status}', only error/pending files can be reprocessed"}), 400

    # 重置状态
    kf.status = "pending"
    kf.error_msg = None
    db.session.commit()

    # 启动后台处理
    from saas.knowledge_processor import start_processing
    start_processing(tenant_id, file_id)

    return jsonify({
        "message": "Reprocessing started",
        "file": _serialize_knowledge_file(kf),
    })
