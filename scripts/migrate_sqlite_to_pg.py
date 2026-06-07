# encoding:utf-8
"""
SQLite → PostgreSQL 数据迁移脚本

将 CowAgent 原有的 SQLite 数据（用户配置、记忆数据、会话历史）迁移到 PostgreSQL，
用于从单机版升级到 SaaS 多租户版。

用法:
    # 自动创建租户 + 迁移所有数据
    python scripts/migrate_sqlite_to_pg.py \
        --auto-create-tenant "公司名称" \
        --database-url postgresql://user:pass@host:5432/dbname

    # 指定已有租户 ID
    python scripts/migrate_sqlite_to_pg.py \
        --tenant-id <tenant_id> \
        --database-url postgresql://user:pass@host:5432/dbname

    # 自定义路径
    python scripts/migrate_sqlite_to_pg.py \
        --auto-create-tenant "公司名称" \
        --database-url postgresql://user:pass@host:5432/dbname \
        --workspace /path/to/cow/workspace

注意:
    - 迁移前请确保 PostgreSQL 已启动且数据库已创建
    - 迁移后需在 config.json 中设置 saas_mode=True 和 database_url
    - 用户数据（user_datas.pkl）会自动导入到 tenants.config_json
    - 会话历史（conversations.db）会迁移到 PostgreSQL conversations 表
"""

import argparse
import json
import os
import pickle
import sqlite3
import struct
import sys
import uuid

# 添加项目根目录到 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _decode_sqlite_embedding(raw) -> list | None:
    """解码 SQLite 中的 embedding（BLOB bytes 或 JSON 字符串）"""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        # float32 BLOB 格式
        try:
            import numpy as np
            return np.frombuffer(raw, dtype=np.float32).tolist()
        except ImportError:
            n = len(raw) // 4
            return list(struct.unpack(f"{n}f", raw))
    # JSON 字符串格式
    try:
        return json.loads(raw)
    except Exception:
        return None


def auto_create_tenant(name: str, slug: str, database_url: str, plan: str = "free") -> str:
    """自动创建租户，返回 tenant_id"""
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)

    with engine.connect() as conn:
        # 检查 slug 是否已存在
        existing = conn.execute(sa.text(
            "SELECT id FROM tenants WHERE slug = :slug"
        ), {"slug": slug}).fetchone()

        if existing:
            print(f"[INFO] Tenant with slug '{slug}' already exists (id={existing.id})")
            return str(existing.id)

        tenant_id = str(uuid.uuid4())
        conn.execute(sa.text("""
            INSERT INTO tenants (id, name, slug, plan, is_active)
            VALUES (:id, :name, :slug, :plan, true)
        """), {
            "id": tenant_id, "name": name,
            "slug": slug, "plan": plan,
        })
        conn.commit()

    engine.dispose()
    print(f"[OK] Created tenant: id={tenant_id}, name={name}, slug={slug}, plan={plan}")
    return tenant_id


def migrate_user_datas(pkl_path: str, tenant_id: str, database_url: str):
    """迁移 user_datas.pkl 到 PostgreSQL tenants.config_json"""
    if not os.path.exists(pkl_path):
        print(f"[SKIP] user_datas.pkl not found: {pkl_path}")
        return

    with open(pkl_path, "rb") as f:
        user_datas = pickle.load(f)

    if not user_datas:
        print("[SKIP] user_datas.pkl is empty")
        return

    import sqlalchemy as sa
    engine = sa.create_engine(database_url)

    # 将用户数据序列化为 JSON 存入 tenants.config_json
    config_json = {}
    for user_id, data in user_datas.items():
        if isinstance(data, dict):
            # 尝试 JSON 序列化，跳过不可序列化的值
            try:
                json.dumps(data)
                config_json[str(user_id)] = data
            except (TypeError, ValueError):
                config_json[str(user_id)] = str(data)

    with engine.connect() as conn:
        result = conn.execute(sa.text(
            "SELECT config_json FROM tenants WHERE id = :tid"
        ), {"tid": tenant_id}).fetchone()

        if result:
            existing = json.loads(result.config_json) if result.config_json else {}
            existing["user_datas"] = config_json
            conn.execute(sa.text(
                "UPDATE tenants SET config_json = :cfg WHERE id = :tid"
            ), {"cfg": json.dumps(existing, ensure_ascii=False), "tid": tenant_id})
        else:
            print(f"[ERROR] Tenant {tenant_id} not found in database")
            return

        conn.commit()
    engine.dispose()
    print(f"[OK] Migrated user_datas ({len(config_json)} users) to tenant {tenant_id}")


def migrate_memory_db(sqlite_path: str, tenant_id: str, database_url: str):
    """迁移 SQLite 记忆数据库到 PostgreSQL memory_chunks / memory_files"""
    if not os.path.exists(sqlite_path):
        print(f"[SKIP] Memory SQLite not found: {sqlite_path}")
        return

    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    # 迁移 chunks
    try:
        rows = sqlite_conn.execute("SELECT * FROM chunks").fetchall()
        migrated = 0
        skipped = 0
        with engine.connect() as conn:
            for row in rows:
                embedding = None
                if row["embedding"]:
                    emb_list = _decode_sqlite_embedding(row["embedding"])
                    if emb_list:
                        embedding = "[" + ",".join(str(float(v)) for v in emb_list) + "]"

                try:
                    conn.execute(sa.text("""
                        INSERT INTO memory_chunks
                        (id, tenant_id, user_id, scope, source, path, start_line, end_line,
                         text, embedding, hash, metadata)
                        VALUES (:id, :tid, :uid, :scope, :source, :path, :sl, :el,
                                :text, :emb, :hash, :meta)
                        ON CONFLICT(id) DO UPDATE SET
                            text = EXCLUDED.text, embedding = EXCLUDED.embedding, hash = EXCLUDED.hash
                    """), {
                        "id": row["id"], "tid": tenant_id,
                        "uid": row.get("user_id"),
                        "scope": row.get("scope", "shared"),
                        "source": row.get("source", "memory"),
                        "path": row.get("path", ""),
                        "sl": row.get("start_line", 0),
                        "el": row.get("end_line", 0),
                        "text": row["text"],
                        "emb": embedding,
                        "hash": row.get("hash", ""),
                        "meta": row.get("metadata"),
                    })
                    migrated += 1
                except Exception as e:
                    skipped += 1
                    if skipped <= 3:
                        print(f"[WARN] Skipping chunk {row['id']}: {e}")
            conn.commit()
        print(f"[OK] Migrated {migrated} memory chunks" + (f", skipped {skipped}" if skipped else ""))
    except Exception as e:
        print(f"[SKIP] Memory chunks migration failed: {e}")

    # 迁移 files
    try:
        rows = sqlite_conn.execute("SELECT * FROM files").fetchall()
        migrated = 0
        with engine.connect() as conn:
            for row in rows:
                conn.execute(sa.text("""
                    INSERT INTO memory_files (path, tenant_id, source, hash, mtime, size)
                    VALUES (:path, :tid, :source, :hash, :mtime, :size)
                    ON CONFLICT(path) DO UPDATE SET
                        hash = EXCLUDED.hash, mtime = EXCLUDED.mtime, size = EXCLUDED.size
                """), {
                    "path": row["path"], "tid": tenant_id,
                    "source": row.get("source", "memory"),
                    "hash": row.get("hash", ""),
                    "mtime": row.get("mtime", 0),
                    "size": row.get("size", 0),
                })
                migrated += 1
            conn.commit()
        print(f"[OK] Migrated {migrated} memory files")
    except Exception as e:
        print(f"[SKIP] Memory files migration failed: {e}")

    sqlite_conn.close()
    engine.dispose()


def migrate_conversations(sqlite_path: str, tenant_id: str, database_url: str):
    """迁移 SQLite 会话数据库到 PostgreSQL conversations 表"""
    if not os.path.exists(sqlite_path):
        print(f"[SKIP] Conversations SQLite not found: {sqlite_path}")
        return

    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    # 创建 conversations 表（如果不存在）
    with engine.connect() as conn:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                channel_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                context_start_seq INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_active TIMESTAMPTZ DEFAULT NOW(),
                msg_count INTEGER NOT NULL DEFAULT 0
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id SERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                extras TEXT NOT NULL DEFAULT '',
                UNIQUE (tenant_id, session_id, seq)
            )
        """))
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_conv_tenant_session
            ON conversations(tenant_id, session_id)
        """))
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_convmsg_tenant_session
            ON conversation_messages(tenant_id, session_id, seq)
        """))
        conn.commit()

    # 迁移 sessions
    sessions_migrated = 0
    try:
        sessions = sqlite_conn.execute("SELECT * FROM sessions").fetchall()
        with engine.connect() as conn:
            for s in sessions:
                conv_id = str(uuid.uuid4())
                created_at = s.get("created_at", 0)
                last_active = s.get("last_active", 0)

                conn.execute(sa.text("""
                    INSERT INTO conversations
                    (id, tenant_id, session_id, channel_type, title, context_start_seq,
                     created_at, last_active, msg_count)
                    VALUES (:id, :tid, :sid, :ctype, :title, :ctx_seq,
                            to_timestamp(:cat), to_timestamp(:lat), :mc)
                    ON CONFLICT (id) DO NOTHING
                """), {
                    "id": conv_id, "tid": tenant_id,
                    "sid": s["session_id"],
                    "ctype": s.get("channel_type", ""),
                    "title": s.get("title", ""),
                    "ctx_seq": s.get("context_start_seq", 0),
                    "cat": created_at, "lat": last_active,
                    "mc": s.get("msg_count", 0),
                })
                sessions_migrated += 1
            conn.commit()
        print(f"[OK] Migrated {sessions_migrated} conversation sessions")
    except Exception as e:
        print(f"[SKIP] Conversation sessions migration failed: {e}")

    # 迁移 messages
    messages_migrated = 0
    try:
        messages = sqlite_conn.execute("SELECT * FROM messages ORDER BY session_id, seq").fetchall()
        batch_size = 500
        with engine.connect() as conn:
            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]
                for m in batch:
                    conn.execute(sa.text("""
                        INSERT INTO conversation_messages
                        (tenant_id, session_id, seq, role, content, created_at, extras)
                        VALUES (:tid, :sid, :seq, :role, :content, :cat, :extras)
                        ON CONFLICT (tenant_id, session_id, seq) DO NOTHING
                    """), {
                        "tid": tenant_id,
                        "sid": m["session_id"],
                        "seq": m["seq"],
                        "role": m["role"],
                        "content": m["content"],
                        "cat": m.get("created_at", 0),
                        "extras": m.get("extras", ""),
                    })
                    messages_migrated += 1
                conn.commit()
        print(f"[OK] Migrated {messages_migrated} conversation messages")
    except Exception as e:
        print(f"[SKIP] Conversation messages migration failed: {e}")

    sqlite_conn.close()
    engine.dispose()


def migrate_config_json(config_path: str, tenant_id: str, database_url: str):
    """迁移 config.json 中的用户配置到 tenants.config_json"""
    if not os.path.exists(config_path):
        print(f"[SKIP] config.json not found: {config_path}")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"[SKIP] Failed to read config.json: {e}")
        return

    # 提取需要迁移的配置项（排除敏感信息和系统配置）
    migrate_keys = [
        "model", "single_chat_prefix", "single_chat_reply_prefix",
        "group_chat_prefix", "group_chat_reply_prefix",
        "group_name_white_list", "image_create_prefix",
        "speech_recognition", "voice_reply_voice",
        "conversation_max_tokens", "temperature",
        "character_desc", "subscribe_msg",
    ]
    migrated_config = {}
    for key in migrate_keys:
        if key in config:
            migrated_config[key] = config[key]

    if not migrated_config:
        print("[SKIP] No migratable config found in config.json")
        return

    import sqlalchemy as sa
    engine = sa.create_engine(database_url)

    with engine.connect() as conn:
        result = conn.execute(sa.text(
            "SELECT config_json FROM tenants WHERE id = :tid"
        ), {"tid": tenant_id}).fetchone()

        if result:
            existing = json.loads(result.config_json) if result.config_json else {}
            existing["migrated_config"] = migrated_config
            conn.execute(sa.text(
                "UPDATE tenants SET config_json = :cfg WHERE id = :tid"
            ), {"cfg": json.dumps(existing, ensure_ascii=False), "tid": tenant_id})
            conn.commit()
            print(f"[OK] Migrated {len(migrated_config)} config items to tenant {tenant_id}")
        else:
            print(f"[ERROR] Tenant {tenant_id} not found")

    engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate CowAgent SQLite data to PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-create tenant and migrate everything
  python scripts/migrate_sqlite_to_pg.py \\
      --auto-create-tenant "My Company" \\
      --database-url postgresql://tom@localhost:5432/cowagent

  # Use existing tenant
  python scripts/migrate_sqlite_to_pg.py \\
      --tenant-id 03b65c1d-eff0-4905-ba67-88ccc9007810 \\
      --database-url postgresql://tom@localhost:5432/cowagent

  # Custom workspace path
  python scripts/migrate_sqlite_to_pg.py \\
      --auto-create-tenant "My Company" \\
      --database-url postgresql://tom@localhost:5432/cowagent \\
      --workspace /path/to/cow/workspace
        """,
    )

    # 租户选择（二选一）
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant-id", help="Existing tenant ID in PostgreSQL")
    group.add_argument("--auto-create-tenant", metavar="NAME",
                       help="Auto-create a tenant with this name")

    # 数据库连接
    parser.add_argument("--database-url", required=True, help="PostgreSQL connection URL")

    # 路径配置
    parser.add_argument("--workspace", default=None,
                        help="CowAgent workspace directory (auto-detected if not set)")
    parser.add_argument("--memory-db", default=None,
                        help="Path to memory SQLite database (e.g. memory/long-term/index.db)")
    parser.add_argument("--conversations-db", default=None,
                        help="Path to conversations SQLite database")
    parser.add_argument("--pkl-path", default=None,
                        help="Path to user_datas.pkl")
    parser.add_argument("--config-path", default=None,
                        help="Path to config.json")

    # 租户配置
    parser.add_argument("--plan", default="free", choices=["free", "pro", "enterprise"],
                        help="Plan for auto-created tenant (default: free)")
    parser.add_argument("--slug", default=None,
                        help="Slug for auto-created tenant (default: derived from name)")

    args = parser.parse_args()

    # 自动检测 workspace 路径
    workspace = os.path.expanduser(args.workspace) if args.workspace else None
    if not workspace:
        # 按优先级检测
        candidates = [
            os.environ.get("COW_WORKSPACE"),
            os.path.expanduser("~/.cow"),
            os.path.expanduser("~/cow"),
        ]
        for c in candidates:
            if c and os.path.isdir(c):
                workspace = c
                break
        if not workspace:
            workspace = os.path.expanduser("~/.cow")

    # 自动检测各数据文件路径
    memory_db = args.memory_db
    if not memory_db:
        # 检测多种可能的路径
        memory_candidates = [
            os.path.join(workspace, "memory", "long-term", "index.db"),
            os.path.join(workspace, "memory.db"),
        ]
        for c in memory_candidates:
            if os.path.exists(c):
                memory_db = c
                break

    conversations_db = args.conversations_db
    if not conversations_db:
        conv_candidates = [
            os.path.join(workspace, "sessions", "conversations.db"),
            os.path.join(workspace, "memory", "long-term", "index.db"),  # 共享 DB
        ]
        for c in conv_candidates:
            if os.path.exists(c):
                conversations_db = c
                break

    pkl_path = args.pkl_path or os.path.join(workspace, "user_datas.pkl")
    config_path = args.config_path
    if not config_path:
        config_candidates = [
            os.path.join(workspace, "config.json"),
            "config.json",
        ]
        for c in config_candidates:
            if os.path.exists(c):
                config_path = c
                break

    # 确定租户 ID
    if args.auto_create_tenant:
        slug = args.slug or args.auto_create_tenant.lower().replace(" ", "-").replace("_", "-")
        # 只保留字母数字和连字符
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
        if not slug:
            slug = "migrated-tenant"
        tenant_id = auto_create_tenant(args.auto_create_tenant, slug, args.database_url, args.plan)
    else:
        tenant_id = args.tenant_id

    print()
    print("=== CowAgent SQLite → PostgreSQL Migration ===")
    print(f"Tenant ID:        {tenant_id}")
    print(f"Database URL:     {args.database_url[:50]}...")
    print(f"Workspace:        {workspace}")
    print(f"Memory DB:        {memory_db or '(not found)'}")
    print(f"Conversations DB: {conversations_db or '(not found)'}")
    print(f"User datas PKL:   {pkl_path} {'(exists)' if os.path.exists(pkl_path) else '(not found)'}")
    print(f"Config JSON:      {config_path or '(not found)'}")
    print()

    # 1. 迁移 config.json
    if config_path:
        migrate_config_json(config_path, tenant_id, args.database_url)

    # 2. 迁移用户数据
    migrate_user_datas(pkl_path, tenant_id, args.database_url)

    # 3. 迁移记忆数据库
    if memory_db:
        migrate_memory_db(memory_db, tenant_id, args.database_url)

    # 4. 迁移会话历史
    if conversations_db:
        migrate_conversations(conversations_db, tenant_id, args.database_url)

    print()
    print("=== Migration Complete ===")
    print("Next steps:")
    print("1. Set saas_mode=True in config.json")
    print(f"2. Set database_url={args.database_url}")
    print("3. Restart CowAgent")
    print()
    print(f"Your tenant ID: {tenant_id}")
    print("Use this ID to create an API key:")
    print(f"  python scripts/saas_admin.py create-key --tenant-id {tenant_id} --name 'Migrated Key'")


if __name__ == "__main__":
    main()
