# encoding:utf-8
"""
CowAgent SaaS 管理员 CLI

用法:
    python scripts/saas_admin.py create-tenant --name "公司A" --slug company-a --email admin@company-a.com
    python scripts/saas_admin.py list-tenants
    python scripts/saas_admin.py create-key --tenant-id <tenant_id> --name "Production Key"
    python scripts/saas_admin.py list-keys --tenant-id <tenant_id>
    python scripts/saas_admin.py tenant-info --tenant-id <tenant_id>
"""

import argparse
import json
import os
import sys

# 添加项目根目录到 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_db_url():
    """从环境变量或配置文件获取数据库 URL"""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            from config import conf
            url = conf().get("database_url", "")
        except Exception:
            pass
    if not url:
        print("[ERROR] DATABASE_URL not set. Use --database-url or set DATABASE_URL env var")
        sys.exit(1)
    return url


def create_flask_app(database_url):
    """创建 Flask app 上下文（SQLAlchemy 需要）"""
    from flask import Flask
    from saas.database import db, init_db
    app = Flask(__name__)
    init_db(app=app, database_uri=database_url)
    return app


def cmd_create_tenant(args):
    from saas.database import db, Tenant, User
    from saas.middleware import generate_api_key
    from saas.database import ApiKey

    app = create_flask_app(args.database_url or get_db_url())
    with app.app_context():
        # 检查 slug 是否已存在
        if Tenant.query.filter_by(slug=args.slug).first():
            print(f"[ERROR] Slug '{args.slug}' already exists")
            return

        tenant = Tenant(name=args.name, slug=args.slug, plan=args.plan)
        db.session.add(tenant)
        db.session.flush()

        user = User(tenant_id=tenant.id, email=args.email, role="owner")
        db.session.add(user)

        raw_key, key_hash, key_prefix = generate_api_key()
        api_key = ApiKey(
            tenant_id=tenant.id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            name="Default Key",
        )
        db.session.add(api_key)
        db.session.commit()

        print(f"[OK] Tenant created successfully!")
        print(f"  Tenant ID:  {tenant.id}")
        print(f"  Name:       {tenant.name}")
        print(f"  Slug:       {tenant.slug}")
        print(f"  Plan:       {tenant.plan}")
        print(f"  API Key:    {raw_key}")
        print(f"  Key Prefix: {key_prefix}...")
        print()
        print(f"  [IMPORTANT] Save the API Key now! It won't be shown again.")


def cmd_list_tenants(args):
    from saas.database import Tenant

    app = create_flask_app(args.database_url or get_db_url())
    with app.app_context():
        tenants = Tenant.query.order_by(Tenant.created_at.desc()).all()
        if not tenants:
            print("[INFO] No tenants found")
            return
        print(f"{'ID':<36} {'Slug':<20} {'Name':<30} {'Plan':<10} {'Active':<8}")
        print("-" * 110)
        for t in tenants:
            print(f"{t.id:<36} {t.slug:<20} {t.name:<30} {t.plan:<10} {'Yes' if t.is_active else 'No':<8}")


def cmd_create_key(args):
    from saas.database import db, ApiKey
    from saas.middleware import generate_api_key

    app = create_flask_app(args.database_url or get_db_url())
    with app.app_context():
        raw_key, key_hash, key_prefix = generate_api_key()
        api_key = ApiKey(
            tenant_id=args.tenant_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            name=args.name or "Unnamed Key",
        )
        db.session.add(api_key)
        db.session.commit()

        print(f"[OK] API Key created!")
        print(f"  Key ID:     {api_key.id}")
        print(f"  Name:       {api_key.name}")
        print(f"  API Key:    {raw_key}")
        print(f"  Key Prefix: {key_prefix}...")
        print()
        print(f"  [IMPORTANT] Save the API Key now! It won't be shown again.")


def cmd_list_keys(args):
    from saas.database import ApiKey

    app = create_flask_app(args.database_url or get_db_url())
    with app.app_context():
        keys = ApiKey.query.filter_by(tenant_id=args.tenant_id).order_by(ApiKey.created_at).all()
        if not keys:
            print(f"[INFO] No API keys found for tenant {args.tenant_id}")
            return
        print(f"{'ID':<36} {'Prefix':<10} {'Name':<20} {'Active':<8} {'Last Used':<20}")
        print("-" * 100)
        for k in keys:
            last_used = k.last_used_at.strftime("%Y-%m-%d %H:%M") if k.last_used_at else "Never"
            print(f"{k.id:<36} {k.key_prefix:<10} {k.name:<20} {'Yes' if k.is_active else 'No':<8} {last_used:<20}")


def cmd_init_db(args):
    """初始化数据库：创建所有表 + pgvector 扩展"""
    database_url = args.database_url or get_db_url()

    # 1. 创建 SaaS 管理表（tenants, users, api_keys, usage_records）
    from flask import Flask
    from saas.database import db, init_db

    app = Flask(__name__)
    init_db(app=app, database_uri=database_url)
    with app.app_context():
        db.create_all()
    print("[OK] SaaS management tables created (tenants, users, api_keys, usage_records)")

    # 2. 创建 Memory 存储表（memory_chunks, memory_files）+ pgvector 扩展
    try:
        import sqlalchemy as sa
        engine = sa.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
            print("[OK] pgvector extension enabled")

            conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS memory_chunks (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT,
                    scope TEXT NOT NULL DEFAULT 'shared',
                    source TEXT NOT NULL DEFAULT 'memory',
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding vector,
                    hash TEXT NOT NULL,
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            conn.execute(sa.text("""
                CREATE INDEX IF NOT EXISTS idx_mc_tenant ON memory_chunks(tenant_id)
            """))
            conn.execute(sa.text("""
                CREATE INDEX IF NOT EXISTS idx_mc_tenant_user ON memory_chunks(tenant_id, user_id)
            """))
            conn.execute(sa.text("""
                CREATE INDEX IF NOT EXISTS idx_mc_tenant_path_hash ON memory_chunks(tenant_id, path, hash)
            """))
            try:
                conn.execute(sa.text("""
                    CREATE INDEX IF NOT EXISTS idx_mc_embedding
                    ON memory_chunks USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                """))
            except Exception:
                pass  # pgvector 索引需要足够数据量
            try:
                conn.execute(sa.text("""
                    CREATE INDEX IF NOT EXISTS idx_mc_text_fts
                    ON memory_chunks USING gin(to_tsvector('simple', text))
                """))
            except Exception:
                pass

            conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS memory_files (
                    path TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'memory',
                    hash TEXT NOT NULL,
                    mtime BIGINT NOT NULL,
                    size BIGINT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            conn.execute(sa.text("""
                CREATE INDEX IF NOT EXISTS idx_mf_tenant ON memory_files(tenant_id)
            """))
            conn.commit()
        engine.dispose()
        print("[OK] Memory storage tables created (memory_chunks, memory_files)")
    except Exception as e:
        print(f"[WARN] Memory tables creation skipped (may already exist): {e}")

    # 3. 自动授权（Docker 部署场景：表由 tom 创建，cowagent 角色需要访问权限）
    try:
        import sqlalchemy as sa
        engine = sa.create_engine(database_url)
        with engine.connect() as conn:
            # 从 DATABASE_URL 解析连接用户，用于设置默认权限
            from urllib.parse import urlparse
            parsed = urlparse(database_url)
            db_user = parsed.username or "cowagent"

            # 如果连接用户不是 cowagent，则给 cowagent 授权
            if db_user != "cowagent":
                conn.execute(sa.text("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO cowagent"))
                conn.execute(sa.text("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO cowagent"))
                conn.execute(sa.text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO cowagent"))
                conn.execute(sa.text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO cowagent"))
                conn.commit()
                print("[OK] Granted privileges to 'cowagent' role")
            else:
                print("[INFO] Connected as 'cowagent', skipping GRANT")
        engine.dispose()
    except Exception as e:
        print(f"[WARN] Grant privileges skipped (may not be needed): {e}")

    print("[OK] Database initialization complete!")


def cmd_tenant_info(args):
    from saas.database import Tenant, User, ApiKey, UsageRecord

    app = create_flask_app(args.database_url or get_db_url())
    with app.app_context():
        tenant = db.session.get(Tenant, args.tenant_id)
        if not tenant:
            print(f"[ERROR] Tenant {args.tenant_id} not found")
            return

        users = User.query.filter_by(tenant_id=args.tenant_id).count()
        keys = ApiKey.query.filter_by(tenant_id=args.tenant_id, is_active=True).count()

        print(f"Tenant Info:")
        print(f"  ID:         {tenant.id}")
        print(f"  Name:       {tenant.name}")
        print(f"  Slug:       {tenant.slug}")
        print(f"  Plan:       {tenant.plan}")
        print(f"  Active:     {tenant.is_active}")
        print(f"  Users:      {users}")
        print(f"  API Keys:   {keys} (active)")
        print(f"  Created:    {tenant.created_at}")

        if tenant.config_json:
            print(f"  Config:     {tenant.config_json[:200]}...")


def main():
    parser = argparse.ArgumentParser(description="CowAgent SaaS Admin CLI")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # create-tenant
    p = subparsers.add_parser("create-tenant", help="Create a new tenant")
    p.add_argument("--name", required=True, help="Tenant display name")
    p.add_argument("--slug", required=True, help="Tenant URL slug (unique)")
    p.add_argument("--email", required=True, help="Owner email address")
    p.add_argument("--plan", default="free", help="Plan (free/pro/enterprise)")

    # init-db
    subparsers.add_parser("init-db", help="Initialize database (create all tables + pgvector)")

    # list-tenants
    subparsers.add_parser("list-tenants", help="List all tenants")

    # create-key
    p = subparsers.add_parser("create-key", help="Create a new API key")
    p.add_argument("--tenant-id", required=True, help="Tenant ID")
    p.add_argument("--name", default=None, help="Key label")

    # list-keys
    p = subparsers.add_parser("list-keys", help="List API keys for a tenant")
    p.add_argument("--tenant-id", required=True, help="Tenant ID")

    # tenant-info
    p = subparsers.add_parser("tenant-info", help="Show tenant details")
    p.add_argument("--tenant-id", required=True, help="Tenant ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "create-tenant": cmd_create_tenant,
        "init-db": cmd_init_db,
        "list-tenants": cmd_list_tenants,
        "create-key": cmd_create_key,
        "list-keys": cmd_list_keys,
        "tenant-info": cmd_tenant_info,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
