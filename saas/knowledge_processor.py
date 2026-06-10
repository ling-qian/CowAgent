# encoding:utf-8
"""
Knowledge Processor — 知识文件处理引擎

流程：
1. 接收上传文件 → 保存到磁盘
2. 提取文本（PDF/TXT/MD/JSON/CSV）
3. 分块（~500 token/块，重叠 50 token）
4. 存储 chunks JSON 到磁盘
5. 更新 KnowledgeFile 状态

知识注入策略（v1）：
- 将知识文本拼接到 system prompt
- 简单可靠，兼容所有 LLM
- 限制知识总量以适配上下文窗口
"""

import concurrent.futures
import json
import os
import threading
import uuid
from typing import List, Optional, Tuple

from common.log import logger

# 知识文件存储根目录
KNOWLEDGE_DIR = os.path.join(os.path.expanduser("~"), "cow", "knowledge")

# 支持的文件类型及 MIME 映射
SUPPORTED_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".csv": "text/csv",
}

# 计划限制
PLAN_LIMITS = {
    "free": {"max_files": 0, "max_size_mb": 0, "max_context_chars": 0},
    "pro": {"max_files": 10, "max_size_mb": 50, "max_context_chars": 50000},
    "enterprise": {"max_files": 50, "max_size_mb": 500, "max_context_chars": 200000},
}

# 单文件最大大小
MAX_FILE_SIZE_MB = 10

# 提取文本最大字符数（防止 OOM）
MAX_EXTRACT_CHARS = 5 * 1024 * 1024  # 5MB 文本


def get_knowledge_dir(tenant_id: str) -> str:
    """获取租户知识文件目录"""
    path = os.path.join(KNOWLEDGE_DIR, tenant_id)
    os.makedirs(path, exist_ok=True)
    return path


def validate_file(filename: str, file_size: int, tenant_plan: str) -> Tuple[bool, str]:
    """验证上传文件

    Returns:
        (valid, error_message)
    """
    # 检查文件类型
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_TYPES:
        return False, f"Unsupported file type: {ext}. Supported: {list(SUPPORTED_TYPES.keys())}"

    # 检查单文件大小
    if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return False, f"File too large: {file_size / 1024 / 1024:.1f}MB. Max: {MAX_FILE_SIZE_MB}MB"

    # 检查计划限制
    limits = PLAN_LIMITS.get(tenant_plan, PLAN_LIMITS["free"])
    if limits["max_files"] == 0:
        return False, f"Knowledge base not available on {tenant_plan} plan. Upgrade to Pro or Enterprise."

    return True, ""


def save_uploaded_file(tenant_id: str, filename: str, file_content: bytes) -> str:
    """保存上传文件到磁盘

    Returns:
        file_path
    """
    file_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(filename)[1].lower()
    dir_path = get_knowledge_dir(tenant_id)
    file_path = os.path.join(dir_path, f"{file_id}{ext}")

    with open(file_path, "wb") as f:
        f.write(file_content)

    return file_path


def process_file_async(tenant_id: str, file_id: str):
    """异步处理知识文件

    在后台线程中执行：
    1. 提取文本
    2. 分块
    3. 存储 chunks
    4. 更新 DB 状态

    注意：需要在 Flask 应用上下文中运行
    """
    from saas.database import db, KnowledgeFile
    import saas as _saas_mod

    flask_app = _saas_mod.get_flask_app()
    if not flask_app:
        logger.error("[KnowledgeProcessor] Flask app not initialized, cannot process file")
        return

    with flask_app.app_context():
        kf = KnowledgeFile.query.get(file_id)
        if not kf:
            logger.warning(f"[KnowledgeProcessor] File {file_id} not found in DB")
            return

        try:
            # 更新状态为处理中
            kf.status = "processing"
            db.session.commit()

            # 1. 提取文本
            text = extract_text(kf.file_path, kf.file_type)
            if not text.strip():
                raise ValueError("No text content extracted from file")

            # 截断过大文本（防止 OOM）
            if len(text) > MAX_EXTRACT_CHARS:
                logger.warning(f"[KnowledgeProcessor] File {file_id}: text too large "
                               f"({len(text)} chars), truncating to {MAX_EXTRACT_CHARS}")
                text = text[:MAX_EXTRACT_CHARS]

            # 2. 分块
            chunks = chunk_text(text)
            logger.info(f"[KnowledgeProcessor] File {file_id}: extracted {len(text)} chars, "
                         f"split into {len(chunks)} chunks")

            # 3. 存储 chunks JSON
            chunks_path = kf.file_path + ".chunks.json"
            chunks_data = {
                "file_id": file_id,
                "filename": kf.filename,
                "chunk_count": len(chunks),
                "chunks": chunks,
            }
            with open(chunks_path, "w", encoding="utf-8") as f:
                json.dump(chunks_data, f, ensure_ascii=False, indent=2)

            # 4. 更新 DB 状态
            kf.status = "ready"
            kf.chunk_count = len(chunks)
            kf.error_msg = None
            db.session.commit()

            logger.info(f"[KnowledgeProcessor] File {file_id} processed successfully")

        except Exception as e:
            logger.error(f"[KnowledgeProcessor] Failed to process file {file_id}: {e}")
            try:
                kf.status = "error"
                kf.error_msg = str(e)[:500]
                db.session.commit()
            except Exception:
                db.session.rollback()


def start_processing(tenant_id: str, file_id: str):
    """启动后台处理线程（使用线程池限制并发）"""
    _submit_to_pool(process_file_async, tenant_id, file_id)


# 知识处理线程池（限制并发，防止资源耗尽）
_KNOWLEDGE_EXECUTOR = None
_MAX_CONCURRENT_PROCESSES = 4


def _get_executor():
    global _KNOWLEDGE_EXECUTOR
    if _KNOWLEDGE_EXECUTOR is None:
        _KNOWLEDGE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_CONCURRENT_PROCESSES,
            thread_name_prefix="knowledge-processor",
        )
    return _KNOWLEDGE_EXECUTOR


def _submit_to_pool(fn, *args):
    """提交任务到线程池"""
    executor = _get_executor()
    executor.submit(fn, *args)


def extract_text(file_path: str, file_type: str) -> str:
    """从文件提取纯文本

    支持：txt, md, pdf, json, csv
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".txt", ".md"):
        return _extract_plain_text(file_path)
    elif ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".json":
        return _extract_json(file_path)
    elif ext == ".csv":
        return _extract_csv(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _extract_plain_text(file_path: str) -> str:
    """提取纯文本/Markdown"""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _extract_pdf(file_path: str) -> str:
    """提取 PDF 文本

    优先使用 PyPDF2，不可用时尝试 pdfplumber
    """
    text_parts = []
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    except ImportError:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
        except ImportError:
            raise RuntimeError(
                "PDF extraction requires PyPDF2 or pdfplumber. "
                "Install with: pip install PyPDF2"
            )
    return "\n\n".join(text_parts)


def _extract_json(file_path: str) -> str:
    """提取 JSON 文本（格式化输出）"""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _extract_csv(file_path: str) -> str:
    """提取 CSV 文本（转换为可读表格格式）"""
    import csv
    rows = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(" | ".join(row))
    return "\n".join(rows)


def chunk_text(text: str, max_tokens: int = 500, overlap_tokens: int = 50) -> List[str]:
    """将文本分块

    使用简单的字符估算（~4 chars/token），
    按段落/行边界分割，保留重叠。
    当文本无段落边界时，按字符强制分割。

    Args:
        text: 输入文本
        max_tokens: 每块最大 token 数
        overlap_tokens: 块间重叠 token 数

    Returns:
        分块后的文本列表
    """
    if not text.strip():
        return []

    chars_per_token = 4
    max_chars = max_tokens * chars_per_token
    overlap_chars = overlap_tokens * chars_per_token

    # 按段落分割
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    current_chars = 0

    for para in paragraphs:
        para_chars = len(para)

        # 如果单个段落超过最大长度，按行再分割
        if para_chars > max_chars:
            lines = para.split("\n")
            for line in lines:
                line_chars = len(line)
                # 如果单行也超过最大长度，强制按字符分割
                if line_chars > max_chars:
                    for i in range(0, line_chars, max_chars - overlap_chars):
                        segment = line[i:i + max_chars]
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        current_chunk = segment
                        current_chars = len(segment)
                    continue

                if current_chars + line_chars > max_chars and current_chunk:
                    chunks.append(current_chunk.strip())
                    if overlap_chars > 0 and len(current_chunk) > overlap_chars:
                        current_chunk = current_chunk[-overlap_chars:] + "\n" + line
                        current_chars = len(current_chunk)
                    else:
                        current_chunk = line
                        current_chars = line_chars
                else:
                    current_chunk += "\n" + line if current_chunk else line
                    current_chars += line_chars
        else:
            if current_chars + para_chars > max_chars and current_chunk:
                chunks.append(current_chunk.strip())
                if overlap_chars > 0 and len(current_chunk) > overlap_chars:
                    current_chunk = current_chunk[-overlap_chars:] + "\n\n" + para
                    current_chars = len(current_chunk)
                else:
                    current_chunk = para
                    current_chars = para_chars
            else:
                current_chunk += "\n\n" + para if current_chunk else para
                current_chars += para_chars

    # 最后一块
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def load_knowledge_context(tenant_id: str, knowledge_ids: List[str],
                           max_chars: int = 50000) -> str:
    """加载知识库内容用于注入 Agent 上下文

    从 chunks.json 文件读取所有已处理的知识文件内容，
    拼接为一段文本，截断到 max_chars。

    Args:
        tenant_id: 租户 ID
        knowledge_ids: 知识文件 ID 列表
        max_chars: 最大注入字符数

    Returns:
        拼接的知识文本（空字符串表示无内容）
    """
    if not knowledge_ids:
        return ""

    from saas.database import KnowledgeFile

    # 批量查询替代 N+1
    valid_ids = [fid for fid in knowledge_ids if fid]
    if not valid_ids:
        return ""
    files = KnowledgeFile.query.filter(
        KnowledgeFile.id.in_(valid_ids),
        KnowledgeFile.tenant_id == tenant_id,
        KnowledgeFile.status == "ready",
    ).all()
    # 按 knowledge_ids 顺序排列
    file_map = {kf.id: kf for kf in files}

    parts = []
    total_chars = 0

    for fid in knowledge_ids:
        kf = file_map.get(fid)
        if not kf:
            continue

        # 读取 chunks 文件
        chunks_path = kf.file_path + ".chunks.json"
        if not os.path.exists(chunks_path):
            logger.warning(f"[KnowledgeProcessor] Chunks file not found: {chunks_path}")
            continue

        try:
            with open(chunks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            chunks = data.get("chunks", [])
            if not chunks:
                continue

            # 拼接文件内容
            file_text = f"### {kf.filename}\n\n"
            file_text += "\n\n".join(chunks)

            if total_chars + len(file_text) > max_chars:
                # 截断
                remaining = max_chars - total_chars - len(f"### {kf.filename}\n\n")
                if remaining > 100:
                    file_text = f"### {kf.filename}\n\n" + chunks[0][:remaining] + "\n...[truncated]"
                    parts.append(file_text)
                    total_chars += len(file_text)
                break

            parts.append(file_text)
            total_chars += len(file_text)

        except Exception as e:
            logger.warning(f"[KnowledgeProcessor] Failed to load chunks for {fid}: {e}")
            continue

    return "\n\n---\n\n".join(parts) if parts else ""


def delete_knowledge_file(tenant_id: str, file_id: str) -> bool:
    """删除知识文件（磁盘文件 + DB 记录）

    Returns:
        是否成功删除
    """
    from saas.database import db, KnowledgeFile

    kf = KnowledgeFile.query.get(file_id)
    if not kf or kf.tenant_id != tenant_id:
        return False

    # 删除磁盘文件
    try:
        if os.path.exists(kf.file_path):
            os.remove(kf.file_path)
        chunks_path = kf.file_path + ".chunks.json"
        if os.path.exists(chunks_path):
            os.remove(chunks_path)
    except Exception as e:
        logger.warning(f"[KnowledgeProcessor] Failed to delete files for {file_id}: {e}")

    # 删除 DB 记录
    db.session.delete(kf)
    db.session.commit()

    return True


def get_plan_limits(tenant_plan: str) -> dict:
    """获取计划限制"""
    return PLAN_LIMITS.get(tenant_plan, PLAN_LIMITS["free"])
