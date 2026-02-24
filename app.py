#!/usr/bin/env python3
"""
PropertyFinder Dashboard - Web UI for managing listings
"""
import os
import sys
import json
import hashlib
import secrets
import shutil
from pathlib import Path
from functools import wraps
from datetime import datetime, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo

# Get the src directory
ROOT_DIR = Path(__file__).parent
SRC_DIR = ROOT_DIR / 'src'

# Add src directory to path
sys.path.insert(0, str(SRC_DIR))

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, g, has_request_context
from werkzeug.utils import secure_filename

from api import PropertyFinderClient, PropertyFinderAPIError, Config
from models import PropertyListing, PropertyType, OfferingType, Location, Price
from utils import BulkListingManager
from database import (
    db, LocalListing, PFSession, User, PFCache, AppSettings, ListingFolder, 
    LoopConfig, LoopListing, DuplicatedListing, LoopExecutionLog, 
    Lead, LeadComment, Contact, Customer,
    TaskBoard, TaskLabel, Task, TaskComment, BoardMember, BOARD_PERMISSIONS, task_assignee_association,
    Workspace, WorkspaceMember, WorkspaceConnection, WorkspaceInvite, PasswordResetToken,
    SystemRole, UserSystemRole, WorkspaceRole, ModulePermission, ObjectACL, FeatureFlag, AuditLog
)
from images import ImageProcessor

# APScheduler for background loop execution
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit

# Setup paths for templates and static files
TEMPLATE_DIR = SRC_DIR / 'dashboard' / 'templates'
STATIC_DIR = SRC_DIR / 'dashboard' / 'static'

# Production settings
IS_PRODUCTION = os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('PRODUCTION', 'false').lower() == 'true'

# Public URL for external access (needed for PropertyFinder media URLs)
# Set APP_PUBLIC_URL in Railway environment variables to your domain
APP_PUBLIC_URL = os.environ.get('APP_PUBLIC_URL') or os.environ.get('RAILWAY_PUBLIC_DOMAIN')
if APP_PUBLIC_URL and not APP_PUBLIC_URL.startswith('http'):
    APP_PUBLIC_URL = f'https://{APP_PUBLIC_URL}'
print(f"[STARTUP] APP_PUBLIC_URL: {APP_PUBLIC_URL or 'NOT SET - local images will NOT work with PropertyFinder!'}")

# Reserved slugs must never be used for workspace routes.
RESERVED_WORKSPACE_SLUGS = {
    'system-admin', 'login', 'logout', 'register', 'api',
    'workspaces', 'workspace', 'static', 'ping', 'favicon.ico'
}

# Storage Configuration - Use Railway Volume in production
RAILWAY_VOLUME_PATH = Path('/data')
if IS_PRODUCTION and RAILWAY_VOLUME_PATH.exists():
    # Use Railway Volume for persistent storage
    UPLOAD_FOLDER = RAILWAY_VOLUME_PATH / 'uploads'
    LISTING_IMAGES_FOLDER = RAILWAY_VOLUME_PATH / 'uploads' / 'listings'
    print(f"[STARTUP] Using Railway Volume at: {RAILWAY_VOLUME_PATH}")
else:
    # Local development storage
    UPLOAD_FOLDER = ROOT_DIR / 'uploads'
    LISTING_IMAGES_FOLDER = ROOT_DIR / 'uploads' / 'listings'
    print(f"[STARTUP] Using local storage at: {UPLOAD_FOLDER}")

# Ensure upload directories exist
try:
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    LISTING_IMAGES_FOLDER.mkdir(parents=True, exist_ok=True)
    (UPLOAD_FOLDER / 'logos').mkdir(parents=True, exist_ok=True)
    (UPLOAD_FOLDER / 'processed').mkdir(parents=True, exist_ok=True)
    print(f"[STARTUP] Upload directories created/verified")
except Exception as e:
    print(f"[STARTUP] Warning: Could not create upload directories: {e}")

DATABASE_PATH = ROOT_DIR / 'data' / 'listings.db'

# Database Configuration - Use PostgreSQL in production if DATABASE_URL is set
DATABASE_URL = os.environ.get('DATABASE_URL')

# Redis Configuration - Use Redis for caching in production
REDIS_URL = os.environ.get('REDIS_URL')

print(f"[STARTUP] Production mode: {IS_PRODUCTION}")
print(f"[STARTUP] DATABASE_URL set: {bool(DATABASE_URL)}")
print(f"[STARTUP] REDIS_URL set: {bool(REDIS_URL)}")

# Ensure data directory exists (only for SQLite)
if not DATABASE_URL:
    print(f"[STARTUP] Using SQLite at: {DATABASE_PATH}")
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"[STARTUP] Data directory created/verified")
    except Exception as e:
        print(f"[STARTUP] Warning: Could not create data directory: {e}")

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Configure Flask-Caching with Redis (if available) or simple cache
from flask_caching import Cache

if REDIS_URL:
    app.config['CACHE_TYPE'] = 'RedisCache'
    app.config['CACHE_REDIS_URL'] = REDIS_URL
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # 5 minutes default
    print(f"[STARTUP] Using Redis cache")
else:
    app.config['CACHE_TYPE'] = 'SimpleCache'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300
    print(f"[STARTUP] Using SimpleCache (in-memory)")

cache = Cache(app)

# Add a simple ping endpoint before any database setup
@app.route('/ping')
def ping():
    return 'pong', 200

@app.route('/favicon.ico')
def favicon():
    return '', 204  # No content

if DATABASE_URL:
    print(f"[STARTUP] Using PostgreSQL database")
    # Railway PostgreSQL fix: replace postgres:// with postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# Initialize database
db.init_app(app)

# Create tables and default admin user
with app.app_context():
    from sqlalchemy import text, inspect
    
    # Run migrations BEFORE create_all - add missing columns to existing tables
    print("[MIGRATION] Checking for required migrations...")
    
    try:
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        print(f"[MIGRATION] Existing tables: {existing_tables}")
    except Exception as e:
        print(f"[MIGRATION] Error inspecting tables: {e}")
        existing_tables = []
    
    # Migration: Add folder_id column to listings table if it doesn't exist
    if 'listings' in existing_tables:
        try:
            columns = [col['name'] for col in inspector.get_columns('listings')]
            print(f"[MIGRATION] Listings table columns: {columns}")
            
            if 'folder_id' not in columns:
                print("[MIGRATION] Adding folder_id column to listings table...")
                try:
                    with db.engine.connect() as conn:
                        # Use PostgreSQL-compatible syntax with IF NOT EXISTS workaround
                        # PostgreSQL doesn't support IF NOT EXISTS for ADD COLUMN, so we catch the error
                        conn.execute(text('ALTER TABLE listings ADD COLUMN folder_id INTEGER NULL'))
                        conn.commit()
                    print("[MIGRATION] ✓ Added folder_id column to listings table")
                except Exception as alter_error:
                    error_str = str(alter_error).lower()
                    if 'already exists' in error_str or 'duplicate column' in error_str:
                        print("[MIGRATION] folder_id column already exists (caught duplicate error)")
                    else:
                        print(f"[MIGRATION] ERROR adding column: {alter_error}")
                        raise
            else:
                print("[MIGRATION] folder_id column already exists")
        except Exception as e:
            print(f"[MIGRATION] Error checking/adding folder_id: {e}")
            # Try to add it anyway with raw SQL that handles duplicates
            try:
                with db.engine.connect() as conn:
                    # For PostgreSQL, use DO block to handle IF NOT EXISTS
                    conn.execute(text("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns 
                                WHERE table_name = 'listings' AND column_name = 'folder_id'
                            ) THEN
                                ALTER TABLE listings ADD COLUMN folder_id INTEGER NULL;
                            END IF;
                        END $$;
                    """))
                    conn.commit()
                print("[MIGRATION] ✓ Added folder_id column using PostgreSQL DO block")
            except Exception as do_error:
                print(f"[MIGRATION] DO block also failed: {do_error}")
    else:
        print("[MIGRATION] listings table does not exist yet, will be created by create_all()")
    
    try:
        print("[STARTUP] Creating database tables...")
        db.create_all()
        print("[STARTUP] Database tables created successfully")
        
        # Migration: Add lead_type column if it doesn't exist
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='crm_leads' AND column_name='lead_type'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding lead_type column to crm_leads table...")
                    conn.execute(text("ALTER TABLE crm_leads ADD COLUMN lead_type VARCHAR(20) DEFAULT 'for_sale'"))
                    conn.commit()
                    print("[MIGRATION] lead_type column added successfully")
        except Exception as e:
            print(f"[MIGRATION] lead_type column migration skipped or failed: {e}")
        
        # Migration: Add contacts table columns if missing
        try:
            with db.engine.connect() as conn:
                # Check and add lead_id column
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='contacts' AND column_name='lead_id'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding lead_id column to contacts table...")
                    conn.execute(text("ALTER TABLE contacts ADD COLUMN lead_id INTEGER REFERENCES crm_leads(id)"))
                    conn.commit()
                # Check and add created_by_id column
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='contacts' AND column_name='created_by_id'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding created_by_id column to contacts table...")
                    conn.execute(text("ALTER TABLE contacts ADD COLUMN created_by_id INTEGER REFERENCES users(id)"))
                    conn.commit()
                # Check and add company column
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='contacts' AND column_name='company'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding company column to contacts table...")
                    conn.execute(text("ALTER TABLE contacts ADD COLUMN company VARCHAR(200)"))
                    conn.commit()
                # Check and add tags column
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='contacts' AND column_name='tags'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding tags column to contacts table...")
                    conn.execute(text("ALTER TABLE contacts ADD COLUMN tags VARCHAR(500)"))
                    conn.commit()
        except Exception as e:
            print(f"[MIGRATION] contacts table migration skipped or failed: {e}")
        
        # Migration: Add is_private column to task_boards if it doesn't exist
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='task_boards' AND column_name='is_private'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding is_private column to task_boards table...")
                    conn.execute(text("ALTER TABLE task_boards ADD COLUMN is_private BOOLEAN DEFAULT TRUE"))
                    conn.commit()
                    print("[MIGRATION] is_private column added successfully")
        except Exception as e:
            print(f"[MIGRATION] task_boards is_private migration skipped or failed: {e}")
        
        # Migration: Create board_members table if it doesn't exist
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='board_members'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating board_members table...")
                    conn.execute(text("""
                        CREATE TABLE board_members (
                            id SERIAL PRIMARY KEY,
                            board_id INTEGER NOT NULL REFERENCES task_boards(id),
                            user_id INTEGER NOT NULL REFERENCES users(id),
                            role VARCHAR(20) DEFAULT 'member',
                            notify_on_assign BOOLEAN DEFAULT TRUE,
                            notify_on_comment BOOLEAN DEFAULT TRUE,
                            notify_on_due BOOLEAN DEFAULT TRUE,
                            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            invited_by_id INTEGER REFERENCES users(id),
                            UNIQUE(board_id, user_id)
                        )
                    """))
                    conn.commit()
                    print("[MIGRATION] board_members table created successfully")
        except Exception as e:
            print(f"[MIGRATION] board_members table creation skipped or failed: {e}")
        
        # Migration: Create task_assignees table if it doesn't exist
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='task_assignees'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating task_assignees table...")
                    conn.execute(text("""
                        CREATE TABLE task_assignees (
                            task_id INTEGER NOT NULL REFERENCES tasks(id),
                            user_id INTEGER NOT NULL REFERENCES users(id),
                            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            assigned_by_id INTEGER REFERENCES users(id),
                            PRIMARY KEY(task_id, user_id)
                        )
                    """))
                    conn.commit()
                    print("[MIGRATION] task_assignees table created successfully")
        except Exception as e:
            print(f"[MIGRATION] task_assignees table creation skipped or failed: {e}")
        
        # Migration: Add section_permissions column to users table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='section_permissions'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding section_permissions column to users table...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN section_permissions TEXT DEFAULT '{}'"))
                    conn.commit()
                    print("[MIGRATION] section_permissions column added successfully")
                    # Give existing admins all permissions
                    conn.execute(text("UPDATE users SET role = 'admin' WHERE role = 'admin'"))
                    conn.commit()
        except Exception as e:
            print(f"[MIGRATION] section_permissions column migration skipped or failed: {e}")
        
        # Migration: Create workspaces tables
        try:
            with db.engine.connect() as conn:
                # Check if workspaces table exists
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='workspaces'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating workspaces table...")
                    conn.execute(text("""
                        CREATE TABLE workspaces (
                            id SERIAL PRIMARY KEY,
                            name VARCHAR(100) NOT NULL,
                            slug VARCHAR(100) UNIQUE NOT NULL,
                            description TEXT,
                            logo_url VARCHAR(500),
                            color VARCHAR(20) DEFAULT 'indigo',
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_by_id INTEGER REFERENCES users(id)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_workspaces_slug ON workspaces(slug)"))
                    conn.execute(text("CREATE INDEX idx_workspaces_is_active ON workspaces(is_active)"))
                    conn.commit()
                    print("[MIGRATION] workspaces table created successfully")
                
                # Check if workspace_members table exists
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='workspace_members'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating workspace_members table...")
                    conn.execute(text("""
                        CREATE TABLE workspace_members (
                            id SERIAL PRIMARY KEY,
                            workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                            user_id INTEGER NOT NULL REFERENCES users(id),
                            role VARCHAR(20) DEFAULT 'member',
                            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            invited_by_id INTEGER REFERENCES users(id),
                            UNIQUE(workspace_id, user_id)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_workspace_members_user ON workspace_members(user_id)"))
                    conn.execute(text("CREATE INDEX idx_workspace_members_workspace ON workspace_members(workspace_id)"))
                    conn.commit()
                    print("[MIGRATION] workspace_members table created successfully")
                
                # Check if workspace_connections table exists
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='workspace_connections'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating workspace_connections table...")
                    conn.execute(text("""
                        CREATE TABLE workspace_connections (
                            id SERIAL PRIMARY KEY,
                            workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                            provider VARCHAR(50) NOT NULL,
                            name VARCHAR(100),
                            is_active BOOLEAN DEFAULT TRUE,
                            credentials TEXT,
                            last_connected_at TIMESTAMP,
                            last_error TEXT,
                            connection_status VARCHAR(20) DEFAULT 'pending',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_by_id INTEGER REFERENCES users(id),
                            UNIQUE(workspace_id, provider)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_workspace_connections_workspace ON workspace_connections(workspace_id)"))
                    conn.execute(text("CREATE INDEX idx_workspace_connections_provider ON workspace_connections(provider)"))
                    conn.commit()
                    print("[MIGRATION] workspace_connections table created successfully")
        except Exception as e:
            print(f"[MIGRATION] workspaces tables creation skipped or failed: {e}")
        
        # Migration: Add workspace_id columns to existing tables
        workspace_tables = ['listing_folders', 'listings', 'crm_leads', 'contacts', 'loop_configs', 'task_boards', 'app_settings', 'pf_cache']
        for table_name in workspace_tables:
            try:
                with db.engine.connect() as conn:
                    result = conn.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table_name}' AND column_name='workspace_id'"))
                    if not result.fetchone():
                        print(f"[MIGRATION] Adding workspace_id column to {table_name}...")
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN workspace_id INTEGER REFERENCES workspaces(id)"))
                        conn.execute(text(f"CREATE INDEX idx_{table_name}_workspace_id ON {table_name}(workspace_id)"))
                        conn.commit()
                        print(f"[MIGRATION] workspace_id column added to {table_name}")
            except Exception as e:
                print(f"[MIGRATION] workspace_id column for {table_name} skipped or failed: {e}")

        # Migration: Add assigned_to_id and original_images to listings
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='listings' AND column_name='assigned_to_id'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding assigned_to_id column to listings...")
                    conn.execute(text("ALTER TABLE listings ADD COLUMN assigned_to_id INTEGER REFERENCES users(id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_listings_assigned_to_id ON listings(assigned_to_id)"))
                    conn.commit()
                    print("[MIGRATION] assigned_to_id column added to listings")
        except Exception as e:
            print(f"[MIGRATION] assigned_to_id column migration skipped or failed: {e}")

        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='listings' AND column_name='original_images'"))
                if not result.fetchone():
                    print("[MIGRATION] Adding original_images column to listings...")
                    conn.execute(text("ALTER TABLE listings ADD COLUMN original_images TEXT"))
                    conn.commit()
                    print("[MIGRATION] original_images column added to listings")
        except Exception as e:
            print(f"[MIGRATION] original_images column migration skipped or failed: {e}")

        # Migration: Add advanced scheduling columns to loop_configs
        loop_schedule_columns = [
            ('schedule_mode', "ALTER TABLE loop_configs ADD COLUMN schedule_mode VARCHAR(32) DEFAULT 'interval'"),
            ('schedule_window_start', "ALTER TABLE loop_configs ADD COLUMN schedule_window_start VARCHAR(5)"),
            ('schedule_window_end', "ALTER TABLE loop_configs ADD COLUMN schedule_window_end VARCHAR(5)"),
            ('schedule_exact_times', "ALTER TABLE loop_configs ADD COLUMN schedule_exact_times TEXT"),
        ]
        for column_name, ddl in loop_schedule_columns:
            try:
                with db.engine.connect() as conn:
                    result = conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='loop_configs' AND column_name=:column_name"
                    ), {'column_name': column_name})
                    if not result.fetchone():
                        print(f"[MIGRATION] Adding {column_name} column to loop_configs...")
                        conn.execute(text(ddl))
                        conn.commit()
                        print(f"[MIGRATION] {column_name} column added to loop_configs")
            except Exception as e:
                print(f"[MIGRATION] {column_name} column migration skipped or failed: {e}")

        # Backfill: default schedule_mode for existing loops
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "UPDATE loop_configs SET schedule_mode = 'interval' "
                    "WHERE schedule_mode IS NULL OR schedule_mode = ''"
                ))
                conn.commit()
                print("[BACKFILL] loop_configs.schedule_mode normalized to interval")
        except Exception as e:
            print(f"[BACKFILL] loop schedule mode normalization skipped or failed: {e}")
        
        # Migration: Update app_settings constraints for workspace scoping
        try:
            with db.engine.connect() as conn:
                # Drop old unique constraint on key if present
                conn.execute(text("ALTER TABLE app_settings DROP CONSTRAINT IF EXISTS app_settings_key_key"))
                # Ensure index on key
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_app_settings_key ON app_settings(key)"))
                # Add unique constraint on (workspace_id, key)
                conn.execute(text("ALTER TABLE app_settings ADD CONSTRAINT uq_app_settings_workspace_key UNIQUE (workspace_id, key)"))
                conn.commit()
                print("[MIGRATION] app_settings workspace constraints updated")
        except Exception as e:
            print(f"[MIGRATION] app_settings constraints update skipped or failed: {e}")
        
        # Migration: Update pf_cache constraints for workspace scoping
        try:
            with db.engine.connect() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pf_cache_type ON pf_cache(cache_type)"))
                conn.execute(text("ALTER TABLE pf_cache ADD CONSTRAINT uq_pf_cache_workspace_type UNIQUE (workspace_id, cache_type)"))
                conn.commit()
                print("[MIGRATION] pf_cache workspace constraints updated")
        except Exception as e:
            print(f"[MIGRATION] pf_cache constraints update skipped or failed: {e}")

        # Migration: Create workspace_invites table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='workspace_invites'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating workspace_invites table...")
                    conn.execute(text("""
                        CREATE TABLE workspace_invites (
                            id SERIAL PRIMARY KEY,
                            workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                            email VARCHAR(120) NOT NULL,
                            role VARCHAR(20) DEFAULT 'member',
                            token_hash VARCHAR(128) NOT NULL,
                            invited_by_id INTEGER REFERENCES users(id),
                            expires_at TIMESTAMP,
                            accepted_at TIMESTAMP,
                            revoked_at TIMESTAMP,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_workspace_invites_workspace ON workspace_invites(workspace_id)"))
                    conn.execute(text("CREATE INDEX idx_workspace_invites_email ON workspace_invites(email)"))
                    conn.execute(text("CREATE INDEX idx_workspace_invites_token ON workspace_invites(token_hash)"))
                    conn.commit()
                    print("[MIGRATION] workspace_invites table created successfully")
        except Exception as e:
            print(f"[MIGRATION] workspace_invites table creation skipped or failed: {e}")

        # Migration: Create password_reset_tokens table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='password_reset_tokens'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating password_reset_tokens table...")
                    conn.execute(text("""
                        CREATE TABLE password_reset_tokens (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            token_hash VARCHAR(128) NOT NULL,
                            created_by_id INTEGER REFERENCES users(id),
                            expires_at TIMESTAMP,
                            used_at TIMESTAMP,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_password_reset_user ON password_reset_tokens(user_id)"))
                    conn.execute(text("CREATE INDEX idx_password_reset_token ON password_reset_tokens(token_hash)"))
                    conn.commit()
                    print("[MIGRATION] password_reset_tokens table created successfully")
        except Exception as e:
            print(f"[MIGRATION] password_reset_tokens table creation skipped or failed: {e}")
        
        # ==================== BITRIX24-STYLE PERMISSION SYSTEM MIGRATION ====================
        
        # Migration: Create system_roles table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='system_roles'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating system_roles table...")
                    conn.execute(text("""
                        CREATE TABLE system_roles (
                            id SERIAL PRIMARY KEY,
                            code VARCHAR(50) UNIQUE NOT NULL,
                            name VARCHAR(100) NOT NULL,
                            description TEXT,
                            is_system BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            capabilities TEXT DEFAULT '{}'
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_system_roles_code ON system_roles(code)"))
                    conn.commit()
                    print("[MIGRATION] system_roles table created successfully")
        except Exception as e:
            print(f"[MIGRATION] system_roles table creation skipped or failed: {e}")
        
        # Migration: Create user_system_roles table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='user_system_roles'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating user_system_roles table...")
                    conn.execute(text("""
                        CREATE TABLE user_system_roles (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            system_role_id INTEGER NOT NULL REFERENCES system_roles(id) ON DELETE CASCADE,
                            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            assigned_by_id INTEGER REFERENCES users(id),
                            UNIQUE(user_id, system_role_id)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_user_system_roles_user ON user_system_roles(user_id)"))
                    conn.execute(text("CREATE INDEX idx_user_system_roles_role ON user_system_roles(system_role_id)"))
                    conn.commit()
                    print("[MIGRATION] user_system_roles table created successfully")
        except Exception as e:
            print(f"[MIGRATION] user_system_roles table creation skipped or failed: {e}")
        
        # Migration: Create workspace_roles table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='workspace_roles'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating workspace_roles table...")
                    conn.execute(text("""
                        CREATE TABLE workspace_roles (
                            id SERIAL PRIMARY KEY,
                            workspace_id INTEGER REFERENCES workspaces(id) ON DELETE CASCADE,
                            code VARCHAR(50) NOT NULL,
                            name VARCHAR(100) NOT NULL,
                            description TEXT,
                            is_default BOOLEAN DEFAULT FALSE,
                            is_system BOOLEAN DEFAULT FALSE,
                            priority INTEGER DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            permission_buckets TEXT DEFAULT '{}'
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_workspace_roles_workspace ON workspace_roles(workspace_id)"))
                    conn.execute(text("CREATE INDEX idx_workspace_roles_code ON workspace_roles(code)"))
                    conn.commit()
                    print("[MIGRATION] workspace_roles table created successfully")
        except Exception as e:
            print(f"[MIGRATION] workspace_roles table creation skipped or failed: {e}")
        
        # Migration: Create module_permissions table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='module_permissions'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating module_permissions table...")
                    conn.execute(text("""
                        CREATE TABLE module_permissions (
                            id SERIAL PRIMARY KEY,
                            workspace_role_id INTEGER NOT NULL REFERENCES workspace_roles(id) ON DELETE CASCADE,
                            module VARCHAR(50) NOT NULL,
                            capabilities TEXT DEFAULT '{}',
                            merge_strategy VARCHAR(20) DEFAULT 'union',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(workspace_role_id, module)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_module_permissions_role ON module_permissions(workspace_role_id)"))
                    conn.execute(text("CREATE INDEX idx_module_permissions_module ON module_permissions(module)"))
                    conn.commit()
                    print("[MIGRATION] module_permissions table created successfully")
        except Exception as e:
            print(f"[MIGRATION] module_permissions table creation skipped or failed: {e}")
        
        # Migration: Create object_acls table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='object_acls'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating object_acls table...")
                    conn.execute(text("""
                        CREATE TABLE object_acls (
                            id SERIAL PRIMARY KEY,
                            object_type VARCHAR(50) NOT NULL,
                            object_id INTEGER NOT NULL,
                            principal_type VARCHAR(20) NOT NULL,
                            principal_id INTEGER NOT NULL,
                            permissions TEXT DEFAULT '{}',
                            inherit_from_parent BOOLEAN DEFAULT TRUE,
                            propagate_to_children BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_by_id INTEGER REFERENCES users(id)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_object_acl_object ON object_acls(object_type, object_id)"))
                    conn.execute(text("CREATE INDEX idx_object_acl_principal ON object_acls(principal_type, principal_id)"))
                    conn.commit()
                    print("[MIGRATION] object_acls table created successfully")
        except Exception as e:
            print(f"[MIGRATION] object_acls table creation skipped or failed: {e}")
        
        # Migration: Create feature_flags table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='feature_flags'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating feature_flags table...")
                    conn.execute(text("""
                        CREATE TABLE feature_flags (
                            id SERIAL PRIMARY KEY,
                            code VARCHAR(100) NOT NULL,
                            name VARCHAR(200) NOT NULL,
                            description TEXT,
                            scope VARCHAR(20) DEFAULT 'global',
                            scope_id INTEGER,
                            is_enabled BOOLEAN DEFAULT FALSE,
                            value TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_by_id INTEGER REFERENCES users(id),
                            UNIQUE(code, scope, scope_id)
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_feature_flags_code ON feature_flags(code)"))
                    conn.execute(text("CREATE INDEX idx_feature_flags_scope ON feature_flags(scope, scope_id)"))
                    conn.commit()
                    print("[MIGRATION] feature_flags table created successfully")
        except Exception as e:
            print(f"[MIGRATION] feature_flags table creation skipped or failed: {e}")
        
        # Migration: Create audit_logs table
        try:
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='audit_logs'"))
                if not result.fetchone():
                    print("[MIGRATION] Creating audit_logs table...")
                    conn.execute(text("""
                        CREATE TABLE audit_logs (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER REFERENCES users(id),
                            user_email VARCHAR(120),
                            action VARCHAR(100) NOT NULL,
                            action_result VARCHAR(20),
                            resource_type VARCHAR(50),
                            resource_id INTEGER,
                            workspace_id INTEGER,
                            details TEXT,
                            ip_address VARCHAR(50),
                            user_agent VARCHAR(500),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                    conn.execute(text("CREATE INDEX idx_audit_logs_user ON audit_logs(user_id)"))
                    conn.execute(text("CREATE INDEX idx_audit_logs_action ON audit_logs(action)"))
                    conn.execute(text("CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id)"))
                    conn.execute(text("CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at)"))
                    conn.execute(text("CREATE INDEX idx_audit_logs_workspace ON audit_logs(workspace_id)"))
                    conn.commit()
                    print("[MIGRATION] audit_logs table created successfully")
        except Exception as e:
            print(f"[MIGRATION] audit_logs table creation skipped or failed: {e}")
        
        # Backfill: Initialize default system roles
        try:
            # SystemRole, UserSystemRole, WorkspaceRole, FeatureFlag imported at top
            
            # Create default system roles if they don't exist
            for code, info in SystemRole.DEFAULT_ROLES.items():
                existing = SystemRole.query.filter_by(code=code).first()
                if not existing:
                    role = SystemRole(
                        code=code,
                        name=info['name'],
                        description=info['description'],
                        is_system=True
                    )
                    role.set_capabilities(info['capabilities'])
                    db.session.add(role)
                    print(f"[BACKFILL] Created system role: {code}")
            db.session.commit()
            
            # Create default workspace role templates (workspace_id = NULL)
            for code, info in WorkspaceRole.DEFAULT_ROLES.items():
                existing = WorkspaceRole.query.filter_by(code=code, workspace_id=None).first()
                if not existing:
                    role = WorkspaceRole(
                        workspace_id=None,  # Template
                        code=code,
                        name=info['name'],
                        description=info['description'],
                        is_system=True,
                        priority=info['priority'],
                        is_default=(code == 'MEMBER')
                    )
                    role.set_permission_buckets(info['buckets'])
                    db.session.add(role)
                    print(f"[BACKFILL] Created workspace role template: {code}")
            db.session.commit()
            
            # Create default feature flags (disabled by default for backward compatibility)
            default_flags = [
                ('permission_enforcement', 'Permission Enforcement', 'Enable strict permission checking', False),
                ('audit_mode', 'Audit Mode', 'Log permission checks without blocking (for testing)', False),
                ('workspace_isolation', 'Workspace Isolation', 'Enforce workspace boundaries for data', False),
                ('object_acl', 'Object-Level ACL', 'Enable per-object permission overrides', False),
            ]
            for code, name, description, enabled in default_flags:
                existing = FeatureFlag.query.filter_by(code=code, scope='global').first()
                if not existing:
                    flag = FeatureFlag(
                        code=code,
                        name=name,
                        description=description,
                        scope='global',
                        is_enabled=enabled
                    )
                    db.session.add(flag)
                    print(f"[BACKFILL] Created feature flag: {code}")
            db.session.commit()
            
            # Bootstrap: ensure there is at least one SYSTEM_ADMIN assignment.
            # Do not derive system access from legacy users.role.
            system_admin_role = SystemRole.query.filter_by(code='SYSTEM_ADMIN').first()
            if system_admin_role:
                existing_count = UserSystemRole.query.filter_by(system_role_id=system_admin_role.id).count()
                if existing_count == 0:
                    bootstrap_email = (os.environ.get('DEFAULT_SYSTEM_ADMIN_EMAIL') or '').strip().lower()
                    bootstrap_user = None
                    if bootstrap_email:
                        bootstrap_user = User.query.filter_by(email=bootstrap_email).first()
                    if not bootstrap_user:
                        bootstrap_user = User.query.filter_by(is_active=True).order_by(User.id.asc()).first()
                    if bootstrap_user:
                        assignment = UserSystemRole(
                            user_id=bootstrap_user.id,
                            system_role_id=system_admin_role.id
                        )
                        db.session.add(assignment)
                        db.session.commit()
                        print(f"[BACKFILL] Bootstrapped SYSTEM_ADMIN to user: {bootstrap_user.email}")
                    else:
                        print("[BACKFILL] SYSTEM_ADMIN bootstrap skipped: no active users found")
                else:
                    print(f"[BACKFILL] SYSTEM_ADMIN assignments exist ({existing_count}), no bootstrap needed")
            
            print("[BACKFILL] Permission system initialization complete")
        except Exception as e:
            print(f"[BACKFILL] Permission system initialization failed: {e}")
            db.session.rollback()
        
        # Backfill: Create default workspace if none exist
        try:
            if Workspace.query.count() == 0:
                # Create a default workspace
                default_workspace = Workspace(
                    name='Yazan Alyoussef',
                    slug='yazanalyoussef',
                    description='Default workspace',
                    color='indigo',
                    is_active=True
                )
                db.session.add(default_workspace)
                db.session.commit()
                print(f"[BACKFILL] Created default workspace: yazanalyoussef")
                
                # Add all existing users to this workspace
                all_users = User.query.all()
                for user in all_users:
                    if UserSystemRole.query.filter_by(user_id=user.id).first():
                        print(f"[BACKFILL] Skipping system user {user.email} for default workspace membership")
                        continue
                    existing = WorkspaceMember.query.filter_by(
                        workspace_id=default_workspace.id,
                        user_id=user.id
                    ).first()
                    if not existing:
                        role = 'owner' if user.role == 'admin' else 'member'
                        member = WorkspaceMember(
                            workspace_id=default_workspace.id,
                            user_id=user.id,
                            role=role
                        )
                        db.session.add(member)
                        print(f"[BACKFILL] Added user {user.email} to workspace as {role}")
                db.session.commit()
                
                # Copy existing PF connection to workspace if exists
                existing_session = PFSession.get_active_session()
                if existing_session:
                    existing_conn = WorkspaceConnection.query.filter_by(
                        workspace_id=default_workspace.id,
                        provider='propertyfinder'
                    ).first()
                    if not existing_conn:
                        pf_conn = WorkspaceConnection(
                            workspace_id=default_workspace.id,
                            provider='propertyfinder',
                            name='PropertyFinder',
                            is_active=True,
                            connection_status='connected'
                        )
                        pf_conn.set_credentials({
                            'api_key': Config.API_KEY,
                            'api_secret': Config.API_SECRET
                        })
                        db.session.add(pf_conn)
                        db.session.commit()
                        print("[BACKFILL] Created PropertyFinder connection for default workspace")
            else:
                # Ensure existing workspace has slug
                for ws in Workspace.query.filter(Workspace.slug == None).all():
                    ws.slug = Workspace.generate_slug(ws.name)
                    print(f"[BACKFILL] Generated slug for workspace: {ws.name} -> {ws.slug}")
                db.session.commit()
                
            print("[BACKFILL] Workspace initialization complete")
        except Exception as e:
            print(f"[BACKFILL] Workspace initialization failed: {e}")
            db.session.rollback()

        # Backfill: Assign workspace_id to existing records if missing
        try:
            default_ws = Workspace.query.filter_by(is_active=True).order_by(Workspace.id.asc()).first()
            if default_ws:
                default_ws_id = default_ws.id
                db.session.execute(text("UPDATE listings SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE crm_leads SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE contacts SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE listing_folders SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE loop_configs SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE task_boards SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE app_settings SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.execute(text("UPDATE pf_cache SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {'ws_id': default_ws_id})
                db.session.commit()
                print(f"[BACKFILL] Assigned workspace_id={default_ws_id} to existing records")
        except Exception as e:
            print(f"[BACKFILL] Workspace_id backfill failed: {e}")
            db.session.rollback()
        
        default_ws_id = get_default_workspace_id()
        
        # Initialize default settings (workspace-scoped)
        AppSettings.init_defaults(workspace_id=default_ws_id)
        
        # Set defaults from .env if not already set in DB
        if default_ws_id:
            if not AppSettings.get('default_agent_email', workspace_id=default_ws_id):
                AppSettings.set('default_agent_email', Config.DEFAULT_AGENT_EMAIL, workspace_id=default_ws_id)
            if not AppSettings.get('default_owner_email', workspace_id=default_ws_id):
                AppSettings.set('default_owner_email', Config.DEFAULT_OWNER_EMAIL, workspace_id=default_ws_id)
        
        # Create default admin user if no users exist
        if User.query.count() == 0:
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@listings.local')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
            admin = User(
                email=admin_email,
                name='Administrator',
                role='admin',
                is_active=True
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            db.session.commit()
            print(f"[STARTUP] Created default admin user: {admin_email}")
        
        print("[STARTUP] Database initialization complete")
        
        # Skip auto-sync in production to avoid slow startup
        # Users can manually sync from the dashboard
        if not IS_PRODUCTION:
            first_run = AppSettings.get('first_run_completed', workspace_id=default_ws_id) != 'true'
            if first_run and Config.validate():
                print("\n🔄 First run detected - syncing PropertyFinder data...")
                try:
                    client = get_client(workspace_id=default_ws_id)
                    
                    # Fetch listings
                    all_listings = []
                    page = 1
                    while True:
                        result = client.get_listings(page=page, per_page=50)
                        listings = result.get('results', [])
                        if not listings:
                            break
                        all_listings.extend(listings)
                        if page >= result.get('pagination', {}).get('totalPages', 1):
                            break
                        page += 1
                        if page > 50:  # Support up to 2500 listings
                            break
                    PFCache.set_cache('listings', all_listings, workspace_id=default_ws_id)
                    print(f"   ✓ Synced {len(all_listings)} listings")
                    
                    # Fetch users
                    try:
                        users_result = client.get_users(per_page=50)
                        users = users_result.get('data', [])
                        PFCache.set_cache('users', users, workspace_id=default_ws_id)
                        print(f"   ✓ Synced {len(users)} users")
                    except:
                        pass
                    
                    # Fetch leads
                    try:
                        leads_result = client.get_leads(per_page=100)
                        leads = leads_result.get('results', [])
                        PFCache.set_cache('leads', leads, workspace_id=default_ws_id)
                        print(f"   ✓ Synced {len(leads)} leads")
                    except:
                        pass
                    
                    AppSettings.set('first_run_completed', 'true', workspace_id=default_ws_id)
                    AppSettings.set('last_sync_at', datetime.now().isoformat(), workspace_id=default_ws_id)
                    print("   ✓ First run sync complete!\n")
                except Exception as e:
                    print(f"   ⚠ First run sync failed: {e}\n")
        else:
            print("✓ Production mode: Skipping auto-sync on startup")
    except Exception as e:
        print(f"⚠ Database initialization error: {e}")
        # Don't crash - let the app start anyway


# ==================== LOOP SCHEDULER ====================

# Global scheduler instance
loop_scheduler = BackgroundScheduler()


def parse_hhmm(value):
    """Parse HH:MM (24-hour) into datetime.time."""
    if value is None:
        raise ValueError('Invalid time format. Use HH:MM (24-hour)')
    text_value = str(value).strip()
    try:
        parsed = datetime.strptime(text_value, '%H:%M')
    except ValueError:
        raise ValueError('Invalid time format. Use HH:MM (24-hour)') from None
    return parsed.time().replace(second=0, microsecond=0)


def normalize_exact_times(values):
    """Normalize list of exact execution times (HH:MM) sorted and deduplicated."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError('schedule_exact_times must be an array of HH:MM strings')
    normalized = []
    seen = set()
    for raw in values:
        hhmm = parse_hhmm(raw).strftime('%H:%M')
        if hhmm not in seen:
            seen.add(hhmm)
            normalized.append(hhmm)
    normalized.sort()
    return normalized


def get_workspace_timezone_name(workspace_id):
    """Return workspace timezone (IANA) with safe fallback."""
    tz_name = (AppSettings.get('workspace_timezone', 'Asia/Dubai', workspace_id=workspace_id) or '').strip()
    if not tz_name:
        tz_name = 'Asia/Dubai'
    try:
        ZoneInfo(tz_name)
        return tz_name
    except Exception:
        return 'Asia/Dubai'


def get_workspace_zoneinfo(workspace_id):
    """Return ZoneInfo for workspace timezone."""
    return ZoneInfo(get_workspace_timezone_name(workspace_id))


def _loop_schedule_mode(loop):
    mode = (getattr(loop, 'schedule_mode', None) or 'interval').strip()
    return mode if mode in LoopConfig.SCHEDULE_MODES else LoopConfig.SCHEDULE_INTERVAL


def _loop_interval_delta(loop):
    hours = float(getattr(loop, 'interval_hours', 0) or 0)
    if hours <= 0:
        hours = 1.0
    return timedelta(hours=hours)


def _format_interval_hours(hours_value):
    try:
        hours = float(hours_value or 0)
    except (TypeError, ValueError):
        hours = 0
    if hours.is_integer():
        return f'{int(hours)}h'
    return f'{hours:g}h'


def _is_local_time_in_window(local_dt, start_time, end_time):
    """Check if local datetime is inside a daily window (supports overnight windows)."""
    current_time = local_dt.timetz().replace(tzinfo=None)
    if start_time == end_time:
        return False
    if start_time < end_time:
        return start_time <= current_time < end_time
    return current_time >= start_time or current_time < end_time


def _next_window_start_local(after_local_dt, start_time):
    """Return next local datetime when the daily window starts after the given moment."""
    candidate = datetime.combine(after_local_dt.date(), start_time, tzinfo=after_local_dt.tzinfo)
    if candidate <= after_local_dt:
        candidate = candidate + timedelta(days=1)
    return candidate


def _next_daily_exact_time_local(after_local_dt, exact_times):
    """Return next local datetime for exact daily times after the given moment."""
    for hhmm in exact_times:
        t = parse_hhmm(hhmm)
        candidate = datetime.combine(after_local_dt.date(), t, tzinfo=after_local_dt.tzinfo)
        if candidate > after_local_dt:
            return candidate
    if not exact_times:
        return None
    first_time = parse_hhmm(exact_times[0])
    return datetime.combine(after_local_dt.date() + timedelta(days=1), first_time, tzinfo=after_local_dt.tzinfo)


def validate_loop_schedule_payload(data):
    """Validate and normalize loop scheduling payload fields."""
    data = data or {}
    normalized = {}

    # interval_hours remains required for interval and windowed mode
    interval_hours_raw = data.get('interval_hours', None)
    if interval_hours_raw is not None:
        try:
            interval_hours = float(interval_hours_raw)
        except (TypeError, ValueError):
            raise ValueError('interval_hours must be greater than 0')
        if interval_hours <= 0:
            raise ValueError('interval_hours must be greater than 0')
        normalized['interval_hours'] = interval_hours

    schedule_mode = data.get('schedule_mode', LoopConfig.SCHEDULE_INTERVAL) or LoopConfig.SCHEDULE_INTERVAL
    schedule_mode = str(schedule_mode).strip()
    if schedule_mode not in LoopConfig.SCHEDULE_MODES:
        raise ValueError('Invalid schedule_mode')
    normalized['schedule_mode'] = schedule_mode

    if schedule_mode == LoopConfig.SCHEDULE_INTERVAL:
        if ('interval_hours' not in normalized) and ('interval_hours' in data):
            raise ValueError('interval_hours must be greater than 0')
        normalized['schedule_window_start'] = None
        normalized['schedule_window_end'] = None
        normalized['schedule_exact_times'] = []
        return normalized

    if schedule_mode == LoopConfig.SCHEDULE_WINDOWED_INTERVAL:
        interval_value = normalized.get('interval_hours')
        if interval_value is None:
            try:
                interval_value = float(data.get('interval_hours', 0))
            except (TypeError, ValueError):
                interval_value = 0
            if interval_value <= 0:
                raise ValueError('interval_hours must be greater than 0')
            normalized['interval_hours'] = interval_value

        start_raw = data.get('schedule_window_start')
        end_raw = data.get('schedule_window_end')
        if not start_raw or not end_raw:
            raise ValueError('schedule_window_start and schedule_window_end are required for windowed_interval')
        start_time = parse_hhmm(start_raw)
        end_time = parse_hhmm(end_raw)
        if start_time == end_time:
            raise ValueError('schedule_window_start and schedule_window_end cannot be the same')
        normalized['schedule_window_start'] = start_time.strftime('%H:%M')
        normalized['schedule_window_end'] = end_time.strftime('%H:%M')
        normalized['schedule_exact_times'] = []
        return normalized

    # daily_times
    exact_times = normalize_exact_times(data.get('schedule_exact_times', []))
    if not exact_times:
        raise ValueError('At least one exact time is required for daily_times')
    normalized['schedule_window_start'] = None
    normalized['schedule_window_end'] = None
    normalized['schedule_exact_times'] = exact_times
    return normalized


def is_loop_schedule_allowed_now(loop, now_utc=None):
    """Return (is_allowed, reason) for current time under loop schedule."""
    if now_utc is None:
        now_utc = datetime.utcnow()
    mode = _loop_schedule_mode(loop)
    if mode == LoopConfig.SCHEDULE_INTERVAL:
        return True, 'ready'

    tz = get_workspace_zoneinfo(loop.workspace_id)
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)

    if mode == LoopConfig.SCHEDULE_WINDOWED_INTERVAL:
        try:
            start_time = parse_hhmm(loop.schedule_window_start)
            end_time = parse_hhmm(loop.schedule_window_end)
        except ValueError:
            return False, 'waiting_window'
        in_window = _is_local_time_in_window(now_local, start_time, end_time)
        return (in_window, 'ready' if in_window else 'waiting_window')

    # daily_times
    exact_times = loop.get_schedule_exact_times() if hasattr(loop, 'get_schedule_exact_times') else []
    if not exact_times:
        return False, 'waiting_time'
    exact_times_set = set(exact_times)
    current_hhmm = now_local.strftime('%H:%M')
    in_exact_time = current_hhmm in exact_times_set
    return (in_exact_time, 'ready' if in_exact_time else 'waiting_time')


def compute_next_loop_run_at(loop, now_utc=None):
    """Compute next UTC execution time for a loop based on its schedule mode."""
    if now_utc is None:
        now_utc = datetime.utcnow()
    now_utc = now_utc.replace(microsecond=0)
    mode = _loop_schedule_mode(loop)

    if mode == LoopConfig.SCHEDULE_INTERVAL:
        return now_utc + _loop_interval_delta(loop)

    tz = get_workspace_zoneinfo(loop.workspace_id)
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)

    if mode == LoopConfig.SCHEDULE_WINDOWED_INTERVAL:
        try:
            start_time = parse_hhmm(loop.schedule_window_start)
            end_time = parse_hhmm(loop.schedule_window_end)
        except ValueError:
            return now_utc + _loop_interval_delta(loop)
        interval_delta = _loop_interval_delta(loop)
        if _is_local_time_in_window(now_local, start_time, end_time):
            candidate_local = now_local + interval_delta
            if _is_local_time_in_window(candidate_local, start_time, end_time):
                return candidate_local.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
        next_start_local = _next_window_start_local(now_local, start_time)
        return next_start_local.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)

    if mode == LoopConfig.SCHEDULE_DAILY_TIMES:
        exact_times = loop.get_schedule_exact_times() if hasattr(loop, 'get_schedule_exact_times') else []
        if not exact_times:
            return now_utc + _loop_interval_delta(loop)
        next_local = _next_daily_exact_time_local(now_local, exact_times)
        if next_local is None:
            return now_utc + _loop_interval_delta(loop)
        return next_local.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)

    return now_utc + _loop_interval_delta(loop)


def get_loop_schedule_summary(loop, workspace_id=None):
    """Human-readable schedule summary for loop table/API."""
    mode = _loop_schedule_mode(loop)
    interval_text = _format_interval_hours(loop.interval_hours)
    if mode == LoopConfig.SCHEDULE_WINDOWED_INTERVAL:
        if loop.schedule_window_start and loop.schedule_window_end:
            return f'Every {interval_text} in {loop.schedule_window_start}-{loop.schedule_window_end}'
        return f'Every {interval_text} (windowed)'
    if mode == LoopConfig.SCHEDULE_DAILY_TIMES:
        times = loop.get_schedule_exact_times() if hasattr(loop, 'get_schedule_exact_times') else []
        return f"At {', '.join(times)}" if times else 'At specific times'
    return f'Every {interval_text}'


def get_loop_schedule_status(loop, now_utc=None):
    """Computed schedule status for UI display."""
    if now_utc is None:
        now_utc = datetime.utcnow()
    if not loop.is_active:
        return 'stopped'
    if loop.is_paused:
        return 'manual_paused'

    mode = _loop_schedule_mode(loop)
    allowed, reason = is_loop_schedule_allowed_now(loop, now_utc=now_utc)
    if mode == LoopConfig.SCHEDULE_WINDOWED_INTERVAL and not allowed:
        return 'waiting_window'
    if mode == LoopConfig.SCHEDULE_DAILY_TIMES and not allowed:
        return 'waiting_time'

    if loop.next_run_at and loop.next_run_at > now_utc:
        if mode == LoopConfig.SCHEDULE_WINDOWED_INTERVAL:
            # If inside window but future schedule time, still "waiting_time"
            return 'waiting_time' if allowed else 'waiting_window'
        if mode == LoopConfig.SCHEDULE_DAILY_TIMES:
            return 'waiting_time'
    return 'ready'


def serialize_loop_for_api(loop):
    """Serialize loop with schedule metadata for UI/API."""
    data = loop.to_dict()
    ws_id = getattr(loop, 'workspace_id', None)
    data['schedule_timezone'] = get_workspace_timezone_name(ws_id)
    data['schedule_summary'] = get_loop_schedule_summary(loop, workspace_id=ws_id)
    data['schedule_status'] = get_loop_schedule_status(loop)
    return data

def execute_loop_job(loop_id):
    """Execute a single loop iteration"""
    with app.app_context():
        try:
            loop = LoopConfig.query.get(loop_id)
            if not loop or not loop.is_active or loop.is_paused:
                print(f"[LOOP] Loop {loop_id} is inactive or paused, skipping")
                return
            
            start_time = datetime.utcnow()
            
            # Get next listing in sequence
            loop_listing = loop.get_next_listing()
            if not loop_listing:
                print(f"[LOOP] Loop {loop_id} has no listings, skipping")
                return
            
            listing = loop_listing.listing
            if loop.workspace_id and listing and listing.workspace_id != loop.workspace_id:
                print(f"[LOOP] Listing {listing.id} not in workspace {loop.workspace_id}, skipping")
                return
            print(f"[LOOP] Executing loop '{loop.name}' for listing {listing.reference}")
            
            success = False
            message = ""
            pf_id = None
            
            try:
                client = get_client(workspace_id=loop.workspace_id)
                
                if loop.loop_type == 'duplicate':
                    # Create a duplicate listing
                    success, message, pf_id = create_duplicate_listing(loop, listing, client)
                else:  # delete_republish
                    # Delete from PF and republish
                    success, message, pf_id = delete_and_republish_listing(loop, listing, client)
                
                if success:
                    loop.consecutive_failures = 0
                    loop_listing.consecutive_failures = 0
                    loop_listing.times_processed += 1
                    loop_listing.last_processed_at = datetime.utcnow()
                else:
                    loop_listing.consecutive_failures += 1
                    loop.consecutive_failures += 1
                    
                    # Check if we need to stop the loop (2 consecutive listings failed 3 times each)
                    if loop.consecutive_failures >= 6:  # 2 listings × 3 attempts
                        loop.is_active = False
                        loop.is_paused = True
                        message += " [LOOP STOPPED - too many failures]"
                        print(f"[LOOP] Loop {loop_id} stopped due to too many failures")
            
            except Exception as exec_err:
                success = False
                message = str(exec_err)
                loop.consecutive_failures += 1
                loop_listing.consecutive_failures += 1
            
            # Log execution
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            log = LoopExecutionLog(
                loop_config_id=loop_id,
                listing_id=listing.id,
                action=loop.loop_type,
                success=success,
                message=message,
                pf_listing_id=pf_id,
                duration_ms=duration_ms
            )
            db.session.add(log)
            
            # Advance to next listing
            loop.advance_index()
            completed_at = datetime.utcnow()
            loop.last_run_at = completed_at
            loop.next_run_at = compute_next_loop_run_at(loop, now_utc=completed_at)
            db.session.commit()
            
            print(f"[LOOP] Completed: success={success}, message={message}")
            
        except Exception as e:
            import traceback
            print(f"[LOOP] Error executing loop {loop_id}: {e}")
            traceback.print_exc()


def create_duplicate_listing(loop, original_listing, client):
    """Create a duplicate of a listing and publish it"""
    try:
        # Check max duplicates limit
        if loop.max_duplicates > 0:
            existing_count = DuplicatedListing.query.filter_by(
                original_listing_id=original_listing.id,
                loop_config_id=loop.id,
                status='published'
            ).count()
            
            if existing_count >= loop.max_duplicates:
                # Delete oldest duplicate first
                oldest = DuplicatedListing.query.filter_by(
                    original_listing_id=original_listing.id,
                    loop_config_id=loop.id,
                    status='published'
                ).order_by(DuplicatedListing.created_at).first()
                
                if oldest and oldest.pf_listing_id:
                    try:
                        client.delete_listing(oldest.pf_listing_id)
                    except:
                        pass
                    oldest.status = 'deleted'
                    oldest.deleted_at = datetime.utcnow()
                    db.session.commit()
        
        # Get or create "Duplicated" folder
        ws_id = loop.workspace_id or original_listing.workspace_id
        dup_folder = ListingFolder.query.filter_by(name='Duplicated', workspace_id=ws_id).first()
        if not dup_folder:
            dup_folder = ListingFolder(
                workspace_id=ws_id,
                name='Duplicated',
                color='#9333ea',  # Purple
                icon='copy',
                description='Listings created by loop system'
            )
            db.session.add(dup_folder)
            db.session.commit()
        
        # Create duplicate listing in our DB
        import uuid
        dup_reference = f"{original_listing.reference}-DUP-{uuid.uuid4().hex[:6].upper()}"
        
        duplicate = LocalListing(
            workspace_id=ws_id,
            reference=dup_reference,
            folder_id=dup_folder.id,
            emirate=original_listing.emirate,
            city=original_listing.city,
            location=original_listing.location,
            location_id=original_listing.location_id,
            category=original_listing.category,
            offering_type=original_listing.offering_type,
            property_type=original_listing.property_type,
            bedrooms=original_listing.bedrooms,
            bathrooms=original_listing.bathrooms,
            size=original_listing.size,
            furnishing_type=original_listing.furnishing_type,
            project_status=original_listing.project_status,
            parking_slots=original_listing.parking_slots,
            floor_number=original_listing.floor_number,
            unit_number=original_listing.unit_number,
            price=original_listing.price,
            downpayment=original_listing.downpayment,
            rent_frequency=original_listing.rent_frequency,
            title_en=original_listing.title_en,
            title_ar=original_listing.title_ar,
            description_en=original_listing.description_en,
            description_ar=original_listing.description_ar,
            images=original_listing.images,
            video_tour=original_listing.video_tour,
            video_360=original_listing.video_360,
            amenities=original_listing.amenities,
            assigned_agent=original_listing.assigned_agent,
            assigned_to_id=original_listing.assigned_to_id,
            original_images=original_listing.original_images,
            developer=original_listing.developer,
            permit_number=original_listing.permit_number,
            available_from=original_listing.available_from,
            status='draft'
        )
        db.session.add(duplicate)
        db.session.commit()
        
        # Create on PropertyFinder
        pf_data = duplicate.to_pf_format()
        result = client.create_listing(pf_data)
        pf_id = result.get('id')
        
        if pf_id:
            duplicate.pf_listing_id = pf_id
            
            # Publish it
            client.publish_listing(pf_id)
            duplicate.status = 'published'
            
            # Track the duplicate
            dup_record = DuplicatedListing(
                original_listing_id=original_listing.id,
                duplicate_listing_id=duplicate.id,
                pf_listing_id=pf_id,
                loop_config_id=loop.id,
                status='published',
                published_at=datetime.utcnow()
            )
            db.session.add(dup_record)
            db.session.commit()
            
            return True, f"Created duplicate {dup_reference}", pf_id
        else:
            return False, "Failed to create on PropertyFinder", None
            
    except Exception as e:
        return False, str(e), None


def delete_and_republish_listing(loop, listing, client):
    """Delete listing from PF and republish it"""
    try:
        # Delete from PF if it exists there
        if listing.pf_listing_id:
            try:
                client.delete_listing(listing.pf_listing_id)
                print(f"[LOOP] Deleted PF listing {listing.pf_listing_id}")
            except Exception as del_err:
                print(f"[LOOP] Warning: Could not delete PF listing: {del_err}")
        
        # Clear PF ID
        old_pf_id = listing.pf_listing_id
        listing.pf_listing_id = None
        listing.status = 'draft'
        db.session.commit()
        
        # Create fresh listing on PF
        pf_data = listing.to_pf_format()
        result = client.create_listing(pf_data)
        pf_id = result.get('id')
        
        if pf_id:
            listing.pf_listing_id = pf_id
            
            # Publish it
            client.publish_listing(pf_id)
            listing.status = 'published'
            db.session.commit()
            
            return True, f"Republished as {pf_id} (was {old_pf_id})", pf_id
        else:
            return False, "Failed to create on PropertyFinder", None
            
    except Exception as e:
        return False, str(e), None


def start_loop_scheduler():
    """Initialize and start the loop scheduler"""
    try:
        # Add a job that checks for pending loops every minute
        loop_scheduler.add_job(
            func=check_and_run_loops,
            trigger=IntervalTrigger(minutes=1),
            id='loop_checker',
            name='Check and run pending loops',
            replace_existing=True
        )
        
        loop_scheduler.start()
        print("[SCHEDULER] Loop scheduler started")
        
        # Shutdown scheduler when app exits
        atexit.register(lambda: loop_scheduler.shutdown())
        
    except Exception as e:
        print(f"[SCHEDULER] Failed to start scheduler: {e}")


def check_and_run_loops():
    """Check for loops that need to run"""
    with app.app_context():
        try:
            now = datetime.utcnow()
            
            # Find active loops that are due
            due_loops = LoopConfig.query.filter(
                LoopConfig.is_active == True,
                LoopConfig.is_paused == False,
                db.or_(
                    LoopConfig.next_run_at == None,
                    LoopConfig.next_run_at <= now
                )
            ).all()
            
            for loop in due_loops:
                allowed_now, _reason = is_loop_schedule_allowed_now(loop, now_utc=now)
                if not allowed_now:
                    loop.next_run_at = compute_next_loop_run_at(loop, now_utc=now)
                    db.session.commit()
                    continue
                print(f"[SCHEDULER] Running loop: {loop.name}")
                execute_loop_job(loop.id)
                
        except Exception as e:
            print(f"[SCHEDULER] Error checking loops: {e}")


def auto_refresh_pf_data():
    """Background job to automatically refresh PropertyFinder data (per-workspace)."""
    with app.app_context():
        try:
            workspaces = Workspace.query.filter_by(is_active=True).all()
            if not workspaces:
                return
            
            for ws in workspaces:
                ws_id = ws.id
                
                # Check if auto-sync is enabled (workspace-scoped)
                auto_sync_enabled = AppSettings.get('auto_sync_enabled', 'true', workspace_id=ws_id) == 'true'
                if not auto_sync_enabled:
                    continue
                
                # Get sync interval (use existing setting name, default: 30 minutes)
                try:
                    sync_interval = int(AppSettings.get('sync_interval_minutes', '30', workspace_id=ws_id))
                except Exception:
                    sync_interval = 30
                
                # Check if cache is stale
                last_updated = PFCache.get_last_update('listings', workspace_id=ws_id)
                if last_updated:
                    age_minutes = (datetime.now() - last_updated).total_seconds() / 60
                    if age_minutes < sync_interval:
                        print(f"[AUTO-REFRESH] Cache is fresh ({age_minutes:.1f}m old), skipping (workspace_id={ws_id})")
                        continue
                
                print(f"[AUTO-REFRESH] Refreshing PropertyFinder data (workspace_id={ws_id})...")
                get_cached_pf_data(force_refresh=True, quick_load=False, workspace_id=ws_id)
                status_result = sync_local_listing_statuses_from_pf_cache(workspace_id=ws_id)
                if status_result.get('matched'):
                    print(f"[AUTO-REFRESH] Status sync (workspace_id={ws_id}): matched={status_result.get('matched')}, updated={status_result.get('updated')}")

                # Fetch credits (account-level analytics)
                try:
                    client = get_client(workspace_id=ws_id)
                    credits = client.get_credits()
                    cache = _get_pf_cache(workspace_id=ws_id)
                    cache['credits'] = credits
                    PFCache.set_cache('credits', credits, workspace_id=ws_id)
                except Exception as e:
                    print(f"[AUTO-REFRESH] Credits sync failed (workspace_id={ws_id}): {e}")
            
            print("[AUTO-REFRESH] Complete")
            
        except Exception as e:
            print(f"[AUTO-REFRESH] Error: {e}")


# Start the scheduler
start_loop_scheduler()

# Add auto-refresh job (check every 5 minutes, but only refresh if stale)
try:
    loop_scheduler.add_job(
        func=auto_refresh_pf_data,
        trigger=IntervalTrigger(minutes=5),
        id='pf_auto_refresh',
        name='Auto-refresh PropertyFinder data',
        replace_existing=True
    )
    print("[SCHEDULER] PF auto-refresh job added")
except Exception as e:
    print(f"[SCHEDULER] Failed to add auto-refresh job: {e}")


# ==================== GLOBAL ERROR HANDLER ====================

@app.errorhandler(404)
def handle_not_found(e):
    """Return JSON for API 404s and plain text for UI routes."""
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'NotFound: 404 Not Found'}), 404
    return 'Page not found', 404


@app.errorhandler(Exception)
def handle_exception(e):
    """Log all unhandled exceptions"""
    import traceback
    error_msg = str(e)
    print(f"[ERROR] Unhandled exception: {error_msg}")
    traceback.print_exc()
    # Return JSON for API requests
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': error_msg, 'type': type(e).__name__}), 500
    return jsonify({'error': error_msg}), 500


# ==================== AUTHENTICATION ====================

def get_current_user():
    """Get the currently logged-in user"""
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login', next=request.url))
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_active:
            session.clear()
            flash('Your session has expired. Please log in again.', 'warning')
            return redirect(url_for('login'))
        
        g.user = user
        return f(*args, **kwargs)
    return decorated_function


def permission_required(permission):
    """Decorator to require a specific permission"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('login', next=request.url))
            
            user = User.query.get(session['user_id'])
            if not user or not user.is_active:
                session.clear()
                flash('Your session has expired. Please log in again.', 'warning')
                return redirect(url_for('login'))

            if is_system_admin(user):
                g.user = user
                return f(*args, **kwargs)

            g.user = user

            # In workspace context, use workspace role/module permissions only.
            ws = None
            try:
                ws = get_active_workspace()
            except Exception:
                ws = None

            if ws:
                extra_permissions = set(get_workspace_user_extra_permissions(user.id, workspace_id=ws.id))
                module_action_map = {
                    'view': ('listings', 'read'),
                    'create': ('listings', 'create'),
                    'edit': ('listings', 'edit'),
                    'delete': ('listings', 'delete'),
                    'publish': ('listings', 'publish'),
                    'bulk_upload': ('listings', 'bulk'),
                    'manage_leads': ('leads', 'read'),
                    'manage_users': ('users', 'read'),
                    'settings': ('settings', 'edit'),
                }
                module_action = module_action_map.get(permission)
                if module_action:
                    from src.services.permissions import get_permission_service
                    service = get_permission_service()
                    module, action = module_action
                    if not service.check_module_access(user, ws.id, module, action) and permission not in extra_permissions:
                        flash('You do not have permission to access this feature.', 'error')
                        return redirect(url_for('workspace_dashboard', workspace_slug=ws.slug))
                elif permission not in extra_permissions:
                    flash('You do not have permission to access this feature.', 'error')
                    return redirect(url_for('workspace_dashboard', workspace_slug=ws.slug))
            elif not user.has_permission(permission):
                flash(f'You do not have permission to access this feature.', 'error')
                return redirect(url_for('index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.before_request
def load_user():
    """Load user and workspace context before each request"""
    g.user = None
    g.workspace = None
    g.is_workspace_context = False
    g.is_admin_context = False
    
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])
    
    # Check if we're in a workspace context (URL starts with workspace slug)
    # Skip for static files and API routes
    if request.path.startswith('/static') or request.path.startswith('/api/'):
        return
    
    # Check for admin context
    if request.path.startswith('/admin'):
        g.is_admin_context = True
        return
    
    # Check for workspace context
    parts = request.path.strip('/').split('/')
    if parts and parts[0] and parts[0] not in RESERVED_WORKSPACE_SLUGS:
        # Check if first part is a workspace slug
        workspace = Workspace.query.filter_by(slug=parts[0], is_active=True).first()
        if workspace:
            g.workspace = workspace
            g.is_workspace_context = True


@app.context_processor
def inject_user():
    """Make user and workspace available in all templates"""
    sys_admin = False
    try:
        sys_admin = is_system_admin(g.user)
    except Exception:
        sys_admin = False
    return dict(
        current_user=g.user,
        current_workspace=getattr(g, 'workspace', None),
        is_workspace_context=getattr(g, 'is_workspace_context', False),
        is_admin_context=getattr(g, 'is_admin_context', False),
        is_system_admin=sys_admin
    )


def is_system_admin(user):
    """Check if user has SYSTEM_ADMIN role via permission service."""
    if not user:
        return False
    from src.services.permissions import get_permission_service
    service = get_permission_service()
    return service.is_system_admin(user)


def has_any_system_role(user):
    """Return True if the user has any platform/system role assignment."""
    if not user:
        return False
    try:
        user_id = user.id if hasattr(user, 'id') else int(user)
    except (TypeError, ValueError):
        return False
    return UserSystemRole.query.filter_by(user_id=user_id).first() is not None


def is_workspace_eligible_user(user):
    """Workspace org users must not also be platform/system users."""
    return bool(user) and not has_any_system_role(user)


WORKSPACE_USER_EXTRA_PERMISSIONS_KEY_PREFIX = 'workspace_user_extra_permissions'


def _workspace_user_extra_permissions_key(user_id):
    return f'{WORKSPACE_USER_EXTRA_PERMISSIONS_KEY_PREFIX}:{int(user_id)}'


def get_workspace_user_extra_permissions(user_id=None, workspace_id=None):
    """Workspace-scoped additive permissions for a user (legacy keys, but not global)."""
    try:
        user = getattr(g, 'user', None)
        resolved_user_id = int(user_id if user_id is not None else (user.id if user else 0))
    except (TypeError, ValueError):
        return []
    if not resolved_user_id:
        return []
    raw = AppSettings.get(_workspace_user_extra_permissions_key(resolved_user_id), '[]', workspace_id=workspace_id)
    try:
        parsed = json.loads(raw) if raw else []
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    allowed = set(User.ALL_PERMISSIONS)
    return [p for p in parsed if isinstance(p, str) and p in allowed]


def set_workspace_user_extra_permissions(user_id, permissions_list, workspace_id=None):
    """Persist workspace-scoped additive permissions for a user."""
    try:
        resolved_user_id = int(user_id)
    except (TypeError, ValueError):
        return []
    allowed = set(User.ALL_PERMISSIONS)
    normalized = []
    for perm in permissions_list or []:
        if isinstance(perm, str) and perm in allowed and perm not in normalized:
            normalized.append(perm)
    AppSettings.set(
        _workspace_user_extra_permissions_key(resolved_user_id),
        json.dumps(normalized),
        workspace_id=workspace_id
    )
    return normalized


def _get_system_user_ids(user_ids):
    """Return a set of user IDs that have at least one system role assignment."""
    ids = []
    for value in (user_ids or []):
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    rows = db.session.query(UserSystemRole.user_id).filter(UserSystemRole.user_id.in_(ids)).distinct().all()
    return {row[0] for row in rows}


def _filter_workspace_memberships_for_org(memberships):
    """Hide platform users from workspace member lists and counts."""
    memberships = list(memberships or [])
    system_user_ids = _get_system_user_ids([m.user_id for m in memberships])
    visible = [m for m in memberships if m.user_id not in system_user_ids]
    return visible, system_user_ids


def workspace_to_org_dict(workspace, include_members=False, include_connections=False):
    """Workspace dict with system-user memberships excluded from org-facing counts/lists."""
    data = workspace.to_dict(include_members=include_members, include_connections=include_connections)
    visible_members, _ = _filter_workspace_memberships_for_org(workspace.members)
    data['member_count'] = len(visible_members)
    if include_members:
        data['members'] = [m.to_dict() for m in visible_members]
    return data


def _user_can_access_workspace(user, workspace):
    """Return True when user is allowed to access workspace context."""
    if not user or not workspace or not workspace.is_active:
        return False
    if is_system_admin(user):
        return True
    membership = WorkspaceMember.query.filter_by(
        workspace_id=workspace.id,
        user_id=user.id
    ).first()
    return membership is not None


def _get_valid_workspace_from_session(user):
    """Resolve session workspace ids and sanitize invalid ones."""
    if not user or not has_request_context():
        return None

    sys_admin = is_system_admin(user)
    selected = None
    keys = ('active_workspace_id',) if sys_admin else ('active_workspace_id', 'current_workspace_id')
    for key in keys:
        ws_id = session.get(key)
        if not ws_id:
            continue
        ws = Workspace.query.get(ws_id)
        if ws and _user_can_access_workspace(user, ws):
            if key == 'active_workspace_id':
                return ws
            if selected is None:
                selected = ws
            continue
        print(f"[WORKSPACE_CTX] sanitize_session user={user.id} key={key} ws_id={ws_id} path={request.path}")
        session.pop(key, None)
    if sys_admin and session.get('current_workspace_id') and not session.get('active_workspace_id'):
        # System admins must explicitly enter workspace using active_workspace_id.
        session.pop('current_workspace_id', None)
    return selected


def _get_user_default_workspace(user):
    """Fallback workspace for org users (first membership by workspace id)."""
    if not user or is_system_admin(user):
        return None
    memberships = WorkspaceMember.query.filter_by(user_id=user.id).order_by(WorkspaceMember.workspace_id.asc()).all()
    for membership in memberships:
        ws = Workspace.query.get(membership.workspace_id)
        if ws and ws.is_active:
            return ws
    return None


def get_active_workspace():
    """Resolve active workspace for current request/session."""
    try:
        user = getattr(g, 'user', None)
        ws = getattr(g, 'workspace', None)
        if ws and _user_can_access_workspace(user, ws):
            return ws
        if ws and user and not _user_can_access_workspace(user, ws):
            print(f"[WORKSPACE_CTX] reject_request_workspace user={user.id} ws_id={ws.id} path={request.path}")

        session_ws = _get_valid_workspace_from_session(user)
        if session_ws:
            return session_ws

        fallback_ws = _get_user_default_workspace(user)
        if fallback_ws:
            return fallback_ws
    except Exception:
        return None
    return None


def get_active_workspace_id():
    ws = get_active_workspace()
    return ws.id if ws else None


def get_default_workspace_id():
    """Fallback workspace for non-authenticated contexts (e.g., webhooks)."""
    try:
        ws = Workspace.query.filter_by(is_active=True).order_by(Workspace.id.asc()).first()
        return ws.id if ws else None
    except Exception:
        return None


def scope_query(query, workspace_id):
    """Apply workspace filter if workspace_id is provided."""
    if workspace_id:
        return query.filter_by(workspace_id=workspace_id)
    return query


def get_workspace_membership_for_user(user=None, workspace_id=None):
    """Return workspace membership for the given user/workspace in request contexts."""
    user = user or getattr(g, 'user', None)
    if not user:
        return None
    ws_id = workspace_id or get_active_workspace_id()
    if not ws_id:
        return None

    cached_membership = getattr(g, 'workspace_membership', None)
    if cached_membership and cached_membership.user_id == user.id and cached_membership.workspace_id == ws_id:
        return cached_membership

    return WorkspaceMember.query.filter_by(workspace_id=ws_id, user_id=user.id).first()


def workspace_user_can_manage_all_listings(user=None, workspace_id=None):
    """Workspace owners/admins and system admins can see/manage all workspace listings."""
    user = user or getattr(g, 'user', None)
    if not user:
        return False
    if is_system_admin(user):
        return True
    membership = get_workspace_membership_for_user(user=user, workspace_id=workspace_id)
    return bool(membership and membership.role in ('owner', 'admin'))


def visible_local_listing_query(workspace_id=None, user=None):
    """Listing query scoped to workspace and current user's row-level visibility."""
    ws_id = workspace_id or get_active_workspace_id()
    query = scope_query(LocalListing.query, ws_id)
    user = user or getattr(g, 'user', None)
    if not user:
        return query.filter(LocalListing.id == -1)
    if workspace_user_can_manage_all_listings(user=user, workspace_id=ws_id):
        return query
    # Non-admin workspace users only see listings assigned to them.
    return query.filter(LocalListing.assigned_to_id == user.id)


def require_workspace_listing_admin(f):
    """Decorator: listing organization actions are admin-only within a workspace."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ws_id = get_active_workspace_id()
        if workspace_user_can_manage_all_listings(workspace_id=ws_id):
            return f(*args, **kwargs)

        message = 'This action is available to workspace admins only.'
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': message}), 403

        flash(message, 'error')
        ws = get_active_workspace()
        if ws:
            return redirect(url_for('workspace_dashboard', workspace_slug=ws.slug))
        return redirect(url_for('index'))
    return decorated


def workspace_user_can_manage_all_leads(user=None, workspace_id=None):
    """Lead visibility/admin actions follow the same role rules as listings."""
    return workspace_user_can_manage_all_listings(user=user, workspace_id=workspace_id)


def visible_lead_query(workspace_id=None, user=None):
    """Lead query scoped to workspace and current user's row-level visibility."""
    ws_id = workspace_id or get_active_workspace_id()
    query = scope_query(Lead.query, ws_id)
    user = user or getattr(g, 'user', None)
    if not user:
        return query.filter(Lead.id == -1)
    if workspace_user_can_manage_all_leads(user=user, workspace_id=ws_id):
        return query
    # Non-admin workspace users only see leads assigned to them.
    return query.filter(Lead.assigned_to_id == user.id)


def get_visible_lead_or_404(lead_id, workspace_id=None, user=None):
    """Fetch a lead through visibility rules (404 if hidden/missing)."""
    ws_id = workspace_id or get_active_workspace_id()
    return visible_lead_query(workspace_id=ws_id, user=user).filter_by(id=lead_id).first_or_404()


def require_workspace_leads_admin(f):
    """Decorator: lead maintenance/sync actions are admin-only."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ws_id = get_active_workspace_id()
        if workspace_user_can_manage_all_leads(workspace_id=ws_id):
            return f(*args, **kwargs)

        message = 'This lead action is available to workspace admins only.'
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': message}), 403

        flash(message, 'error')
        ws = get_active_workspace()
        if ws:
            return redirect(url_for('workspace_leads', workspace_slug=ws.slug))
        return redirect(url_for('index'))
    return decorated


def workspace_user_can_manage_loops(user=None, workspace_id=None):
    """Loops are restricted to workspace admins/owners (and system admins)."""
    return workspace_user_can_manage_all_listings(user=user, workspace_id=workspace_id)


def require_workspace_loops_admin(f):
    """Decorator: loops are admin-only until loop ownership is implemented."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ws_id = get_active_workspace_id()
        if workspace_user_can_manage_loops(workspace_id=ws_id):
            return f(*args, **kwargs)

        message = 'Loops are visible to workspace admins only.'
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': message}), 403

        flash(message, 'error')
        ws = get_active_workspace()
        if ws:
            return redirect(url_for('workspace_dashboard', workspace_slug=ws.slug))
        return redirect(url_for('index'))
    return decorated


def require_active_workspace(f):
    """Decorator to ensure an active workspace context is available."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ws = get_active_workspace()
        if not ws:
            if request.path.startswith('/api/'):
                print(f"[WORKSPACE_CTX] workspace_required_api user={getattr(g.user, 'id', None)} path={request.path}")
                return jsonify({'success': False, 'error': 'workspace_required'}), 400
            if g.user and is_system_admin(g.user):
                print(f"[WORKSPACE_CTX] workspace_required_system_admin user={g.user.id} redirect=system_admin")
                flash('Select a workspace to continue.', 'warning')
                return redirect(url_for('system_admin_page'))
            print(f"[WORKSPACE_CTX] workspace_required_logout user={getattr(g.user, 'id', None)} redirect=logout")
            flash('You are not assigned to any workspace.', 'warning')
            return redirect(url_for('logout'))
        g.workspace = ws
        g.is_workspace_context = True
        if g.user and not is_system_admin(g.user):
            g.workspace_membership = WorkspaceMember.query.filter_by(
                workspace_id=ws.id,
                user_id=g.user.id
            ).first()
        else:
            g.workspace_membership = None
        session['current_workspace_id'] = ws.id
        return f(*args, **kwargs)
    return decorated


# ==================== INVITE/RESET HELPERS ====================
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _public_base_url():
    if APP_PUBLIC_URL:
        return APP_PUBLIC_URL.rstrip('/')
    return request.url_root.rstrip('/')


def _is_workspace_admin(user_id, workspace_id):
    member = WorkspaceMember.query.filter_by(workspace_id=workspace_id, user_id=user_id).first()
    return member and member.role in ('owner', 'admin')


def can_manage_workspace_members(user, workspace_id):
    """Allow workspace owners/admins and system admins to manage workspace members."""
    if not user:
        return False
    if is_system_admin(user):
        return True
    return bool(_is_workspace_admin(user.id, workspace_id))


def _validate_assignee(workspace_id, assignee_id):
    if not assignee_id:
        return None
    try:
        assignee_id = int(assignee_id)
    except (TypeError, ValueError):
        return None
    member = WorkspaceMember.query.filter_by(workspace_id=workspace_id, user_id=assignee_id).first()
    if not member:
        return None
    return assignee_id


def _normalize_upload_path(path):
    """Normalize stored upload paths to a relative path (e.g., listings/123/file.jpg)."""
    if not path or not isinstance(path, str):
        return None
    path = path.strip()
    if not path:
        return None
    if path.startswith('http') and '/uploads/' in path:
        path = path.split('/uploads/', 1)[1]
    if path.startswith('/uploads/'):
        path = path[len('/uploads/'):]
    if path.startswith('/'):
        path = path.lstrip('/')
    return path or None


USER_IMAGE_SETTING_DEFAULTS = {
    'image_default_ratio': 'landscape_16_9',
    'image_default_size': 'full_hd',
    'image_quality': '90',
    'image_format': 'JPEG',
    'image_qr_enabled': 'false',
    'image_default_qr_data': '',
    'image_qr_data': '',
    'image_qr_position': 'bottom_right',
    'image_qr_size_percent': '12',
    'image_qr_color': '#000000',
    'image_logo_enabled': 'false',
    'image_logo_position': 'bottom_left',
    'image_logo_size': '15',
    'image_logo_opacity': '80',
    'image_default_logo': '',
}


def _resolve_image_settings_scope(workspace_id=None, user_id=None):
    ws_id = workspace_id if workspace_id is not None else get_active_workspace_id()
    if user_id is None:
        user = getattr(g, 'user', None)
        user_id = user.id if user else None
    return ws_id, user_id


def _user_image_setting_key(key, user_id):
    return f'user_image:{user_id}:{key}'


def get_user_image_setting(key, default=None, workspace_id=None, user_id=None):
    """Read image editor preference stored per-user-per-workspace."""
    ws_id, resolved_user_id = _resolve_image_settings_scope(workspace_id=workspace_id, user_id=user_id)
    fallback = USER_IMAGE_SETTING_DEFAULTS.get(key, '')
    if default is not None:
        fallback = default
    if not resolved_user_id:
        return fallback
    return AppSettings.get(_user_image_setting_key(key, resolved_user_id), fallback, workspace_id=ws_id)


def set_user_image_setting(key, value, workspace_id=None, user_id=None):
    """Persist image editor preference per-user-per-workspace."""
    ws_id, resolved_user_id = _resolve_image_settings_scope(workspace_id=workspace_id, user_id=user_id)
    if not resolved_user_id:
        return None
    return AppSettings.set(_user_image_setting_key(key, resolved_user_id), value, workspace_id=ws_id)


def get_all_user_image_settings(workspace_id=None, user_id=None):
    """Return normalized image editor settings for the current user/workspace."""
    ws_id, resolved_user_id = _resolve_image_settings_scope(workspace_id=workspace_id, user_id=user_id)
    return {
        'image_default_ratio': get_user_image_setting('image_default_ratio', workspace_id=ws_id, user_id=resolved_user_id),
        'image_default_size': get_user_image_setting('image_default_size', workspace_id=ws_id, user_id=resolved_user_id),
        'image_quality': get_user_image_setting('image_quality', workspace_id=ws_id, user_id=resolved_user_id),
        'image_format': get_user_image_setting('image_format', workspace_id=ws_id, user_id=resolved_user_id),
        'image_qr_enabled': get_user_image_setting('image_qr_enabled', workspace_id=ws_id, user_id=resolved_user_id),
        'image_qr_data': get_user_image_setting('image_default_qr_data', workspace_id=ws_id, user_id=resolved_user_id),
        'image_qr_position': get_user_image_setting('image_qr_position', workspace_id=ws_id, user_id=resolved_user_id),
        'image_qr_size_percent': get_user_image_setting('image_qr_size_percent', workspace_id=ws_id, user_id=resolved_user_id),
        'image_qr_color': get_user_image_setting('image_qr_color', workspace_id=ws_id, user_id=resolved_user_id),
        'image_logo_enabled': get_user_image_setting('image_logo_enabled', workspace_id=ws_id, user_id=resolved_user_id),
        'image_logo_position': get_user_image_setting('image_logo_position', workspace_id=ws_id, user_id=resolved_user_id),
        'image_logo_size': get_user_image_setting('image_logo_size', workspace_id=ws_id, user_id=resolved_user_id),
        'image_logo_opacity': get_user_image_setting('image_logo_opacity', workspace_id=ws_id, user_id=resolved_user_id),
        'image_default_logo': get_user_image_setting('image_default_logo', workspace_id=ws_id, user_id=resolved_user_id),
    }


def _load_original_images_raw(listing):
    """Return a list of stored original image paths (relative to uploads/)."""
    if not listing or not listing.original_images:
        return []
    try:
        parsed = json.loads(listing.original_images)
        images = parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        images = listing.original_images.split('|') if listing.original_images else []
    result = []
    for img in images:
        if isinstance(img, str):
            normalized = _normalize_upload_path(img)
            if normalized:
                result.append(normalized)
    return result


def _merge_unique_paths(existing, new_items):
    """Merge two lists of paths while preserving order and uniqueness."""
    seen = set()
    merged = []
    for item in (existing or []) + (new_items or []):
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _get_invite_by_token(token):
    token_hash = _hash_token(token)
    return WorkspaceInvite.query.filter_by(token_hash=token_hash).first()


def _invite_is_valid(invite):
    if not invite or invite.accepted_at or invite.revoked_at:
        return False
    if invite.expires_at and invite.expires_at < datetime.utcnow():
        return False
    return True


def _accept_invite(invite, user):
    if user.email.lower() != invite.email.lower():
        return False, 'This invite was sent to a different email.'
    if has_any_system_role(user):
        return False, 'Platform/system users cannot join workspaces through invite links.'
    membership = WorkspaceMember.query.filter_by(
        workspace_id=invite.workspace_id,
        user_id=user.id
    ).first()
    if not membership:
        membership = WorkspaceMember(
            workspace_id=invite.workspace_id,
            user_id=user.id,
            role=invite.role or 'member',
            invited_by_id=invite.invited_by_id
        )
        db.session.add(membership)
    invite.accepted_at = datetime.utcnow()
    db.session.commit()
    return True, None


# ==================== CACHE ====================
# In-memory cache for PropertyFinder data (backed by DB for persistence)
_pf_cache = {}

def _resolve_pf_workspace_id(workspace_id=None):
    if workspace_id is not None:
        return workspace_id
    try:
        return get_active_workspace_id()
    except Exception:
        return None

def _get_pf_cache(workspace_id=None):
    ws_id = _resolve_pf_workspace_id(workspace_id)
    key = str(ws_id) if ws_id is not None else 'global'
    cache = _pf_cache.get(key)
    if not cache:
        cache = {
            'listings': None,  # None = not loaded yet, [] = empty
            'users': None,
            'leads': None,
            'locations': None,
            'credits': None,
            'last_updated': None,
            'cache_duration': 1800,  # 30 minutes in seconds (was 5 min)
            'db_loaded': False,  # Track if we've loaded from DB
            'error': None,
            'workspace_id': ws_id
        }
        _pf_cache[key] = cache
    return cache

def get_cached_listings(workspace_id=None):
    """Get cached listings - lazy load from DB (workspace-aware)."""
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    if cache['listings'] is None:
        # Load from DB cache
        db_data = PFCache.get_cache('listings', workspace_id=ws_id)
        cache['listings'] = db_data if db_data else []
        cache['last_updated'] = PFCache.get_last_update('listings', workspace_id=ws_id)
        cache['db_loaded'] = True
        print(f"[Cache] Loaded {len(cache['listings'])} listings from DB cache (workspace_id={ws_id})")
    return cache['listings']

def get_cached_users(workspace_id=None):
    """Get cached users - lazy load from DB (workspace-aware)."""
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    if cache['users'] is None:
        cache['users'] = PFCache.get_cache('users', workspace_id=ws_id) or []
    return cache['users']

def get_cached_leads(workspace_id=None):
    """Get cached leads - lazy load from DB (workspace-aware)."""
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    if cache['leads'] is None:
        cache['leads'] = PFCache.get_cache('leads', workspace_id=ws_id) or []
    return cache['leads']

def get_cached_locations(workspace_id=None):
    """Get cached locations - lazy load from DB (workspace-aware)."""
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    if cache['locations'] is None:
        cached = PFCache.get_cache('locations', workspace_id=ws_id)
        cache['locations'] = cached if isinstance(cached, dict) else {}
    return cache['locations']

def build_location_map(listings, force_refresh=False, workspace_id=None):
    """Build a map of location IDs to names - uses cache only, no API calls for performance."""
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    
    # Get existing location cache from DB
    location_map = get_cached_locations(workspace_id=ws_id)
    
    # If we have any cache, just return it - don't make slow API calls
    if location_map and not force_refresh:
        return location_map
    
    # Only fetch locations if explicitly refreshing or cache is empty
    if not force_refresh:
        return location_map
    
    # Get all unique location IDs from listings that are missing
    missing_ids = set()
    for l in listings:
        loc_id = l.get('location', {}).get('id')
        if loc_id and str(loc_id) not in location_map:
            missing_ids.add(loc_id)
    
    if not missing_ids:
        return location_map
    
    print(f"[Locations] Fetching {len(missing_ids)} missing location(s)... (workspace_id={ws_id})")
    
    # Fetch location names from API - limit to 3 quick searches only
    try:
        client = get_client(workspace_id=ws_id)
        # Only search the main emirates to avoid too many API calls
        search_terms = ['Dubai', 'Abu Dhabi', 'Sharjah']
        
        for term in search_terms[:3]:  # Limit to 3 searches max
            if not missing_ids:
                break
            try:
                result = client.get_locations(search=term, per_page=100)
                for loc in result.get('data', []):
                    loc_id = str(loc.get('id'))
                    if loc_id not in location_map:
                        tree = loc.get('tree', [])
                        if tree:
                            names = [t.get('name', '') for t in tree]
                            location_map[loc_id] = ' > '.join(names)
                        else:
                            location_map[loc_id] = loc.get('name', f'Location {loc_id}')
                        missing_ids.discard(int(loc_id))
            except Exception:
                pass
        
        # Save to cache
        cache['locations'] = location_map
        PFCache.set_cache('locations', location_map, workspace_id=ws_id)
        
    except Exception as e:
        print(f"Error building location map: {e}")
    
    return location_map

def load_cache_from_db(workspace_id=None):
    """Load cached data from database on first access - LAZY loading (workspace-aware)."""
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    # Only load last_updated, not the actual data (get from listings cache specifically)
    if cache['last_updated'] is None:
        cache['last_updated'] = PFCache.get_last_update('listings', workspace_id=ws_id)

def get_cached_pf_data(force_refresh=False, quick_load=False, workspace_id=None):
    """Get PropertyFinder data with caching (DB-backed, workspace-aware).
    
    Args:
        force_refresh: If True, fetch fresh data from API
        quick_load: If True, only fetch first page of listings (faster)
        workspace_id: Workspace to scope cache/data
    """
    cache = _get_pf_cache(workspace_id)
    ws_id = cache['workspace_id']
    
    # Load timestamp from DB on first access
    load_cache_from_db(workspace_id=ws_id)
    
    # Check if cache is valid (don't refresh if we have data and it's recent)
    if not force_refresh and cache['last_updated']:
        age = (datetime.now() - cache['last_updated']).total_seconds()
        # Use lazy loading - only load listings when needed
        cached_listings = get_cached_listings(workspace_id=ws_id)
        if age < cache['cache_duration'] and cached_listings:
            # Load the rest lazily for return
            return {
                'listings': cached_listings,
                'users': get_cached_users(workspace_id=ws_id),
                'leads': get_cached_leads(workspace_id=ws_id),
                'credits': cache['credits'],
                'last_updated': cache['last_updated'],
                'error': None
            }
    
    # If we have data from DB but it's older, return it immediately (background refresh later)
    has_cached_data = len(get_cached_listings(workspace_id=ws_id)) > 0
    
    # Fetch fresh data
    try:
        client = get_client(workspace_id=ws_id)
        
        # Fetch listings (paginated) - increased limits for large portfolios
        all_listings = []
        page = 1
        max_pages = 10 if quick_load else 50  # Quick: 500 listings, Full: 2500 listings max
        
        while page <= max_pages:
            result = client.get_listings(page=page, per_page=50)
            listings = result.get('results', [])
            if not listings:
                break
            all_listings.extend(listings)
            
            pagination = result.get('pagination', {})
            if page >= pagination.get('totalPages', 1):
                break
            page += 1
        
        cache['listings'] = all_listings
        PFCache.set_cache('listings', all_listings, workspace_id=ws_id)
        
        # Fetch users (single page only)
        try:
            users_result = client.get_users(per_page=50)
            cache['users'] = users_result.get('data', [])
            PFCache.set_cache('users', cache['users'], workspace_id=ws_id)
        except:
            pass
        
        # Fetch leads - limit to 2 pages max for performance
        if not quick_load:
            try:
                all_leads = []
                leads_page = 1
                max_leads_pages = 2  # 100 leads max
                
                while leads_page <= max_leads_pages:
                    leads_result = client.get_leads(page=leads_page, per_page=50)
                    leads = leads_result.get('data', [])
                    if not leads:
                        break
                    all_leads.extend(leads)
                    
                    leads_pagination = leads_result.get('pagination', {})
                    if leads_page >= leads_pagination.get('totalPages', 1):
                        break
                    leads_page += 1
                
                cache['leads'] = all_leads
                PFCache.set_cache('leads', all_leads, workspace_id=ws_id)
                
                # Sync leads to CRM (in background ideally)
                sync_pf_leads_to_db(all_leads, workspace_id=ws_id)
            except:
                cache['leads'] = []
        
        # Skip credits fetch for performance (fetch on-demand if needed)
        
        cache['last_updated'] = datetime.now()
        cache['error'] = None
        AppSettings.set('last_sync_at', datetime.now().isoformat(), workspace_id=ws_id)
        
    except PropertyFinderAPIError as e:
        cache['error'] = f"API Error: {e.message}"
        if has_cached_data:
            return cache
    except Exception as e:
        cache['error'] = f"Error: {str(e)}"
        if has_cached_data:
            return cache
    
    return cache


def sync_pf_leads_to_db(pf_leads, workspace_id=None):
    """Sync PropertyFinder leads to CRM database (workspace-aware)."""
    from database import Lead
    from dateutil import parser as date_parser
    
    ws_id = _resolve_pf_workspace_id(workspace_id)
    
    # Build a map of PF agent email -> L-Manager user for auto-assignment
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    if ws_id:
        lm_users = User.query.join(
            WorkspaceMember, WorkspaceMember.user_id == User.id
        ).filter(
            WorkspaceMember.workspace_id == ws_id,
            User.is_active == True
        ).all()
    else:
        lm_users = User.query.filter_by(is_active=True).all()
    
    # Map PF agent email to L-Manager user
    email_to_lm_user = {u.email.lower(): u for u in lm_users}
    
    # Map PF agent ID to their email
    pf_agent_email_map = {}
    for pf_user in pf_users:
        pf_id = pf_user.get('publicProfile', {}).get('id')
        pf_email = pf_user.get('email', '').lower()
        if pf_id and pf_email:
            pf_agent_email_map[str(pf_id)] = pf_email
    
    imported = 0
    updated = 0
    for pf_lead in pf_leads:
        try:
            pf_id = str(pf_lead.get('id', ''))
            if not pf_id:
                continue
            
            # Check if already exists
            if ws_id:
                existing = Lead.query.filter_by(
                    source='propertyfinder',
                    source_id=pf_id,
                    workspace_id=ws_id
                ).first()
            else:
                existing = Lead.query.filter_by(source='propertyfinder', source_id=pf_id).first()
            
            # Extract contact info from sender - new structure has contacts array
            sender = pf_lead.get('sender', {})
            contacts = sender.get('contacts', [])
            
            # Extract phone/email from contacts array
            phone = ''
            email = ''
            whatsapp = ''
            for contact in contacts:
                if contact.get('type') == 'phone':
                    phone = contact.get('value', '')
                    # If channel is whatsapp, this is also the whatsapp number
                    if pf_lead.get('channel') == 'whatsapp':
                        whatsapp = phone
                elif contact.get('type') == 'email':
                    email = contact.get('value', '')
            
            # Get agent info from publicProfile
            public_profile = pf_lead.get('publicProfile', {})
            
            # Parse received date
            received_at = None
            if pf_lead.get('createdAt'):
                try:
                    received_at = date_parser.parse(pf_lead.get('createdAt'))
                except:
                    pass
            
            # Get listing info
            listing_info = pf_lead.get('listing', {})
            listing_id = str(listing_info.get('id', ''))
            listing_ref = listing_info.get('reference', listing_id)
            
            if existing:
                # Update existing lead with new PF status if changed
                if existing.pf_status != pf_lead.get('status'):
                    existing.pf_status = pf_lead.get('status', '')
                    existing.response_link = pf_lead.get('responseLink', '')
                    updated += 1
                continue
            
            # Auto-assign to L-Manager user based on PF agent email
            pf_agent_id_str = str(public_profile.get('id', ''))
            assigned_to_id = None
            if pf_agent_id_str and pf_agent_id_str in pf_agent_email_map:
                pf_agent_email = pf_agent_email_map[pf_agent_id_str]
                if pf_agent_email in email_to_lm_user:
                    assigned_to_id = email_to_lm_user[pf_agent_email].id
            
            # Create new lead
            lead = Lead(
                source='propertyfinder',
                source_id=pf_id,
                channel=pf_lead.get('channel', ''),
                name=sender.get('name', 'Unknown'),
                email=email,
                phone=phone,
                whatsapp=whatsapp,
                message=pf_lead.get('message', ''),
                listing_reference=listing_ref,
                pf_listing_id=listing_id,
                response_link=pf_lead.get('responseLink', ''),
                status='new',
                pf_status=pf_lead.get('status', ''),
                priority='medium',
                pf_agent_id=pf_agent_id_str,
                pf_agent_name=public_profile.get('name', ''),
                assigned_to_id=assigned_to_id,  # Auto-assigned if email matches
                received_at=received_at,
                workspace_id=ws_id
            )
            db.session.add(lead)
            imported += 1
        except Exception as e:
            print(f"Error importing lead {pf_lead.get('id')}: {e}")
            continue
    
    if imported > 0 or updated > 0:
        try:
            db.session.commit()
            if imported > 0:
                print(f"✓ Synced {imported} new leads to database")
            if updated > 0:
                print(f"✓ Updated {updated} existing leads")
        except Exception as e:
            db.session.rollback()
            print(f"Error committing leads: {e}")

# Configuration
ALLOWED_EXTENSIONS = {'json', 'csv'}
UPLOAD_FOLDER.mkdir(exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max for image processing


# Global error handler for unhandled exceptions
@app.errorhandler(Exception)
def handle_exception(e):
    """Handle unhandled exceptions"""
    import traceback
    print(f"[ERROR] Unhandled exception: {type(e).__name__}: {e}")
    traceback.print_exc()
    
    # Check if it's an API request
    if request.path.startswith('/api/'):
        return jsonify({
            'error': f'{type(e).__name__}: {str(e)}',
            'success': False
        }), 500
    
    # For non-API requests, show error page or redirect
    flash(f'An error occurred: {str(e)}', 'error')
    return redirect(url_for('index'))


def get_client(workspace_id=None):
    """Get PropertyFinder API client"""
    workspace_id = workspace_id if workspace_id is not None else get_active_workspace_id()
    if workspace_id:
        conn = WorkspaceConnection.query.filter_by(
            workspace_id=workspace_id,
            provider='propertyfinder',
            is_active=True
        ).first()
        if conn:
            creds = conn.get_credentials()
            api_key = creds.get('api_key')
            api_secret = creds.get('api_secret')
            base_url = creds.get('base_url') or Config.API_BASE_URL
            if api_key and api_secret:
                if Config.DEBUG:
                    print(f"[DEBUG] Using workspace PF credentials (workspace_id={workspace_id})")
                return PropertyFinderClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
    if Config.DEBUG and workspace_id:
        print(f"[DEBUG] No workspace PF credentials found (workspace_id={workspace_id}); using env")
    return PropertyFinderClient()


def resolve_assigned_agent_id(local_listing, client):
    """
    Ensure assigned_agent is a PF publicProfile ID.
    Accepts numeric PF IDs or agent email and auto-maps to PF ID.
    """
    assigned_raw = local_listing.assigned_agent
    assigned = str(assigned_raw).strip() if assigned_raw is not None else ''
    if not assigned:
        return False, 'Assigned Agent is required (email or PF ID).'

    if assigned.isdigit():
        return True, None

    if '@' not in assigned:
        return False, 'Assigned Agent must be a PF ID or a valid email.'

    target_email = assigned.lower()

    ws_id = local_listing.workspace_id or get_active_workspace_id()
    # Try cached users first
    users = PFCache.get_cache('users', workspace_id=ws_id) or []
    if not users:
        users = []
        page = 1
        per_page = 50
        max_pages = 20
        while page <= max_pages:
            result = client.get_users(page=page, per_page=per_page)
            data = result.get('data', []) if isinstance(result, dict) else []
            if not data:
                break
            users.extend(data)

            pagination = result.get('pagination', {}) if isinstance(result, dict) else {}
            total_pages = pagination.get('totalPages') or pagination.get('total_pages')
            if total_pages and page >= int(total_pages):
                break
            if len(data) < per_page:
                break
            page += 1

        if users:
            PFCache.set_cache('users', users, workspace_id=ws_id)

    matched = None
    for user in users:
        email = (user.get('email') or user.get('user', {}).get('email') or '').lower()
        if email == target_email:
            matched = user
            break

    if not matched:
        return False, f'Assigned Agent email not found in PropertyFinder users: {assigned}'

    public_profile = matched.get('publicProfile') or {}
    pf_id = public_profile.get('id') or matched.get('publicProfileId') or matched.get('id')
    if not pf_id:
        return False, f'Assigned Agent has no public profile ID in PropertyFinder: {assigned}'

    local_listing.assigned_agent = str(pf_id)
    db.session.commit()
    return True, None


def validate_location_id(local_listing, client):
    """Validate listing location_id against PropertyFinder search results."""
    if not local_listing.location_id:
        return False, 'Location ID is required. Please re-select the location from search.'
    location_text = (local_listing.location or '').strip()
    if not location_text:
        return False, 'Location name is required. Please re-select the location from search.'

    try:
        result = client.get_locations(search=location_text, page=1)
    except PropertyFinderAPIError as e:
        return False, f'Failed to validate location with PropertyFinder: {e.message}'

    locations = []
    if isinstance(result, dict):
        if isinstance(result.get('data'), list):
            locations = result.get('data', [])
        elif isinstance(result.get('results'), list):
            locations = result.get('results', [])
        elif isinstance(result.get('locations'), list):
            locations = result.get('locations', [])
    elif isinstance(result, list):
        locations = result

    if not locations:
        return False, 'PropertyFinder did not return any locations for the selected text. Please re-select the location from search.'

    try:
        target_id = int(local_listing.location_id)
    except (ValueError, TypeError):
        return False, 'Location ID is invalid. Please re-select the location from search.'

    for loc in locations:
        try:
            if int(loc.get('id')) == target_id:
                return True, None
        except (ValueError, TypeError):
            continue

    return False, 'Invalid Location ID for PropertyFinder. Please re-select the location from search.'


def validate_media_urls(listing_data):
    """
    Validate that media image URLs are publicly accessible.
    Returns (ok, message, failed_urls, warnings).
    """
    media = listing_data.get('media') or {}
    images = media.get('images') or []
    urls = []
    for img in images:
        if not isinstance(img, dict):
            continue
        original = img.get('original') or {}
        url = original.get('url')
        if url:
            urls.append(str(url).strip())

    warnings = []
    max_warn = getattr(Config, 'MAX_IMAGES_WARN', 15)
    if max_warn and len(urls) > max_warn:
        warnings.append(
            f"Listing has {len(urls)} images (recommended <= {max_warn}). Consider reducing images."
        )

    if not urls:
        return True, None, None, warnings
    if getattr(Config, 'SKIP_MEDIA', False):
        return True, None, None, warnings

    import requests

    failed = []
    for url in urls:
        if not url:
            continue
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10)
            status = resp.status_code
            resp.close()
            if status == 405:
                resp = requests.get(url, allow_redirects=True, timeout=10, stream=True)
                resp.raw.read(1024)
                status = resp.status_code
                resp.close()
            if status < 200 or status >= 400:
                failed.append(url)
        except requests.RequestException:
            failed.append(url)

    if not failed:
        return True, None, None, warnings

    max_list = 3
    shown = failed[:max_list]
    remaining = len(failed) - len(shown)
    message = f"One or more image URLs are not publicly reachable: {', '.join(shown)}"
    if remaining > 0:
        message += f" (and {remaining} more)"
    message += ". Check APP_PUBLIC_URL and ensure images are publicly accessible."
    return False, message, shown, warnings


def map_pf_state_to_local_status(pf_state: str):
    """Map PF listing state to local listing status."""
    if not pf_state:
        return None
    pf_state = str(pf_state).lower()
    if pf_state in ('live', 'published', 'live_pending_unpublishing'):
        return 'live'
    if pf_state in (
        'draft',
        'pf_draft',
        'unpublished',
        'takendown',
        'live_unpublishing_failed',
        'live_pending_deletion',
        'takendown_pending_deletion',
    ):
        return 'draft'
    return None


def _extract_pf_listings(response: dict):
    """Extract listings list from varying PF response shapes."""
    if not isinstance(response, dict):
        return []
    if isinstance(response.get('data'), list):
        return response['data']
    results = response.get('results')
    if isinstance(results, dict) and isinstance(results.get('items'), list):
        return results['items']
    if isinstance(response.get('items'), list):
        return response['items']
    return []


def _normalize_pf_listing(response: dict):
    """Normalize PF listing response to a listing dict."""
    if not isinstance(response, dict):
        return None
    if isinstance(response.get('data'), dict):
        return response.get('data')
    return response


def _pf_listing_reference(listing: dict):
    """Get reference string from a PF listing dict."""
    if not isinstance(listing, dict):
        return ''
    ref = listing.get('reference')
    if not ref and isinstance(listing.get('listing'), dict):
        ref = listing.get('listing', {}).get('reference')
    return str(ref).strip() if ref else ''


def extract_pf_state_from_listing(listing: dict):
    """Extract listing state from PF listing payload."""
    if not isinstance(listing, dict):
        return None
    state = listing.get('state')
    if isinstance(state, dict):
        state = state.get('stage') or state.get('type') or state.get('state')
    if not state:
        is_live = listing.get('portals', {}).get('propertyfinder', {}).get('isLive')
        if is_live is True:
            state = 'live'
        elif is_live is False:
            state = 'draft'
    return state


def find_pf_listing_in_cache_by_reference(reference: str, workspace_id=None):
    """Find PF listing in cached listings by reference (workspace-aware)."""
    ref = str(reference or '').strip().lower()
    if not ref:
        return None
    for listing in get_cached_listings(workspace_id=workspace_id) or []:
        if _pf_listing_reference(listing).lower() == ref:
            return listing
    return None


def find_pf_listing_in_cache_by_id(pf_listing_id: str, workspace_id=None):
    """Find PF listing in cached listings by PF listing id (workspace-aware)."""
    if not pf_listing_id:
        return None
    target = str(pf_listing_id)
    for listing in get_cached_listings(workspace_id=workspace_id) or []:
        if str(listing.get('id')) == target:
            return listing
    return None


def find_pf_listing_by_reference(client, reference: str):
    """Find PF listing by reference using filter[reference]."""
    if not reference:
        return None
    try:
        resp = client.get_listings(page=1, per_page=1, **{'filter[reference]': reference})
    except Exception:
        return None
    listings = _extract_pf_listings(resp)
    if not listings:
        return None
    return listings[0]


def sync_local_listing_statuses_from_pf_cache(workspace_id=None):
    """Sync LocalListing.status using cached PF listings (no extra API calls, workspace-aware)."""
    ws_id = _resolve_pf_workspace_id(workspace_id)
    pf_listings = get_cached_listings(workspace_id=ws_id) or []
    if not pf_listings:
        return {'matched': 0, 'updated': 0, 'changed': 0}

    pf_by_id = {}
    pf_by_ref = {}
    for listing in pf_listings:
        if not isinstance(listing, dict):
            continue
        pf_id = listing.get('id')
        if pf_id is not None:
            pf_by_id[str(pf_id)] = listing
        ref = _pf_listing_reference(listing)
        if ref:
            pf_by_ref[ref.lower()] = listing

    matched = 0
    updated = 0
    changed = 0

    query = LocalListing.query
    if ws_id:
        query = query.filter_by(workspace_id=ws_id)
    for local in query.all():
        pf_listing = None
        if local.pf_listing_id:
            pf_listing = pf_by_id.get(str(local.pf_listing_id))
        if not pf_listing and local.reference:
            pf_listing = pf_by_ref.get(str(local.reference).strip().lower())
        if not pf_listing:
            continue

        matched += 1
        pf_id = pf_listing.get('id')
        if pf_id and (not local.pf_listing_id or str(local.pf_listing_id) != str(pf_id)):
            local.pf_listing_id = str(pf_id)
            changed += 1

        pf_state = extract_pf_state_from_listing(pf_listing)
        new_status = map_pf_state_to_local_status(pf_state)
        if new_status and local.status != new_status:
            local.status = new_status
            local.updated_at = datetime.utcnow()
            updated += 1
            changed += 1

    if changed:
        db.session.commit()

    return {'matched': matched, 'updated': updated, 'changed': changed}


def generate_reference_id():
    """Generate a unique reference ID for listings"""
    import uuid
    import time
    # Format: REF-YYYYMMDD-XXXXX (e.g., REF-20251227-A3B4C)
    date_part = time.strftime('%Y%m%d')
    unique_part = uuid.uuid4().hex[:5].upper()
    return f"REF-{date_part}-{unique_part}"


def convert_google_drive_url(url):
    """
    Convert Google Drive share links to direct CDN URLs.
    
    Supports formats:
    - https://drive.google.com/file/d/FILE_ID/view?usp=sharing
    - https://drive.google.com/open?id=FILE_ID
    - https://drive.google.com/uc?id=FILE_ID
    
    Returns CDN URL: https://lh3.googleusercontent.com/d/FILE_ID
    This format is more reliable for external services like PropertyFinder.
    """
    import re
    
    if not url:
        return url
    
    url = url.strip()
    
    # Already a CDN URL
    if 'lh3.googleusercontent.com' in url:
        return url
    
    # Not a Google Drive URL
    if 'drive.google.com' not in url:
        return url
    
    # Pattern 1: /file/d/FILE_ID/
    match = re.search(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    
    # Pattern 2: ?id=FILE_ID or &id=FILE_ID
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    
    # Could not extract ID, return as-is
    return url


def process_image_urls(images_input):
    """
    Process image URLs from form input.
    Handles:
    - JSON array (new format from image manager)
    - Newline-separated text (legacy)
    - Pipe-separated text (legacy)
    
    Converts Google Drive links to direct URLs.
    Returns JSON string of URLs (new format).
    """
    if not images_input:
        return '[]'
    
    urls = []
    
    # Try JSON first (new format)
    try:
        parsed = json.loads(images_input)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    url = item.strip()
                elif isinstance(item, dict):
                    url = item.get('url', '')
                else:
                    continue
                
                if url and url.lower() != 'none':
                    # Convert Google Drive links
                    if 'drive.google.com' in url:
                        url = convert_google_drive_url(url)
                    urls.append(url)
            return json.dumps(urls)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Fall back to text format (legacy)
    for line in images_input.replace('|', '\n').split('\n'):
        url = line.strip()
        if url and url.lower() != 'none' and (url.startswith('http://') or url.startswith('https://') or url.startswith('/')):
            # Convert Google Drive links
            if 'drive.google.com' in url:
                url = convert_google_drive_url(url)
            urls.append(url)
    
    return json.dumps(urls)


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def api_error_handler(f):
    """Decorator to handle API errors"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except PropertyFinderAPIError as e:
            request_id = None
            details = None
            cloudfront = None
            meta = None
            if isinstance(e.response, dict):
                request_id = e.response.get('_request_id')
                details = e.response.get('errors') or e.response.get('error') or e.response.get('raw')
                cloudfront = e.response.get('_cloudfront')
                meta = {
                    'status_code': e.response.get('_status_code'),
                    'content_type': e.response.get('_content_type'),
                    'headers': e.response.get('_headers'),
                }
                if not any(meta.values()):
                    meta = None
            if request.path.startswith('/api/') or request.is_json or request.headers.get('Accept') == 'application/json':
                return jsonify({
                    'error': e.message,
                    'status_code': e.status_code,
                    'request_id': request_id,
                    'details': details,
                    'cloudfront': cloudfront,
                    'meta': meta
                }), e.status_code or 500
            if cloudfront and isinstance(cloudfront, dict):
                cf_id = cloudfront.get('cf_id')
                if cf_id:
                    flash(
                        f'PropertyFinder CDN blocked this request. Try again later or contact PF support with CloudFront ID: {cf_id}',
                        'error'
                    )
                else:
                    flash('PropertyFinder CDN blocked this request. Try again later or contact PF support.', 'error')
            elif request_id:
                flash(f'API Error: {e.message} (Request ID: {request_id})', 'error')
            else:
                flash(f'API Error: {e.message}', 'error')
            return redirect(request.referrer or url_for('index'))
        except Exception as e:
            if request.path.startswith('/api/') or request.is_json or request.headers.get('Accept') == 'application/json':
                return jsonify({'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'error')
            return redirect(request.referrer or url_for('index'))
    return decorated_function


def transform_api_listing_to_local(api_listing):
    """
    Transform PropertyFinder API listing response to local database field format.
    This allows the listing_form.html template to work with API data.
    """
    # Extract price info
    price = None
    price_type = None
    if api_listing.get('price'):
        price_obj = api_listing['price']
        price_type = price_obj.get('type', 'sale')
        amounts = price_obj.get('amounts', {})
        # Get the appropriate price based on type
        price = amounts.get(price_type) or amounts.get('sale') or amounts.get('yearly')
    
    # Extract location info
    location = ''
    if api_listing.get('location'):
        loc = api_listing['location']
        if isinstance(loc, dict):
            location = loc.get('fullName', {}).get('en', '') if isinstance(loc.get('fullName'), dict) else str(loc.get('id', ''))
    
    # Extract images
    images = []
    if api_listing.get('media', {}).get('images'):
        for img in api_listing['media']['images']:
            if isinstance(img, dict):
                url = img.get('medium', {}).get('url') or img.get('original', {}).get('url')
                if url:
                    images.append(url)
    
    return {
        'id': api_listing.get('id'),
        'reference': api_listing.get('reference', ''),
        'emirate': api_listing.get('uaeEmirate', ''),
        'city': '',  # Not directly available in API response
        'location': location,
        'category': api_listing.get('category', ''),
        'offering_type': price_type if price_type in ['sale', 'rent'] else ('rent' if price_type in ['yearly', 'monthly', 'weekly', 'daily'] else 'sale'),
        'property_type': api_listing.get('type', ''),
        'bedrooms': api_listing.get('bedrooms', ''),
        'bathrooms': api_listing.get('bathrooms', ''),
        'size': api_listing.get('size'),
        'furnishing_type': api_listing.get('furnishingType', ''),
        'project_status': api_listing.get('projectStatus', ''),
        'parking_slots': api_listing.get('parkingSlots'),
        'floor_number': api_listing.get('floorNumber', ''),
        'price': price,
        'downpayment': api_listing.get('price', {}).get('downPayment', 0) if api_listing.get('price') else 0,
        'rent_frequency': price_type if price_type in ['yearly', 'monthly', 'weekly', 'daily'] else '',
        'title_en': api_listing.get('title', {}).get('en', '') if isinstance(api_listing.get('title'), dict) else '',
        'title_ar': api_listing.get('title', {}).get('ar', '') if isinstance(api_listing.get('title'), dict) else '',
        'description_en': api_listing.get('description', {}).get('en', '') if isinstance(api_listing.get('description'), dict) else '',
        'description_ar': api_listing.get('description', {}).get('ar', '') if isinstance(api_listing.get('description'), dict) else '',
        'images': images,
        'video_tour': api_listing.get('media', {}).get('videos', {}).get('default', ''),
        'video_360': api_listing.get('media', {}).get('videos', {}).get('view360', ''),
        'amenities': api_listing.get('amenities', []),
        'assigned_agent': api_listing.get('assignedTo', {}).get('name', '') if api_listing.get('assignedTo') else '',
        'developer': api_listing.get('developer', ''),
        'status': 'live' if api_listing.get('portals', {}).get('propertyfinder', {}).get('isLive') else 'draft',
        'pf_listing_id': api_listing.get('id'),
        'created_at': api_listing.get('createdAt'),
    }


# ==================== AUTH PAGES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if g.user:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Contact an administrator.', 'error')
                return render_template('login.html')
            
            # Log in the user
            session.clear()
            session['user_id'] = user.id
            if remember:
                session.permanent = True
            
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            flash(f'Welcome back, {user.name}!', 'success')
            
            # Redirect to next page or index
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'error')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout the user"""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/users')
@permission_required('manage_users')
@require_active_workspace
def users_page():
    """User management page (workspace-scoped)"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_users', workspace_slug=ws.slug))
    ws_id = get_active_workspace_id()
    memberships = WorkspaceMember.query.filter_by(workspace_id=ws_id).all()
    user_ids = [m.user_id for m in memberships]
    users = User.query.filter(User.id.in_(user_ids)).order_by(User.created_at.desc()).all()
    membership_by_user_id = {m.user_id: m for m in memberships}
    user_payloads = []
    for u in users:
        data = u.to_dict()
        member = membership_by_user_id.get(u.id)
        if member:
            data['workspace_member_id'] = member.id
            data['workspace_role'] = member.role
            data['workspace_role_name'] = WorkspaceMember.ROLES.get(member.role, {}).get('name', member.role.title())
            extra_perms = get_workspace_user_extra_permissions(u.id, workspace_id=ws_id)
            data['workspace_custom_permissions'] = extra_perms
            data['has_workspace_custom_permissions'] = bool(extra_perms)
        user_payloads.append(data)
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    workspace = Workspace.query.get(ws_id)
    can_manage_members = can_manage_workspace_members(g.user, ws_id)
    return render_template('users.html', 
                           users=user_payloads,
                           roles=User.ROLES,
                           workspace_member_roles=WorkspaceMember.ROLES,
                           all_permissions=User.ALL_PERMISSIONS,
                           pf_users=pf_users,
                           workspace=workspace.to_dict() if workspace else None,
                           workspace_memberships={m.user_id: m for m in memberships},
                           workspace_memberships_json={m.user_id: m.to_dict() for m in memberships},
                           can_manage_members=can_manage_members)


@app.route('/users/create', methods=['POST'])
@permission_required('manage_users')
def create_user():
    """Create a new user"""
    ws = get_active_workspace()
    if ws:
        flash('Use workspace invites and membership management from the Workspace Users page.', 'error')
        return redirect(url_for('workspace_users', workspace_slug=ws.slug))

    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'viewer')
    pf_agent_id = request.form.get('pf_agent_id', '').strip() or None
    pf_agent_name = request.form.get('pf_agent_name', '').strip() or None
    use_custom_permissions = request.form.get('use_custom_permissions') == 'on'
    
    if not email or not name or not password:
        flash('All fields are required.', 'error')
        return redirect(url_for('users_page'))
    
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('users_page'))
    
    if role not in User.ROLES:
        flash('Invalid role selected.', 'error')
        return redirect(url_for('users_page'))
    
    user = User(email=email, name=name, role=role, pf_agent_id=pf_agent_id, pf_agent_name=pf_agent_name)
    user.set_password(password)
    
    # Set custom permissions if enabled
    if use_custom_permissions:
        custom_perms = request.form.getlist('custom_permissions')
        user.set_custom_permissions(custom_perms)
    
    db.session.add(user)
    db.session.flush()

    ws_id = get_active_workspace_id()
    if ws_id:
        existing_member = WorkspaceMember.query.filter_by(workspace_id=ws_id, user_id=user.id).first()
        if not existing_member:
            member_role = 'admin' if role == 'admin' else 'member'
            db.session.add(WorkspaceMember(
                workspace_id=ws_id,
                user_id=user.id,
                role=member_role,
                invited_by_id=g.user.id
            ))
    db.session.commit()
    
    flash(f'User "{name}" created successfully.', 'success')
    return redirect(url_for('users_page'))


@app.route('/users/<int:user_id>/edit', methods=['POST'])
@permission_required('manage_users')
def edit_user(user_id):
    """Edit a user"""
    ws = get_active_workspace()
    if ws:
        flash('Use workspace membership management from the Workspace Users page.', 'error')
        return redirect(url_for('workspace_users', workspace_slug=ws.slug))

    user = User.query.get_or_404(user_id)
    
    # Prevent editing the last admin
    if user.role == 'admin' and User.query.filter_by(role='admin', is_active=True).count() == 1:
        if request.form.get('role') != 'admin' or request.form.get('is_active') == 'false':
            flash('Cannot demote or deactivate the last admin.', 'error')
            return redirect(url_for('users_page'))
    
    user.name = request.form.get('name', user.name).strip()
    user.role = request.form.get('role', user.role)
    user.is_active = request.form.get('is_active') != 'false'
    user.pf_agent_id = request.form.get('pf_agent_id', '').strip() or None
    user.pf_agent_name = request.form.get('pf_agent_name', '').strip() or None
    
    # Handle custom permissions
    use_custom_permissions = request.form.get('use_custom_permissions') == 'on'
    if use_custom_permissions:
        custom_perms = request.form.getlist('custom_permissions')
        user.set_custom_permissions(custom_perms)
    else:
        user.set_custom_permissions(None)  # Clear custom permissions, use role defaults
    
    # Update password if provided
    new_password = request.form.get('password', '').strip()
    if new_password:
        user.set_password(new_password)
    
    db.session.commit()
    flash(f'User "{user.name}" updated successfully.', 'success')
    return redirect(url_for('users_page'))


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@permission_required('manage_users')
def delete_user(user_id):
    """Delete a user"""
    ws = get_active_workspace()
    if ws:
        flash('Remove users from the workspace from the Workspace Users page instead of deleting the account.', 'error')
        return redirect(url_for('workspace_users', workspace_slug=ws.slug))

    user = User.query.get_or_404(user_id)
    
    # Prevent self-deletion
    if user.id == g.user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('users_page'))
    
    # Prevent deleting the last admin
    if user.role == 'admin' and User.query.filter_by(role='admin', is_active=True).count() == 1:
        flash('Cannot delete the last admin.', 'error')
        return redirect(url_for('users_page'))
    
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User "{user.name}" deleted.', 'success')
    return redirect(url_for('users_page'))


# ==================== WORKSPACE INVITES & PASSWORD RESET ====================

@app.route('/api/workspaces/<int:workspace_id>/invites', methods=['POST'])
@login_required
def api_create_workspace_invite(workspace_id):
    """Create an invite link for a workspace"""
    workspace = Workspace.query.get_or_404(workspace_id)
    if not can_manage_workspace_members(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    role = (data.get('role') or 'member').strip()
    expires_in = int(data.get('expires_in', 7 * 24 * 3600))
    
    if not email:
        return jsonify({'success': False, 'error': 'Email is required'}), 400
    if role not in WorkspaceMember.ROLES:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        if has_any_system_role(existing_user):
            return jsonify({'success': False, 'error': 'System users cannot be invited to workspaces.'}), 400
        existing_member = WorkspaceMember.query.filter_by(workspace_id=workspace_id, user_id=existing_user.id).first()
        if existing_member:
            return jsonify({'success': False, 'error': 'User is already a member of this workspace'}), 400
    
    token = _generate_token()
    invite = WorkspaceInvite(
        workspace_id=workspace_id,
        email=email,
        role=role,
        token_hash=_hash_token(token),
        invited_by_id=g.user.id,
        expires_at=datetime.utcnow() + timedelta(seconds=expires_in)
    )
    db.session.add(invite)
    db.session.commit()
    
    invite_link = f"{_public_base_url()}/invite/{token}"
    return jsonify({
        'success': True,
        'invite': invite.to_dict(),
        'invite_link': invite_link
    })


@app.route('/api/workspaces/<int:workspace_id>/invites', methods=['GET'])
@login_required
def api_list_workspace_invites(workspace_id):
    """List pending invites for a workspace"""
    if not can_manage_workspace_members(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    invites = WorkspaceInvite.query.filter_by(workspace_id=workspace_id).order_by(WorkspaceInvite.created_at.desc()).all()
    pending = []
    for inv in invites:
        if inv.accepted_at or inv.revoked_at:
            continue
        if inv.expires_at and inv.expires_at < datetime.utcnow():
            continue
        pending.append(inv.to_dict())
    return jsonify({'success': True, 'invites': pending})


@app.route('/api/workspaces/<int:workspace_id>/invites/<int:invite_id>/revoke', methods=['POST'])
@login_required
def api_revoke_workspace_invite(workspace_id, invite_id):
    """Revoke a workspace invite"""
    if not can_manage_workspace_members(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    invite = WorkspaceInvite.query.filter_by(id=invite_id, workspace_id=workspace_id).first_or_404()
    invite.revoked_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/invite/<token>', methods=['GET', 'POST'])
def accept_invite(token):
    """Accept a workspace invite"""
    invite = _get_invite_by_token(token)
    if not _invite_is_valid(invite):
        return render_template('invite_accept.html', error='This invite link is invalid or expired.')
    
    workspace = Workspace.query.get(invite.workspace_id)
    if request.method == 'GET':
        if g.user:
            ok, err = _accept_invite(invite, g.user)
            if not ok:
                return render_template('invite_accept.html', invite=invite, workspace=workspace, error=err)
            flash('You have joined the workspace.', 'success')
            return redirect(url_for('workspace_dashboard', workspace_slug=workspace.slug))
        
        existing_user = User.query.filter_by(email=invite.email).first()
        if existing_user and has_any_system_role(existing_user):
            return render_template(
                'invite_accept.html',
                invite=invite,
                workspace=workspace,
                error='This email belongs to a platform/system user and cannot join a workspace by invite.'
            )
        return render_template(
            'invite_accept.html',
            invite=invite,
            workspace=workspace,
            existing_user=bool(existing_user),
            login_next=url_for('login', next=request.path)
        )
    
    # POST: create user + accept invite
    name = (request.form.get('name') or '').strip()
    password = request.form.get('password') or ''
    
    if not name or not password:
        return render_template('invite_accept.html', invite=invite, workspace=workspace, error='Name and password are required.')
    
    existing_user = User.query.filter_by(email=invite.email).first()
    if existing_user:
        if has_any_system_role(existing_user):
            return render_template(
                'invite_accept.html',
                invite=invite,
                workspace=workspace,
                error='This email belongs to a platform/system user and cannot join a workspace by invite.'
            )
        return render_template('invite_accept.html', invite=invite, workspace=workspace, error='This email already exists. Please log in to accept the invite.')
    
    user = User(email=invite.email, name=name, role='user')
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    
    membership = WorkspaceMember(
        workspace_id=invite.workspace_id,
        user_id=user.id,
        role=invite.role or 'member',
        invited_by_id=invite.invited_by_id
    )
    db.session.add(membership)
    invite.accepted_at = datetime.utcnow()
    db.session.commit()
    
    session['user_id'] = user.id
    flash('Account created and workspace joined.', 'success')
    return redirect(url_for('workspace_dashboard', workspace_slug=workspace.slug))


@app.route('/api/workspaces/<int:workspace_id>/members/<int:member_id>/reset-password', methods=['POST'])
@login_required
def api_create_reset_link(workspace_id, member_id):
    """Create a password reset link for a workspace member"""
    if not can_manage_workspace_members(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    member = WorkspaceMember.query.filter_by(id=member_id, workspace_id=workspace_id).first_or_404()
    if member.user and has_any_system_role(member.user):
        return jsonify({'success': False, 'error': 'System users are managed from System Admin only.'}), 400
    token = _generate_token()
    reset = PasswordResetToken(
        user_id=member.user_id,
        token_hash=_hash_token(token),
        created_by_id=g.user.id,
        expires_at=datetime.utcnow() + timedelta(hours=24)
    )
    db.session.add(reset)
    db.session.commit()
    
    reset_link = f"{_public_base_url()}/reset/{token}"
    return jsonify({'success': True, 'reset_link': reset_link})


@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Reset password using a token"""
    token_hash = _hash_token(token)
    reset = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if not reset or reset.used_at or (reset.expires_at and reset.expires_at < datetime.utcnow()):
        return render_template('reset_password.html', error='This reset link is invalid or expired.')
    
    if request.method == 'GET':
        return render_template('reset_password.html')
    
    password = request.form.get('password') or ''
    if not password:
        return render_template('reset_password.html', error='Password is required.')
    
    user = User.query.get(reset.user_id)
    if not user:
        return render_template('reset_password.html', error='User not found.')
    
    user.set_password(password)
    reset.used_at = datetime.utcnow()
    db.session.commit()
    
    flash('Password updated successfully.', 'success')
    return redirect(url_for('login'))


@app.route('/permissions')
@permission_required('manage_users')
def permissions_page():
    """Permission Center - manage user permissions"""
    if not is_system_admin(g.user):
        ws = get_active_workspace()
        if ws:
            return redirect(url_for('workspace_dashboard', workspace_slug=ws.slug))
        flash('Access denied. System administrators only.', 'error')
        return redirect(url_for('index'))
    users = User.query.filter(User.role != 'admin').order_by(User.name).all()
    pf_users = PFCache.get_cache('users', workspace_id=get_active_workspace_id()) or []
    return render_template('permissions.html',
                           users=[u.to_dict() for u in users],
                           sections=User.SECTIONS,
                           action_labels=User.ACTION_LABELS,
                           pf_users=pf_users)


@app.route('/api/users/<int:user_id>/permissions', methods=['GET'])
@permission_required('manage_users')
def api_get_user_permissions(user_id):
    """Get a user's permissions"""
    user = User.query.get_or_404(user_id)
    return jsonify({
        'success': True,
        'user': user.to_dict(),
        'section_permissions': user.get_section_permissions()
    })


@app.route('/api/users/<int:user_id>/permissions', methods=['PUT'])
@permission_required('manage_users')
def api_update_user_permissions(user_id):
    """Update a user's permissions"""
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    
    if user.role == 'admin':
        return jsonify({'success': False, 'error': 'Cannot modify admin permissions'}), 400
    
    section_permissions = data.get('section_permissions', {})
    user.set_section_permissions(section_permissions)
    
    # Update PF agent restriction if provided
    if 'pf_agent_id' in data:
        user.pf_agent_id = data.get('pf_agent_id') or None
        user.pf_agent_name = data.get('pf_agent_name') or None
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'user': user.to_dict(),
        'message': f'Permissions updated for {user.name}'
    })


# ==================== WORKSPACE-SCOPED ROUTES ====================
# These routes are for accessing a specific workspace's CRM
# URL pattern: /<workspace_slug>/...

def require_workspace_access(f):
    """Decorator to ensure user has access to the workspace"""
    @wraps(f)
    def decorated_function(workspace_slug, *args, **kwargs):
        workspace = Workspace.query.filter_by(slug=workspace_slug, is_active=True).first()
        if not workspace:
            flash('Workspace not found.', 'error')
            print(f"[WORKSPACE_CTX] workspace_not_found user={getattr(g.user, 'id', None)} slug={workspace_slug} redirect=index")
            return redirect(url_for('index'))
        
        if not g.user:
            return redirect(url_for('login'))
        
        # Check if user is member of this workspace
        membership = WorkspaceMember.query.filter_by(
            workspace_id=workspace.id,
            user_id=g.user.id
        ).first()
        
        # System admins can access only after explicit enter
        sys_admin = is_system_admin(g.user)
        if sys_admin:
            active_ws_id = session.get('active_workspace_id')
            if active_ws_id != workspace.id:
                print(f"[WORKSPACE_CTX] sys_admin_workspace_block user={g.user.id} requested={workspace.id} active={active_ws_id} redirect=system_admin")
                flash('Enter the workspace to access it.', 'warning')
                return redirect(url_for('system_admin_page'))
        
        if not membership and not sys_admin:
            if session.get('current_workspace_id') == workspace.id:
                session.pop('current_workspace_id', None)
            if session.get('active_workspace_id') == workspace.id:
                session.pop('active_workspace_id', None)
            print(f"[WORKSPACE_CTX] unauthorized_workspace user={g.user.id} ws_id={workspace.id} redirect=index")
            flash('You do not have access to this workspace.', 'error')
            return redirect(url_for('index'))
        
        g.workspace = workspace
        g.workspace_membership = membership
        g.is_workspace_context = True
        session['current_workspace_id'] = workspace.id
        
        return f(workspace_slug, *args, **kwargs)
    return decorated_function


@app.route('/<workspace_slug>')
@app.route('/<workspace_slug>/')
@login_required
@require_workspace_access
def workspace_dashboard(workspace_slug):
    """Workspace-scoped dashboard"""
    try:
        ws_id = g.workspace.id
        base_query = visible_local_listing_query(ws_id)
        stats = {
            'total': base_query.count(),
            'published': base_query.filter_by(status='published').count(),
            'draft': base_query.filter_by(status='draft').count(),
        }
        recent = base_query.order_by(LocalListing.updated_at.desc()).limit(5).all()
        return render_template('index.html', stats=stats, recent_listings=[l.to_dict() for l in recent])
    except Exception as e:
        print(f"[ERROR] Workspace dashboard error: {e}")
        import traceback
        traceback.print_exc()
        raise


@app.route('/<workspace_slug>/listings')
@login_required
@require_workspace_access
def workspace_listings(workspace_slug):
    """Workspace-scoped listings page"""
    return _render_listings_page(g.workspace.id)


@app.route('/<workspace_slug>/leads')
@login_required
@require_workspace_access
def workspace_leads(workspace_slug):
    """Workspace-scoped leads page"""
    return render_template('leads.html')


@app.route('/<workspace_slug>/tasks')
@login_required
@require_workspace_access
def workspace_tasks(workspace_slug):
    """Workspace-scoped tasks page"""
    return render_template('tasks.html')


@app.route('/<workspace_slug>/insights')
@login_required
@require_workspace_access
def workspace_insights(workspace_slug):
    """Workspace-scoped insights page"""
    return render_template('insights.html')


@app.route('/<workspace_slug>/image-editor')
@login_required
@require_workspace_access
def workspace_image_editor(workspace_slug):
    """Workspace-scoped image editor"""
    ws_id = g.workspace.id if g.workspace else get_active_workspace_id()
    settings = get_all_user_image_settings(workspace_id=ws_id)
    return render_template('image_editor.html', settings=settings)


@app.route('/<workspace_slug>/loops')
@login_required
@require_workspace_access
@require_workspace_loops_admin
def workspace_loops(workspace_slug):
    """Workspace-scoped loops page"""
    ws_id = g.workspace.id if g.workspace else get_active_workspace_id()
    loops = LoopConfig.query.filter_by(workspace_id=ws_id).order_by(LoopConfig.created_at.desc()).all()

    duplicated_folder = ListingFolder.query.filter_by(name='Duplicated', workspace_id=ws_id).first()
    if duplicated_folder:
        listings = visible_local_listing_query(ws_id).filter(
            db.or_(
                LocalListing.folder_id != duplicated_folder.id,
                LocalListing.folder_id == None
            )
        ).order_by(LocalListing.reference).all()
    else:
        listings = visible_local_listing_query(ws_id).order_by(LocalListing.reference).all()

    return render_template('loops.html', loops=loops, listings=listings)


@app.route('/<workspace_slug>/auth')
@login_required
@require_workspace_access
def workspace_auth(workspace_slug):
    """Workspace-scoped PF Connect page"""
    return render_template('auth.html')


@app.route('/<workspace_slug>/users')
@login_required
@require_workspace_access
def workspace_users(workspace_slug):
    """Workspace-scoped users page - only for workspace admins"""
    if not g.workspace.is_admin(g.user.id) and not is_system_admin(g.user):
        flash('Access denied. Workspace admin only.', 'error')
        return redirect(url_for('workspace_dashboard', workspace_slug=workspace_slug))

    ws_id = g.workspace.id
    memberships_all = WorkspaceMember.query.filter_by(workspace_id=ws_id).all()
    memberships, _ = _filter_workspace_memberships_for_org(memberships_all)
    user_ids = [m.user_id for m in memberships]
    users = User.query.filter(User.id.in_(user_ids)).order_by(User.created_at.desc()).all() if user_ids else []
    membership_by_user_id = {m.user_id: m for m in memberships}
    user_payloads = []
    for u in users:
        data = u.to_dict()
        member = membership_by_user_id.get(u.id)
        if member:
            data['workspace_member_id'] = member.id
            data['workspace_role'] = member.role
            data['workspace_role_name'] = WorkspaceMember.ROLES.get(member.role, {}).get('name', member.role.title())
            extra_perms = get_workspace_user_extra_permissions(u.id, workspace_id=ws_id)
            data['workspace_custom_permissions'] = extra_perms
            data['has_workspace_custom_permissions'] = bool(extra_perms)
        user_payloads.append(data)
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    workspace = Workspace.query.get(ws_id)
    can_manage_members = can_manage_workspace_members(g.user, ws_id)

    return render_template('users.html',
                           users=user_payloads,
                           roles=User.ROLES,
                           workspace_member_roles=WorkspaceMember.ROLES,
                           all_permissions=User.ALL_PERMISSIONS,
                           pf_users=pf_users,
                           workspace=workspace.to_dict() if workspace else None,
                           workspace_memberships={m.user_id: m for m in memberships},
                           workspace_memberships_json={m.user_id: m.to_dict() for m in memberships},
                           can_manage_members=can_manage_members)


@app.route('/<workspace_slug>/settings')
@login_required
@require_workspace_access
def workspace_settings(workspace_slug):
    """Workspace-scoped settings page"""
    if not g.workspace.is_admin(g.user.id) and not is_system_admin(g.user):
        flash('Access denied. Workspace admin only.', 'error')
        return redirect(url_for('workspace_dashboard', workspace_slug=workspace_slug))

    ws_id = g.workspace.id
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    app_settings = AppSettings.get_all(workspace_id=ws_id)

    return render_template('settings.html', 
        config={
            'api_base_url': Config.API_BASE_URL,
            'has_api_key': bool(Config.API_KEY),
            'has_api_secret': bool(Config.API_SECRET),
            'has_legacy_token': bool(Config.API_TOKEN),
            'agency_id': Config.AGENCY_ID,
            'debug': Config.DEBUG,
            'bulk_batch_size': Config.BULK_BATCH_SIZE,
            'bulk_delay': Config.BULK_DELAY_SECONDS,
            'default_agent_email': app_settings.get('default_agent_email', Config.DEFAULT_AGENT_EMAIL),
            'default_owner_email': app_settings.get('default_owner_email', Config.DEFAULT_OWNER_EMAIL),
        },
        app_settings=app_settings,
        pf_users=pf_users
    )


# ==================== WORKSPACES MANAGEMENT (ADMIN ONLY) ====================

@app.route('/workspaces')
@login_required
def workspaces_page():
    """Workspace management page - only for admins"""
    if not is_system_admin(g.user):
        flash('Access denied. System administrators only.', 'error')
        return redirect(url_for('index'))
    
    # Models imported at top
    workspaces = Workspace.query.order_by(Workspace.name).all()
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    system_user_ids = _get_system_user_ids([u.id for u in users])
    users = [u for u in users if u.id not in system_user_ids]
    return render_template('workspaces.html', 
                           workspaces=[workspace_to_org_dict(w, include_members=True, include_connections=True) for w in workspaces],
                           users=[u.to_dict() for u in users],
                           providers=WorkspaceConnection.PROVIDERS,
                           colors=Workspace.COLORS)


@app.route('/api/workspaces', methods=['GET'])
@login_required
def api_list_workspaces():
    """List workspaces for current user"""
    # Models imported at top
    
    if is_system_admin(g.user):
        # Admins see all workspaces
        workspaces = Workspace.query.filter_by(is_active=True).all()
    else:
        # Regular users only see their workspaces
        memberships = WorkspaceMember.query.filter_by(user_id=g.user.id).all()
        workspace_ids = [m.workspace_id for m in memberships]
        workspaces = Workspace.query.filter(
            Workspace.id.in_(workspace_ids),
            Workspace.is_active == True
        ).all()
    
    return jsonify({
        'success': True,
        'workspaces': [workspace_to_org_dict(w) for w in workspaces]
    })


@app.route('/api/workspaces', methods=['POST'])
@login_required
def api_create_workspace():
    """Create a new workspace"""
    if not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Admin only'}), 403
    
    # Models imported at top
    data = request.get_json() or {}
    
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    
    requested_slug = (data.get('slug') or '').strip()
    slug_source = requested_slug if requested_slug else name
    slug = Workspace.generate_slug(slug_source)
    if not slug:
        return jsonify({'success': False, 'error': 'Invalid workspace slug'}), 400
    if slug in RESERVED_WORKSPACE_SLUGS:
        return jsonify({'success': False, 'error': f'Slug "{slug}" is reserved'}), 400

    # Ensure unique slug
    base_slug = slug
    counter = 1
    while Workspace.query.filter_by(slug=slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1
    
    workspace = Workspace(
        name=name,
        slug=slug,
        description=data.get('description', ''),
        color=data.get('color', 'indigo'),
        created_by_id=g.user.id
    )
    db.session.add(workspace)
    db.session.flush()  # Get the ID

    creator_added_as_owner = False
    if is_workspace_eligible_user(g.user):
        member = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=g.user.id,
            role='owner'
        )
        db.session.add(member)
        creator_added_as_owner = True

    db.session.commit()

    # Initialize default settings for this workspace
    AppSettings.init_defaults(workspace_id=workspace.id)

    response = {
        'success': True,
        'workspace': workspace_to_org_dict(workspace, include_members=True)
    }
    if not creator_added_as_owner:
        response['message'] = 'Workspace created. Add or invite an org owner to manage it.'
        response['owner_required'] = True
    return jsonify(response)


@app.route('/api/workspaces/<int:workspace_id>', methods=['PUT'])
@login_required
def api_update_workspace(workspace_id):
    """Update a workspace"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    
    if not workspace.is_admin(g.user.id) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json() or {}
    
    if 'name' in data:
        workspace.name = data['name'].strip()
    if 'slug' in data:
        new_slug = Workspace.generate_slug((data.get('slug') or '').strip())
        if not new_slug:
            return jsonify({'success': False, 'error': 'Invalid workspace slug'}), 400
        if new_slug != workspace.slug and new_slug in RESERVED_WORKSPACE_SLUGS:
            return jsonify({'success': False, 'error': f'Slug "{new_slug}" is reserved'}), 400
        existing = Workspace.query.filter(
            Workspace.slug == new_slug,
            Workspace.id != workspace.id
        ).first()
        if existing:
            return jsonify({'success': False, 'error': 'Slug already in use'}), 400
        workspace.slug = new_slug
    if 'description' in data:
        workspace.description = data['description']
    if 'color' in data:
        workspace.color = data['color']
    if 'logo_url' in data:
        workspace.logo_url = data['logo_url']
    if 'is_active' in data and is_system_admin(g.user):
        workspace.is_active = data['is_active']
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'workspace': workspace_to_org_dict(workspace)
    })


@app.route('/api/workspaces/<int:workspace_id>', methods=['DELETE'])
@login_required
def api_delete_workspace(workspace_id):
    """Delete a workspace"""
    if not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Admin only'}), 403
    
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    
    db.session.delete(workspace)
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'Workspace "{workspace.name}" deleted'})


@app.route('/api/workspaces/<int:workspace_id>/members', methods=['GET'])
@login_required
def api_get_workspace_members(workspace_id):
    """Get workspace members"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    if not workspace.get_member(g.user.id) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    members, _ = _filter_workspace_memberships_for_org(workspace.members)
    member_payloads = []
    for m in members:
        data = m.to_dict()
        data['extra_permissions'] = get_workspace_user_extra_permissions(m.user_id, workspace_id=workspace_id)
        member_payloads.append(data)
    return jsonify({
        'success': True,
        'members': member_payloads
    })


@app.route('/api/workspaces/<int:workspace_id>/members', methods=['POST'])
@login_required
def api_add_workspace_member(workspace_id):
    """Add a member to workspace"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    
    if not workspace.is_admin(g.user.id) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    user_id = data.get('user_id')
    role = data.get('role', 'member')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'User ID required'}), 400

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid user ID'}), 400

    target_user = User.query.get(user_id)
    if not target_user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    if has_any_system_role(target_user):
        return jsonify({'success': False, 'error': 'System users cannot be added as workspace members.'}), 400
    
    # Check if already a member
    existing = WorkspaceMember.query.filter_by(
        workspace_id=workspace_id,
        user_id=user_id
    ).first()
    
    if existing:
        return jsonify({'success': False, 'error': 'User is already a member'}), 400
    
    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        invited_by_id=g.user.id
    )
    db.session.add(member)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'member': member.to_dict()
    })


@app.route('/api/workspaces/<int:workspace_id>/members/<int:member_id>', methods=['PUT'])
@login_required
def api_update_workspace_member(workspace_id, member_id):
    """Update a workspace member's role"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    member = WorkspaceMember.query.get_or_404(member_id)
    
    if member.workspace_id != workspace_id:
        return jsonify({'success': False, 'error': 'Member not in this workspace'}), 400

    if not workspace.is_admin(g.user.id) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    if member.user and has_any_system_role(member.user):
        return jsonify({'success': False, 'error': 'System users cannot be managed as workspace members.'}), 400
    
    data = request.get_json() or {}
    if 'role' in data:
        # Prevent demoting the last owner
        if member.role == 'owner':
            owner_count = WorkspaceMember.query.filter_by(
                workspace_id=workspace_id,
                role='owner'
            ).count()
            if owner_count <= 1 and data['role'] != 'owner':
                return jsonify({'success': False, 'error': 'Cannot demote the last owner'}), 400
        member.role = data['role']

    updated_extra_permissions = None
    if 'extra_permissions' in data:
        if not isinstance(data.get('extra_permissions'), list):
            return jsonify({'success': False, 'error': 'extra_permissions must be a list'}), 400
        updated_extra_permissions = set_workspace_user_extra_permissions(
            member.user_id,
            data.get('extra_permissions') or [],
            workspace_id=workspace_id
        )
    
    db.session.commit()
    member_data = member.to_dict()
    if updated_extra_permissions is None:
        updated_extra_permissions = get_workspace_user_extra_permissions(member.user_id, workspace_id=workspace_id)
    member_data['extra_permissions'] = updated_extra_permissions
    return jsonify({
        'success': True,
        'member': member_data
    })


@app.route('/api/workspaces/<int:workspace_id>/members/<int:member_id>', methods=['DELETE'])
@login_required
def api_remove_workspace_member(workspace_id, member_id):
    """Remove a member from workspace"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    member = WorkspaceMember.query.get_or_404(member_id)
    
    if member.workspace_id != workspace_id:
        return jsonify({'success': False, 'error': 'Member not in this workspace'}), 400
    
    if not workspace.is_admin(g.user.id) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Prevent removing the last owner
    if member.role == 'owner':
        owner_count = WorkspaceMember.query.filter_by(
            workspace_id=workspace_id,
            role='owner'
        ).count()
        if owner_count <= 1:
            return jsonify({'success': False, 'error': 'Cannot remove the last owner'}), 400
    
    db.session.delete(member)
    db.session.commit()
    
    return jsonify({'success': True})


# ==================== WORKSPACE CONNECTIONS ====================

@app.route('/workspaces/<int:workspace_id>/connections')
@login_required
def connections_page(workspace_id):
    """Connection center for a workspace"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    
    # Check access
    if not workspace.get_member(g.user.id) and not is_system_admin(g.user):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    
    return render_template('connections.html',
                           workspace=workspace.to_dict(include_connections=True),
                           providers=WorkspaceConnection.PROVIDERS)


@app.route('/api/workspaces/<int:workspace_id>/connections', methods=['GET'])
@login_required
def api_get_connections(workspace_id):
    """Get workspace connections"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    if not workspace.get_member(g.user.id) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    include_secrets = workspace.is_admin(g.user.id) or is_system_admin(g.user)
    
    return jsonify({
        'success': True,
        'connections': [c.to_dict(include_secrets=include_secrets) for c in workspace.connections]
    })


@app.route('/api/workspaces/<int:workspace_id>/connections', methods=['POST'])
@login_required
def api_create_connection(workspace_id):
    """Create a new connection"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    
    member = workspace.get_member(g.user.id)
    if not (member and member.can_manage_connections()) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    provider = data.get('provider')
    
    if not provider or provider not in WorkspaceConnection.PROVIDERS:
        return jsonify({'success': False, 'error': 'Invalid provider'}), 400
    
    # Check if connection already exists
    existing = WorkspaceConnection.query.filter_by(
        workspace_id=workspace_id,
        provider=provider
    ).first()
    
    if existing:
        return jsonify({'success': False, 'error': f'{provider} connection already exists'}), 400
    
    connection = WorkspaceConnection(
        workspace_id=workspace_id,
        provider=provider,
        name=data.get('name'),
        created_by_id=g.user.id
    )
    connection.set_credentials(data.get('credentials', {}))
    
    db.session.add(connection)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'connection': connection.to_dict()
    })


@app.route('/api/workspaces/<int:workspace_id>/connections/<int:connection_id>', methods=['PUT'])
@login_required
def api_update_connection(workspace_id, connection_id):
    """Update a connection"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    connection = WorkspaceConnection.query.get_or_404(connection_id)
    
    if connection.workspace_id != workspace_id:
        return jsonify({'success': False, 'error': 'Connection not in this workspace'}), 400
    
    member = workspace.get_member(g.user.id)
    if not (member and member.can_manage_connections()) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    
    if 'name' in data:
        connection.name = data['name']
    if 'is_active' in data:
        connection.is_active = data['is_active']
    if 'credentials' in data:
        connection.set_credentials(data['credentials'])
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'connection': connection.to_dict()
    })


@app.route('/api/workspaces/<int:workspace_id>/connections/<int:connection_id>', methods=['DELETE'])
@login_required
def api_delete_connection(workspace_id, connection_id):
    """Delete a connection"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    connection = WorkspaceConnection.query.get_or_404(connection_id)
    
    if connection.workspace_id != workspace_id:
        return jsonify({'success': False, 'error': 'Connection not in this workspace'}), 400
    
    member = workspace.get_member(g.user.id)
    if not (member and member.can_manage_connections()) and not is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    db.session.delete(connection)
    db.session.commit()
    
    return jsonify({'success': True})


@app.route('/api/workspaces/<int:workspace_id>/connections/<int:connection_id>/test', methods=['POST'])
@login_required
def api_test_connection(workspace_id, connection_id):
    """Test a connection"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    connection = WorkspaceConnection.query.get_or_404(connection_id)
    
    if connection.workspace_id != workspace_id:
        return jsonify({'success': False, 'error': 'Connection not in this workspace'}), 400
    
    try:
        if connection.provider == 'propertyfinder':
            # Test PropertyFinder connection
            creds = connection.get_credentials()
            api_key = creds.get('api_key')
            api_secret = creds.get('api_secret')
            
            if not api_key or not api_secret:
                return jsonify({'success': False, 'error': 'Missing API credentials'}), 400
            
            import requests
            response = requests.post(
                'https://atlas.propertyfinder.com/v1/auth/token',
                json={'apiKey': api_key, 'apiSecret': api_secret},
                timeout=10
            )
            
            if response.status_code == 200:
                connection.connection_status = 'connected'
                connection.last_connected_at = datetime.utcnow()
                connection.last_error = None
            else:
                connection.connection_status = 'error'
                connection.last_error = f'HTTP {response.status_code}: {response.text[:200]}'
        else:
            # Generic test - just mark as connected
            connection.connection_status = 'connected'
            connection.last_connected_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': connection.connection_status == 'connected',
            'status': connection.connection_status,
            'error': connection.last_error
        })
        
    except Exception as e:
        connection.connection_status = 'error'
        connection.last_error = str(e)
        db.session.commit()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== WORKSPACE SESSION ====================

@app.route('/api/workspace/switch/<int:workspace_id>', methods=['POST'])
@login_required
def api_switch_workspace(workspace_id):
    """Switch to a different workspace"""
    # Models imported at top
    workspace = Workspace.query.get_or_404(workspace_id)
    
    # Check access
    if not is_system_admin(g.user):
        member = WorkspaceMember.query.filter_by(
            workspace_id=workspace_id,
            user_id=g.user.id
        ).first()
        if not member:
            return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    session['active_workspace_id'] = workspace_id
    
    return jsonify({
        'success': True,
        'workspace': workspace.to_dict()
    })


@app.route('/workspaces/<int:workspace_id>/enter', methods=['POST'])
@login_required
def enter_workspace(workspace_id):
    """Enter a workspace (sets active workspace for system admins)"""
    workspace = Workspace.query.get_or_404(workspace_id)
    if not workspace.is_active:
        flash('Workspace is not active.', 'error')
        return redirect(url_for('system_admin_page'))

    if not is_system_admin(g.user):
        member = WorkspaceMember.query.filter_by(
            workspace_id=workspace_id,
            user_id=g.user.id
        ).first()
        if not member:
            flash('You do not have access to this workspace.', 'error')
            return redirect(url_for('system_admin_page'))

    session['active_workspace_id'] = workspace_id
    return redirect(url_for('workspace_dashboard', workspace_slug=workspace.slug))


@app.route('/workspaces/exit', methods=['POST'])
@login_required
def exit_workspace():
    """Exit workspace context for system admins"""
    if not is_system_admin(g.user):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    session.pop('active_workspace_id', None)
    session.pop('current_workspace_id', None)
    return redirect(url_for('system_admin_page'))


# ==================== BITRIX24-STYLE PERMISSION SYSTEM APIs ====================

@app.route('/system-admin')
@login_required
def system_admin_page():
    """Global system administration page - only for system admins"""
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        flash('Access denied. System administrators only.', 'error')
        return redirect(url_for('index'))
    
    return render_template('system_admin.html')


@app.route('/workspace/<int:workspace_id>/admin')
@login_required
def workspace_admin_page(workspace_id):
    """Workspace administration page"""
    # Models imported at top
    from src.services.permissions import get_permission_service
    
    workspace = Workspace.query.get_or_404(workspace_id)
    service = get_permission_service()
    
    # Check if user can access workspace admin
    if not service.is_workspace_admin(g.user, workspace_id) and not is_system_admin(g.user):
        flash('Access denied. Workspace administrators only.', 'error')
        return redirect(url_for('index'))
    
    return render_template('workspace_admin.html', workspace=workspace_to_org_dict(workspace, include_members=True))


# --- System Roles API ---

@app.route('/api/system/roles', methods=['GET'])
@login_required
def api_get_system_roles():
    """Get all system roles"""
    from src.database.models import SystemRole
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    roles = SystemRole.query.order_by(SystemRole.code).all()
    return jsonify({
        'success': True,
        'roles': [r.to_dict() for r in roles]
    })


@app.route('/api/system/roles', methods=['POST'])
@login_required
def api_create_system_role():
    """Create a custom system role"""
    from src.database.models import SystemRole
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    code = data.get('code', '').strip().upper()
    name = data.get('name', '').strip()
    
    if not code or not name:
        return jsonify({'success': False, 'error': 'Code and name are required'}), 400
    
    if SystemRole.query.filter_by(code=code).first():
        return jsonify({'success': False, 'error': 'Role code already exists'}), 400
    
    role = SystemRole(
        code=code,
        name=name,
        description=data.get('description', ''),
        is_system=False
    )
    role.set_capabilities(data.get('capabilities', {}))
    db.session.add(role)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'role': role.to_dict()
    })


@app.route('/api/system/roles/<int:role_id>', methods=['PUT'])
@login_required
def api_update_system_role(role_id):
    """Update a system role"""
    from src.database.models import SystemRole
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    role = SystemRole.query.get_or_404(role_id)
    
    if role.is_system:
        return jsonify({'success': False, 'error': 'Cannot modify built-in system roles'}), 400
    
    data = request.get_json()
    role.name = data.get('name', role.name)
    role.description = data.get('description', role.description)
    if 'capabilities' in data:
        role.set_capabilities(data['capabilities'])
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'role': role.to_dict()
    })


# --- User System Role Assignment API ---

@app.route('/api/users/<int:user_id>/system-roles', methods=['GET'])
@login_required
def api_get_user_system_roles(user_id):
    """Get system roles for a user"""
    from src.database.models import UserSystemRole
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user) and g.user.id != user_id:
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    assignments = UserSystemRole.query.filter_by(user_id=user_id).all()
    return jsonify({
        'success': True,
        'roles': [a.to_dict() for a in assignments]
    })


@app.route('/api/users/<int:user_id>/system-roles', methods=['POST'])
@login_required
def api_assign_system_role(user_id):
    """Assign a system role to a user"""
    from src.database.models import SystemRole, UserSystemRole, AuditLog
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    role_code = data.get('role_code')
    
    role = SystemRole.query.filter_by(code=role_code).first()
    if not role:
        return jsonify({'success': False, 'error': 'Role not found'}), 404
    
    # Check if already assigned
    existing = UserSystemRole.query.filter_by(
        user_id=user_id,
        system_role_id=role.id
    ).first()
    if existing:
        return jsonify({'success': False, 'error': 'Role already assigned'}), 400
    
    assignment = UserSystemRole(
        user_id=user_id,
        system_role_id=role.id,
        assigned_by_id=g.user.id
    )
    db.session.add(assignment)
    
    # Audit log
    log = AuditLog(
        user_id=g.user.id,
        user_email=g.user.email,
        action=AuditLog.ACTION_ROLE_ASSIGNED,
        resource_type='user',
        resource_id=user_id
    )
    log.set_details({'role_code': role_code, 'assigned_to_user_id': user_id})
    db.session.add(log)
    
    db.session.commit()
    
    # Clear cache
    service.clear_cache(user_id)
    
    return jsonify({
        'success': True,
        'assignment': assignment.to_dict()
    })


@app.route('/api/users/<int:user_id>/system-roles/<role_code>', methods=['DELETE'])
@login_required
def api_remove_system_role(user_id, role_code):
    """Remove a system role from a user"""
    from src.database.models import SystemRole, UserSystemRole, AuditLog
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    role = SystemRole.query.filter_by(code=role_code).first()
    if not role:
        return jsonify({'success': False, 'error': 'Role not found'}), 404
    
    assignment = UserSystemRole.query.filter_by(
        user_id=user_id,
        system_role_id=role.id
    ).first()
    if not assignment:
        return jsonify({'success': False, 'error': 'Assignment not found'}), 404
    
    db.session.delete(assignment)
    
    # Audit log
    log = AuditLog(
        user_id=g.user.id,
        user_email=g.user.email,
        action=AuditLog.ACTION_ROLE_REMOVED,
        resource_type='user',
        resource_id=user_id
    )
    log.set_details({'role_code': role_code, 'removed_from_user_id': user_id})
    db.session.add(log)
    
    db.session.commit()
    
    # Clear cache
    service.clear_cache(user_id)
    
    return jsonify({'success': True})


# --- Workspace Roles API ---

@app.route('/api/workspaces/<int:workspace_id>/roles', methods=['GET'])
@login_required
def api_get_workspace_roles(workspace_id):
    """Get roles for a workspace"""
    # Models imported at top
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_workspace_member(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    # Get workspace-specific roles and global templates
    roles = WorkspaceRole.query.filter(
        (WorkspaceRole.workspace_id == workspace_id) | 
        (WorkspaceRole.workspace_id.is_(None))
    ).order_by(WorkspaceRole.priority.desc()).all()
    
    return jsonify({
        'success': True,
        'roles': [r.to_dict() for r in roles]
    })


@app.route('/api/workspaces/<int:workspace_id>/roles', methods=['POST'])
@login_required
def api_create_workspace_role(workspace_id):
    """Create a custom role for a workspace"""
    # Models imported at top
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_workspace_admin(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    code = data.get('code', '').strip().upper()
    name = data.get('name', '').strip()
    
    if not code or not name:
        return jsonify({'success': False, 'error': 'Code and name are required'}), 400
    
    # Check for duplicate in this workspace
    existing = WorkspaceRole.query.filter_by(
        workspace_id=workspace_id,
        code=code
    ).first()
    if existing:
        return jsonify({'success': False, 'error': 'Role code already exists in this workspace'}), 400
    
    role = WorkspaceRole(
        workspace_id=workspace_id,
        code=code,
        name=name,
        description=data.get('description', ''),
        priority=data.get('priority', 0),
        is_default=data.get('is_default', False),
        is_system=False
    )
    role.set_permission_buckets(data.get('permission_buckets', {}))
    db.session.add(role)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'role': role.to_dict()
    })


@app.route('/api/workspaces/<int:workspace_id>/roles/<int:role_id>', methods=['PUT'])
@login_required
def api_update_workspace_role(workspace_id, role_id):
    """Update a workspace role"""
    # Models imported at top
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_workspace_admin(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    role = WorkspaceRole.query.get_or_404(role_id)
    
    if role.workspace_id != workspace_id:
        return jsonify({'success': False, 'error': 'Role not in this workspace'}), 400
    
    if role.is_system:
        return jsonify({'success': False, 'error': 'Cannot modify built-in roles'}), 400
    
    data = request.get_json()
    role.name = data.get('name', role.name)
    role.description = data.get('description', role.description)
    role.priority = data.get('priority', role.priority)
    role.is_default = data.get('is_default', role.is_default)
    if 'permission_buckets' in data:
        role.set_permission_buckets(data['permission_buckets'])
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'role': role.to_dict()
    })


# --- Module Permissions API ---

@app.route('/api/workspaces/<int:workspace_id>/roles/<int:role_id>/modules', methods=['GET'])
@login_required
def api_get_module_permissions(workspace_id, role_id):
    """Get module permissions for a workspace role"""
    from src.database.models import ModulePermission, WorkspaceRole
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_workspace_member(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    role = WorkspaceRole.query.get_or_404(role_id)
    if role.workspace_id not in (None, workspace_id):
        return jsonify({'success': False, 'error': 'Role not in this workspace'}), 400
    
    perms = ModulePermission.query.filter_by(workspace_role_id=role_id).all()
    return jsonify({
        'success': True,
        'modules': [p.to_dict() for p in perms]
    })


@app.route('/api/workspaces/<int:workspace_id>/roles/<int:role_id>/modules/<module>', methods=['PUT'])
@login_required
def api_set_module_permissions(workspace_id, role_id, module):
    """Set module permissions for a workspace role"""
    from src.database.models import ModulePermission, WorkspaceRole
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_workspace_admin(g.user, workspace_id):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    # Verify role belongs to workspace
    role = WorkspaceRole.query.get_or_404(role_id)
    if role.workspace_id != workspace_id and role.workspace_id is not None:
        return jsonify({'success': False, 'error': 'Role not in this workspace'}), 400
    
    data = request.get_json()
    
    perm = ModulePermission.query.filter_by(
        workspace_role_id=role_id,
        module=module
    ).first()
    
    if not perm:
        perm = ModulePermission(
            workspace_role_id=role_id,
            module=module
        )
        db.session.add(perm)
    
    perm.set_capabilities(data.get('capabilities', {}))
    perm.merge_strategy = data.get('merge_strategy', 'union')
    db.session.commit()
    
    return jsonify({
        'success': True,
        'module_permission': perm.to_dict()
    })


# --- Feature Flags API ---

@app.route('/api/system/feature-flags', methods=['GET'])
@login_required
def api_get_feature_flags():
    """Get all feature flags"""
    from src.database.models import FeatureFlag
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    flags = FeatureFlag.query.order_by(FeatureFlag.code).all()
    return jsonify({
        'success': True,
        'flags': [f.to_dict() for f in flags]
    })


@app.route('/api/system/feature-flags/<int:flag_id>', methods=['PUT'])
@login_required
def api_update_feature_flag(flag_id):
    """Update a feature flag"""
    from src.database.models import FeatureFlag
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    flag = FeatureFlag.query.get_or_404(flag_id)
    data = request.get_json()
    
    flag.is_enabled = data.get('is_enabled', flag.is_enabled)
    flag.value = data.get('value', flag.value)
    flag.updated_by_id = g.user.id
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'flag': flag.to_dict()
    })


# --- User Effective Permissions API ---

@app.route('/api/users/<int:user_id>/permissions', methods=['GET'])
@login_required
def api_get_user_effective_permissions(user_id):
    """Get effective permissions for a user"""
    from src.services.permissions import get_permission_service, list_effective_permissions
    
    service = get_permission_service()
    
    # Users can see their own permissions, admins can see anyone's
    if g.user.id != user_id and not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    user = User.query.get_or_404(user_id)
    workspace_id = request.args.get('workspace_id', type=int)
    module = request.args.get('module')
    
    perms = list_effective_permissions(
        user,
        workspace_id=workspace_id,
        module=module
    )
    
    return jsonify({
        'success': True,
        'user_id': user_id,
        'permissions': perms
    })


# --- Audit Logs API ---

@app.route('/api/system/audit-logs', methods=['GET'])
@login_required
def api_get_audit_logs():
    """Get audit logs"""
    from src.database.models import AuditLog
    from src.services.permissions import get_permission_service
    
    service = get_permission_service()
    if not service.is_system_admin(g.user):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    
    # Filters
    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action')
    workspace_id = request.args.get('workspace_id', type=int)
    
    query = AuditLog.query
    
    if user_id:
        query = query.filter_by(user_id=user_id)
    if action:
        query = query.filter_by(action=action)
    if workspace_id:
        query = query.filter_by(workspace_id=workspace_id)
    
    logs = query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return jsonify({
        'success': True,
        'logs': [l.to_dict() for l in logs.items],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': logs.total,
            'pages': logs.pages
        }
    })


# --- Check Access API (for frontend) ---

@app.route('/api/check-access', methods=['POST'])
@login_required
def api_check_access():
    """Check if current user has access to perform an action"""
    from src.services.permissions import check_access
    
    data = request.get_json()
    
    result = check_access(
        g.user,
        action=data.get('action'),
        resource_type=data.get('resource_type'),
        resource_id=data.get('resource_id'),
        workspace_id=data.get('workspace_id'),
        module=data.get('module')
    )
    
    return jsonify({
        'success': True,
        'allowed': result
    })


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile page"""
    if request.method == 'POST':
        g.user.name = request.form.get('name', g.user.name).strip()
        
        # Update password if provided
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        
        if new_password:
            if not g.user.check_password(current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('profile'))
            g.user.set_password(new_password)
            flash('Password updated successfully.', 'success')
        
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    
    return render_template('profile.html')


# ==================== PAGES ====================

@app.route('/')
@login_required
def index():
    """Dashboard home page - redirects to workspace if user has one"""
    ws = get_active_workspace()
    if ws and ws.is_active:
        session['current_workspace_id'] = ws.id
        print(f"[WORKSPACE_CTX] index_redirect_workspace user={g.user.id} ws_id={ws.id} slug={ws.slug}")
        return redirect(url_for('workspace_dashboard', workspace_slug=ws.slug))

    # System admins land on system admin UI unless they explicitly enter a workspace
    if is_system_admin(g.user):
        print(f"[WORKSPACE_CTX] index_redirect_system_admin user={g.user.id}")
        return redirect(url_for('system_admin_page'))
    
    memberships_count = WorkspaceMember.query.filter_by(user_id=g.user.id).count()
    if memberships_count > 0:
        print(f"[WORKSPACE_CTX] index_no_active_workspace user={g.user.id} memberships={memberships_count} redirect=logout")
        flash('No active workspace is available for your account. Please contact your administrator.', 'warning')
        return redirect(url_for('logout'))

    # No workspace memberships found
    print(f"[WORKSPACE_CTX] index_no_membership user={g.user.id} redirect=logout")
    flash('You are not assigned to any workspace. Please contact your administrator.', 'warning')
    return redirect(url_for('logout'))


@app.route('/listings')
@login_required
def listings():
    """List all listings page - uses local database"""
    ws_id = get_active_workspace_id()
    if ws_id:
        ws = get_active_workspace()
        query_args = request.args.to_dict(flat=True)
        return redirect(url_for('workspace_listings', workspace_slug=ws.slug, **query_args))
    if not ws_id and is_system_admin(g.user):
        flash('Select a workspace to continue.', 'warning')
        return redirect(url_for('system_admin_page'))
    if not ws_id:
        flash('You are not assigned to any workspace.', 'warning')
        return redirect(url_for('logout'))
    return _render_listings_page(ws_id)


def _render_listings_page(workspace_id):
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    status = request.args.get('status')
    sort_by = request.args.get('sort_by', 'updated_at')
    sort_order = request.args.get('sort_order', 'desc')
    search = request.args.get('search', '').strip()
    folder_id = request.args.get('folder_id', type=int)  # Folder filter
    show_duplicates = request.args.get('show_duplicates', '0') == '1'  # Hide duplicates by default
    assigned_to_filter = request.args.get('assigned_to_id')
    
    query = visible_local_listing_query(workspace_id)
    
    # Get the Duplicated folder ID
    duplicated_folder = ListingFolder.query.filter_by(
        name='Duplicated',
        workspace_id=workspace_id
    ).first()
    
    # Filter by folder
    if folder_id is not None:
        if folder_id == 0:
            # Show uncategorized listings (no folder)
            query = query.filter(LocalListing.folder_id.is_(None))
        else:
            query = query.filter_by(folder_id=folder_id)
    else:
        # When viewing all listings, hide duplicated folder unless show_duplicates is on
        if not show_duplicates and duplicated_folder:
            query = query.filter(
                db.or_(
                    LocalListing.folder_id != duplicated_folder.id,
                    LocalListing.folder_id.is_(None)
                )
            )
    
    # Filter by status
    if status:
        query = query.filter_by(status=status)

    # Filter by assignee
    if assigned_to_filter:
        if assigned_to_filter == 'unassigned':
            query = query.filter(LocalListing.assigned_to_id.is_(None))
        else:
            try:
                assignee_id = int(assigned_to_filter)
                query = query.filter_by(assigned_to_id=assignee_id)
            except (TypeError, ValueError):
                pass
    
    # Search filter
    if search:
        search_term = f'%{search}%'
        query = query.filter(
            db.or_(
                LocalListing.reference.ilike(search_term),
                LocalListing.title_en.ilike(search_term),
                LocalListing.location.ilike(search_term),
                LocalListing.city.ilike(search_term)
            )
        )
    
    # Sorting
    valid_sort_columns = {
        'updated_at': LocalListing.updated_at,
        'created_at': LocalListing.created_at,
        'price': LocalListing.price,
        'reference': LocalListing.reference,
        'title': LocalListing.title_en,
        'views': LocalListing.views,
        'leads': LocalListing.leads,
        'status': LocalListing.status,
        'bedrooms': LocalListing.bedrooms,
        'size': LocalListing.size
    }
    
    sort_column = valid_sort_columns.get(sort_by, LocalListing.updated_at)
    if sort_order == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())
    
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Get folders for sidebar (counts are visibility-aware for non-admins)
    if workspace_user_can_manage_all_listings(workspace_id=workspace_id):
        folders = ListingFolder.get_all_with_counts(workspace_id=workspace_id)
    else:
        visible_counts = {}
        for listing in visible_local_listing_query(workspace_id).all():
            visible_counts[listing.folder_id] = visible_counts.get(listing.folder_id, 0) + 1
        folders = []
        for folder in ListingFolder.query.filter_by(workspace_id=workspace_id).order_by(ListingFolder.name).all():
            data = folder.to_dict()
            data['listing_count'] = visible_counts.get(folder.id, 0)
            folders.append(data)
    current_folder = None
    if folder_id:
        current_folder = ListingFolder.query.filter_by(id=folder_id, workspace_id=workspace_id).first()
    uncategorized_count = visible_local_listing_query(workspace_id).filter(
        LocalListing.folder_id.is_(None)
    ).count()
    
    # Count duplicates (for showing toggle info)
    duplicates_count = 0
    if duplicated_folder:
        duplicates_count = visible_local_listing_query(workspace_id).filter_by(folder_id=duplicated_folder.id).count()

    folder_nav_urls = None
    active_workspace = getattr(g, 'workspace', None) or get_active_workspace()
    if active_workspace:
        base_query_args = request.args.to_dict(flat=True)
        base_query_args.pop('page', None)  # reset pagination when changing folder filter
        base_query_args.pop('folder_id', None)
        folder_nav_urls = {
            'all': url_for('workspace_listings', workspace_slug=active_workspace.slug, **base_query_args),
            'clear': url_for('workspace_listings', workspace_slug=active_workspace.slug, **base_query_args),
            'uncategorized': url_for('workspace_listings', workspace_slug=active_workspace.slug, folder_id=0, **base_query_args),
            'folders': {}
        }
        for folder in folders:
            folder_id_value = folder.get('id') if isinstance(folder, dict) else getattr(folder, 'id', None)
            if folder_id_value is None:
                continue
            folder_nav_urls['folders'][folder_id_value] = url_for(
                'workspace_listings',
                workspace_slug=active_workspace.slug,
                folder_id=folder_id_value,
                **base_query_args
            )
    
    return render_template('listings.html', 
                         listings=[l.to_dict() for l in pagination.items],
                         pagination={
                             'current_page': pagination.page,
                             'last_page': pagination.pages,
                             'per_page': per_page,
                             'total': pagination.total,
                             'has_prev': pagination.has_prev,
                             'has_next': pagination.has_next
                         },
                         folders=folders,
                         current_folder=current_folder.to_dict() if current_folder else None,
                         folder_id=folder_id,
                         uncategorized_count=uncategorized_count,
                         duplicates_count=duplicates_count,
                         show_duplicates=show_duplicates,
                         page=page,
                         per_page=per_page,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         search=search,
                         status=status or '',
                         assigned_to_id=assigned_to_filter or '',
                         folder_nav_urls=folder_nav_urls,
                         current_user_id=g.user.id if g.user else None,
                         workspace_members=[
                             m.user.to_dict() for m in WorkspaceMember.query.filter_by(workspace_id=workspace_id).all()
                         ])

@app.route('/listings/new')
@permission_required('create')
@require_active_workspace
def new_listing():
    """New listing form page"""
    property_types = [
        {'code': pt.value, 'name': pt.name.replace('_', ' ').title()} 
        for pt in PropertyType
    ]
    ws_id = get_active_workspace_id()
    members = WorkspaceMember.query.filter_by(workspace_id=ws_id).all() if ws_id else []
    return render_template('listing_form.html', 
                         listing=None, 
                         property_types=property_types,
                         edit_mode=False,
                         workspace_members=[m.user.to_dict() for m in members])


@app.route('/listings/<listing_id>')
@login_required
@require_active_workspace
@api_error_handler
def view_listing(listing_id):
    """View single listing page - checks local DB first, then PropertyFinder API"""
    # Try local database first (for integer IDs)
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing:
            listing_dict = local_listing.to_dict()
            # If synced to PF, fetch current PF state for display (best-effort)
            if local_listing.pf_listing_id:
                try:
                    client = get_client(workspace_id=ws_id)
                    pf_listing = find_pf_listing_in_cache_by_id(local_listing.pf_listing_id, workspace_id=ws_id)
                    if not pf_listing:
                        pf_listing = find_pf_listing_in_cache_by_reference(local_listing.reference, workspace_id=ws_id)
                    if not pf_listing:
                        pf_listing = _normalize_pf_listing(client.get_listing(local_listing.pf_listing_id))
                    state = extract_pf_state_from_listing(pf_listing)
                    if state:
                        listing_dict['pf_state'] = state
                except PropertyFinderAPIError as e:
                    listing_dict['pf_state_error'] = e.message
                except Exception:
                    listing_dict['pf_state_error'] = 'Unable to fetch PF state'
            return render_template('listing_detail.html', listing=listing_dict)
    except (ValueError, TypeError):
        pass  # Not an integer ID, try API
    
    # Try PropertyFinder API
    client = get_client(workspace_id=get_active_workspace_id())
    listing = client.get_listing(listing_id)
    return render_template('listing_detail.html', listing=listing.get('data', listing))


@app.route('/listings/<listing_id>/edit')
@permission_required('edit')
@require_active_workspace
@api_error_handler
def edit_listing(listing_id):
    """Edit listing form page - checks local DB first, then PropertyFinder API"""
    property_types = [
        {'code': pt.value, 'name': pt.name.replace('_', ' ').title()} 
        for pt in PropertyType
    ]
    ws_id = get_active_workspace_id()
    members = WorkspaceMember.query.filter_by(workspace_id=ws_id).all() if ws_id else []
    
    # Try local database first (for integer IDs)
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing:
            # Pass the model object directly so template can use get_images()
            return render_template('listing_form.html', 
                                 listing=local_listing,
                                 property_types=property_types,
                                 edit_mode=True,
                                 workspace_members=[m.user.to_dict() for m in members])
    except (ValueError, TypeError):
        pass  # Not an integer ID, try API
    
    # Try PropertyFinder API
    client = get_client(workspace_id=get_active_workspace_id())
    listing = client.get_listing(listing_id)
    # Transform API response to local field names for form compatibility
    api_listing = listing.get('data', listing)
    transformed = transform_api_listing_to_local(api_listing)
    return render_template('listing_form.html', 
                         listing=transformed,
                         property_types=property_types,
                         edit_mode=True,
                         workspace_members=[m.user.to_dict() for m in members])


@app.route('/bulk')
@permission_required('bulk_upload')
@require_active_workspace
def bulk_upload():
    """Bulk upload page"""
    # Get settings for defaults
    app_settings = AppSettings.get_all(workspace_id=get_active_workspace_id())
    return render_template('bulk_upload.html', defaults={
        'agent_email': Config.DEFAULT_AGENT_EMAIL,
        'owner_email': Config.DEFAULT_OWNER_EMAIL,
        'agent_id': app_settings.get('default_pf_agent_id', '')
    })


@app.route('/insights')
@login_required
@require_active_workspace
def insights():
    """Insights and analytics page - loads without API calls, data fetched on demand"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_insights', workspace_slug=ws.slug))
    ws_id = get_active_workspace_id()
    # Get local listings only (no API call)
    local_listings = visible_local_listing_query(ws_id).all()
    local_data = [listing.to_dict() for listing in local_listings]
    
    # Return empty PF data - user will load on demand
    return render_template('insights.html', 
                         pf_listings=[],
                         local_listings=local_data,
                         users=[],
                         leads=[],
                         credits=None,
                         error_message=None,
                         cache_age=None,
                         data_loaded=False)


@app.route('/api/pf/refresh', methods=['POST'])
@login_required
@require_active_workspace
def api_refresh_pf_data():
    """API: Force refresh PropertyFinder data cache"""
    ws_id = get_active_workspace_id()
    cache = get_cached_pf_data(force_refresh=True, workspace_id=ws_id)
    return jsonify({
        'success': cache.get('error') is None,
        'listings_count': len(cache['listings']),
        'users_count': len(cache['users']),
        'error': cache.get('error'),
        'cached_at': cache['last_updated'].isoformat() if cache['last_updated'] else None
    })


@app.route('/api/pf/insights', methods=['GET'])
@login_required
@require_active_workspace
def api_pf_insights():
    """API: Get all PropertyFinder data for insights page (on-demand loading)"""
    user_id = request.args.get('user_id')
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    ws_id = get_active_workspace_id()
    
    # FAST PATH: Always try cache first (DB-backed, survives restarts)
    cached_listings = get_cached_listings(workspace_id=ws_id)  # Loads from DB if not in memory
    cached_users = get_cached_users(workspace_id=ws_id)
    cached_leads = get_cached_leads(workspace_id=ws_id)
    
    # If we have cached data and not forcing refresh, return immediately (no API calls)
    if cached_listings and not force_refresh:
        print(f"[Insights] Returning {len(cached_listings)} cached listings (no API call)")
        # Get last_updated from DB if not in memory
        cache_obj = _get_pf_cache(workspace_id=ws_id)
        last_updated = cache_obj.get('last_updated') or PFCache.get_last_update('listings', workspace_id=ws_id)
        cache = {
            'listings': cached_listings,
            'users': cached_users,
            'leads': cached_leads,
            'last_updated': last_updated,
            'from_cache': True
        }
    elif force_refresh:
        # User explicitly requested refresh - fetch from API
        print(f"[Insights] Force refresh requested, fetching from API...")
        cache = get_cached_pf_data(force_refresh=True, quick_load=False, workspace_id=ws_id)
    else:
        # No cache at all - do quick initial load
        print(f"[Insights] No cache found, doing quick API load...")
        cache = get_cached_pf_data(force_refresh=True, quick_load=True, workspace_id=ws_id)
    
    listings = cache.get('listings', [])
    leads = cache.get('leads', [])
    
    # Get cached location map (no API calls - just use what we have)
    location_map = get_cached_locations(workspace_id=ws_id)
    
    # Filter by user if specified
    if user_id:
        user_id = int(user_id)
        listings = [l for l in listings if 
                   l.get('publicProfile', {}).get('id') == user_id or
                   l.get('assignedTo', {}).get('id') == user_id]
        leads = [l for l in leads if 
                l.get('publicProfile', {}).get('id') == user_id]
    
    return jsonify({
        'success': cache.get('error') is None or len(listings) > 0,
        'listings': listings,
        'users': cache.get('users', []),
        'leads': leads,
        'locations': location_map,
        'error': cache.get('error') if not listings else None,
        'cached_at': cache['last_updated'].isoformat() if cache.get('last_updated') else None,
        'from_cache': not force_refresh
    })


@app.route('/api/pf/locations/refresh', methods=['POST'])
@login_required
@require_active_workspace
def api_refresh_locations():
    """API: Refresh location cache from API (on-demand)"""
    ws_id = get_active_workspace_id()
    listings = get_cached_listings(workspace_id=ws_id)
    location_map = build_location_map(listings, force_refresh=True, workspace_id=ws_id)
    return jsonify({
        'success': True,
        'count': len(location_map),
        'locations': location_map
    })


@app.route('/api/pf/users', methods=['GET'])
@login_required
@require_active_workspace
def api_pf_users():
    """API: Get PropertyFinder users (lightweight, for agent dropdown)"""
    try:
        ws_id = get_active_workspace_id()
        client = get_client(workspace_id=ws_id)
        users_result = client.get_users(per_page=50)
        users = users_result.get('data', [])
        return jsonify({
            'success': True,
            'users': users
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'users': [],
            'error': str(e)
        })


@app.route('/api/pf/listings', methods=['GET'])
@login_required
@require_active_workspace
def api_pf_listings():
    """API: Get cached PropertyFinder listings"""
    ws_id = get_active_workspace_id()
    cache = get_cached_pf_data(workspace_id=ws_id)
    return jsonify({
        'listings': cache['listings'],
        'count': len(cache['listings']),
        'cached_at': cache['last_updated'].isoformat() if cache['last_updated'] else None
    })


@app.route('/settings')
@permission_required('settings')
@require_active_workspace
def settings():
    """Settings page"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_settings', workspace_slug=ws.slug))
    # Get PF users for default agent dropdown
    ws_id = get_active_workspace_id()
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    app_settings = AppSettings.get_all(workspace_id=ws_id)
    
    return render_template('settings.html', 
        config={
            'api_base_url': Config.API_BASE_URL,
            'has_api_key': bool(Config.API_KEY),
            'has_api_secret': bool(Config.API_SECRET),
            'has_legacy_token': bool(Config.API_TOKEN),
            'agency_id': Config.AGENCY_ID,
            'debug': Config.DEBUG,
            'bulk_batch_size': Config.BULK_BATCH_SIZE,
            'bulk_delay': Config.BULK_DELAY_SECONDS,
            'default_agent_email': app_settings.get('default_agent_email', Config.DEFAULT_AGENT_EMAIL),
            'default_owner_email': app_settings.get('default_owner_email', Config.DEFAULT_OWNER_EMAIL),
        },
        app_settings=app_settings,
        pf_users=pf_users
    )


@app.route('/api/settings', methods=['GET'])
@login_required
@require_active_workspace
def api_get_settings():
    """API: Get all app settings"""
    ws_id = get_active_workspace_id()
    settings = AppSettings.get_all(workspace_id=ws_id)
    last_sync = PFCache.get_last_update(workspace_id=ws_id)
    return jsonify({
        'success': True,
        'settings': settings,
        'last_sync': last_sync.isoformat() if last_sync else None
    })


@app.route('/api/storage', methods=['GET'])
@login_required
@require_active_workspace
def api_storage_info():
    """API: Get storage usage information"""
    import shutil
    
    def get_dir_size(path):
        """Get directory size recursively"""
        total = 0
        file_count = 0
        files = []
        try:
            for entry in os.scandir(path):
                if entry.is_file():
                    size = entry.stat().st_size
                    total += size
                    file_count += 1
                    files.append({
                        'name': entry.name,
                        'path': str(entry.path),
                        'size': size,
                        'size_mb': round(size / (1024 * 1024), 2)
                    })
                elif entry.is_dir():
                    sub_size, sub_count, sub_files = get_dir_size(entry.path)
                    total += sub_size
                    file_count += sub_count
                    files.extend(sub_files)
        except PermissionError:
            pass
        return total, file_count, files
    
    def format_size(bytes):
        if bytes < 1024:
            return f"{bytes} B"
        elif bytes < 1024 * 1024:
            return f"{bytes / 1024:.2f} KB"
        elif bytes < 1024 * 1024 * 1024:
            return f"{bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{bytes / (1024 * 1024 * 1024):.2f} GB"
    
    storage_info = {
        'is_production': IS_PRODUCTION,
        'volume_path': str(RAILWAY_VOLUME_PATH) if IS_PRODUCTION else 'N/A (local)',
        'upload_folder': str(UPLOAD_FOLDER),
        'directories': {}
    }
    
    # Check volume disk usage (if on Railway)
    if IS_PRODUCTION and RAILWAY_VOLUME_PATH.exists():
        try:
            disk = shutil.disk_usage(str(RAILWAY_VOLUME_PATH))
            storage_info['volume'] = {
                'total': format_size(disk.total),
                'used': format_size(disk.used),
                'free': format_size(disk.free),
                'used_percent': round(disk.used / disk.total * 100, 1),
                'total_bytes': disk.total,
                'used_bytes': disk.used,
                'free_bytes': disk.free
            }
        except Exception as e:
            storage_info['volume_error'] = str(e)
    
    # Analyze upload directories
    directories_to_check = [
        ('uploads', UPLOAD_FOLDER),
        ('listings', LISTING_IMAGES_FOLDER),
        ('logos', UPLOAD_FOLDER / 'logos'),
        ('processed', UPLOAD_FOLDER / 'processed'),
        ('temp', UPLOAD_FOLDER / 'temp'),
    ]
    
    total_size = 0
    total_files = 0
    all_files = []
    
    for name, path in directories_to_check:
        if path.exists():
            size, count, files = get_dir_size(str(path))
            total_size += size
            total_files += count
            all_files.extend(files)
            storage_info['directories'][name] = {
                'path': str(path),
                'size': format_size(size),
                'size_bytes': size,
                'file_count': count
            }
    
    storage_info['total'] = {
        'size': format_size(total_size),
        'size_bytes': total_size,
        'file_count': total_files
    }
    
    # Get largest files
    all_files.sort(key=lambda x: x['size'], reverse=True)
    storage_info['largest_files'] = all_files[:20]  # Top 20 largest
    
    # Database size (if SQLite)
    if not DATABASE_URL and DATABASE_PATH.exists():
        db_size = DATABASE_PATH.stat().st_size
        storage_info['database'] = {
            'path': str(DATABASE_PATH),
            'size': format_size(db_size),
            'size_bytes': db_size
        }
    
    return jsonify(storage_info)


@app.route('/api/storage/files', methods=['GET'])
@permission_required('settings')
@require_active_workspace
def api_storage_files():
    """API: Get all files in storage with linked listing info"""
    def get_all_files(path):
        """Get all files recursively"""
        files = []
        try:
            for entry in os.scandir(path):
                if entry.is_file():
                    stat = entry.stat()
                    files.append({
                        'name': entry.name,
                        'path': str(entry.path),
                        'size': stat.st_size,
                        'size_mb': round(stat.st_size / (1024 * 1024), 2),
                        'modified': stat.st_mtime
                    })
                elif entry.is_dir():
                    files.extend(get_all_files(entry.path))
        except PermissionError:
            pass
        return files
    
    all_files = []
    if UPLOAD_FOLDER.exists():
        all_files = get_all_files(str(UPLOAD_FOLDER))
    
    # Build a map of image URLs to listings for quick lookup
    ws_id = get_active_workspace_id()
    listings = LocalListing.query.filter_by(workspace_id=ws_id).all()
    image_to_listing = {}
    
    for listing in listings:
        if listing.images:
            for img_url in listing.get_images():
                # Extract the relative path from URL
                if '/uploads/' in img_url:
                    rel_path = img_url.split('/uploads/')[-1]
                    image_to_listing[rel_path] = {
                        'id': listing.id,
                        'reference': listing.reference,
                        'title': listing.title_en or listing.reference
                    }
    
    # Add linked listing info to files
    for file in all_files:
        file['linked_listing'] = None
        # Check if this file is linked to a listing
        if '/uploads/' in file['path']:
            rel_path = file['path'].split('/uploads/')[-1]
            if rel_path in image_to_listing:
                file['linked_listing'] = image_to_listing[rel_path]
    
    # Sort by size descending
    all_files.sort(key=lambda x: x['size'], reverse=True)
    
    return jsonify({
        'success': True,
        'files': all_files,
        'count': len(all_files)
    })


@app.route('/storage')
@permission_required('settings')
@require_active_workspace
def storage_page():
    """Storage management page"""
    return render_template('storage.html')


@app.route('/api/storage/delete', methods=['POST'])
@permission_required('settings')
@require_active_workspace
def api_storage_delete_files():
    """API: Delete specific files from storage and update DB references"""
    data = request.get_json() or {}
    # Accept both 'files' and 'paths' for flexibility
    files_to_delete = data.get('files', []) or data.get('paths', [])
    update_db = data.get('update_db', True)  # Whether to remove from listing images
    
    if not files_to_delete:
        return jsonify({'error': 'No files specified'}), 400
    
    deleted_files = []
    deleted_size = 0
    updated_listings = []
    ws_id = get_active_workspace_id()
    errors = []
    
    for file_path in files_to_delete:
        try:
            # Handle both absolute paths and relative paths
            if file_path.startswith('/'):
                # Absolute path - extract relative part
                if '/uploads/' in file_path:
                    relative_path = file_path.split('/uploads/')[-1]
                    full_path = UPLOAD_FOLDER / relative_path
                else:
                    full_path = Path(file_path)
                    relative_path = file_path
            else:
                # Relative path
                relative_path = file_path
                full_path = UPLOAD_FOLDER / file_path
            
            # Ensure the path is within UPLOAD_FOLDER
            try:
                full_path.resolve().relative_to(UPLOAD_FOLDER.resolve())
            except ValueError:
                errors.append(f"{file_path}: Path outside upload folder")
                continue
            
            if full_path.exists() and full_path.is_file():
                size = full_path.stat().st_size
                
                # Update database - remove this image from any listings
                if update_db:
                    listings = LocalListing.query.filter_by(workspace_id=ws_id).all()
                    for listing in listings:
                        if listing.images:
                            images = listing.get_images()
                            # Check if any image URL contains this path
                            new_images = []
                            found = False
                            for img_url in images:
                                if relative_path in img_url or file_path in img_url:
                                    found = True
                                else:
                                    new_images.append(img_url)
                            
                            if found:
                                # Update listing with remaining images
                                listing.images = json.dumps(new_images) if new_images else None
                                updated_listings.append({
                                    'id': listing.id,
                                    'reference': listing.reference
                                })
                    
                    if updated_listings:
                        db.session.commit()
                
                # Delete the file
                full_path.unlink()
                deleted_files.append(file_path)
                deleted_size += size
            else:
                errors.append(f"{file_path}: File not found")
                
        except Exception as e:
            errors.append(f"{file_path}: {str(e)}")
    
    # Format size
    if deleted_size < 1024:
        size_str = f"{deleted_size} B"
    elif deleted_size < 1024 * 1024:
        size_str = f"{deleted_size / 1024:.2f} KB"
    else:
        size_str = f"{deleted_size / (1024 * 1024):.2f} MB"
    
    return jsonify({
        'success': True,
        'deleted_count': len(deleted_files),
        'deleted_size': size_str,
        'deleted_size_bytes': deleted_size,
        'deleted_files': deleted_files,
        'updated_listings': updated_listings if updated_listings else None,
        'errors': errors if errors else None
    })


@app.route('/api/storage/cleanup', methods=['POST'])
@permission_required('settings')
@require_active_workspace
def api_storage_cleanup():
    """API: Clean up unused storage"""
    data = request.get_json() or {}
    cleanup_type = data.get('type', 'temp')  # temp, orphaned, all
    
    deleted_files = []
    deleted_size = 0
    errors = []
    
    try:
        # Clean temp files
        if cleanup_type in ['temp', 'all']:
            temp_dir = UPLOAD_FOLDER / 'temp'
            if temp_dir.exists():
                for f in temp_dir.iterdir():
                    if f.is_file():
                        try:
                            size = f.stat().st_size
                            f.unlink()
                            deleted_files.append(str(f.name))
                            deleted_size += size
                        except Exception as e:
                            errors.append(f"{f.name}: {e}")
        
        # Clean orphaned listing images (images not referenced by any listing)
        if cleanup_type in ['orphaned', 'all']:
            # Get all image URLs from listings
            ws_id = get_active_workspace_id()
            all_listings = LocalListing.query.filter_by(workspace_id=ws_id).all()
            referenced_images = set()
            
            for listing in all_listings:
                if listing.images:
                    for img_url in listing.get_images():
                        # Extract filename from URL
                        if '/uploads/' in img_url:
                            referenced_images.add(img_url.split('/uploads/')[-1])
                if listing.original_images:
                    for orig_path in _load_original_images_raw(listing):
                        referenced_images.add(orig_path)
            
            # Check listing images folder
            if LISTING_IMAGES_FOLDER.exists():
                for listing_dir in LISTING_IMAGES_FOLDER.iterdir():
                    if listing_dir.is_dir():
                        for f in listing_dir.iterdir():
                            if f.is_file():
                                relative_path = f"listings/{listing_dir.name}/{f.name}"
                                if relative_path not in referenced_images:
                                    try:
                                        size = f.stat().st_size
                                        f.unlink()
                                        deleted_files.append(relative_path)
                                        deleted_size += size
                                    except Exception as e:
                                        errors.append(f"{relative_path}: {e}")
        
        # Format deleted size
        if deleted_size < 1024:
            size_str = f"{deleted_size} B"
        elif deleted_size < 1024 * 1024:
            size_str = f"{deleted_size / 1024:.2f} KB"
        else:
            size_str = f"{deleted_size / (1024 * 1024):.2f} MB"
        
        return jsonify({
            'success': True,
            'deleted_count': len(deleted_files),
            'deleted_size': size_str,
            'deleted_size_bytes': deleted_size,
            'deleted_files': deleted_files[:50],  # Limit response size
            'errors': errors if errors else None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings', methods=['POST'])
@permission_required('settings')
@require_active_workspace
def api_update_settings():
    """API: Update app settings"""
    data = request.get_json() or {}
    ws_id = get_active_workspace_id()
    
    allowed_keys = ['sync_interval_minutes', 'auto_sync_enabled', 'workspace_timezone',
                    'default_agent_email', 'default_owner_email', 'default_pf_agent_id']

    if 'workspace_timezone' in data:
        timezone_name = (data.get('workspace_timezone') or '').strip()
        if not timezone_name:
            timezone_name = 'Asia/Dubai'
            data['workspace_timezone'] = timezone_name
        try:
            ZoneInfo(timezone_name)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid workspace timezone'}), 400
    
    for key in allowed_keys:
        if key in data:
            AppSettings.set(key, data[key], workspace_id=ws_id)
    
    return jsonify({'success': True, 'settings': AppSettings.get_all(workspace_id=get_active_workspace_id())})


@app.route('/api/sync', methods=['POST'])
@login_required
@require_active_workspace
def api_manual_sync():
    """API: Trigger manual sync of PropertyFinder data"""
    try:
        ws_id = get_active_workspace_id()
        client = get_client(workspace_id=ws_id)
        
        # Fetch listings
        all_listings = []
        page = 1
        while True:
            result = client.get_listings(page=page, per_page=50)
            listings = result.get('results', [])
            if not listings:
                break
            all_listings.extend(listings)
            if page >= result.get('pagination', {}).get('totalPages', 1):
                break
            page += 1
            if page > 50:  # Support up to 2500 listings
                break
        PFCache.set_cache('listings', all_listings, workspace_id=ws_id)
        
        # Fetch users
        users = []
        try:
            users_result = client.get_users(per_page=50)
            users = users_result.get('data', [])
            PFCache.set_cache('users', users, workspace_id=ws_id)
        except:
            pass
        
        # Fetch leads
        leads = []
        try:
            leads_result = client.get_leads(per_page=100)
            leads = leads_result.get('results', [])
            PFCache.set_cache('leads', leads, workspace_id=ws_id)
        except:
            pass
        
        AppSettings.set('last_sync_at', datetime.now().isoformat(), workspace_id=ws_id)
        
        return jsonify({
            'success': True,
            'listings_count': len(all_listings),
            'users_count': len(users),
            'leads_count': len(leads),
            'synced_at': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== API ENDPOINTS ====================

@app.route('/api/listings', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_get_listings():
    """API: Get all listings"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.get_listings(page=page, per_page=per_page)
    return jsonify(result)


@app.route('/api/listings', methods=['POST'])
@api_error_handler
@login_required
@require_active_workspace
def api_create_listing():
    """API: Create a new listing"""
    data = request.get_json()
    
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.create_listing(data)
    
    return jsonify({'success': True, 'data': result}), 201


@app.route('/api/listings/<listing_id>', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_get_listing(listing_id):
    """API: Get a single listing"""
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.get_listing(listing_id)
    return jsonify(result)


@app.route('/api/listings/<listing_id>', methods=['PUT', 'PATCH'])
@api_error_handler
@login_required
@require_active_workspace
def api_update_listing(listing_id):
    """API: Update a listing"""
    data = request.get_json()
    
    client = get_client(workspace_id=get_active_workspace_id())
    if request.method == 'PUT':
        result = client.update_listing(listing_id, data)
    else:
        result = client.patch_listing(listing_id, data)
    
    return jsonify({'success': True, 'data': result})


@app.route('/api/listings/<listing_id>', methods=['DELETE'])
@api_error_handler
@login_required
@require_active_workspace
def api_delete_listing(listing_id):
    """API: Delete a listing"""
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.delete_listing(listing_id)
    return jsonify({'success': True, 'message': 'Listing deleted'})


@app.route('/api/listings/<listing_id>/publish', methods=['POST'])
@api_error_handler
@login_required
@require_active_workspace
def api_publish_listing(listing_id):
    """API: Publish a listing"""
    ws_id = get_active_workspace_id()
    client = get_client(workspace_id=ws_id)
    
    # Check if this is a local listing ID (integer)
    try:
        local_id = int(listing_id)
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing:
            media_warnings = []
            if local_listing.pf_listing_id:
                # Check if PF listing still exists
                try:
                    pf_listing = client.get_listing(local_listing.pf_listing_id)
                    if not pf_listing or not pf_listing.get('id'):
                        # PF listing doesn't exist anymore, need to create new one
                        local_listing.pf_listing_id = None
                        db.session.commit()
                except:
                    # PF listing not found, clear the ID
                    local_listing.pf_listing_id = None
                    db.session.commit()
            
            if not local_listing.pf_listing_id:
                # Need to create on PF first
                ok, error = resolve_assigned_agent_id(local_listing, client)
                if not ok:
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error}), 400
                    flash(error, 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))

                ok, error = validate_location_id(local_listing, client)
                if not ok:
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error}), 400
                    flash(error, 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))

                listing_data = local_listing.to_pf_format()
                
                # Validate required fields
                missing = []
                if not listing_data.get('title'):
                    missing.append('Title')
                if not listing_data.get('description'):
                    missing.append('Description')
                if not listing_data.get('price'):
                    missing.append('Price')
                if not listing_data.get('location'):
                    missing.append('Location')
                if not listing_data.get('assignedTo'):
                    missing.append('Assigned Agent')
                if not listing_data.get('bedrooms'):
                    missing.append('Bedrooms')
                if not listing_data.get('bathrooms'):
                    missing.append('Bathrooms')
                
                if missing:
                    error_msg = f"Cannot publish. Missing required fields: {', '.join(missing)}"
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))

                # Validate media URLs (public access required by PF)
                ok, error, failed_urls, warnings = validate_media_urls(listing_data)
                if warnings:
                    media_warnings = warnings
                    if not (request.is_json or request.headers.get('Accept') == 'application/json'):
                        for warning in warnings:
                            flash(warning, 'warning')
                if not ok:
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error, 'details': failed_urls}), 400
                    flash(error, 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))
                
                # Create on PF
                try:
                    result = client.create_listing(listing_data)
                    pf_id = result.get('id')
                    if pf_id:
                        local_listing.pf_listing_id = pf_id
                        db.session.commit()
                    else:
                        error_msg = f"Failed to create listing on PropertyFinder: {result}"
                        request_id = result.get('_request_id') if isinstance(result, dict) else None
                        if request_id:
                            error_msg = f"{error_msg} (Request ID: {request_id})"
                        if request.is_json or request.headers.get('Accept') == 'application/json':
                            return jsonify({'success': False, 'error': error_msg}), 400
                        flash(error_msg, 'error')
                        return redirect(url_for('view_listing', listing_id=listing_id))
                except Exception as e:
                    request_id = None
                    cloudfront = None
                    msg = str(e)
                    if isinstance(e, PropertyFinderAPIError):
                        msg = e.message
                        if isinstance(e.response, dict):
                            request_id = e.response.get('_request_id')
                            cloudfront = e.response.get('_cloudfront')
                            if cloudfront and isinstance(cloudfront, dict):
                                cf_id = cloudfront.get('cf_id')
                                if cf_id:
                                    msg = (
                                        "PropertyFinder CDN blocked this request. "
                                        f"Try again later or contact PF support with CloudFront ID: {cf_id}"
                                    )
                    if request_id and not (cloudfront and isinstance(cloudfront, dict)):
                        msg = f"{msg} (Request ID: {request_id})"
                    error_msg = f"Failed to create listing on PropertyFinder: {msg}"
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error_msg, 'cloudfront': cloudfront}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('view_listing', listing_id=listing_id))
            
            # Now publish
            try:
                result = client.publish_listing(local_listing.pf_listing_id)
                local_listing.status = 'pending'
                db.session.commit()
                
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    response_payload = {
                        'success': True,
                        'data': result,
                        'pf_listing_id': local_listing.pf_listing_id
                    }
                    if media_warnings:
                        response_payload['warnings'] = media_warnings
                    return jsonify(response_payload)
                flash(f'Publish request submitted for listing {local_listing.pf_listing_id}', 'success')
                return redirect(url_for('view_listing', listing_id=listing_id))
            except PropertyFinderAPIError as e:
                error_msg = f"PropertyFinder rejected publish request: {e.message}"
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    return jsonify({'success': False, 'error': error_msg}), e.status_code or 400
                flash(error_msg, 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass
    
    # Direct PF listing ID
    result = client.publish_listing(listing_id)
    return jsonify({'success': True, 'data': result})


@app.route('/api/listings/<listing_id>/unpublish', methods=['POST'])
@api_error_handler
@login_required
@require_active_workspace
def api_unpublish_listing(listing_id):
    """API: Unpublish a listing"""
    ws_id = get_active_workspace_id()
    client = get_client(workspace_id=ws_id)
    
    # Check if this is a local listing ID (integer)
    try:
        local_id = int(listing_id)
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing and local_listing.pf_listing_id:
            # Use the PF listing ID instead
            client.unpublish_listing(local_listing.pf_listing_id)
            pf_state = None
            try:
                pf_listing = _normalize_pf_listing(client.get_listing(local_listing.pf_listing_id))
                pf_state = extract_pf_state_from_listing(pf_listing)
            except PropertyFinderAPIError:
                pf_state = None

            new_status = map_pf_state_to_local_status(pf_state) if pf_state else None
            if new_status and local_listing.status != new_status:
                local_listing.status = new_status
                local_listing.updated_at = datetime.utcnow()
                db.session.commit()

            return jsonify({
                'success': True,
                'pf_state': pf_state,
                'status': local_listing.status,
                'message': 'Unpublish request submitted'
            })
        if local_listing and not local_listing.pf_listing_id:
            return jsonify({'success': False, 'error': 'Listing is not synced to PropertyFinder'}), 400
    except (ValueError, TypeError):
        pass
    
    result = client.unpublish_listing(listing_id)
    return jsonify({'success': True, 'data': result})


@app.route('/api/bulk/upload', methods=['POST'])
@api_error_handler
@login_required
@require_active_workspace
def api_bulk_upload():
    """API: Bulk upload listings from file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Use JSON or CSV'}), 400
    
    filename = secure_filename(file.filename)
    filepath = Path(app.config['UPLOAD_FOLDER']) / filename
    file.save(str(filepath))
    
    publish = request.form.get('publish', 'false').lower() == 'true'
    
    client = get_client(workspace_id=get_active_workspace_id())
    manager = BulkListingManager(client)
    
    try:
        if filename.endswith('.json'):
            result = manager.create_listings_from_json(str(filepath), publish=publish)
        else:
            result = manager.create_listings_from_csv(str(filepath), publish=publish)
        
        # Clean up uploaded file
        filepath.unlink()
        
        return jsonify({
            'success': True,
            'total': result.total,
            'successful': result.successful,
            'failed': result.failed,
            'results': result.results,
            'errors': result.errors
        })
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise


@app.route('/api/bulk/create', methods=['POST'])
@api_error_handler
@login_required
@require_active_workspace
def api_bulk_create():
    """API: Bulk create listings from JSON array"""
    data = request.get_json()
    listings = data.get('listings', [])
    publish = data.get('publish', False)
    
    if not listings:
        return jsonify({'error': 'No listings provided'}), 400
    
    client = get_client(workspace_id=get_active_workspace_id())
    manager = BulkListingManager(client)
    result = manager.create_listings_from_list(listings, publish=publish)
    
    return jsonify({
        'success': True,
        'total': result.total,
        'successful': result.successful,
        'failed': result.failed,
        'results': result.results,
        'errors': result.errors
    })


@app.route('/api/reference/<ref_type>', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_reference_data(ref_type):
    """API: Get reference data"""
    client = get_client(workspace_id=get_active_workspace_id())
    
    if ref_type == 'property-types':
        result = client.get_property_types()
    elif ref_type == 'locations':
        query = request.args.get('q', '')
        result = client.get_locations(query)
    elif ref_type == 'amenities':
        result = client.get_amenities()
    elif ref_type == 'agents':
        result = client.get_agents()
    else:
        return jsonify({'error': 'Unknown reference type'}), 400
    
    return jsonify(result)


@app.route('/api/account', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_account():
    """API: Get account info"""
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.get_account()
    return jsonify(result)


@app.route('/api/config', methods=['GET'])
def api_get_config():
    """API: Get current configuration status (without secrets)"""
    has_key = bool(Config.API_KEY)
    has_secret = bool(Config.API_SECRET)
    
    # Show partial key/secret for verification
    key_preview = Config.API_KEY[:8] + '••••••' if Config.API_KEY else '••••••••'
    secret_preview = Config.API_SECRET[:8] + '••••••' if Config.API_SECRET else '••••••••'
    
    return jsonify({
        'has_api_key': has_key,
        'has_api_secret': has_secret,
        'api_key_preview': key_preview,
        'api_secret_preview': secret_preview,
        'api_base_url': Config.API_BASE_URL
    })


@app.route('/api/version', methods=['GET'])
def api_version():
    """API: Return build/version info for deployment verification"""
    git_sha = os.environ.get('RAILWAY_GIT_COMMIT_SHA') or os.environ.get('GIT_COMMIT')
    build_time = os.environ.get('BUILD_TIME') or os.environ.get('RAILWAY_DEPLOYMENT_ID')
    return jsonify({
        'git_sha': git_sha,
        'build_time': build_time
    })


@app.route('/api/test-connection', methods=['GET', 'POST'])
@login_required
@require_active_workspace
def api_test_pf_connection():
    """API: Test the Enterprise API connection"""
    try:
        client = get_client(workspace_id=get_active_workspace_id())
        result = client.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e),
            'base_url': Config.API_BASE_URL
        })


@app.route('/api/pf/webhooks', methods=['GET'])
@permission_required('settings')
@require_active_workspace
def api_pf_list_webhooks():
    """API: List PropertyFinder webhooks"""
    event_type = request.args.get('eventType') or request.args.get('event_type')
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.list_webhooks(event_type=event_type)
    return jsonify(result)


@app.route('/api/pf/webhooks', methods=['POST'])
@permission_required('settings')
@require_active_workspace
def api_pf_create_webhook():
    """API: Create PropertyFinder webhook subscription"""
    data = request.get_json() or {}
    event_id = data.get('eventId') or data.get('event_id')
    url = data.get('url')
    secret = data.get('secret') or Config.WEBHOOK_SECRET or None

    if not event_id:
        return jsonify({'success': False, 'error': 'eventId is required'}), 400

    if not url:
        if Config.WEBHOOK_URL:
            url = Config.WEBHOOK_URL
        else:
            if not APP_PUBLIC_URL:
                return jsonify({'success': False, 'error': 'APP_PUBLIC_URL or PF_WEBHOOK_URL is required'}), 400
            url = f"{APP_PUBLIC_URL}/webhooks/propertyfinder"

    client = get_client(workspace_id=get_active_workspace_id())
    result = client.create_webhook(event_id=event_id, url=url, secret=secret)
    return jsonify({'success': True, 'data': result})


@app.route('/api/pf/webhooks/<event_id>', methods=['DELETE'])
@permission_required('settings')
@require_active_workspace
def api_pf_delete_webhook(event_id):
    """API: Delete PropertyFinder webhook subscription"""
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.delete_webhook(event_id)
    return jsonify({'success': True, 'data': result})


@app.route('/api/users', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_get_users():
    """API: Get users (agents) from PF"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('perPage', 15, type=int)
    
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.get_users(page=page, per_page=per_page)
    return jsonify(result)


@app.route('/api/locations', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_get_locations():
    """API: Search locations"""
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.get_locations(search=search, page=page)
    return jsonify(result)


@app.route('/api/credits', methods=['GET'])
@api_error_handler
@login_required
@require_active_workspace
def api_get_credits():
    """API: Get credits info"""
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.get_credits()
    return jsonify(result)


# ==================== FORM HANDLERS ====================

@app.route('/listings/create', methods=['POST'])
@require_active_workspace
@api_error_handler
def create_listing_form():
    """Handle listing creation form submission - saves locally first"""
    form = request.form
    ws_id = get_active_workspace_id()
    
    can_manage_all = workspace_user_can_manage_all_listings(workspace_id=ws_id)
    assigned_to_id = None
    if form.get('assigned_to_id'):
        assigned_to_id = _validate_assignee(ws_id, form.get('assigned_to_id'))
        if not assigned_to_id:
            flash('Assigned user must be a member of this workspace.', 'error')
            return redirect(url_for('new_listing'))
    if not can_manage_all:
        assigned_to_id = g.user.id
    
    # Auto-generate reference if not provided
    reference = form.get('reference')
    if not reference or not reference.strip():
        reference = generate_reference_id()
    
    # Create local listing first
    local_listing = LocalListing(
        workspace_id=ws_id,
        emirate=form.get('emirate'),
        city=form.get('city'),
        category=form.get('category'),
        offering_type=form.get('offering_type'),
        property_type=form.get('property_type'),
        location=form.get('location'),
        location_id=form.get('location_id') if form.get('location_id') else None,
        assigned_agent=form.get('assigned_agent'),
        assigned_to_id=assigned_to_id,
        reference=reference,
        bedrooms=form.get('bedrooms'),
        bathrooms=form.get('bathrooms'),
        size=float(form.get('size')) if form.get('size') else None,
        parking_slots=int(form.get('parking_slots')) if form.get('parking_slots') else None,
        furnishing_type=form.get('furnishing_type'),
        project_status=form.get('project_status'),
        floor_number=form.get('floor_number'),
        unit_number=form.get('unit_number'),
        price=float(form.get('price')) if form.get('price') else None,
        downpayment=float(form.get('downpayment')) if form.get('downpayment') else None,
        rent_frequency=form.get('rent_frequency'),
        title_en=form.get('title_en'),
        title_ar=form.get('title_ar'),
        description_en=form.get('description_en'),
        description_ar=form.get('description_ar'),
        video_tour=convert_google_drive_url(form.get('video_tour')) if form.get('video_tour') else None,
        video_360=convert_google_drive_url(form.get('video_360')) if form.get('video_360') else None,
        permit_number=form.get('permit_number'),
        owner_name=form.get('owner_name'),
        developer=form.get('developer'),
        status='draft'
    )
    
    # Handle images (auto-convert Google Drive links)
    images = form.get('images', '')
    if images:
        local_listing.images = process_image_urls(images)
    
    # Handle amenities
    amenities = form.getlist('amenities')
    local_listing.amenities = ','.join(amenities) if amenities else ''
    
    db.session.add(local_listing)
    db.session.commit()
    
    flash('Listing saved as draft!', 'success')
    return redirect(url_for('view_listing', listing_id=local_listing.id))


@app.route('/listings/<listing_id>/update', methods=['POST'])
@require_active_workspace
@api_error_handler
def update_listing_form(listing_id):
    """Handle listing update form submission"""
    
    # Check if this is a local listing first
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing:
            # Update local listing
            form = request.form
            can_manage_all = workspace_user_can_manage_all_listings(workspace_id=ws_id)
            
            local_listing.emirate = form.get('emirate')
            local_listing.city = form.get('city')
            local_listing.category = form.get('category')
            local_listing.offering_type = form.get('offering_type')
            local_listing.property_type = form.get('property_type')
            local_listing.location = form.get('location')
            local_listing.location_id = form.get('location_id') if form.get('location_id') else None
            local_listing.assigned_agent = form.get('assigned_agent')
            local_listing.reference = form.get('reference')
            
            if can_manage_all:
                if form.get('assigned_to_id'):
                    assigned_to_id = _validate_assignee(ws_id, form.get('assigned_to_id'))
                    if not assigned_to_id:
                        flash('Assigned user must be a member of this workspace.', 'error')
                        return redirect(url_for('edit_listing', listing_id=listing_id))
                    local_listing.assigned_to_id = assigned_to_id
                else:
                    local_listing.assigned_to_id = None
            else:
                local_listing.assigned_to_id = g.user.id
            
            # Specifications
            local_listing.bedrooms = form.get('bedrooms')
            local_listing.bathrooms = form.get('bathrooms')
            local_listing.size = float(form.get('size')) if form.get('size') else None
            local_listing.parking_slots = int(form.get('parking_slots')) if form.get('parking_slots') else None
            local_listing.furnishing_type = form.get('furnishing_type')
            local_listing.project_status = form.get('project_status')
            local_listing.floor_number = form.get('floor_number')
            local_listing.unit_number = form.get('unit_number')
            
            # Price
            local_listing.price = float(form.get('price')) if form.get('price') else None
            local_listing.downpayment = float(form.get('downpayment')) if form.get('downpayment') else None
            local_listing.rent_frequency = form.get('rent_frequency')
            
            # Content
            local_listing.title_en = form.get('title_en')
            local_listing.title_ar = form.get('title_ar')
            local_listing.description_en = form.get('description_en')
            local_listing.description_ar = form.get('description_ar')
            
            # Media (auto-convert Google Drive links)
            images = form.get('images', '')
            if images:
                local_listing.images = process_image_urls(images)
            local_listing.video_tour = convert_google_drive_url(form.get('video_tour')) if form.get('video_tour') else None
            local_listing.video_360 = convert_google_drive_url(form.get('video_360')) if form.get('video_360') else None
            
            # Amenities
            amenities = form.getlist('amenities')
            local_listing.amenities = ','.join(amenities) if amenities else ''
            
            # Compliance
            local_listing.permit_number = form.get('permit_number')
            
            # Other
            local_listing.owner_name = form.get('owner_name')
            local_listing.developer = form.get('developer')
            
            db.session.commit()
            
            flash('Listing updated successfully!', 'success')
            return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass  # Not an integer ID, continue with PF listing
    
    # PropertyFinder listing
    data = build_listing_from_form(request.form)
    
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.update_listing(listing_id, data)
    
    flash('Listing updated successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/delete', methods=['POST'])
@require_active_workspace
@api_error_handler
def delete_listing_form(listing_id):
    """Handle listing deletion"""
    client = get_client(workspace_id=get_active_workspace_id())
    client.delete_listing(listing_id)
    
    flash('Listing deleted successfully!', 'success')
    return redirect(url_for('listings'))


@app.route('/listings/<listing_id>/duplicate', methods=['POST'])
@login_required
@require_active_workspace
def duplicate_listing(listing_id):
    """Duplicate an existing listing with a new reference ID"""
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        original = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        
        if not original:
            flash('Listing not found', 'error')
            return redirect(url_for('listings'))
        
        # Create a new listing with copied data
        new_listing = LocalListing(
            workspace_id=original.workspace_id,
            # Property basics
            reference=generate_reference_id(),  # New reference
            category=original.category,
            offering_type=original.offering_type,
            property_type=original.property_type,
            
            # Location
            emirate=original.emirate,
            city=original.city,
            location=original.location,
            location_id=original.location_id,
            
            # Property details
            bedrooms=original.bedrooms,
            bathrooms=original.bathrooms,
            size=original.size,
            furnishing_type=original.furnishing_type,
            project_status=original.project_status,
            parking_slots=original.parking_slots,
            floor_number=original.floor_number,
            unit_number=original.unit_number,
            
            # Price
            price=original.price,
            downpayment=original.downpayment,
            rent_frequency=original.rent_frequency,
            
            # Content
            title_en=original.title_en,
            title_ar=original.title_ar,
            description_en=original.description_en,
            description_ar=original.description_ar,
            
            # Media
            images=original.images,
            video_tour=original.video_tour,
            video_360=original.video_360,
            original_images=original.original_images,
            
            # Amenities
            amenities=original.amenities,
            
            # Assignment
            assigned_agent=original.assigned_agent,
            assigned_to_id=original.assigned_to_id,
            owner_id=original.owner_id,
            owner_name=original.owner_name,
            
            # Other
            developer=original.developer,
            permit_number=None,  # Clear permit - needs new one
            available_from=original.available_from,
            
            # Status
            status='draft',  # Always start as draft
            pf_listing_id=None  # Not synced yet
        )
        
        db.session.add(new_listing)
        db.session.commit()
        
        flash(f'Listing duplicated successfully! New reference: {new_listing.reference}', 'success')
        return redirect(url_for('edit_listing', listing_id=new_listing.id))
        
    except (ValueError, TypeError):
        flash('Can only duplicate local listings', 'error')
        return redirect(url_for('view_listing', listing_id=listing_id))
    except Exception as e:
        flash(f'Error duplicating listing: {str(e)}', 'error')
        return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/send-to-pf', methods=['POST'])
@login_required
@require_active_workspace
def send_to_pf_draft(listing_id):
    """Send listing to PropertyFinder as draft (without publishing)"""
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        
        if not local_listing:
            flash('Listing not found', 'error')
            return redirect(url_for('listings'))
        
        # Check if already on PF
        if local_listing.pf_listing_id:
            flash(f'Listing already on PropertyFinder (ID: {local_listing.pf_listing_id})', 'info')
            return redirect(url_for('view_listing', listing_id=listing_id))
        
        client = get_client(workspace_id=ws_id)

        # Preflight: resolve assigned agent and validate location
        ok, error = resolve_assigned_agent_id(local_listing, client)
        if not ok:
            flash(error, 'error')
            return redirect(url_for('edit_listing', listing_id=listing_id))

        ok, error = validate_location_id(local_listing, client)
        if not ok:
            flash(error, 'error')
            return redirect(url_for('edit_listing', listing_id=listing_id))
        
        # Build listing data
        listing_data = local_listing.to_pf_format()
        
        # Check for required fields
        missing_fields = []
        if not listing_data.get('title'):
            missing_fields.append('Title (English or Arabic)')
        if not listing_data.get('description'):
            missing_fields.append('Description (English or Arabic)')
        if not listing_data.get('price'):
            missing_fields.append('Price')
        if not listing_data.get('location'):
            missing_fields.append('Location ID (use location search)')
        if not listing_data.get('assignedTo'):
            missing_fields.append('Assigned Agent')
        if not listing_data.get('uaeEmirate'):
            missing_fields.append('Emirate')
        if not listing_data.get('bedrooms'):
            missing_fields.append('Bedrooms')
        if not listing_data.get('bathrooms'):
            missing_fields.append('Bathrooms')
        
        if missing_fields:
            flash(f'Cannot send to PF. Missing required fields: {", ".join(missing_fields)}', 'error')
            return redirect(url_for('edit_listing', listing_id=listing_id))

        # Validate media URLs (public access required by PF)
        ok, error, failed_urls, warnings = validate_media_urls(listing_data)
        if warnings:
            for warning in warnings:
                flash(warning, 'warning')
        if not ok:
            flash(error, 'error')
            return redirect(url_for('edit_listing', listing_id=listing_id))
        
        # Create on PropertyFinder as draft
        try:
            result = client.create_listing(listing_data)
            pf_listing_id = result.get('id')
            
            if not pf_listing_id:
                error_msg = result.get('error') or result.get('message') or str(result)
                request_id = result.get('_request_id') if isinstance(result, dict) else None
                if request_id:
                    error_msg = f"{error_msg} (Request ID: {request_id})"
                flash(f'PropertyFinder rejected the listing: {error_msg}', 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
            
            local_listing.pf_listing_id = str(pf_listing_id)
            local_listing.status = 'pf_draft'  # On PF as draft
            db.session.commit()
            
            flash(f'Listing sent to PropertyFinder as draft! PF ID: {pf_listing_id}', 'success')
            return redirect(url_for('view_listing', listing_id=listing_id))
            
        except PropertyFinderAPIError as e:
            request_id = e.response.get('_request_id') if isinstance(e.response, dict) else None
            cloudfront = e.response.get('_cloudfront') if isinstance(e.response, dict) else None
            msg = e.message
            if cloudfront and isinstance(cloudfront, dict):
                cf_id = cloudfront.get('cf_id')
                if cf_id:
                    msg = (
                        "PropertyFinder CDN blocked this request. "
                        f"Try again later or contact PF support with CloudFront ID: {cf_id}"
                    )
            elif request_id:
                msg = f"{msg} (Request ID: {request_id})"
            flash(f'Failed to create on PropertyFinder: {msg}', 'error')
            return redirect(url_for('view_listing', listing_id=listing_id))
        except Exception as e:
            flash(f'Failed to create on PropertyFinder: {str(e)}', 'error')
            return redirect(url_for('view_listing', listing_id=listing_id))
            
    except (ValueError, TypeError):
        flash('Can only send local listings to PF draft', 'error')
        return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/publish', methods=['POST'])
@require_active_workspace
@api_error_handler
def publish_listing_form(listing_id):
    """Handle listing publish from web form"""
    # Check if this is a local listing
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing:
            client = get_client(workspace_id=ws_id)
            
            # Check if already synced to PropertyFinder
            if local_listing.pf_listing_id:
                # Verify PF listing still exists
                try:
                    pf_listing = client.get_listing(local_listing.pf_listing_id)
                    if not pf_listing or not pf_listing.get('id'):
                        # PF listing doesn't exist anymore
                        local_listing.pf_listing_id = None
                        db.session.commit()
                        flash('PropertyFinder listing no longer exists. Creating new one...', 'warning')
                except:
                    # PF listing not found
                    local_listing.pf_listing_id = None
                    db.session.commit()
                    flash('PropertyFinder listing not found. Creating new one...', 'warning')
            
            # If no PF listing ID, need to create first
            if not local_listing.pf_listing_id:
                # Build listing data from local listing
                listing_data = local_listing.to_pf_format()
                
                # Check for required fields
                missing_fields = []
                if not listing_data.get('title'):
                    missing_fields.append('Title (English or Arabic)')
                if not listing_data.get('description'):
                    missing_fields.append('Description (English or Arabic)')
                if not listing_data.get('price'):
                    missing_fields.append('Price')
                if not listing_data.get('location'):
                    missing_fields.append('Location ID (use location search)')
                if not listing_data.get('assignedTo'):
                    missing_fields.append('Assigned Agent')
                if not listing_data.get('uaeEmirate'):
                    missing_fields.append('Emirate')
                if not listing_data.get('bedrooms'):
                    missing_fields.append('Bedrooms')
                if not listing_data.get('bathrooms'):
                    missing_fields.append('Bathrooms')
                
                if missing_fields:
                    flash(f'Cannot publish. Missing required fields: {", ".join(missing_fields)}', 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))
                
                # Create on PropertyFinder
                try:
                    result = client.create_listing(listing_data)
                    pf_listing_id = result.get('id')
                    
                    if not pf_listing_id:
                        error_msg = result.get('error') or result.get('message') or str(result)
                        flash(f'PropertyFinder rejected the listing: {error_msg}', 'error')
                        return redirect(url_for('view_listing', listing_id=listing_id))
                    
                    local_listing.pf_listing_id = str(pf_listing_id)
                    local_listing.status = 'draft'
                    db.session.commit()
                    flash(f'Listing created on PropertyFinder (ID: {pf_listing_id})', 'success')
                    
                except PropertyFinderAPIError as e:
                    flash(f'Failed to create on PropertyFinder: {e.message}', 'error')
                    return redirect(url_for('view_listing', listing_id=listing_id))
                except Exception as e:
                    flash(f'Failed to create on PropertyFinder: {str(e)}', 'error')
                    return redirect(url_for('view_listing', listing_id=listing_id))
            
            # Now publish on PropertyFinder
            try:
                publish_result = client.publish_listing(local_listing.pf_listing_id)
                local_listing.status = 'live'  # PF publishes instantly
                db.session.commit()
                flash(f'Listing published successfully! PF ID: {local_listing.pf_listing_id}', 'success')
                return redirect(url_for('view_listing', listing_id=listing_id))
                
            except PropertyFinderAPIError as e:
                flash(f'PropertyFinder rejected publish: {e.message}', 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
            except Exception as e:
                flash(f'Failed to publish: {str(e)}', 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass  # Not an integer ID, continue with PF listing
    
    # PropertyFinder listing
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.publish_listing(listing_id)
    
    flash('Listing published successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/unpublish', methods=['POST'])
@require_active_workspace
@api_error_handler
def unpublish_listing_form(listing_id):
    """Handle listing unpublish from web form"""
    # Check if this is a local listing
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if local_listing:
            if not local_listing.pf_listing_id:
                flash('This listing is not published on PropertyFinder', 'warning')
                return redirect(url_for('view_listing', listing_id=listing_id))
            
            # Unpublish on PropertyFinder
            client = get_client(workspace_id=ws_id)
            client.unpublish_listing(local_listing.pf_listing_id)

            pf_state = None
            try:
                pf_listing = _normalize_pf_listing(client.get_listing(local_listing.pf_listing_id))
                pf_state = extract_pf_state_from_listing(pf_listing)
            except PropertyFinderAPIError:
                pf_state = None

            new_status = map_pf_state_to_local_status(pf_state) if pf_state else None
            if new_status and local_listing.status != new_status:
                old_status = local_listing.status
                local_listing.status = new_status
                local_listing.updated_at = datetime.utcnow()
                db.session.commit()
                if new_status == 'draft':
                    flash('Listing unpublished successfully!', 'success')
                else:
                    flash(
                        f'Unpublish requested. PF state: {pf_state}. Local status updated from {old_status or "draft"} to {new_status}.',
                        'info'
                    )
            else:
                if pf_state:
                    flash(f'Unpublish requested. PF state: {pf_state}. Local status unchanged.', 'info')
                else:
                    flash('Unpublish request submitted. Status will update after PF confirms.', 'info')
            return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass  # Not an integer ID, continue with PF listing
    
    # PropertyFinder listing
    client = get_client(workspace_id=get_active_workspace_id())
    result = client.unpublish_listing(listing_id)
    
    flash('Listing unpublished successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/sync-pf-status', methods=['POST'])
@login_required
@require_active_workspace
@api_error_handler
def sync_pf_status_form(listing_id):
    """Sync local listing status with PropertyFinder state"""
    try:
        local_id = int(listing_id)
        ws_id = get_active_workspace_id()
        local_listing = visible_local_listing_query(ws_id).filter_by(id=local_id).first()
        if not local_listing:
            flash('Listing not found', 'error')
            return redirect(request.referrer or url_for('listings'))

        client = get_client(workspace_id=ws_id)
        pf_listing_id = local_listing.pf_listing_id
        pf_listing = None

        if pf_listing_id:
            pf_listing = find_pf_listing_in_cache_by_id(pf_listing_id, workspace_id=ws_id)
        if not pf_listing:
            pf_listing = find_pf_listing_in_cache_by_reference(local_listing.reference, workspace_id=ws_id)
        if not pf_listing and local_listing.reference:
            pf_listing = find_pf_listing_by_reference(client, local_listing.reference)
        if not pf_listing and pf_listing_id:
            try:
                pf_listing = _normalize_pf_listing(client.get_listing(pf_listing_id))
            except PropertyFinderAPIError:
                pf_listing = None

        if not pf_listing:
            flash('This listing is not synced to PropertyFinder', 'warning')
            return redirect(request.referrer or url_for('view_listing', listing_id=listing_id))

        pf_listing_id = str(pf_listing.get('id')) if pf_listing.get('id') else pf_listing_id
        if pf_listing_id and pf_listing_id != local_listing.pf_listing_id:
            local_listing.pf_listing_id = pf_listing_id
            local_listing.updated_at = datetime.utcnow()
            db.session.commit()

        pf_state = extract_pf_state_from_listing(pf_listing)
        if not pf_state:
            flash('Unable to determine PropertyFinder listing state', 'error')
            return redirect(request.referrer or url_for('view_listing', listing_id=listing_id))

        new_status = map_pf_state_to_local_status(pf_state)
        if new_status and local_listing.status != new_status:
            old_status = local_listing.status
            local_listing.status = new_status
            db.session.commit()
            flash(
                f'PF state: {pf_state}. Local status updated from {old_status or "draft"} to {new_status}.',
                'success'
            )
        else:
            flash(f'PF state: {pf_state}. Local status is already up to date.', 'info')

        return redirect(request.referrer or url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        flash('Can only sync local listings', 'error')
        return redirect(request.referrer or url_for('listings'))


def build_listing_from_form(form):
    """Build listing dict from form data"""
    data = {
        'title': form.get('title'),
        'description': form.get('description'),
        'property_type': form.get('property_type'),
        'offering_type': form.get('offering_type'),
    }
    
    # Price
    price_amount = form.get('price')
    if price_amount:
        data['price'] = {
            'amount': float(price_amount),
            'currency': form.get('currency', 'AED')
        }
        if form.get('rent_frequency'):
            data['price']['frequency'] = form.get('rent_frequency')
    
    # Location
    location = {}
    for field in ['city', 'community', 'sub_community', 'building', 'street']:
        if form.get(field):
            location[field] = form.get(field)
    if location:
        data['location'] = location
    
    # Numeric fields
    for field in ['bedrooms', 'bathrooms', 'parking']:
        if form.get(field):
            data[field] = int(form.get(field))
    
    for field in ['size', 'plot_size']:
        if form.get(field):
            data[field] = float(form.get(field))
    
    # String fields
    for field in ['reference_number', 'permit_number', 'completion_status', 'furnishing']:
        if form.get(field):
            data[field] = form.get(field)
    
    # Amenities (comma-separated)
    if form.get('amenities'):
        data['amenities'] = [a.strip() for a in form.get('amenities').split(',')]
    
    # Images (newline-separated URLs)
    if form.get('images'):
        data['images'] = [img.strip() for img in form.get('images').split('\n') if img.strip()]
    
    # Boolean
    data['featured'] = form.get('featured') == 'on'
    
    return data


# ==================== LOCAL DATABASE API ====================

@app.route('/api/local/listings', methods=['GET'])
@login_required
@require_active_workspace
def api_local_get_listings():
    """Get all local listings"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    status = request.args.get('status')
    
    ws_id = get_active_workspace_id()
    query = visible_local_listing_query(ws_id)
    if status:
        query = query.filter_by(status=status)
    
    query = query.order_by(LocalListing.updated_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'data': [l.to_dict() for l in pagination.items],
        'meta': {
            'current_page': pagination.page,
            'last_page': pagination.pages,
            'per_page': per_page,
            'total': pagination.total
        }
    })


@app.route('/api/local/listings', methods=['POST'])
@login_required
@require_active_workspace
def api_local_create_listing():
    """Create a local listing"""
    data = request.get_json()
    ws_id = get_active_workspace_id()
    
    # Check if reference already exists
    existing = LocalListing.query.filter_by(reference=data.get('reference'), workspace_id=ws_id).first()
    if existing:
        return jsonify({'error': 'Reference already exists'}), 400
    
    listing = LocalListing.from_dict(data)
    listing.workspace_id = ws_id
    if not workspace_user_can_manage_all_listings(workspace_id=ws_id):
        listing.assigned_to_id = g.user.id
    db.session.add(listing)
    db.session.commit()
    
    return jsonify({'success': True, 'data': listing.to_dict()}), 201


@app.route('/api/local/listings/<int:listing_id>', methods=['GET'])
@login_required
@require_active_workspace
def api_local_get_listing(listing_id):
    """Get a single local listing"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    return jsonify({'data': listing.to_dict()})


@app.route('/api/local/listings/<int:listing_id>', methods=['PUT'])
@login_required
@require_active_workspace
def api_local_update_listing(listing_id):
    """Update a local listing"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    data = request.get_json()
    can_manage_all = workspace_user_can_manage_all_listings(workspace_id=ws_id)
    
    if 'assigned_to_id' in data:
        if can_manage_all:
            assigned_to_id = _validate_assignee(ws_id, data.get('assigned_to_id'))
            if data.get('assigned_to_id') and not assigned_to_id:
                return jsonify({'success': False, 'error': 'Assigned user must be in this workspace'}), 400
            listing.assigned_to_id = assigned_to_id
        else:
            listing.assigned_to_id = g.user.id
    
    # Update fields
    for key, value in data.items():
        if hasattr(listing, key) and key not in ['id', 'created_at', 'assigned_to_id']:
            if key == 'images' and isinstance(value, list):
                value = '|'.join(value)
            elif key == 'amenities' and isinstance(value, list):
                value = ','.join(value)
            setattr(listing, key, value)
    
    listing.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True, 'data': listing.to_dict()})


@app.route('/api/local/listings/<int:listing_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_local_delete_listing(listing_id):
    """Delete a local listing"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    db.session.delete(listing)
    db.session.commit()

    # Remove listing image folder (including originals)
    try:
        listing_dir = LISTING_IMAGES_FOLDER / str(listing_id)
        if listing_dir.exists():
            shutil.rmtree(listing_dir, ignore_errors=True)
    except Exception as e:
        print(f"[ListingDelete] Failed to remove listing folder {listing_id}: {e}")
    
    return jsonify({'success': True, 'message': 'Listing deleted'})


@app.route('/api/local/listings/<int:listing_id>/sync-pf-status', methods=['POST'])
@login_required
@require_active_workspace
@api_error_handler
def api_local_sync_pf_status(listing_id):
    """Sync local listing status with PropertyFinder state"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    client = get_client(workspace_id=ws_id)
    pf_listing_id = listing.pf_listing_id
    pf_listing = None

    if pf_listing_id:
        pf_listing = find_pf_listing_in_cache_by_id(pf_listing_id, workspace_id=ws_id)
    if not pf_listing:
        pf_listing = find_pf_listing_in_cache_by_reference(listing.reference, workspace_id=ws_id)
    if not pf_listing and listing.reference:
        pf_listing = find_pf_listing_by_reference(client, listing.reference)
    if not pf_listing and pf_listing_id:
        try:
            pf_listing = _normalize_pf_listing(client.get_listing(pf_listing_id))
        except PropertyFinderAPIError:
            pf_listing = None

    if not pf_listing:
        return jsonify({'success': False, 'error': 'Listing is not synced to PropertyFinder'}), 400

    pf_listing_id = str(pf_listing.get('id')) if pf_listing.get('id') else pf_listing_id
    if pf_listing_id and pf_listing_id != listing.pf_listing_id:
        listing.pf_listing_id = pf_listing_id
        listing.updated_at = datetime.utcnow()
        db.session.commit()

    pf_state = extract_pf_state_from_listing(pf_listing)
    if not pf_state:
        return jsonify({'success': False, 'error': 'Unable to determine PropertyFinder listing state'}), 500

    new_status = map_pf_state_to_local_status(pf_state)
    if new_status and listing.status != new_status:
        old_status = listing.status
        listing.status = new_status
        listing.updated_at = datetime.utcnow()
        db.session.commit()
        msg = f'PF state: {pf_state}. Local status updated from {old_status or "draft"} to {new_status}.'
        return jsonify({'success': True, 'pf_state': pf_state, 'status': new_status, 'message': msg})
    else:
        msg = f'PF state: {pf_state}. Local status is already up to date.'
        return jsonify({'success': True, 'pf_state': pf_state, 'status': listing.status, 'message': msg})


@app.route('/api/local/listings/bulk', methods=['POST'])
@login_required
@require_active_workspace
def api_local_bulk_create():
    """Bulk create local listings"""
    data = request.get_json()
    listings_data = data.get('listings', [])
    ws_id = get_active_workspace_id()
    
    created = []
    errors = []
    
    for idx, item in enumerate(listings_data):
        try:
            # Check for duplicate reference
            if LocalListing.query.filter_by(reference=item.get('reference'), workspace_id=ws_id).first():
                errors.append({'index': idx, 'error': 'Reference already exists', 'reference': item.get('reference')})
                continue
            
            listing = LocalListing.from_dict(item)
            listing.workspace_id = ws_id
            db.session.add(listing)
            db.session.flush()
            created.append(listing.to_dict())
        except Exception as e:
            errors.append({'index': idx, 'error': str(e), 'reference': item.get('reference')})
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'created': len(created),
        'errors': len(errors),
        'error_details': errors
    })


@app.route('/api/local/listings/bulk-update', methods=['POST'])
@login_required
@require_active_workspace
def api_local_bulk_update():
    """Bulk update local listings (admin-only; currently supports reassignment)."""
    ws_id = get_active_workspace_id()
    if not workspace_user_can_manage_all_listings(workspace_id=ws_id):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    data = request.get_json() or {}
    listing_ids = data.get('ids') or []
    if not listing_ids or not isinstance(listing_ids, list):
        return jsonify({'success': False, 'error': 'No listings selected'}), 400

    updates = {}
    if 'assigned_to_id' in data:
        raw_assignee = data.get('assigned_to_id')
        if raw_assignee in ('null', None):
            updates['assigned_to_id'] = None
        else:
            assigned_to_id = _validate_assignee(ws_id, raw_assignee)
            if not assigned_to_id:
                return jsonify({'success': False, 'error': 'Assigned user must be in this workspace'}), 400
            updates['assigned_to_id'] = assigned_to_id

    if not updates:
        return jsonify({'success': False, 'error': 'No updates provided'}), 400

    # Apply only to visible listings; admins can see all workspace listings.
    visible_listings = visible_local_listing_query(ws_id).filter(LocalListing.id.in_(listing_ids)).all()
    updated = 0
    for listing in visible_listings:
        for field, value in updates.items():
            setattr(listing, field, value)
        listing.updated_at = datetime.utcnow()
        updated += 1

    db.session.commit()
    return jsonify({
        'success': True,
        'updated': updated,
        'message': f'Updated {updated} listing(s)'
    })


@app.route('/api/local/stats', methods=['GET'])
@login_required
@require_active_workspace
def api_local_stats():
    """Get local listings statistics"""
    ws_id = get_active_workspace_id()
    base_query = visible_local_listing_query(ws_id)
    total = base_query.count()
    published = base_query.filter_by(status='published').count()
    draft = base_query.filter_by(status='draft').count()
    
    for_sale = base_query.filter_by(offering_type='sale').count()
    for_rent = base_query.filter_by(offering_type='rent').count()
    
    return jsonify({
        'total': total,
        'published': published,
        'draft': draft,
        'for_sale': for_sale,
        'for_rent': for_rent
    })


# ==================== PF AUTHENTICATION ====================

@app.route('/auth')
@login_required
@permission_required('settings')
@require_active_workspace
def auth_page():
    """PropertyFinder authentication page"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_auth', workspace_slug=ws.slug))
    pf_session = PFSession.query.first()
    return render_template('auth.html', session=pf_session)


@app.route('/api/auth/save-session', methods=['POST'])
def save_pf_session():
    """Save PropertyFinder session cookies from browser"""
    data = request.get_json()
    
    # Get or create session
    pf_session = PFSession.query.first()
    if not pf_session:
        pf_session = PFSession()
        db.session.add(pf_session)
    
    pf_session.cookies = json.dumps(data.get('cookies', {}))
    pf_session.user_agent = data.get('userAgent')
    pf_session.logged_in = data.get('loggedIn', False)
    pf_session.email = data.get('email')
    pf_session.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Session saved'})


@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Check authentication status"""
    pf_session = PFSession.query.first()
    
    if pf_session and pf_session.logged_in:
        return jsonify({
            'authenticated': True,
            'email': pf_session.email,
            'updated_at': pf_session.updated_at.isoformat() if pf_session.updated_at else None
        })
    
    return jsonify({'authenticated': False})


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Clear saved session"""
    PFSession.query.delete()
    db.session.commit()
    return jsonify({'success': True})


# ==================== CRM: LEADS ====================

from database import Lead, Customer

@app.route('/leads')
@login_required
@require_active_workspace
def leads_page():
    """Leads management page"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_leads', workspace_slug=ws.slug))
    return render_template('leads.html')


@app.route('/api/leads/config', methods=['GET'])
@login_required
@require_active_workspace
def api_get_leads_config():
    """Get lead configuration (statuses, sources, team members)"""
    import json
    
    # Get custom statuses and sources from settings
    ws_id = get_active_workspace_id()
    statuses_json = AppSettings.get('lead_statuses', workspace_id=ws_id)
    sources_json = AppSettings.get('lead_sources', workspace_id=ws_id)
    
    try:
        statuses = json.loads(statuses_json) if statuses_json else []
    except:
        statuses = []
    
    try:
        sources = json.loads(sources_json) if sources_json else []
    except:
        sources = []
    
    # Get all users for assignment
    users = User.query.join(
        WorkspaceMember, WorkspaceMember.user_id == User.id
    ).filter(
        WorkspaceMember.workspace_id == ws_id,
        User.is_active == True
    ).all()
    team_members = [{'id': u.id, 'name': u.name, 'email': u.email, 'role': u.role} for u in users]
    
    return jsonify({
        'statuses': statuses,
        'sources': sources,
        'team_members': team_members
    })


@app.route('/api/leads/config', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_leads_admin
def api_update_leads_config():
    """Update lead configuration (statuses, sources)"""
    import json
    ws_id = get_active_workspace_id()
    data = request.get_json() or {}
    
    if 'statuses' in data:
        AppSettings.set('lead_statuses', json.dumps(data['statuses']), workspace_id=ws_id)
    
    if 'sources' in data:
        AppSettings.set('lead_sources', json.dumps(data['sources']), workspace_id=ws_id)
    
    return jsonify({'success': True})


@app.route('/api/leads', methods=['GET'])
@login_required
@require_active_workspace
def api_get_leads():
    """Get leads based on user permissions.
    
    - Admin users see all leads (including unassigned)
    - Non-admin users only see leads assigned to them
    - Unassigned leads (assigned_to_id is NULL) are only visible to admins
    """
    from sqlalchemy.orm import joinedload
    
    # Use joinedload to prevent N+1 query problem (each lead would query user separately)
    ws_id = get_active_workspace_id()
    query = visible_lead_query(ws_id).options(joinedload(Lead.assigned_to)).order_by(Lead.created_at.desc())
    leads = query.all()
    return jsonify({'leads': [l.to_dict() for l in leads]})


@app.route('/api/leads', methods=['POST'])
@login_required
@require_active_workspace
def api_create_lead():
    """Create a new lead"""
    data = request.get_json() or {}
    ws_id = get_active_workspace_id()
    can_manage_all = workspace_user_can_manage_all_leads(workspace_id=ws_id)
    assigned_to_id = None
    if can_manage_all:
        assigned_to_id = _validate_assignee(ws_id, data.get('assigned_to_id'))
        if data.get('assigned_to_id') and not assigned_to_id:
            return jsonify({'success': False, 'error': 'Assigned user must be in this workspace'}), 400
    else:
        assigned_to_id = g.user.id
    
    lead = Lead(
        workspace_id=ws_id,
        name=data.get('name'),
        email=data.get('email'),
        phone=data.get('phone'),
        whatsapp=data.get('whatsapp'),
        source=data.get('source', 'other'),
        message=data.get('message'),
        listing_reference=data.get('listing_reference'),
        priority=data.get('priority', 'medium'),
        status=data.get('status', 'new'),
        assigned_to_id=assigned_to_id
    )
    
    # Set lead_type if the column exists
    if hasattr(Lead, 'lead_type'):
        lead.lead_type = data.get('lead_type', 'for_sale')
    
    db.session.add(lead)
    db.session.commit()
    
    return jsonify({'success': True, 'lead': lead.to_dict()})


@app.route('/api/leads/<int:lead_id>', methods=['GET'])
@login_required
@require_active_workspace
def api_get_lead(lead_id):
    """Get a single lead"""
    ws_id = get_active_workspace_id()
    lead = get_visible_lead_or_404(lead_id, workspace_id=ws_id)
    return jsonify({'lead': lead.to_dict()})


@app.route('/api/leads/<int:lead_id>', methods=['PATCH'])
@login_required
@require_active_workspace
def api_update_lead(lead_id):
    """Update a lead"""
    ws_id = get_active_workspace_id()
    lead = get_visible_lead_or_404(lead_id, workspace_id=ws_id)
    data = request.get_json() or {}
    can_manage_all = workspace_user_can_manage_all_leads(workspace_id=ws_id)

    if 'assigned_to_id' in data:
        if can_manage_all:
            assigned_to_id = _validate_assignee(ws_id, data.get('assigned_to_id'))
            if data.get('assigned_to_id') and not assigned_to_id:
                return jsonify({'success': False, 'error': 'Assigned user must be in this workspace'}), 400
            lead.assigned_to_id = assigned_to_id
        else:
            requested_assignee = data.get('assigned_to_id')
            try:
                requested_assignee_int = int(requested_assignee) if requested_assignee not in (None, '', 'null') else None
            except (TypeError, ValueError):
                requested_assignee_int = None
            if requested_assignee_int != g.user.id:
                return jsonify({'success': False, 'error': 'You cannot reassign leads'}), 403
            lead.assigned_to_id = g.user.id
    
    for field in ['name', 'email', 'phone', 'whatsapp', 'source', 'message', 
                  'listing_reference', 'status', 'priority', 'lead_type', 'notes']:
        if field in data:
            setattr(lead, field, data[field])
    
    if 'status' in data and data['status'] == 'contacted':
        lead.last_contact = datetime.utcnow()
    
    db.session.commit()
    return jsonify({'success': True, 'lead': lead.to_dict()})


@app.route('/api/leads/<int:lead_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_lead(lead_id):
    """Delete a lead"""
    ws_id = get_active_workspace_id()
    lead = get_visible_lead_or_404(lead_id, workspace_id=ws_id)
    db.session.delete(lead)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/leads/bulk-delete', methods=['POST'])
@login_required
@require_active_workspace
def api_bulk_delete_leads():
    """Delete multiple leads at once"""
    data = request.get_json() or {}
    lead_ids = data.get('ids', [])
    
    if not lead_ids:
        return jsonify({'success': False, 'error': 'No leads selected'}), 400
    
    ws_id = get_active_workspace_id()
    visible_leads = visible_lead_query(ws_id).filter(Lead.id.in_(lead_ids)).all()
    deleted = 0
    for lead in visible_leads:
        db.session.delete(lead)
        deleted += 1
    
    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@app.route('/api/leads/bulk-update', methods=['POST'])
@login_required
@require_active_workspace
def api_bulk_update_leads():
    """Bulk update status, source, or assigned person for multiple leads"""
    data = request.get_json() or {}
    lead_ids = data.get('ids', [])
    
    if not lead_ids:
        return jsonify({'success': False, 'error': 'No leads selected'}), 400
    
    ws_id = get_active_workspace_id()
    can_manage_all = workspace_user_can_manage_all_leads(workspace_id=ws_id)
    updates = {}
    if 'status' in data and data['status']:
        updates['status'] = data['status']
    if 'source' in data and data['source']:
        updates['source'] = data['source']
    if 'assigned_to_id' in data:
        if not can_manage_all:
            return jsonify({'success': False, 'error': 'You cannot bulk reassign leads'}), 403
        raw_assignee = data.get('assigned_to_id')
        if raw_assignee == '':
            pass  # no change
        elif raw_assignee in ('null', None):
            updates['assigned_to_id'] = None
        else:
            assigned_to_id = _validate_assignee(ws_id, raw_assignee)
            if not assigned_to_id:
                return jsonify({'success': False, 'error': 'Assigned user must be in this workspace'}), 400
            updates['assigned_to_id'] = assigned_to_id
    
    if not updates:
        return jsonify({'success': False, 'error': 'No updates provided'}), 400
    
    updated = 0
    visible_leads = visible_lead_query(ws_id).filter(Lead.id.in_(lead_ids)).all()
    for lead in visible_leads:
        for field, value in updates.items():
            setattr(lead, field, value)
        updated += 1
    
    db.session.commit()
    return jsonify({'success': True, 'updated': updated})


@app.route('/api/leads/cleanup-sources', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_leads_admin
def api_cleanup_lead_sources():
    """Fix leads with invalid source IDs (like source_12345...) by setting them to 'other'"""
    import json
    
    # Get valid sources
    ws_id = get_active_workspace_id()
    sources_json = AppSettings.get('lead_sources', workspace_id=ws_id)
    try:
        valid_sources = json.loads(sources_json) if sources_json else []
        valid_source_ids = {s['id'] for s in valid_sources}
    except:
        valid_source_ids = set()
    
    # Add some default valid sources
    valid_source_ids.update(['propertyfinder', 'bayut', 'website', 'facebook', 'instagram', 
                              'whatsapp', 'phone', 'email', 'referral', 'zapier', 'other'])
    
    # Find and fix leads with invalid sources
    fixed = 0
    leads = scope_query(Lead.query, ws_id).all()
    for lead in leads:
        if lead.source and lead.source not in valid_source_ids:
            lead.source = 'other'
            fixed += 1
    
    db.session.commit()
    return jsonify({'success': True, 'fixed': fixed})


@app.route('/api/leads/<int:lead_id>/comments', methods=['GET'])
@login_required
@require_active_workspace
def api_get_lead_comments(lead_id):
    """Get all comments for a lead"""
    try:
        from werkzeug.exceptions import HTTPException
        ws_id = get_active_workspace_id()
        lead = get_visible_lead_or_404(lead_id, workspace_id=ws_id)
        comments = LeadComment.query.filter_by(lead_id=lead_id).order_by(LeadComment.created_at.desc()).all()
        return jsonify({'comments': [c.to_dict() for c in comments]})
    except HTTPException:
        raise
    except Exception:
        # Table might not exist yet
        return jsonify({'comments': []})


@app.route('/api/leads/<int:lead_id>/comments', methods=['POST'])
@login_required
@require_active_workspace
def api_add_lead_comment(lead_id):
    """Add a comment to a lead"""
    ws_id = get_active_workspace_id()
    lead = get_visible_lead_or_404(lead_id, workspace_id=ws_id)
    data = request.get_json() or {}
    
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'error': 'Comment content is required'}), 400
    
    comment = LeadComment(
        lead_id=lead_id,
        user_id=g.user.id,
        content=content
    )
    db.session.add(comment)
    db.session.commit()
    
    return jsonify({'success': True, 'comment': comment.to_dict()})


@app.route('/api/leads/<int:lead_id>/comments/<int:comment_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_lead_comment(lead_id, comment_id):
    """Delete a comment from a lead"""
    ws_id = get_active_workspace_id()
    get_visible_lead_or_404(lead_id, workspace_id=ws_id)
    comment = LeadComment.query.filter_by(id=comment_id, lead_id=lead_id).first_or_404()
    
    # Only allow deletion by comment author or admin
    if comment.user_id != g.user.id and not workspace_user_can_manage_all_leads(workspace_id=ws_id):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    db.session.delete(comment)
    db.session.commit()
    
    return jsonify({'success': True})


@app.route('/api/leads/refresh-agents', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_leads_admin
def api_refresh_lead_agents():
    """Refresh agent names for all PF leads based on listing's assignedTo"""
    ws_id = get_active_workspace_id()
    # Get PF users and listings
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    pf_listings = PFCache.get_cache('listings', workspace_id=ws_id) or []
    
    user_map = {u.get('publicProfile', {}).get('id'): u for u in pf_users}
    listing_map = {l.get('id'): l for l in pf_listings}
    for l in pf_listings:
        if l.get('reference'):
            listing_map[l.get('reference')] = l
    
    # Update all PF leads
    leads = Lead.query.filter_by(source='propertyfinder', workspace_id=ws_id).all()
    updated = 0
    
    for lead in leads:
        listing_id = lead.pf_listing_id or lead.listing_reference
        pf_agent_id = None
        pf_agent_name = None
        
        # Get agent from listing's assignedTo
        if listing_id and listing_id in listing_map:
            pf_listing = listing_map[listing_id]
            assigned_to = pf_listing.get('assignedTo', {})
            if assigned_to and assigned_to.get('id'):
                pf_agent_id = str(assigned_to.get('id'))
        
        # Fallback to existing pf_agent_id
        if not pf_agent_id and lead.pf_agent_id:
            pf_agent_id = lead.pf_agent_id
        
        # Map agent ID to name
        if pf_agent_id:
            agent_id_int = int(pf_agent_id) if pf_agent_id.isdigit() else None
            if agent_id_int and agent_id_int in user_map:
                user = user_map[agent_id_int]
                pf_agent_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
        
        # Update if changed
        if pf_agent_id != lead.pf_agent_id or pf_agent_name != lead.pf_agent_name:
            lead.pf_agent_id = pf_agent_id
            lead.pf_agent_name = pf_agent_name
            updated += 1
    
    db.session.commit()
    return jsonify({'success': True, 'updated': updated, 'total': len(leads)})


@app.route('/api/leads/auto-assign', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_leads_admin
def api_auto_assign_leads():
    """Auto-assign unassigned leads to L-Manager users based on PF agent email matching.
    
    This endpoint looks at leads that have a pf_agent_id but no assigned_to_id,
    finds the PF agent's email, and assigns the lead to the L-Manager user with
    the matching email.
    """
    ws_id = get_active_workspace_id()
    # Get PF users to map agent ID to email
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    pf_agent_email_map = {}
    for pf_user in pf_users:
        pf_id = pf_user.get('publicProfile', {}).get('id')
        pf_email = pf_user.get('email', '').lower()
        if pf_id and pf_email:
            pf_agent_email_map[str(pf_id)] = pf_email
    
    # Get L-Manager users by email
    lm_users = User.query.join(
        WorkspaceMember, WorkspaceMember.user_id == User.id
    ).filter(
        WorkspaceMember.workspace_id == ws_id,
        User.is_active == True
    ).all()
    email_to_lm_user = {u.email.lower(): u for u in lm_users}
    
    # Find unassigned leads that have a PF agent
    unassigned_leads = Lead.query.filter(
        Lead.assigned_to_id.is_(None),
        Lead.pf_agent_id.isnot(None),
        Lead.pf_agent_id != '',
        Lead.workspace_id == ws_id
    ).all()
    
    assigned_count = 0
    no_match_count = 0
    
    for lead in unassigned_leads:
        if lead.pf_agent_id in pf_agent_email_map:
            pf_email = pf_agent_email_map[lead.pf_agent_id]
            if pf_email in email_to_lm_user:
                lead.assigned_to_id = email_to_lm_user[pf_email].id
                assigned_count += 1
            else:
                no_match_count += 1
        else:
            no_match_count += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'assigned': assigned_count,
        'no_match': no_match_count,
        'total_unassigned': len(unassigned_leads)
    })


@app.route('/api/leads/sync-pf', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_leads_admin
def api_sync_leads_from_pf():
    """Sync leads from PropertyFinder"""
    from dateutil import parser as date_parser
    
    try:
        ws_id = get_active_workspace_id()
        client = get_client(workspace_id=ws_id)
        # Fetch all leads with pagination
        all_pf_leads = []
        page = 1
        while True:
            result = client.get_leads(page=page, per_page=50)
            leads_data = result.get('data', [])
            if not leads_data:
                break
            all_pf_leads.extend(leads_data)
            pagination = result.get('pagination', {})
            if page >= pagination.get('totalPages', 1):
                break
            page += 1
            if page > 10:  # Safety limit
                break
        
        # Get PF users to map agent names and emails
        pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
        user_map = {u.get('publicProfile', {}).get('id'): u for u in pf_users}
        
        # Build map of PF agent email -> L-Manager user for auto-assignment
        lm_users = User.query.join(
            WorkspaceMember, WorkspaceMember.user_id == User.id
        ).filter(
            WorkspaceMember.workspace_id == ws_id,
            User.is_active == True
        ).all()
        email_to_lm_user = {u.email.lower(): u for u in lm_users}
        
        # Map PF agent ID to their email
        pf_agent_email_map = {}
        for pf_user in pf_users:
            pf_id = pf_user.get('publicProfile', {}).get('id')
            pf_email = pf_user.get('email', '').lower()
            if pf_id and pf_email:
                pf_agent_email_map[str(pf_id)] = pf_email
        
        # Get PF listings to map listing owners (assignedTo)
        pf_listings = PFCache.get_cache('listings', workspace_id=ws_id) or []
        listing_map = {l.get('id'): l for l in pf_listings}
        # Also map by reference
        for l in pf_listings:
            if l.get('reference'):
                listing_map[l.get('reference')] = l
        
        imported = 0
        skipped = 0
        for pf_lead in all_pf_leads:
            # Check if already exists
            source_id = str(pf_lead.get('id'))
            existing = Lead.query.filter_by(source='propertyfinder', source_id=source_id, workspace_id=ws_id).first()
            if existing:
                skipped += 1
                continue
            
            # Extract contact info - PF API uses 'sender' not 'contact'
            sender = pf_lead.get('sender', {})
            listing = pf_lead.get('listing', {})
            public_profile = pf_lead.get('publicProfile', {})
            
            # Get phone/email from contacts array
            contacts = sender.get('contacts', [])
            phone = None
            email = None
            for c in contacts:
                if c.get('type') == 'phone':
                    phone = c.get('value')
                elif c.get('type') == 'email':
                    email = c.get('value')
            
            # Parse received date from PF
            received_at = None
            if pf_lead.get('createdAt'):
                try:
                    received_at = date_parser.parse(pf_lead.get('createdAt'))
                except:
                    pass
            
            # Get agent info - use listing's assignedTo (the listing owner/agent)
            listing_id = listing.get('id') or listing.get('reference')
            pf_agent_id = None
            pf_agent_name = None
            
            # First try to get agent from listing's assignedTo
            if listing_id and listing_id in listing_map:
                pf_listing = listing_map[listing_id]
                assigned_to = pf_listing.get('assignedTo', {})
                if assigned_to and assigned_to.get('id'):
                    pf_agent_id = str(assigned_to.get('id'))
            
            # Fallback to publicProfile if no listing agent
            if not pf_agent_id and public_profile.get('id'):
                pf_agent_id = str(public_profile.get('id'))
            
            # Map agent ID to name
            if pf_agent_id:
                agent_id_int = int(pf_agent_id) if pf_agent_id.isdigit() else None
                if agent_id_int and agent_id_int in user_map:
                    user = user_map[agent_id_int]
                    pf_agent_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
            
            # Auto-assign to L-Manager user based on PF agent email
            assigned_to_id = None
            if pf_agent_id and pf_agent_id in pf_agent_email_map:
                pf_agent_email = pf_agent_email_map[pf_agent_id]
                if pf_agent_email in email_to_lm_user:
                    assigned_to_id = email_to_lm_user[pf_agent_email].id
            
            lead = Lead(
                workspace_id=ws_id,
                source='propertyfinder',
                source_id=source_id,
                channel=pf_lead.get('channel'),  # whatsapp, email, call
                name=sender.get('name', 'Unknown'),
                email=email,
                phone=phone,
                whatsapp=phone if pf_lead.get('channel') == 'whatsapp' else None,
                message=pf_lead.get('message'),
                pf_listing_id=str(listing.get('id')) if listing.get('id') else None,
                listing_reference=listing.get('reference'),
                response_link=pf_lead.get('responseLink'),
                status='new',
                pf_status=pf_lead.get('status'),  # sent, delivered, read, replied
                priority='medium',
                pf_agent_id=pf_agent_id,
                pf_agent_name=pf_agent_name,
                assigned_to_id=assigned_to_id,  # Auto-assigned if email matches
                received_at=received_at
            )
            db.session.add(lead)
            imported += 1
        
        db.session.commit()
        return jsonify({
            'success': True, 
            'imported': imported, 
            'skipped': skipped,
            'total': len(all_pf_leads)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== CONTACTS ====================

from database import Contact

@app.route('/api/contacts', methods=['GET'])
@login_required
@require_active_workspace
def api_get_contacts():
    """Get all contacts with optional search"""
    ws_id = get_active_workspace_id()
    search = request.args.get('search', '').strip()
    query = Contact.query.filter_by(workspace_id=ws_id).order_by(Contact.name)
    
    if search:
        query = query.filter(
            (Contact.name.ilike(f'%{search}%')) |
            (Contact.phone.ilike(f'%{search}%')) |
            (Contact.email.ilike(f'%{search}%')) |
            (Contact.company.ilike(f'%{search}%'))
        )
    
    contacts = query.all()
    return jsonify({
        'contacts': [c.to_dict() for c in contacts],
        'country_codes': Contact.COUNTRY_CODES
    })


@app.route('/api/contacts', methods=['POST'])
@login_required
@require_active_workspace
def api_create_contact():
    """Create a new contact"""
    data = request.get_json() or {}
    ws_id = get_active_workspace_id()
    
    # Validate required fields
    if not data.get('name') or not data.get('phone'):
        return jsonify({'error': 'Name and phone are required'}), 400
    
    # Build full phone with country code
    phone = data.get('phone', '').strip()
    country_code = data.get('country_code', '+971').strip()
    
    # If phone already has country code, use it as-is
    if phone.startswith('+'):
        full_phone = phone
    else:
        # Remove leading zero and prepend country code
        full_phone = country_code + phone.lstrip('0')
    
    if data.get('lead_id'):
        lead = visible_lead_query(ws_id).filter_by(id=data.get('lead_id')).first()
        if not lead:
            return jsonify({'error': 'Lead not found'}), 404
    
    contact = Contact(
        workspace_id=ws_id,
        name=data.get('name'),
        phone=full_phone,
        country_code=country_code,
        email=data.get('email'),
        company=data.get('company'),
        notes=data.get('notes'),
        tags=','.join(data.get('tags', [])) if isinstance(data.get('tags'), list) else data.get('tags'),
        lead_id=data.get('lead_id'),
        created_by_id=current_user.id
    )
    
    db.session.add(contact)
    db.session.commit()
    
    return jsonify({'success': True, 'contact': contact.to_dict()})


@app.route('/api/contacts/<int:contact_id>', methods=['GET'])
@login_required
@require_active_workspace
def api_get_contact(contact_id):
    """Get a single contact"""
    ws_id = get_active_workspace_id()
    contact = Contact.query.filter_by(id=contact_id, workspace_id=ws_id).first_or_404()
    return jsonify(contact.to_dict())


@app.route('/api/contacts/<int:contact_id>', methods=['PATCH'])
@login_required
@require_active_workspace
def api_update_contact(contact_id):
    """Update a contact"""
    ws_id = get_active_workspace_id()
    contact = Contact.query.filter_by(id=contact_id, workspace_id=ws_id).first_or_404()
    data = request.get_json()
    
    if 'name' in data:
        contact.name = data['name']
    if 'phone' in data:
        phone = data['phone'].strip()
        country_code = data.get('country_code', contact.country_code or '+971').strip()
        if phone.startswith('+'):
            contact.phone = phone
        else:
            contact.phone = country_code + phone.lstrip('0')
        contact.country_code = country_code
    if 'email' in data:
        contact.email = data['email']
    if 'company' in data:
        contact.company = data['company']
    if 'notes' in data:
        contact.notes = data['notes']
    if 'tags' in data:
        contact.tags = ','.join(data['tags']) if isinstance(data['tags'], list) else data['tags']
    
    db.session.commit()
    return jsonify({'success': True, 'contact': contact.to_dict()})


@app.route('/api/contacts/<int:contact_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_contact(contact_id):
    """Delete a contact"""
    ws_id = get_active_workspace_id()
    contact = Contact.query.filter_by(id=contact_id, workspace_id=ws_id).first_or_404()
    db.session.delete(contact)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/contacts/from-lead/<int:lead_id>', methods=['POST'])
@login_required
@require_active_workspace
def api_create_contact_from_lead(lead_id):
    """Create a contact from an existing lead"""
    try:
        from werkzeug.exceptions import HTTPException
        ws_id = get_active_workspace_id()
        lead = get_visible_lead_or_404(lead_id, workspace_id=ws_id)
        
        # Check if contact already exists with same phone
        phone = lead.phone or lead.whatsapp or ''
        if phone:
            try:
                existing = Contact.query.filter(
                    (Contact.phone == phone) | 
                    (Contact.phone == phone.lstrip('+').lstrip('0'))
                ).filter(Contact.workspace_id == ws_id).first()
                if existing:
                    return jsonify({'success': True, 'contact': existing.to_dict(), 'existing': True})
            except Exception as e:
                print(f"[DEBUG] Error checking existing contact: {e}")
        
        # Extract country code from phone if present
        country_code = '+971'  # Default UAE
        phone_number = phone
        
        if phone and phone.startswith('+'):
            for code, _ in Contact.COUNTRY_CODES:
                if phone.startswith(code):
                    country_code = code
                    phone_number = phone[len(code):]
                    break
        
        # Create contact with only basic required fields
        contact = Contact(
            name=lead.name or 'Unknown',
            phone=phone_number or '',
            country_code=country_code,
            email=lead.email or '',
            workspace_id=ws_id
        )
        
        # Set optional fields safely
        if hasattr(contact, 'notes'):
            contact.notes = lead.message or ''
        
        db.session.add(contact)
        db.session.commit()
        
        return jsonify({'success': True, 'contact': contact.to_dict()})
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed to create contact from lead: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== TASK MANAGEMENT (Trello-like) ====================

@app.route('/tasks')
@login_required
@require_active_workspace
def tasks_page():
    """Tasks management page - Trello-like boards"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_tasks', workspace_slug=ws.slug))
    return render_template('tasks.html')


# ---- Task Boards API ----

@app.route('/api/boards', methods=['GET'])
@login_required
@require_active_workspace
def api_get_boards():
    """Get all task boards the user has access to"""
    try:
        include_archived = request.args.get('include_archived', 'false') == 'true'
        user_id = session.get('user_id')
        ws_id = get_active_workspace_id()
        
        # Get boards where user is creator or member
        created_boards = TaskBoard.query.filter(
            TaskBoard.created_by_id == user_id,
            TaskBoard.workspace_id == ws_id
        )
        member_board_ids = db.session.query(BoardMember.board_id).filter(BoardMember.user_id == user_id).subquery()
        member_boards = TaskBoard.query.filter(
            TaskBoard.id.in_(member_board_ids),
            TaskBoard.workspace_id == ws_id
        )
        # Also include public boards
        public_boards = TaskBoard.query.filter(
            TaskBoard.is_private == False,
            TaskBoard.workspace_id == ws_id
        )
        
        query = created_boards.union(member_boards).union(public_boards)
        
        if not include_archived:
            # Re-filter for archived (union loses filters)
            boards = [b for b in query.all() if not b.is_archived]
        else:
            boards = query.all()
        
        # Sort by favorite then updated
        boards.sort(key=lambda b: (not b.is_favorite, b.updated_at or b.created_at), reverse=True)
        
        # Add user's role to each board
        result = []
        for board in boards:
            board_dict = board.to_dict()
            board_dict['my_role'] = board.get_user_role(user_id) or ('viewer' if not board.is_private else None)
            result.append(board_dict)
        
        return jsonify({'success': True, 'boards': result})
    except Exception as e:
        import traceback
        print(f"Error in api_get_boards: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/boards', methods=['POST'])
@login_required
@require_active_workspace
def api_create_board():
    """Create a new task board"""
    try:
        import uuid
        data = request.get_json()
        
        if not data.get('name'):
            return jsonify({'success': False, 'error': 'Board name is required'}), 400
        
        # Create default columns
        default_columns = [
            {'id': str(uuid.uuid4()), 'name': 'To Do', 'color': '#6b7280'},
            {'id': str(uuid.uuid4()), 'name': 'In Progress', 'color': '#3b82f6'},
            {'id': str(uuid.uuid4()), 'name': 'Review', 'color': '#f59e0b'},
            {'id': str(uuid.uuid4()), 'name': 'Done', 'color': '#10b981'}
        ]
        
        ws_id = get_active_workspace_id()
        board = TaskBoard(
            workspace_id=ws_id,
            name=data['name'],
            description=data.get('description', ''),
            color=data.get('color', '#3b82f6'),
            icon=data.get('icon', 'clipboard'),
            is_private=data.get('is_private', True),
            created_by_id=session.get('user_id')
        )
        board.set_columns(data.get('columns', default_columns))
        
        db.session.add(board)
        db.session.commit()
        
        # Return board with permissions for creator (owner)
        board_dict = board.to_dict()
        board_dict['my_role'] = 'owner'
        board_dict['my_permissions'] = BOARD_PERMISSIONS.get('owner', {})
        
        return jsonify({'success': True, 'board': board_dict})
    except Exception as e:
        import traceback
        print(f"Error in api_create_board: {e}")
        traceback.print_exc()
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/boards/<int:board_id>', methods=['GET'])
@login_required
@require_active_workspace
def api_get_board(board_id):
    """Get a single board with tasks"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    # Check access
    if board.is_private and not board.user_can(user_id, 'can_view'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    board_dict = board.to_dict(include_tasks=True, include_members=True)
    board_dict['my_role'] = board.get_user_role(user_id) or 'viewer'
    board_dict['my_permissions'] = BOARD_PERMISSIONS.get(board_dict['my_role'], {})
    
    return jsonify({'success': True, 'board': board_dict})


@app.route('/api/boards/<int:board_id>', methods=['PUT'])
@login_required
@require_active_workspace
def api_update_board(board_id):
    """Update a board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    # Check permission
    if not board.user_can(user_id, 'can_edit_board'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    
    if 'name' in data:
        board.name = data['name']
    if 'description' in data:
        board.description = data['description']
    if 'color' in data:
        board.color = data['color']
    if 'icon' in data:
        board.icon = data['icon']
    if 'columns' in data:
        board.set_columns(data['columns'])
    if 'is_archived' in data:
        board.is_archived = data['is_archived']
    if 'is_favorite' in data:
        board.is_favorite = data['is_favorite']
    if 'is_private' in data:
        board.is_private = data['is_private']
    
    db.session.commit()
    return jsonify({'success': True, 'board': board.to_dict()})


@app.route('/api/boards/<int:board_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_board(board_id):
    """Delete a board and all its tasks"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    # Only owner can delete
    if not board.user_can(user_id, 'can_delete_board'):
        return jsonify({'success': False, 'error': 'Only the owner can delete this board'}), 403
    
    db.session.delete(board)
    db.session.commit()
    return jsonify({'success': True})


# ---- Board Members API ----

@app.route('/api/boards/<int:board_id>/members', methods=['GET'])
@login_required
@require_active_workspace
def api_get_board_members(board_id):
    """Get all members of a board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    if not board.user_can(user_id, 'can_view'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    return jsonify({
        'success': True, 
        'members': board.get_all_members_with_creator(),
        'available_roles': BOARD_PERMISSIONS
    })


@app.route('/api/boards/<int:board_id>/members', methods=['POST'])
@login_required
@require_active_workspace
def api_add_board_member(board_id):
    """Add a member to the board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    if not board.user_can(user_id, 'can_manage_members'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    member_user_id = data.get('user_id')
    member_email = data.get('email')
    role = data.get('role', 'member')
    
    # Find user by ID or email
    if member_user_id:
        target_user = User.query.get(member_user_id)
    elif member_email:
        target_user = User.query.filter_by(email=member_email).first()
    else:
        return jsonify({'success': False, 'error': 'User ID or email required'}), 400
    
    if not target_user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # Ensure user belongs to this workspace
    if not WorkspaceMember.query.filter_by(workspace_id=ws_id, user_id=target_user.id).first():
        return jsonify({'success': False, 'error': 'User not in this workspace'}), 400
    
    # Check if already a member
    existing = BoardMember.query.filter_by(board_id=board_id, user_id=target_user.id).first()
    if existing:
        return jsonify({'success': False, 'error': 'User is already a member'}), 400
    
    # Can't add creator as member (they're already owner)
    if target_user.id == board.created_by_id:
        return jsonify({'success': False, 'error': 'Cannot add the board owner as a member'}), 400
    
    # Validate role
    if role not in BOARD_PERMISSIONS:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    
    # Can't make someone owner
    if role == 'owner':
        return jsonify({'success': False, 'error': 'Cannot assign owner role'}), 400
    
    member = BoardMember(
        board_id=board_id,
        user_id=target_user.id,
        role=role,
        invited_by_id=user_id
    )
    
    db.session.add(member)
    db.session.commit()
    
    return jsonify({'success': True, 'member': member.to_dict()})


@app.route('/api/boards/<int:board_id>/members/<int:member_user_id>', methods=['PUT'])
@login_required
@require_active_workspace
def api_update_board_member(board_id, member_user_id):
    """Update a member's role"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    if not board.user_can(user_id, 'can_manage_members'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    member = BoardMember.query.filter_by(board_id=board_id, user_id=member_user_id).first()
    if not member:
        return jsonify({'success': False, 'error': 'Member not found'}), 404
    
    data = request.get_json()
    new_role = data.get('role')
    
    if new_role:
        if new_role not in BOARD_PERMISSIONS or new_role == 'owner':
            return jsonify({'success': False, 'error': 'Invalid role'}), 400
        member.role = new_role
    
    if 'notify_on_assign' in data:
        member.notify_on_assign = data['notify_on_assign']
    if 'notify_on_comment' in data:
        member.notify_on_comment = data['notify_on_comment']
    if 'notify_on_due' in data:
        member.notify_on_due = data['notify_on_due']
    
    db.session.commit()
    return jsonify({'success': True, 'member': member.to_dict()})


@app.route('/api/boards/<int:board_id>/members/<int:member_user_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_remove_board_member(board_id, member_user_id):
    """Remove a member from the board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    # Can remove self or if have permission
    if member_user_id != user_id and not board.user_can(user_id, 'can_manage_members'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    member = BoardMember.query.filter_by(board_id=board_id, user_id=member_user_id).first()
    if not member:
        return jsonify({'success': False, 'error': 'Member not found'}), 404
    
    db.session.delete(member)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/boards/<int:board_id>/available-users', methods=['GET'])
@login_required
@require_active_workspace
def api_get_available_users_for_board(board_id):
    """Get users that can be added to the board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    if not board.user_can(user_id, 'can_manage_members'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Get all active users except those already on the board
    existing_member_ids = [board.created_by_id] + [m.user_id for m in board.members.all()]
    available_users = User.query.join(
        WorkspaceMember, WorkspaceMember.user_id == User.id
    ).filter(
        WorkspaceMember.workspace_id == ws_id,
        User.is_active == True,
        ~User.id.in_(existing_member_ids)
    ).order_by(User.name).all()
    
    return jsonify({
        'success': True,
        'users': [{'id': u.id, 'name': u.name, 'email': u.email} for u in available_users]
    })


# ---- Task Labels API ----

@app.route('/api/boards/<int:board_id>/labels', methods=['GET'])
@login_required
@require_active_workspace
def api_get_board_labels(board_id):
    """Get labels for a board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    if board.is_private and not board.user_can(user_id, 'can_view'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    labels = TaskLabel.query.filter(TaskLabel.board_id == board_id).all()
    return jsonify({'success': True, 'labels': [l.to_dict() for l in labels]})


@app.route('/api/boards/<int:board_id>/labels', methods=['POST'])
@login_required
@require_active_workspace
def api_create_label(board_id):
    """Create a new label"""
    data = request.get_json()
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    if not board.user_can(user_id, 'can_edit_board'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    label = TaskLabel(
        name=data.get('name', 'New Label'),
        color=data.get('color', '#6b7280'),
        board_id=board_id
    )
    
    db.session.add(label)
    db.session.commit()
    return jsonify({'success': True, 'label': label.to_dict()})


@app.route('/api/labels/<int:label_id>', methods=['PUT'])
@login_required
@require_active_workspace
def api_update_label(label_id):
    """Update a label"""
    label = TaskLabel.query.get_or_404(label_id)
    ws_id = get_active_workspace_id()
    if label.board_id is None:
        return jsonify({'success': False, 'error': 'Label not found'}), 404
    if label.board_id:
        board = TaskBoard.query.filter_by(id=label.board_id, workspace_id=ws_id).first()
        if not board:
            return jsonify({'success': False, 'error': 'Label not found'}), 404
        user_id = session.get('user_id')
        if not board.user_can(user_id, 'can_edit_board'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
    data = request.get_json()
    
    if 'name' in data:
        label.name = data['name']
    if 'color' in data:
        label.color = data['color']
    
    db.session.commit()
    return jsonify({'success': True, 'label': label.to_dict()})


@app.route('/api/labels/<int:label_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_label(label_id):
    """Delete a label"""
    label = TaskLabel.query.get_or_404(label_id)
    ws_id = get_active_workspace_id()
    if label.board_id is None:
        return jsonify({'success': False, 'error': 'Label not found'}), 404
    if label.board_id:
        board = TaskBoard.query.filter_by(id=label.board_id, workspace_id=ws_id).first()
        if not board:
            return jsonify({'success': False, 'error': 'Label not found'}), 404
        user_id = session.get('user_id')
        if not board.user_can(user_id, 'can_edit_board'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
    db.session.delete(label)
    db.session.commit()
    return jsonify({'success': True})


# ---- Tasks API ----
def _get_task_in_workspace(task_id, ws_id):
    """Fetch a task scoped to the active workspace."""
    return Task.query.join(
        TaskBoard, Task.board_id == TaskBoard.id
    ).filter(
        Task.id == task_id,
        TaskBoard.workspace_id == ws_id
    ).first_or_404()


def _get_task_comment_in_workspace(comment_id, ws_id):
    """Fetch a task comment scoped to the active workspace."""
    return TaskComment.query.join(
        Task, TaskComment.task_id == Task.id
    ).join(
        TaskBoard, Task.board_id == TaskBoard.id
    ).filter(
        TaskComment.id == comment_id,
        TaskBoard.workspace_id == ws_id
    ).first_or_404()

@app.route('/api/boards/<int:board_id>/tasks', methods=['GET'])
@login_required
@require_active_workspace
def api_get_board_tasks(board_id):
    """Get all tasks for a board"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    # Check access
    if board.is_private and not board.user_can(user_id, 'can_view'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    tasks = Task.query.filter_by(board_id=board_id).order_by(Task.position).all()
    return jsonify({'success': True, 'tasks': [t.to_dict() for t in tasks]})


@app.route('/api/boards/<int:board_id>/tasks', methods=['POST'])
@login_required
@require_active_workspace
def api_create_task(board_id):
    """Create a new task"""
    ws_id = get_active_workspace_id()
    board = TaskBoard.query.filter_by(id=board_id, workspace_id=ws_id).first_or_404()
    user_id = session.get('user_id')
    
    # Check permission
    if not board.user_can(user_id, 'can_create_tasks'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    
    if not data.get('title'):
        return jsonify({'success': False, 'error': 'Task title is required'}), 400
    
    # Get next position in column
    column_id = data.get('column_id')
    if not column_id:
        columns = board.get_columns()
        column_id = columns[0]['id'] if columns else 'default'
    
    max_position = db.session.query(db.func.max(Task.position)).filter(
        Task.board_id == board_id,
        Task.column_id == column_id
    ).scalar() or 0
    
    task = Task(
        title=data['title'],
        description=data.get('description', ''),
        board_id=board_id,
        column_id=column_id,
        position=max_position + 1,
        priority=data.get('priority', 'medium'),
        created_by_id=user_id
    )
    
    if data.get('due_date'):
        try:
            task.due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
        except:
            pass
    
    # Handle primary assignee (workspace members only)
    allowed_user_ids = {
        m.user_id for m in WorkspaceMember.query.filter_by(workspace_id=ws_id).all()
    }
    if data.get('assignee_id') and data['assignee_id'] in allowed_user_ids:
        task.assignee_id = data['assignee_id']
    
    if data.get('cover_color'):
        task.cover_color = data['cover_color']
    
    # Handle labels
    if data.get('label_ids'):
        labels = TaskLabel.query.filter(
            TaskLabel.id.in_(data['label_ids']),
            TaskLabel.board_id == board_id
        ).all()
        task.labels = labels
    
    db.session.add(task)
    db.session.flush()  # Get task ID
    
    # Handle multiple assignees
    if data.get('assignee_ids'):
        assignee_ids = [i for i in data['assignee_ids'] if i in allowed_user_ids]
        if assignee_ids:
            # First one is primary assignee
            task.assignee_id = assignee_ids[0]
            # Rest go to secondary assignees
            if len(assignee_ids) > 1:
                secondary_assignees = User.query.filter(User.id.in_(assignee_ids[1:])).all()
                task.assignees = secondary_assignees
    
    db.session.commit()
    
    return jsonify({'success': True, 'task': task.to_dict()})


@app.route('/api/tasks/<int:task_id>', methods=['GET'])
@login_required
@require_active_workspace
def api_get_task(task_id):
    """Get a single task with all details"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    user_id = session.get('user_id')
    board = task.board
    
    # Check access
    if board.is_private and not board.user_can(user_id, 'can_view'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    task_dict = task.to_dict()
    task_dict['comments'] = [c.to_dict() for c in task.comments.order_by(TaskComment.created_at.desc()).all()]
    
    # Add board members for assignee picker
    task_dict['available_assignees'] = board.get_all_members_with_creator()
    
    return jsonify({'success': True, 'task': task_dict})


@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
@login_required
@require_active_workspace
def api_update_task(task_id):
    """Update a task"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    user_id = session.get('user_id')
    board = task.board
    
    # Check permission - editors can edit, members can only edit if assigned to them
    can_edit = board.user_can(user_id, 'can_edit')
    is_assigned = task.is_assigned_to(user_id)
    is_creator = task.created_by_id == user_id
    
    if not can_edit and not is_assigned and not is_creator:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    
    if 'title' in data:
        task.title = data['title']
    if 'description' in data:
        task.description = data['description']
    if 'priority' in data:
        task.priority = data['priority']
    if 'cover_color' in data:
        task.cover_color = data['cover_color']
    if 'cover_image' in data:
        task.cover_image = data['cover_image']
    
    # Handle primary assignee (workspace members only)
    allowed_user_ids = {
        m.user_id for m in WorkspaceMember.query.filter_by(workspace_id=ws_id).all()
    }
    if 'assignee_id' in data:
        task.assignee_id = data['assignee_id'] if data['assignee_id'] in allowed_user_ids else None
    
    # Handle multiple assignees (includes primary)
    if 'assignee_ids' in data:
        assignee_ids = [i for i in data['assignee_ids'] if i in allowed_user_ids]
        if assignee_ids:
            # First one is primary assignee
            task.assignee_id = assignee_ids[0]
            # Rest go to secondary assignees
            if len(assignee_ids) > 1:
                secondary_assignees = User.query.filter(User.id.in_(assignee_ids[1:])).all()
                task.assignees = secondary_assignees
            else:
                task.assignees = []
        else:
            task.assignee_id = None
            task.assignees = []
    
    if 'due_date' in data:
        if data['due_date']:
            try:
                task.due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
            except:
                pass
        else:
            task.due_date = None
    
    if 'start_date' in data:
        if data['start_date']:
            try:
                task.start_date = datetime.fromisoformat(data['start_date'].replace('Z', '+00:00'))
            except:
                pass
        else:
            task.start_date = None
    
    if 'is_completed' in data:
        task.is_completed = data['is_completed']
        if data['is_completed']:
            task.completed_at = datetime.utcnow()
        else:
            task.completed_at = None
    
    if 'checklist' in data:
        task.set_checklist(data['checklist'])
    
    if 'label_ids' in data:
        labels = TaskLabel.query.filter(
            TaskLabel.id.in_(data['label_ids']),
            TaskLabel.board_id == task.board_id
        ).all()
        task.labels = labels
    
    db.session.commit()
    return jsonify({'success': True, 'task': task.to_dict()})


@app.route('/api/tasks/<int:task_id>/move', methods=['PATCH'])
@login_required
@require_active_workspace
def api_move_task(task_id):
    """Move a task to a different column or position"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    data = request.get_json()
    
    new_column_id = data.get('column_id', task.column_id)
    new_position = data.get('position', task.position)
    new_board_id = data.get('board_id', task.board_id)
    if new_board_id != task.board_id:
        TaskBoard.query.filter_by(id=new_board_id, workspace_id=ws_id).first_or_404()
    
    # If moving to a different column, update positions
    if new_column_id != task.column_id or new_board_id != task.board_id:
        # Decrease positions in old column
        Task.query.filter(
            Task.board_id == task.board_id,
            Task.column_id == task.column_id,
            Task.position > task.position
        ).update({Task.position: Task.position - 1})
        
        # Increase positions in new column from target position
        Task.query.filter(
            Task.board_id == new_board_id,
            Task.column_id == new_column_id,
            Task.position >= new_position
        ).update({Task.position: Task.position + 1})
    else:
        # Same column, just reorder
        if new_position > task.position:
            Task.query.filter(
                Task.board_id == task.board_id,
                Task.column_id == task.column_id,
                Task.position > task.position,
                Task.position <= new_position
            ).update({Task.position: Task.position - 1})
        elif new_position < task.position:
            Task.query.filter(
                Task.board_id == task.board_id,
                Task.column_id == task.column_id,
                Task.position >= new_position,
                Task.position < task.position
            ).update({Task.position: Task.position + 1})
    
    task.board_id = new_board_id
    task.column_id = new_column_id
    task.position = new_position
    
    db.session.commit()
    return jsonify({'success': True, 'task': task.to_dict()})


@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_task(task_id):
    """Delete a task"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    user_id = session.get('user_id')
    board = task.board
    
    # Check permission - need delete permission, or be creator
    can_delete = board.user_can(user_id, 'can_delete_tasks')
    is_creator = task.created_by_id == user_id
    
    if not can_delete and not is_creator:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Update positions of tasks below this one
    Task.query.filter(
        Task.board_id == task.board_id,
        Task.column_id == task.column_id,
        Task.position > task.position
    ).update({Task.position: Task.position - 1})
    
    db.session.delete(task)
    db.session.commit()
    return jsonify({'success': True})


# ---- My Tasks API ----

@app.route('/api/my-tasks', methods=['GET'])
@login_required
@require_active_workspace
def api_get_my_tasks():
    """Get all tasks assigned to the current user across all boards"""
    user_id = session.get('user_id')
    ws_id = get_active_workspace_id()
    board_ids = db.session.query(TaskBoard.id).filter(TaskBoard.workspace_id == ws_id).subquery()
    
    # Get tasks where user is primary assignee or in assignees list
    primary_tasks = Task.query.filter(Task.assignee_id == user_id, Task.board_id.in_(board_ids))
    
    # Get task IDs from task_assignees association
    assigned_task_ids = db.session.query(task_assignee_association.c.task_id).filter(
        task_assignee_association.c.user_id == user_id
    ).subquery()
    secondary_tasks = Task.query.filter(Task.id.in_(assigned_task_ids), Task.board_id.in_(board_ids))
    
    all_tasks = primary_tasks.union(secondary_tasks).order_by(Task.due_date.asc().nullslast(), Task.priority.desc()).all()
    
    # Group by board
    tasks_by_board = {}
    for task in all_tasks:
        board_id = task.board_id
        if board_id not in tasks_by_board:
            tasks_by_board[board_id] = {
                'board': task.board.to_dict() if task.board else None,
                'tasks': []
            }
        tasks_by_board[board_id]['tasks'].append(task.to_dict())
    
    return jsonify({
        'success': True,
        'tasks': [task.to_dict() for task in all_tasks],
        'tasks_by_board': list(tasks_by_board.values()),
        'total': len(all_tasks)
    })


@app.route('/api/tasks/<int:task_id>/assign', methods=['POST'])
@login_required
@require_active_workspace
def api_assign_task(task_id):
    """Assign users to a task"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    user_id = session.get('user_id')
    board = task.board
    
    # Check permission
    if not board.user_can(user_id, 'can_edit'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    data = request.get_json()
    allowed_user_ids = {
        m.user_id for m in WorkspaceMember.query.filter_by(workspace_id=ws_id).all()
    }
    assignee_ids = [i for i in data.get('assignee_ids', []) if i in allowed_user_ids]
    
    # Set primary assignee (first in list)
    if assignee_ids:
        task.assignee_id = assignee_ids[0]
        # Set additional assignees
        if len(assignee_ids) > 1:
            additional_assignees = User.query.filter(User.id.in_(assignee_ids[1:])).all()
            task.assignees = additional_assignees
        else:
            task.assignees = []
    else:
        task.assignee_id = None
        task.assignees = []
    
    db.session.commit()
    return jsonify({'success': True, 'task': task.to_dict()})


@app.route('/api/tasks/<int:task_id>/unassign', methods=['POST'])
@login_required
@require_active_workspace
def api_unassign_task(task_id):
    """Remove assignment from a task"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    user_id = session.get('user_id')
    board = task.board
    
    # Can unassign self or have edit permission
    target_user_id = request.get_json().get('user_id', user_id)
    
    if target_user_id != user_id and not board.user_can(user_id, 'can_edit'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    # Remove from primary assignee
    if task.assignee_id == target_user_id:
        task.assignee_id = None
    
    # Remove from additional assignees
    task.assignees = [u for u in task.assignees if u.id != target_user_id]
    
    db.session.commit()
    return jsonify({'success': True, 'task': task.to_dict()})


# ---- Task Comments API ----

@app.route('/api/tasks/<int:task_id>/comments', methods=['GET'])
@login_required
@require_active_workspace
def api_get_task_comments(task_id):
    """Get comments for a task"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    comments = task.comments.order_by(TaskComment.created_at.desc()).all()
    return jsonify({'success': True, 'comments': [c.to_dict() for c in comments]})


@app.route('/api/tasks/<int:task_id>/comments', methods=['POST'])
@login_required
@require_active_workspace
def api_create_task_comment(task_id):
    """Add a comment to a task"""
    ws_id = get_active_workspace_id()
    task = _get_task_in_workspace(task_id, ws_id)
    data = request.get_json()
    
    if not data.get('content'):
        return jsonify({'success': False, 'error': 'Comment content is required'}), 400
    
    comment = TaskComment(
        task_id=task_id,
        user_id=session.get('user_id'),
        content=data['content']
    )
    
    db.session.add(comment)
    db.session.commit()
    
    return jsonify({'success': True, 'comment': comment.to_dict()})


@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
@login_required
@require_active_workspace
def api_delete_task_comment(comment_id):
    """Delete a comment"""
    ws_id = get_active_workspace_id()
    comment = _get_task_comment_in_workspace(comment_id, ws_id)
    
    # Only allow delete by author or admin
    if comment.user_id != session.get('user_id'):
        user = User.query.get(session.get('user_id'))
        if not user or user.role != 'admin':
            return jsonify({'success': False, 'error': 'Not authorized'}), 403
    
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'success': True})


# ==================== WEBHOOKS ====================

@app.route('/webhooks/zapier', methods=['POST'])
def webhook_zapier():
    """
    Receive leads from Zapier
    
    Expected payload:
    {
        "name": "John Doe",
        "email": "john@example.com",
        "phone": "+971501234567",
        "message": "Interested in property",
        "source": "facebook",  // facebook, instagram, website, etc.
        "listing_reference": "ABC-123"  // optional
    }
    """
    # Verify webhook secret if configured
    secret = request.headers.get('X-Webhook-Secret')
    expected_secret = os.environ.get('ZAPIER_WEBHOOK_SECRET')
    if expected_secret and secret != expected_secret:
        return jsonify({'error': 'Invalid webhook secret'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    ws_id = get_default_workspace_id()
    # Create lead
    lead = Lead(
        workspace_id=ws_id,
        source=data.get('source', 'zapier'),
        name=data.get('name', 'Unknown'),
        email=data.get('email'),
        phone=data.get('phone'),
        whatsapp=data.get('whatsapp') or data.get('phone'),
        message=data.get('message'),
        listing_reference=data.get('listing_reference'),
        status='new',
        priority=data.get('priority', 'medium')
    )
    
    db.session.add(lead)
    db.session.commit()
    
    return jsonify({'success': True, 'lead_id': lead.id})


@app.route('/webhooks/propertyfinder', methods=['POST'])
def webhook_propertyfinder():
    """Receive lead notifications from PropertyFinder webhook"""
    raw_body = request.get_data() or b''
    signature = request.headers.get('X-Signature', '')
    if Config.WEBHOOK_SECRET:
        import hmac
        import hashlib
        expected = hmac.new(
            Config.WEBHOOK_SECRET.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            return jsonify({'error': 'Invalid webhook signature'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    payload = data.get('payload') or data.get('data') or data
    event_id = data.get('eventId') or data.get('event_id')
    if event_id and not str(event_id).startswith('lead.'):
        return jsonify({'success': True, 'ignored': True, 'event_id': event_id})

    # Extract lead info from PF webhook payload (WHPayloadLead)
    sender = payload.get('sender', {}) if isinstance(payload, dict) else {}
    contacts = sender.get('contacts', []) if isinstance(sender, dict) else []
    phone = ''
    email = ''
    whatsapp = ''
    for contact in contacts:
        if contact.get('type') == 'phone':
            phone = contact.get('value', '')
            if payload.get('channel') == 'whatsapp':
                whatsapp = phone
        elif contact.get('type') == 'email':
            email = contact.get('value', '')

    listing = payload.get('listing', {}) if isinstance(payload, dict) else {}
    public_profile = payload.get('publicProfile', {}) if isinstance(payload, dict) else {}

    source_id = payload.get('id') or data.get('id') or payload.get('leadId') or data.get('leadId')
    if not source_id:
        import hashlib
        source_id = hashlib.sha256(raw_body).hexdigest()

    ws_id = get_default_workspace_id()
    if ws_id:
        existing = Lead.query.filter_by(source='propertyfinder', source_id=str(source_id), workspace_id=ws_id).first()
    else:
        existing = Lead.query.filter_by(source='propertyfinder', source_id=str(source_id)).first()

    if existing:
        # Update status/response link if present
        existing.pf_status = payload.get('status', existing.pf_status)
        if payload.get('responseLink'):
            existing.response_link = payload.get('responseLink')
        db.session.commit()
        return jsonify({'success': True, 'lead_id': existing.id, 'event_id': event_id})

    # Auto-assign to L-Manager user based on PF agent email
    pf_users = PFCache.get_cache('users', workspace_id=ws_id) or []
    if ws_id:
        lm_users = User.query.join(
            WorkspaceMember, WorkspaceMember.user_id == User.id
        ).filter(
            WorkspaceMember.workspace_id == ws_id,
            User.is_active == True
        ).all()
    else:
        lm_users = User.query.filter_by(is_active=True).all()
    email_to_lm_user = {u.email.lower(): u for u in lm_users}
    pf_agent_email_map = {}
    for pf_user in pf_users:
        pf_id = pf_user.get('publicProfile', {}).get('id')
        pf_email = pf_user.get('email', '').lower()
        if pf_id and pf_email:
            pf_agent_email_map[str(pf_id)] = pf_email

    pf_agent_id = str(public_profile.get('id', '')) if public_profile else ''
    assigned_to_id = None
    if pf_agent_id and pf_agent_id in pf_agent_email_map:
        pf_agent_email = pf_agent_email_map[pf_agent_id]
        if pf_agent_email in email_to_lm_user:
            assigned_to_id = email_to_lm_user[pf_agent_email].id

    lead = Lead(
        workspace_id=ws_id,
        source='propertyfinder',
        source_id=str(source_id),
        channel=payload.get('channel', ''),
        name=sender.get('name', 'Unknown'),
        email=email,
        phone=phone,
        whatsapp=whatsapp,
        message=payload.get('message') or data.get('message'),
        pf_listing_id=str(listing.get('id')) if listing.get('id') else None,
        listing_reference=listing.get('reference'),
        response_link=payload.get('responseLink', ''),
        status='new',
        pf_status=payload.get('status', ''),
        priority='medium',
        pf_agent_id=pf_agent_id,
        pf_agent_name=public_profile.get('name', ''),
        assigned_to_id=assigned_to_id
    )

    db.session.add(lead)
    db.session.commit()

    return jsonify({'success': True, 'lead_id': lead.id, 'event_id': event_id})


# ==================== IMAGE EDITOR ENDPOINTS ====================

# Ensure logos directory exists
LOGOS_DIR = UPLOAD_FOLDER / 'logos'
PROCESSED_IMAGES_DIR = UPLOAD_FOLDER / 'processed'

@app.route('/image-editor')
@login_required
@require_active_workspace
def image_editor():
    """Image editor page"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_image_editor', workspace_slug=ws.slug))
    # Get all image settings
    ws_id = get_active_workspace_id()
    settings = get_all_user_image_settings(workspace_id=ws_id)
    return render_template('image_editor.html', settings=settings)


@app.route('/api/images/settings', methods=['GET'])
@login_required
@require_active_workspace
def api_get_image_settings():
    """Get image processing settings"""
    ws_id = get_active_workspace_id()
    settings = {
        'default_logo': get_user_image_setting('image_default_logo', workspace_id=ws_id),
        'default_qr_data': get_user_image_setting('image_default_qr_data', workspace_id=ws_id),
        'default_ratio': get_user_image_setting('image_default_ratio', '16:9', workspace_id=ws_id),
        'qr_position': get_user_image_setting('image_qr_position', 'bottom-right', workspace_id=ws_id),
        'qr_size_percent': int(get_user_image_setting('image_qr_size_percent', '15', workspace_id=ws_id) or 15),
        'logo_position': get_user_image_setting('image_logo_position', 'bottom-left', workspace_id=ws_id),
        'logo_opacity': int(get_user_image_setting('image_logo_opacity', '80', workspace_id=ws_id) or 80)
    }
    return jsonify(settings)


@app.route('/api/images/settings', methods=['POST'])
@login_required
@require_active_workspace
def api_save_image_settings():
    """Save image processing settings"""
    data = request.get_json(silent=True) or {}
    ws_id = get_active_workspace_id()
    
    if 'default_qr_data' in data:
        set_user_image_setting('image_default_qr_data', data['default_qr_data'], workspace_id=ws_id)
    if 'default_ratio' in data:
        set_user_image_setting('image_default_ratio', data['default_ratio'], workspace_id=ws_id)
    if 'qr_position' in data:
        set_user_image_setting('image_qr_position', data['qr_position'], workspace_id=ws_id)
    if 'qr_size_percent' in data:
        set_user_image_setting('image_qr_size_percent', str(data['qr_size_percent']), workspace_id=ws_id)
    if 'logo_position' in data:
        set_user_image_setting('image_logo_position', data['logo_position'], workspace_id=ws_id)
    if 'logo_opacity' in data:
        set_user_image_setting('image_logo_opacity', str(data['logo_opacity']), workspace_id=ws_id)
    
    return jsonify({'success': True, 'message': 'Settings saved'})


@app.route('/api/images/upload-logo', methods=['POST'])
@login_required
@require_active_workspace
def api_upload_logo():
    """Upload default logo"""
    ws_id = get_active_workspace_id()
    if 'logo' not in request.files:
        return jsonify({'error': 'No logo file provided'}), 400
    
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Ensure logos directory exists
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Save logo with secure filename
    filename = secure_filename(file.filename)
    # Use timestamp to avoid caching issues
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'png'
    logo_filename = f'default_logo_{timestamp}.{ext}'
    logo_path = LOGOS_DIR / logo_filename
    
    file.save(str(logo_path))
    
    # Save path in settings
    relative_path = f'uploads/logos/{logo_filename}'
    set_user_image_setting('image_default_logo', relative_path, workspace_id=ws_id)
    
    return jsonify({
        'success': True,
        'logo_path': relative_path,
        'message': 'Logo uploaded successfully'
    })


@app.route('/api/images/process-single', methods=['POST'])
@login_required
@require_active_workspace
def api_process_single_image():
    """Process a single image from base64 data"""
    import base64
    from io import BytesIO
    
    temp_logo_path = None
    ws_id = get_active_workspace_id()
    
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({'error': 'No image provided'}), 400
        
        # Parse base64 image
        image_data_url = data['image']
        if ',' in image_data_url:
            header, encoded = image_data_url.split(',', 1)
        else:
            encoded = image_data_url
        
        try:
            image_bytes = base64.b64decode(encoded)
        except Exception as decode_err:
            print(f"[ImageProcessor] Base64 decode error: {decode_err}")
            return jsonify({'error': f'Invalid image data: {decode_err}'}), 400
        
        print(f"[ImageProcessor] Processing image, size: {len(image_bytes)} bytes")
        
        # Get processing options with safe defaults
        target_ratio = data.get('ratio', '') or None
        qr_data = data.get('qr_data') or None
        qr_position = (data.get('qr_position', 'bottom_right') or 'bottom_right').replace('-', '_')
        qr_size = int(data.get('qr_size_percent', 12) or 12)
        qr_color = data.get('qr_color', '#000000') or '#000000'
        logo_data = data.get('logo_data')
        logo_position = (data.get('logo_position', 'bottom_left') or 'bottom_left').replace('-', '_')
        logo_size = int(data.get('logo_size_percent', 10) or 10)
        logo_opacity = float(data.get('logo_opacity', 0.9) or 0.9)
        output_format = data.get('format', 'JPEG') or 'JPEG'
        quality = int(data.get('quality', 90) or 90)
        size_preset = data.get('size', 'original') or 'original'
        
        print(f"[ImageProcessor] Options: ratio={target_ratio}, qr={bool(qr_data)}, format={output_format}")
        
        # Handle logo from base64 if provided
        logo_source = None
        if logo_data and logo_data.startswith('data:'):
            try:
                if ',' in logo_data:
                    _, logo_encoded = logo_data.split(',', 1)
                else:
                    logo_encoded = logo_data
                logo_bytes = base64.b64decode(logo_encoded)
                # Save to temp file
                import tempfile
                fd, temp_logo_path = tempfile.mkstemp(suffix='.png')
                with os.fdopen(fd, 'wb') as f:
                    f.write(logo_bytes)
                logo_source = temp_logo_path
                print(f"[ImageProcessor] Logo saved to temp file: {temp_logo_path}")
            except Exception as logo_err:
                print(f"[ImageProcessor] Logo decode error: {logo_err}")
        elif not logo_data:
            # Check for default logo
            logo_setting = get_user_image_setting('image_default_logo', workspace_id=ws_id)
            if logo_setting:
                potential_path = str(ROOT_DIR / logo_setting)
                if Path(potential_path).exists():
                    logo_source = potential_path
                    print(f"[ImageProcessor] Using default logo: {logo_source}")
        
        # Create processor and process image
        processor = ImageProcessor()
        
        processed_bytes, metadata = processor.process_image(
            image_source=image_bytes,
            ratio=target_ratio,
            size=size_preset,
            qr_data=qr_data,
            qr_position=qr_position,
            qr_size_percent=qr_size,
            qr_color=qr_color,
            logo_source=logo_source,
            logo_position=logo_position,
            logo_size_percent=logo_size,
            logo_opacity=logo_opacity,
            output_format=output_format,
            quality=quality
        )
        
        print(f"[ImageProcessor] Processed successfully: {metadata.get('final_size')}")
        
        # Convert to data URI
        mime_types = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}
        mime_type = mime_types.get(output_format.upper(), 'image/jpeg')
        output_base64 = base64.b64encode(processed_bytes).decode('utf-8')
        data_uri = f"data:{mime_type};base64,{output_base64}"
        
        return jsonify({
            'success': True,
            'image': data_uri,
            'metadata': {
                'original_size': list(metadata['original_size']),
                'final_size': list(metadata['final_size']),
                'file_size': metadata['file_size'],
                'format': output_format
            }
        })
                
    except Exception as e:
        import traceback
        print(f"[ImageProcessor] ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({'error': f'{type(e).__name__}: {str(e)}'}), 500
    
    finally:
        # Clean up temp logo file
        if temp_logo_path and os.path.exists(temp_logo_path):
            try:
                os.unlink(temp_logo_path)
            except:
                pass


@app.route('/api/settings/images', methods=['GET'])
@login_required
@require_active_workspace
def api_get_settings_images():
    """Get image settings (alternate endpoint)"""
    return api_get_image_settings()


@app.route('/api/settings/images', methods=['POST'])
@login_required
@require_active_workspace
def api_save_settings_images():
    """Save image settings"""
    data = request.get_json(silent=True) or {}
    ws_id = get_active_workspace_id()
    
    settings_map = {
        'ratio': 'image_default_ratio',
        'size': 'image_default_size',
        'quality': 'image_quality',
        'format': 'image_format',
        'qrEnabled': 'image_qr_enabled',
        'qrData': 'image_default_qr_data',
        'qrPosition': 'image_qr_position',
        'qrSize': 'image_qr_size_percent',
        'qrColor': 'image_qr_color',
        'logoEnabled': 'image_logo_enabled',
        'logoPosition': 'image_logo_position',
        'logoSize': 'image_logo_size',
        'logoOpacity': 'image_logo_opacity',
    }
    
    for js_key, db_key in settings_map.items():
        if js_key in data:
            value = data[js_key]
            # Convert booleans to strings
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            set_user_image_setting(db_key, str(value), workspace_id=ws_id)
    
    # Handle logo data if provided as base64
    if data.get('logoData') and data['logoData'].startswith('data:'):
        import base64
        try:
            logo_data_url = data['logoData']
            if ',' in logo_data_url:
                _, encoded = logo_data_url.split(',', 1)
            else:
                encoded = logo_data_url
            logo_bytes = base64.b64decode(encoded)
            
            # Save logo
            LOGOS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            logo_filename = f'default_logo_{timestamp}.png'
            logo_path = LOGOS_DIR / logo_filename
            with open(logo_path, 'wb') as f:
                f.write(logo_bytes)
            
            set_user_image_setting('image_default_logo', f'uploads/logos/{logo_filename}', workspace_id=ws_id)
        except Exception as e:
            print(f"Error saving logo: {e}")
    
    return jsonify({'success': True, 'message': 'Settings saved successfully'})


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve uploaded files"""
    from flask import send_from_directory
    return send_from_directory(str(UPLOAD_FOLDER), filename)


# ==================== IMAGE PROCESSING WITH SAVED SETTINGS ====================

@app.route('/api/images/process-with-settings', methods=['POST'])
@login_required
@require_active_workspace
def api_process_image_with_settings():
    """Process an image using saved settings and save to disk
    
    Accepts either:
    - 'image': base64 data URL
    - 'url': URL to download (server downloads to bypass CORS)
    """
    import base64
    import uuid
    
    temp_logo_path = None
    
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        listing_id = data.get('listing_id')
        ws_id = get_active_workspace_id()
        if listing_id:
            listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first()
            if not listing:
                return jsonify({'error': 'Listing not found'}), 404
        image_bytes = None
        
        # Option 1: URL - server downloads it (bypasses CORS)
        if 'url' in data and data['url']:
            url = data['url']
            print(f"[ProcessWithSettings] Downloading from URL: {url[:100]}...")
            
            # Skip if it's already a local processed image
            if url.startswith('/uploads/'):
                return jsonify({
                    'success': True,
                    'url': url,
                    'skipped': True,
                    'message': 'Already a local processed image'
                })
            
            try:
                import requests as http_requests
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                resp = http_requests.get(url, headers=headers, timeout=30, stream=True)
                resp.raise_for_status()
                image_bytes = resp.content
                print(f"[ProcessWithSettings] Downloaded {len(image_bytes)} bytes")
            except Exception as dl_err:
                print(f"[ProcessWithSettings] Download failed: {dl_err}")
                return jsonify({'error': f'Failed to download image: {dl_err}'}), 400
        
        # Option 2: Base64 image data
        elif 'image' in data and data['image']:
            image_data_url = data['image']
            if ',' in image_data_url:
                header, encoded = image_data_url.split(',', 1)
            else:
                encoded = image_data_url
            
            try:
                image_bytes = base64.b64decode(encoded)
            except Exception as decode_err:
                return jsonify({'error': f'Invalid image data: {decode_err}'}), 400
        else:
            return jsonify({'error': 'No image or url provided'}), 400
        
        # Load saved settings
        settings = {
            'ratio': get_user_image_setting('image_default_ratio', workspace_id=ws_id) or None,
            'size': get_user_image_setting('image_default_size', 'full_hd', workspace_id=ws_id) or 'full_hd',
            'quality': int(get_user_image_setting('image_quality', '90', workspace_id=ws_id) or 90),
            'format': get_user_image_setting('image_format', 'JPEG', workspace_id=ws_id) or 'JPEG',
            'qr_enabled': get_user_image_setting('image_qr_enabled', 'false', workspace_id=ws_id) == 'true',
            'qr_data': get_user_image_setting('image_default_qr_data', '', workspace_id=ws_id) or '',
            'qr_position': get_user_image_setting('image_qr_position', 'bottom_right', workspace_id=ws_id) or 'bottom_right',
            'qr_size': int(get_user_image_setting('image_qr_size_percent', '12', workspace_id=ws_id) or 12),
            'qr_color': get_user_image_setting('image_qr_color', '#000000', workspace_id=ws_id) or '#000000',
            'logo_enabled': get_user_image_setting('image_logo_enabled', 'false', workspace_id=ws_id) == 'true',
            'logo_path': get_user_image_setting('image_default_logo', workspace_id=ws_id),
            'logo_position': get_user_image_setting('image_logo_position', 'bottom_left', workspace_id=ws_id) or 'bottom_left',
            'logo_size': int(get_user_image_setting('image_logo_size', '10', workspace_id=ws_id) or 10),
            'logo_opacity': float(get_user_image_setting('image_logo_opacity', '0.9', workspace_id=ws_id) or 0.9),
        }
        
        print(f"[ProcessWithSettings] Using settings: ratio={settings['ratio']}, qr={settings['qr_enabled']}, logo={settings['logo_enabled']}")
        
        # Prepare QR data
        qr_data = settings['qr_data'] if settings['qr_enabled'] else None
        
        # Prepare logo
        logo_source = None
        if settings['logo_enabled'] and settings['logo_path']:
            potential_path = str(ROOT_DIR / settings['logo_path'])
            if Path(potential_path).exists():
                logo_source = potential_path
        
        # Create processor and process image
        processor = ImageProcessor()
        
        processed_bytes, metadata = processor.process_image(
            image_source=image_bytes,
            ratio=settings['ratio'],
            size=settings['size'],
            qr_data=qr_data,
            qr_position=settings['qr_position'].replace('-', '_'),
            qr_size_percent=settings['qr_size'],
            qr_color=settings['qr_color'],
            logo_source=logo_source,
            logo_position=settings['logo_position'].replace('-', '_'),
            logo_size_percent=settings['logo_size'],
            logo_opacity=settings['logo_opacity'],
            output_format=settings['format'],
            quality=settings['quality']
        )
        
        # Determine file extension
        ext = settings['format'].lower()
        if ext == 'jpeg':
            ext = 'jpg'
        
        # Save processed image to disk
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        filename = f'processed_{timestamp}_{unique_id}.{ext}'
        
        if listing_id:
            save_dir = LISTING_IMAGES_FOLDER / str(listing_id)
            relative_path = f'listings/{listing_id}/{filename}'
        else:
            save_dir = UPLOAD_FOLDER / 'processed'
            relative_path = f'processed/{filename}'
        
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / filename
        
        with open(filepath, 'wb') as f:
            f.write(processed_bytes)
        
        url = f'/uploads/{relative_path}'
        
        print(f"[ProcessWithSettings] Saved: {relative_path} ({len(processed_bytes)} bytes)")
        
        return jsonify({
            'success': True,
            'url': url,
            'metadata': {
                'original_size': list(metadata['original_size']),
                'final_size': list(metadata['final_size']),
                'file_size': len(processed_bytes),
                'format': settings['format']
            }
        })
        
    except Exception as e:
        import traceback
        print(f"[ProcessWithSettings] ERROR: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== IMAGE UPLOAD ENDPOINT ====================

@app.route('/api/images/upload', methods=['POST'])
@login_required
@require_active_workspace
def api_upload_image():
    """Upload a single image file with automatic optimization for large files"""
    import uuid
    from PIL import Image
    import io
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file type
    allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({'error': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'}), 400
    
    try:
        # Read file into memory
        file_data = file.read()
        original_size = len(file_data)
        original_bytes = file_data
        original_ext = ext
        
        # Get listing_id if provided (for organizing files)
        listing_id = request.form.get('listing_id')
        ws_id = get_active_workspace_id()
        listing = None
        if listing_id:
            listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first()
            if not listing:
                return jsonify({'error': 'Listing not found'}), 404
        
        # Generate unique filename (always save as JPEG for consistency and smaller size)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        
        # Optimize image if it's large (> 2MB) or if it's a PNG (convert to JPEG)
        optimized = False
        if original_size > 2 * 1024 * 1024 or ext == 'png':
            try:
                img = Image.open(io.BytesIO(file_data))
                
                # Convert to RGB if necessary (for PNG with transparency)
                if img.mode in ('RGBA', 'LA', 'P'):
                    # Create white background for transparent images
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Resize if image is very large (max 4000px on longest side for quality)
                max_dimension = 4000
                if max(img.size) > max_dimension:
                    ratio = max_dimension / max(img.size)
                    new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                    img = img.resize(new_size, Image.LANCZOS)
                    print(f"[ImageUpload] Resized from {img.size} to {new_size}")
                
                # Save as optimized JPEG with high quality (92 is good balance)
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=92, optimize=True)
                file_data = output.getvalue()
                ext = 'jpg'
                optimized = True
                
                print(f"[ImageUpload] Optimized: {original_size} -> {len(file_data)} bytes ({100 - len(file_data)*100//original_size}% reduction)")
            except Exception as opt_err:
                print(f"[ImageUpload] Optimization failed, using original: {opt_err}")
                # If optimization fails, use original data
        
        filename = f'img_{timestamp}_{unique_id}.{ext}'
        
        # Determine save path
        if listing_id:
            save_dir = LISTING_IMAGES_FOLDER / str(listing_id)
            relative_path = f'listings/{listing_id}/{filename}'
        else:
            save_dir = UPLOAD_FOLDER / 'temp'
            relative_path = f'temp/{filename}'
        
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / filename
        
        # Save file
        with open(filepath, 'wb') as f:
            f.write(file_data)
        
        # Get file size after saving
        file_size = filepath.stat().st_size
        
        # Generate URL
        url = f'/uploads/{relative_path}'
        
        print(f"[ImageUpload] Saved: {relative_path} ({file_size} bytes){' [optimized]' if optimized else ''}")

        # Save original copy if listing_id was provided
        original_relative_path = None
        if listing_id and listing:
            try:
                originals_dir = LISTING_IMAGES_FOLDER / str(listing_id) / 'originals'
                originals_dir.mkdir(parents=True, exist_ok=True)
                orig_filename = f'orig_{timestamp}_{unique_id}.{original_ext}'
                orig_path = originals_dir / orig_filename
                with open(orig_path, 'wb') as f:
                    f.write(original_bytes)
                original_relative_path = f'listings/{listing_id}/originals/{orig_filename}'
            except Exception as e:
                print(f"[ImageUpload] Failed to store original image: {e}")
        
        if listing_id and listing and original_relative_path:
            try:
                existing_originals = _load_original_images_raw(listing)
                merged_originals = _merge_unique_paths(existing_originals, [original_relative_path])
                listing.original_images = json.dumps(merged_originals)
                listing.updated_at = datetime.utcnow()
                db.session.commit()
            except Exception as e:
                print(f"[ImageUpload] Failed to update listing originals: {e}")
        
        return jsonify({
            'success': True,
            'id': unique_id,
            'url': url,
            'filename': filename,
            'size': file_size,
            'original_size': original_size,
            'optimized': optimized
        })
        
    except Exception as e:
        import traceback
        print(f"[ImageUpload] Error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== FOLDER API ENDPOINTS ====================

@app.route('/api/folders', methods=['GET'])
@login_required
@require_active_workspace
def api_get_folders():
    """API: Get all folders"""
    ws_id = get_active_workspace_id()
    if workspace_user_can_manage_all_listings(workspace_id=ws_id):
        folders = ListingFolder.get_all_with_counts(workspace_id=ws_id)
        uncategorized_count = scope_query(LocalListing.query, ws_id).filter(LocalListing.folder_id.is_(None)).count()
    else:
        folder_rows = ListingFolder.query.filter_by(workspace_id=ws_id).order_by(ListingFolder.name).all()
        visible_counts = {}
        for listing in visible_local_listing_query(ws_id).all():
            visible_counts[listing.folder_id] = visible_counts.get(listing.folder_id, 0) + 1
        folders = []
        for folder in folder_rows:
            folder_data = folder.to_dict()
            folder_data['listing_count'] = visible_counts.get(folder.id, 0)
            folders.append(folder_data)
        uncategorized_count = visible_counts.get(None, 0)
    return jsonify({
        'folders': folders,
        'uncategorized_count': uncategorized_count
    })


@app.route('/api/folders', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_listing_admin
def api_create_folder():
    """API: Create a new folder"""
    data = request.get_json(silent=True) or request.json or {}
    ws_id = get_active_workspace_id()
    
    if not data.get('name'):
        return jsonify({'error': 'Folder name is required'}), 400

    parent_id = data.get('parent_id')
    if parent_id:
        parent = ListingFolder.query.filter_by(id=parent_id, workspace_id=ws_id).first()
        if not parent:
            return jsonify({'error': 'Parent folder not found'}), 404
    
    # Check if folder with same name exists
    existing = ListingFolder.query.filter_by(name=data['name'], workspace_id=ws_id).first()
    if existing:
        return jsonify({'error': 'A folder with this name already exists'}), 400
    
    try:
        folder = ListingFolder(
            workspace_id=ws_id,
            name=data['name'],
            color=data.get('color', 'indigo'),
            icon=data.get('icon', 'fa-folder'),
            description=data.get('description'),
            parent_id=parent_id
        )
        db.session.add(folder)
        db.session.commit()
        
        return jsonify({'folder': folder.to_dict(), 'message': 'Folder created successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create folder: {str(e)}'}), 500


@app.route('/api/folders/<int:folder_id>', methods=['GET'])
@login_required
@require_active_workspace
def api_get_folder(folder_id):
    """API: Get a single folder"""
    ws_id = get_active_workspace_id()
    folder = ListingFolder.query.filter_by(id=folder_id, workspace_id=ws_id).first_or_404()
    data = folder.to_dict()
    if not workspace_user_can_manage_all_listings(workspace_id=ws_id):
        data['listing_count'] = visible_local_listing_query(ws_id).filter_by(folder_id=folder.id).count()
    return jsonify({'folder': data})


@app.route('/api/folders/<int:folder_id>', methods=['PUT', 'PATCH'])
@login_required
@require_active_workspace
@require_workspace_listing_admin
def api_update_folder(folder_id):
    """API: Update a folder"""
    ws_id = get_active_workspace_id()
    folder = ListingFolder.query.filter_by(id=folder_id, workspace_id=ws_id).first_or_404()
    data = request.json
    
    if 'name' in data:
        folder.name = data['name']
    if 'color' in data:
        folder.color = data['color']
    if 'icon' in data:
        folder.icon = data['icon']
    if 'description' in data:
        folder.description = data['description']
    if 'parent_id' in data:
        parent_id = data['parent_id']
        if parent_id:
            parent = ListingFolder.query.filter_by(id=parent_id, workspace_id=ws_id).first()
            if not parent:
                return jsonify({'error': 'Parent folder not found'}), 404
        folder.parent_id = parent_id
    
    db.session.commit()
    return jsonify({'folder': folder.to_dict(), 'message': 'Folder updated successfully'})


@app.route('/api/folders/<int:folder_id>', methods=['DELETE'])
@login_required
@require_active_workspace
@require_workspace_listing_admin
def api_delete_folder(folder_id):
    """API: Delete a folder (moves listings to uncategorized)"""
    ws_id = get_active_workspace_id()
    folder = ListingFolder.query.filter_by(id=folder_id, workspace_id=ws_id).first_or_404()
    
    # Move all listings in this folder to uncategorized
    LocalListing.query.filter_by(folder_id=folder_id, workspace_id=ws_id).update({'folder_id': None})
    
    db.session.delete(folder)
    db.session.commit()
    
    return jsonify({'message': 'Folder deleted successfully'})


@app.route('/api/listings/move-to-folder', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_listing_admin
def api_move_listings_to_folder():
    """API: Move listings to a folder"""
    data = request.json
    listing_ids = data.get('listing_ids', [])
    folder_id = data.get('folder_id')  # None means uncategorized
    ws_id = get_active_workspace_id()
    
    if not listing_ids:
        return jsonify({'error': 'No listings specified'}), 400
    
    # Verify folder exists if specified
    if folder_id is not None:
        folder = ListingFolder.query.filter_by(id=folder_id, workspace_id=ws_id).first()
        if not folder:
            return jsonify({'error': 'Folder not found'}), 404
    
    # Update listings
    updated = LocalListing.query.filter(
        LocalListing.id.in_(listing_ids),
        LocalListing.workspace_id == ws_id
    ).update(
        {'folder_id': folder_id},
        synchronize_session=False
    )
    db.session.commit()
    
    return jsonify({
        'message': f'Moved {updated} listings',
        'updated_count': updated
    })


# ==================== LOOP MANAGEMENT ENDPOINTS ====================

@app.route('/loops')
@login_required
@require_active_workspace
@require_workspace_loops_admin
def loops_page():
    """Loop management page"""
    ws = get_active_workspace()
    if ws:
        return redirect(url_for('workspace_loops', workspace_slug=ws.slug))
    ws_id = get_active_workspace_id()
    loops = LoopConfig.query.filter_by(workspace_id=ws_id).order_by(LoopConfig.created_at.desc()).all()
    
    # Get primary listings only (exclude "Duplicated" folder)
    duplicated_folder = ListingFolder.query.filter_by(name='Duplicated', workspace_id=ws_id).first()
    if duplicated_folder:
        listings = LocalListing.query.filter(
            db.or_(
                LocalListing.folder_id != duplicated_folder.id,
                LocalListing.folder_id == None
            ),
            LocalListing.workspace_id == ws_id
        ).order_by(LocalListing.reference).all()
    else:
        listings = LocalListing.query.filter_by(workspace_id=ws_id).order_by(LocalListing.reference).all()
    
    return render_template('loops.html', loops=loops, listings=listings)


@app.route('/api/loops', methods=['GET'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_get_loops():
    """Get all loop configurations"""
    ws_id = get_active_workspace_id()
    loops = LoopConfig.query.filter_by(workspace_id=ws_id).order_by(LoopConfig.created_at.desc()).all()
    return jsonify({
        'success': True,
        'loops': [serialize_loop_for_api(loop) for loop in loops]
    })


@app.route('/api/loops', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_create_loop():
    """Create a new loop configuration"""
    data = request.get_json() or {}
    ws_id = get_active_workspace_id()
    
    if not data.get('name'):
        return jsonify({'error': 'Name is required'}), 400
    
    if not data.get('listing_ids') or len(data['listing_ids']) == 0:
        return jsonify({'error': 'At least one listing is required'}), 400

    try:
        schedule_payload = validate_loop_schedule_payload({
            'schedule_mode': data.get('schedule_mode', LoopConfig.SCHEDULE_INTERVAL),
            'interval_hours': data.get('interval_hours', 1),
            'schedule_window_start': data.get('schedule_window_start'),
            'schedule_window_end': data.get('schedule_window_end'),
            'schedule_exact_times': data.get('schedule_exact_times', []),
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    
    loop = LoopConfig(
        workspace_id=ws_id,
        name=data['name'],
        loop_type=data.get('loop_type', 'duplicate'),
        interval_hours=float(schedule_payload.get('interval_hours', data.get('interval_hours', 1))),
        keep_duplicates=data.get('keep_duplicates', True),
        max_duplicates=int(data.get('max_duplicates', 0)),
        is_active=data.get('is_active', False)
    )
    loop.schedule_mode = schedule_payload['schedule_mode']
    loop.schedule_window_start = schedule_payload.get('schedule_window_start')
    loop.schedule_window_end = schedule_payload.get('schedule_window_end')
    loop.set_schedule_exact_times(schedule_payload.get('schedule_exact_times', []))
    db.session.add(loop)
    db.session.flush()  # Get the ID
    
    # Add listings to the loop
    for idx, listing_id in enumerate(data['listing_ids']):
        listing = visible_local_listing_query(ws_id).filter_by(id=int(listing_id)).first()
        if not listing:
            db.session.rollback()
            return jsonify({'error': f'Listing {listing_id} not found in workspace'}), 404
        loop_listing = LoopListing(
            loop_config_id=loop.id,
            listing_id=int(listing_id),
            order_index=idx
        )
        db.session.add(loop_listing)
    
    # Calculate next run time if active
    if loop.is_active:
        loop.next_run_at = compute_next_loop_run_at(loop, now_utc=datetime.utcnow())
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>', methods=['GET'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_get_loop(loop_id):
    """Get a single loop configuration"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    # Include listings with details
    loop_data = serialize_loop_for_api(loop)
    loop_data['listings'] = [ll.to_dict() for ll in loop.listings.order_by(LoopListing.order_index).all()]
    
    return jsonify({
        'success': True,
        'loop': loop_data
    })


@app.route('/api/loops/<int:loop_id>', methods=['PUT'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_update_loop(loop_id):
    """Update a loop configuration"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    data = request.get_json() or {}

    schedule_input = {
        'schedule_mode': data.get('schedule_mode', loop.schedule_mode or LoopConfig.SCHEDULE_INTERVAL),
        'interval_hours': data.get('interval_hours', loop.interval_hours),
        'schedule_window_start': data.get('schedule_window_start', loop.schedule_window_start),
        'schedule_window_end': data.get('schedule_window_end', loop.schedule_window_end),
        'schedule_exact_times': data.get('schedule_exact_times', loop.get_schedule_exact_times()),
    }
    try:
        schedule_payload = validate_loop_schedule_payload(schedule_input)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    
    if 'name' in data:
        loop.name = data['name']
    if 'loop_type' in data:
        loop.loop_type = data['loop_type']
    if ('interval_hours' in data) or (schedule_payload.get('interval_hours') is not None):
        loop.interval_hours = float(schedule_payload.get('interval_hours', loop.interval_hours))
    loop.schedule_mode = schedule_payload['schedule_mode']
    loop.schedule_window_start = schedule_payload.get('schedule_window_start')
    loop.schedule_window_end = schedule_payload.get('schedule_window_end')
    loop.set_schedule_exact_times(schedule_payload.get('schedule_exact_times', []))
    if 'keep_duplicates' in data:
        loop.keep_duplicates = data['keep_duplicates']
    if 'max_duplicates' in data:
        loop.max_duplicates = int(data['max_duplicates'])
    
    # Update listings if provided
    if 'listing_ids' in data:
        # Remove old listings
        LoopListing.query.filter_by(loop_config_id=loop.id).delete()
        
        # Add new listings
        for idx, listing_id in enumerate(data['listing_ids']):
            listing = visible_local_listing_query(ws_id).filter_by(id=int(listing_id)).first()
            if not listing:
                db.session.rollback()
                return jsonify({'error': f'Listing {listing_id} not found in workspace'}), 404
            loop_listing = LoopListing(
                loop_config_id=loop.id,
                listing_id=int(listing_id),
                order_index=idx
            )
            db.session.add(loop_listing)
        
        # Reset index
        loop.current_index = 0

    schedule_fields = {'schedule_mode', 'interval_hours', 'schedule_window_start', 'schedule_window_end', 'schedule_exact_times'}
    if loop.is_active and not loop.is_paused and schedule_fields.intersection(data.keys()):
        loop.next_run_at = compute_next_loop_run_at(loop, now_utc=datetime.utcnow())
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>', methods=['DELETE'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_delete_loop(loop_id):
    """Delete a loop configuration"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    # Delete associated records
    LoopListing.query.filter_by(loop_config_id=loop.id).delete()
    LoopExecutionLog.query.filter_by(loop_config_id=loop.id).delete()
    
    db.session.delete(loop)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Loop deleted'
    })


@app.route('/api/loops/<int:loop_id>/start', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_start_loop(loop_id):
    """Start a loop"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    loop.is_active = True
    loop.is_paused = False
    loop.consecutive_failures = 0
    loop.next_run_at = compute_next_loop_run_at(loop, now_utc=datetime.utcnow())
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Loop "{loop.name}" started',
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>/stop', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_stop_loop(loop_id):
    """Stop a loop"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    loop.is_active = False
    loop.is_paused = False
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Loop "{loop.name}" stopped',
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>/pause', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_pause_loop(loop_id):
    """Pause a loop"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    loop.is_paused = True
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Loop "{loop.name}" paused',
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>/resume', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_resume_loop(loop_id):
    """Resume a paused loop"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    loop.is_paused = False
    loop.next_run_at = compute_next_loop_run_at(loop, now_utc=datetime.utcnow())
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Loop "{loop.name}" resumed',
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>/run-now', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_run_loop_now(loop_id):
    """Manually trigger a loop execution"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    if not loop.is_active:
        loop.is_active = True
        loop.is_paused = False
    
    # Execute immediately
    execute_loop_job(loop.id)
    
    return jsonify({
        'success': True,
        'message': f'Loop "{loop.name}" executed',
        'loop': serialize_loop_for_api(loop)
    })


@app.route('/api/loops/<int:loop_id>/logs', methods=['GET'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_get_loop_logs(loop_id):
    """Get execution logs for a loop"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    limit = request.args.get('limit', 50, type=int)
    logs = LoopExecutionLog.query.filter_by(loop_config_id=loop_id).order_by(
        LoopExecutionLog.executed_at.desc()
    ).limit(limit).all()
    
    return jsonify({
        'success': True,
        'logs': [log.to_dict() for log in logs]
    })


@app.route('/api/loops/<int:loop_id>/duplicates', methods=['GET'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_get_loop_duplicates(loop_id):
    """Get duplicates created by a loop"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    duplicates = DuplicatedListing.query.filter_by(loop_config_id=loop_id).order_by(
        DuplicatedListing.created_at.desc()
    ).all()
    
    return jsonify({
        'success': True,
        'duplicates': [dup.to_dict() for dup in duplicates]
    })


@app.route('/api/loops/<int:loop_id>/cleanup', methods=['POST'])
@login_required
@require_active_workspace
@require_workspace_loops_admin
def api_cleanup_loop_duplicates(loop_id):
    """Delete all duplicates created by a loop from PropertyFinder"""
    ws_id = get_active_workspace_id()
    loop = LoopConfig.query.filter_by(id=loop_id, workspace_id=ws_id).first_or_404()
    
    # Stop the loop first
    loop.is_active = False
    loop.is_paused = False
    
    client = get_client(workspace_id=ws_id)
    deleted_count = 0
    errors = []
    
    duplicates = DuplicatedListing.query.filter_by(
        loop_config_id=loop_id,
        status='published'
    ).all()
    
    for dup in duplicates:
        try:
            if dup.pf_listing_id:
                client.delete_listing(dup.pf_listing_id)
            dup.status = 'deleted'
            dup.deleted_at = datetime.utcnow()
            deleted_count += 1
        except Exception as e:
            errors.append(f"{dup.pf_listing_id}: {str(e)}")
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Deleted {deleted_count} duplicates from PropertyFinder',
        'deleted_count': deleted_count,
        'errors': errors if errors else None
    })


# ==================== LISTING IMAGES ENDPOINTS ====================

@app.route('/api/listings/summary', methods=['GET'])
@login_required
@require_active_workspace
def api_listings_summary():
    """Get summary of all listings for dropdown selection"""
    try:
        ws_id = get_active_workspace_id()
        listings = visible_local_listing_query(ws_id).order_by(LocalListing.reference).all()
        
        result = []
        for l in listings:
            # Count images
            image_count = 0
            if l.images:
                try:
                    imgs = json.loads(l.images) if isinstance(l.images, str) else l.images
                    image_count = len(imgs) if isinstance(imgs, list) else 0
                except:
                    pass
            
            result.append({
                'id': l.id,
                'reference': l.reference or f'ID-{l.id}',
                'title': l.title_en or 'Untitled',
                'title_en': l.title_en or 'Untitled',
                'city': l.city,
                'property_type': l.property_type,
                'offering_type': l.offering_type,
                'status': l.status or 'draft',
                'image_count': image_count
            })
        
        return jsonify({'listings': result})
    except Exception as e:
        import traceback
        print(f"[ERROR] api_listings_summary: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/listings/<int:listing_id>/images', methods=['GET'])
@login_required
@require_active_workspace
def api_get_listing_images(listing_id):
    """Get images for a listing"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    
    images = []
    if listing.images:
        try:
            images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
        except:
            images = []
    
    return jsonify({
        'listing_id': listing_id,
        'reference': listing.reference,
        'images': images,
        'count': len(images)
    })


@app.route('/api/listings/<int:listing_id>/original-images', methods=['GET'])
@login_required
@require_active_workspace
def api_get_listing_original_images(listing_id):
    """Get original images for a listing (for reprocessing)"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    originals = listing._parse_original_images()
    return jsonify({
        'listing_id': listing_id,
        'reference': listing.reference,
        'originals': originals,
        'count': len(originals)
    })


@app.route('/api/listings/<int:listing_id>/images', methods=['POST'])
@permission_required('edit')
@require_active_workspace
def api_assign_images_to_listing(listing_id):
    """Assign processed images to a listing"""
    import base64
    import uuid
    
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    data = request.json
    
    if not data or 'images' not in data:
        return jsonify({'error': 'No images provided'}), 400
    
    images_data = data['images']  # List of base64 data URIs
    mode = data.get('mode', 'append')  # 'append' or 'replace'
    originals_data = data.get('originals') or []
    
    # Get existing images
    existing_images = []
    if mode == 'append' and listing.images:
        try:
            existing_images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
            if not isinstance(existing_images, list):
                existing_images = []
        except:
            existing_images = []
    
    # Create listing images directory
    listing_dir = LISTING_IMAGES_FOLDER / str(listing_id)
    listing_dir.mkdir(parents=True, exist_ok=True)
    originals_dir = None
    if originals_data:
        originals_dir = listing_dir / 'originals'
        originals_dir.mkdir(parents=True, exist_ok=True)
    
    # Save new images
    new_image_paths = []
    for i, img_data in enumerate(images_data):
        try:
            # Parse base64 data
            if ',' in img_data:
                header, encoded = img_data.split(',', 1)
                # Determine format from header
                if 'png' in header.lower():
                    ext = 'png'
                elif 'webp' in header.lower():
                    ext = 'webp'
                else:
                    ext = 'jpg'
            else:
                encoded = img_data
                ext = 'jpg'
            
            image_bytes = base64.b64decode(encoded)
            
            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_id = str(uuid.uuid4())[:8]
            filename = f'img_{timestamp}_{unique_id}.{ext}'
            filepath = listing_dir / filename
            
            # Save image
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            
            # Store relative path for database
            relative_path = f'listings/{listing_id}/{filename}'
            new_image_paths.append(relative_path)
            
            print(f"[ListingImages] Saved image: {relative_path} ({len(image_bytes)} bytes)")
            
        except Exception as e:
            print(f"[ListingImages] Error saving image {i}: {e}")
            continue

    # Save original images (optional)
    new_original_paths = []
    if originals_dir:
        for i, orig_data in enumerate(originals_data):
            try:
                if ',' in orig_data:
                    header, encoded = orig_data.split(',', 1)
                    if 'png' in header.lower():
                        ext = 'png'
                    elif 'webp' in header.lower():
                        ext = 'webp'
                    else:
                        ext = 'jpg'
                else:
                    encoded = orig_data
                    ext = 'jpg'

                image_bytes = base64.b64decode(encoded)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_id = str(uuid.uuid4())[:8]
                filename = f'orig_{timestamp}_{unique_id}.{ext}'
                filepath = originals_dir / filename
                with open(filepath, 'wb') as f:
                    f.write(image_bytes)
                relative_path = f'listings/{listing_id}/originals/{filename}'
                new_original_paths.append(relative_path)
                print(f"[ListingImages] Saved original: {relative_path} ({len(image_bytes)} bytes)")
            except Exception as e:
                print(f"[ListingImages] Error saving original {i}: {e}")
                continue
    
    # Combine with existing images
    all_images = existing_images + new_image_paths
    
    # Update listing
    listing.images = json.dumps(all_images)
    if new_original_paths:
        existing_originals = _load_original_images_raw(listing)
        merged_originals = _merge_unique_paths(existing_originals, new_original_paths)
        listing.original_images = json.dumps(merged_originals)
    listing.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'listing_id': listing_id,
        'images_added': len(new_image_paths),
        'total_images': len(all_images),
        'images': all_images,
        'originals_added': len(new_original_paths),
        'message': f'Added {len(new_image_paths)} images to listing'
    })


@app.route('/api/listings/<int:listing_id>/images', methods=['DELETE'])
@permission_required('edit')
@require_active_workspace
def api_delete_listing_images(listing_id):
    """Delete images from a listing"""
    ws_id = get_active_workspace_id()
    listing = visible_local_listing_query(ws_id).filter_by(id=listing_id).first_or_404()
    data = request.json
    
    images_to_delete = data.get('images', [])  # List of image paths to delete
    delete_all = data.get('delete_all', False)
    
    # Get existing images
    existing_images = []
    if listing.images:
        try:
            existing_images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
        except:
            existing_images = []
    
    if delete_all:
        images_to_delete = existing_images.copy()
    
    # Delete files and update list
    deleted_count = 0
    for img_path in images_to_delete:
        if img_path in existing_images:
            existing_images.remove(img_path)
            # Try to delete the actual file
            try:
                full_path = UPLOAD_FOLDER / img_path
                if full_path.exists():
                    full_path.unlink()
                    deleted_count += 1
            except Exception as e:
                print(f"[ListingImages] Error deleting file {img_path}: {e}")
    
    # Update listing
    listing.images = json.dumps(existing_images)
    listing.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'deleted_count': deleted_count,
        'remaining_images': len(existing_images),
        'images': existing_images
    })


@app.route('/api/listings/search', methods=['GET'])
@login_required
@require_active_workspace
def api_search_listings():
    """Search listings for assignment dropdown"""
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 20)), 50)
    
    # Build query
    ws_id = get_active_workspace_id()
    listings_query = visible_local_listing_query(ws_id)
    
    if query:
        search = f'%{query}%'
        listings_query = listings_query.filter(
            db.or_(
                LocalListing.reference.ilike(search),
                LocalListing.title_en.ilike(search),
                LocalListing.location.ilike(search),
                LocalListing.property_type.ilike(search)
            )
        )
    
    listings = listings_query.order_by(LocalListing.updated_at.desc()).limit(limit).all()
    
    results = []
    for listing in listings:
        # Count existing images
        image_count = 0
        if listing.images:
            try:
                images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
                image_count = len(images) if isinstance(images, list) else 0
            except:
                pass
        
        results.append({
            'id': listing.id,
            'reference': listing.reference,
            'title': listing.title_en or f'{listing.property_type} in {listing.location}',
            'location': listing.location,
            'property_type': listing.property_type,
            'price': listing.price,
            'image_count': image_count,
            'status': listing.status
        })
    
    return jsonify({
        'results': results,
        'count': len(results)
    })


# ==================== HEALTH CHECK ====================

@app.route('/health')
def health_check():
    """Health check endpoint for Railway"""
    try:
        # Check database connection
        db.session.execute(db.text('SELECT 1'))
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'environment': 'production' if IS_PRODUCTION else 'development'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500


if __name__ == '__main__':
    print("=" * 50)
    print("PropertyFinder Dashboard")
    print("=" * 50)
    
    if not Config.validate():
        print("\n⚠ Warning: API credentials not configured in .env")
        print("  Some features may not work until configured")
    
    port = int(os.environ.get('PORT', 5000))
    debug = not IS_PRODUCTION and Config.DEBUG
    
    print(f"\nEnvironment: {'Production' if IS_PRODUCTION else 'Development'}")
    print(f"Starting server at http://localhost:{port}")
    print("Press Ctrl+C to stop\n")
    
    app.run(debug=debug, host='0.0.0.0', port=port)

# This runs when gunicorn imports the module
print("[STARTUP] App module fully loaded and ready to serve requests")
