#!/usr/bin/env python3
"""Initialize SaaS database schema for Neon - creates all tables directly"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

database_url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DATABASE_URL', '')

from flask import Flask
from saas.database import db, init_db

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 5,
    "max_overflow": 10,
    "pool_pre_ping": True,
}

db.init_app(app)

with app.app_context():
    db.create_all()
    print("✓ All tables created successfully!")

    # Verify
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print(f"\nCreated tables ({len(tables)}):")
    for table in sorted(tables):
        print(f"  - {table}")