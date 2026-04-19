# ================= PRODUCTION-GRADE app.py =================
# Cross-device project visibility fix: All users see all projects
import os
import gc
import re
import socket
import secrets
import hashlib
import logging
from io import BytesIO
from datetime import datetime, timedelta
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, flash, session, jsonify, send_from_directory
)
from flask_mail import Mail, Message
from flask_migrate import Migrate
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None

# ================= INIT =================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================= DATABASE CONFIG =================
def _host_resolves(host: str) -> bool:
    """Return True if the hostname can be resolved from this machine."""
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror:
        return False

def get_database_url():
    raw_url = os.getenv("DATABASE_URL", "").strip()
    running_on_fly = bool(os.getenv("FLY_APP_NAME"))
    
    # Local/dev fallback - use SQLite for local development
    if not raw_url:
        logger.warning("No DATABASE_URL found, using SQLite for local development.")
        return "sqlite:///projects.db"
    
    # Check if we're trying to use a Fly.io internal host locally
    if ".internal" in raw_url and not running_on_fly:
        logger.warning(
            "DATABASE_URL points to a Fly.io internal host but app is running locally. "
            "Using SQLite fallback instead."
        )
        return "sqlite:///projects.db"
    
    # Normalize old Heroku-style URL
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)
    
    parsed = urlparse(raw_url)
    host = parsed.hostname or ""
    
    # If we're not on Fly.io and host doesn't resolve, use SQLite
    if not running_on_fly and host and not _host_resolves(host):
        logger.warning(
            f"Database host '{host}' cannot be resolved locally. Using SQLite fallback."
        )
        return "sqlite:///projects.db"
    
    safe_host = host or "unknown-host"
    logger.info(f"Using database host: {safe_host}")
    return raw_url

# Configure database with proper error handling for local development
try:
    database_url = get_database_url()
    logger.info(f"Database configuration: {database_url[:50]}...")
    
    app.config.update(
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            'pool_pre_ping': True,
            'pool_recycle': 300,
        } if 'postgresql' in database_url else {},  # Only use pool options for PostgreSQL
        SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
        UPLOAD_FOLDER="static/uploads",
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=True if os.getenv('FLY_APP_NAME') else False,
    )
except Exception as e:
    logger.error(f"Error configuring database: {e}")
    # Fallback to SQLite
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///projects.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
        UPLOAD_FOLDER="static/uploads",
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=False,
    )

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ================= MAIL =================
app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", 587)),
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
)
mail = Mail(app)

# ================= DB =================
from models import db, User, Project, Task, MediaFile, TeamMember

db.init_app(app)
migrate = Migrate(app, db)

# Initialize database with error handling
with app.app_context():
    try:
        db.create_all()
        logger.info("Database tables created/verified successfully")
        
        # Set first user as admin if no admin exists
        if not User.query.filter_by(role='admin').first():
            first_user = User.query.first()
            if first_user:
                first_user.role = 'admin'
                db.session.commit()
                logger.info(f"Set {first_user.email} as admin")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        logger.warning("Continuing with limited functionality...")

# ================= HELPERS =================
def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

def get_or_create_team_member(user):
    """Ensure every user has a corresponding TeamMember record"""
    try:
        tm = TeamMember.query.filter_by(email=user.email).first()
        if not tm:
            tm = TeamMember(email=user.email, name=user.name, user_id=user.id)
            db.session.add(tm)
            db.session.commit()
            logger.info(f"Created TeamMember for {user.email}")
        elif tm.user_id != user.id:
            tm.user_id = user.id
            tm.name = user.name
            db.session.commit()
            logger.info(f"Updated TeamMember link for {user.email}")
        return tm
    except Exception as e:
        logger.error(f"Error in get_or_create_team_member: {e}")
        db.session.rollback()
        return None

def parse_date(val):
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

def _safe_list(value):
    if not value:
        return []
    try:
        return list(value)
    except TypeError:
        return []

def project_is_trashed(project):
    if not project:
        return False
    trash_flags = ["is_trashed", "trashed", "deleted", "is_deleted"]
    for field in trash_flags:
        if hasattr(project, field):
            try:
                if bool(getattr(project, field)):
                    return True
            except Exception:
                pass
    return False

def get_active_projects(projects):
    return [p for p in projects if not project_is_trashed(p)]

def get_trashed_projects(projects):
    return [p for p in projects if project_is_trashed(p)]

def mark_project_as_trashed(project):
    updated = False
    bool_fields = ["is_trashed", "trashed", "deleted", "is_deleted"]
    for field in bool_fields:
        if hasattr(project, field):
            try:
                setattr(project, field, True)
                updated = True
            except Exception:
                pass
    
    if hasattr(project, "trashed_at"):
        try:
            setattr(project, "trashed_at", datetime.utcnow())
            updated = True
        except Exception:
            pass
    
    return updated

def restore_project(project):
    """Restore a trashed project"""
    restored = False
    bool_fields = ["is_trashed", "trashed", "deleted", "is_deleted"]
    for field in bool_fields:
        if hasattr(project, field):
            try:
                setattr(project, field, False)
                restored = True
            except Exception:
                pass
    
    if hasattr(project, "trashed_at"):
        try:
            setattr(project, "trashed_at", None)
            restored = True
        except Exception:
            pass
    
    return restored

def add_all_members_to_project(project):
    """Add all team members to a project for full visibility"""
    try:
        all_members = TeamMember.query.all()
        for member in all_members:
            if member not in project.team_members:
                project.team_members.append(member)
                logger.info(f"Added {member.email} to project '{project.name}'")
    except Exception as e:
        logger.error(f"Error adding members to project: {e}")

def get_database_url():
    raw_url = os.getenv("DATABASE_URL", "").strip()
    running_on_fly = bool(os.getenv("FLY_APP_NAME"))
    
    # If we're on Fly.io, we need PostgreSQL
    if running_on_fly:
        # Use the PostgreSQL URL from environment variable
        # Make sure it's set correctly on Fly.io secrets
        postgres_url = os.getenv("DATABASE_URL")
        if postgres_url and "postgresql://" in postgres_url:
            logger.info("Running on Fly.io - using PostgreSQL database")
            return postgres_url
        else:
            logger.error("Fly.io deployment requires PostgreSQL DATABASE_URL")
            return "sqlite:///projects.db"
    
    # Local development - use SQLite
    logger.info("Running locally - using SQLite database")
    return "sqlite:///projects.db"


# ================= AUTH =================
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        name = request.form['name'].strip()
        password = request.form['password']

        if User.query.filter_by(email=email).first():
            flash("User already exists")
            return redirect(url_for('login'))

        user = User(email=email, name=name,
                    password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()

        # Create corresponding TeamMember
        tm = TeamMember.query.filter_by(email=email).first()
        if not tm:
            tm = TeamMember(email=email, name=name, user_id=user.id)
            db.session.add(tm)

        db.session.commit()
        flash("Registration successful! Please login.")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_email'] = user.email
            session['user_role'] = getattr(user, 'role', 'team_member')
            session.permanent = True
            
            # Ensure TeamMember exists
            get_or_create_team_member(user)
            
            logger.info(f"User {email} logged in with role {session['user_role']}")
            return redirect(url_for('dashboard'))

        flash("Invalid credentials")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin():
    """Admin dashboard redirect"""
    if session.get('user_role') != 'admin':
        flash("Admin access required")
        return redirect(url_for('dashboard'))
    return redirect(url_for('dashboard'))

# ================= DASHBOARD =================
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else 'login')

@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    tm = get_or_create_team_member(user)
    return render_template('index.html')

# ================= API =================
@app.route('/api/my_projects', methods=['GET'])
@login_required
def api_my_projects():
    """
    CRITICAL FIX: All authenticated users see ALL projects
    This ensures cross-device visibility for both admin and team members
    """
    try:
        user = User.query.get(session['user_id'])
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
            
        tm = get_or_create_team_member(user)
        
        include_trashed = request.args.get('include_trashed', '0') == '1'
        user_role = session.get('user_role', 'team_member')
        
        logger.info(f"api_my_projects called by {user.email} (role={user_role}, include_trashed={include_trashed})")
        
        # FIXED: ALL users see ALL projects (complete cross-user visibility)
        all_projects = Project.query.all()
        logger.info(f"User {user.email} fetching all {len(all_projects)} projects from database")
        
        # Separate active and trashed
        active_projects = get_active_projects(all_projects)
        trashed_projects = get_trashed_projects(all_projects) if include_trashed else []
        
        logger.info(f"Found {len(active_projects)} active, {len(trashed_projects)} trashed projects for {user.email}")
        
        # Serialize projects
        def serialize_project(p):
            tasks_list = []
            for task in _safe_list(getattr(p, "tasks", [])):
                media_list = [
                    {"id": m.id, "filename": m.filename, "filepath": m.filepath}
                    for m in _safe_list(getattr(task, "media_files", []))
                ]
                tasks_list.append({
                    "id": task.id,
                    "name": task.name,
                    "progress": task.progress or 0,
                    "start_date": task.start_date.strftime("%Y-%m-%d") if task.start_date else None,
                    "end_date": task.end_date.strftime("%Y-%m-%d") if task.end_date else None,
                    "media": media_list
                })
            
            completion = 0
            if tasks_list:
                completion = sum(t.get('progress', 0) for t in tasks_list) // len(tasks_list)
            
            return {
                "id": p.id,
                "name": p.name,
                "project_type": getattr(p, 'project_type', 'General'),
                "start_date": p.start_date.strftime("%Y-%m-%d") if p.start_date else None,
                "end_date": p.end_date.strftime("%Y-%m-%d") if p.end_date else None,
                "completion": completion,
                "tasks": tasks_list,
                "created_by": getattr(p, 'created_by_email', None),
                "is_trashed": project_is_trashed(p)
            }
        
        response_data = {
            "success": True,
            "projects": [serialize_project(p) for p in active_projects],
            "trashed_projects": [serialize_project(p) for p in trashed_projects] if include_trashed else [],
            "total_active": len(active_projects),
            "total_trashed": len(trashed_projects)
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error in api_my_projects: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects', methods=['GET', 'POST'])
@login_required
def api_projects():
    if request.method == 'GET':
        # Redirect to api_my_projects for consistency
        return api_my_projects()
        
    elif request.method == 'POST':
        try:
            data = request.get_json(silent=True) or {}
            name = data.get('name')
            project_type = data.get('project_type') or data.get('type', 'General')
            start_date = parse_date(data.get('start_date'))
            end_date = parse_date(data.get('end_date'))

            if not name:
                return jsonify({"success": False, "error": "Project name required"}), 400

            user = User.query.get(session['user_id'])
            if not user:
                return jsonify({"success": False, "error": "User not found"}), 404
                
            tm = get_or_create_team_member(user)
            user_role = session.get('user_role', 'team_member')

            logger.info(f"Creating project '{name}' for user {user.email} (role={user_role})")

            project = Project(
                name=name,
                project_type=project_type,
                start_date=start_date,
                end_date=end_date
            )

            # Track who created the project if model supports it
            if hasattr(project, 'created_by_email'):
                project.created_by_email = user.email
                logger.info(f"Set created_by_email to {user.email}")

            db.session.add(project)
            db.session.flush()

            # CRITICAL FIX: Add ALL team members to the project for full visibility
            add_all_members_to_project(project)
            logger.info(f"Project '{name}' created with {len(project.team_members)} team members for full visibility")

            db.session.commit()
            
            logger.info(f"Project '{name}' (ID={project.id}) created successfully")

            return jsonify({
                "success": True,
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "member_count": len(project.team_members)
                }
            }), 201
            
        except Exception as e:
            logger.error(f"Error in api_projects POST: {e}", exc_info=True)
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>', methods=['PUT', 'DELETE'])
@login_required
def api_project_detail(project_id):
    try:
        project = Project.query.get_or_404(project_id)
        user = User.query.get(session['user_id'])
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
            
        tm = get_or_create_team_member(user)
        user_role = session.get('user_role', 'team_member')

        # All users can now see and edit all projects (full visibility)
        if request.method == 'PUT':
            data = request.get_json(silent=True) or {}
            if 'name' in data:
                project.name = data['name']
            if 'project_type' in data:
                project.project_type = data['project_type']
            if 'start_date' in data:
                project.start_date = parse_date(data['start_date'])
            if 'end_date' in data:
                project.end_date = parse_date(data['end_date'])

            db.session.commit()
            logger.info(f"Project {project_id} updated by {user.email}")
            return jsonify({"success": True})

        elif request.method == 'DELETE':
            # Only admin can permanently delete projects
            if user_role != 'admin':
                return jsonify({"success": False, "error": "Only admin can delete projects"}), 403
                
            # Permanent delete
            for task in project.tasks:
                for media in task.media_files:
                    try:
                        if media.filepath and os.path.exists(media.filepath):
                            os.remove(media.filepath)
                    except Exception as e:
                        logger.warning(f"Could not delete file: {e}")
                    db.session.delete(media)
                db.session.delete(task)
            
            project.team_members.clear()
            db.session.delete(project)
            db.session.commit()
            
            logger.info(f"Project {project_id} permanently deleted by {user.email}")
            return jsonify({"success": True, "message": "Project permanently deleted"})
            
    except Exception as e:
        logger.error(f"Error in api_project_detail: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>/trash', methods=['POST'])
@login_required
def api_trash_project(project_id):
    """Move project to trash (soft delete)"""
    try:
        project = Project.query.get_or_404(project_id)
        user = User.query.get(session['user_id'])
        
        logger.info(f"User {user.email} moving project {project_id} to trash")

        if mark_project_as_trashed(project):
            db.session.commit()
            logger.info(f"Project {project_id} moved to trash by {user.email}")
            return jsonify({"success": True, "message": "Project moved to trash"})
        else:
            return jsonify({"success": False, "error": "Could not trash project"}), 500
            
    except Exception as e:
        logger.error(f"Error in api_trash_project: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>/restore', methods=['POST'])
@login_required
def api_restore_project(project_id):
    """Restore project from trash"""
    try:
        project = Project.query.get_or_404(project_id)
        user = User.query.get(session['user_id'])
        user_role = session.get('user_role', 'team_member')

        if user_role != 'admin':
            return jsonify({"success": False, "error": "Only admin can restore projects"}), 403

        if restore_project(project):
            db.session.commit()
            logger.info(f"Project {project_id} restored by {user.email}")
            return jsonify({"success": True, "message": "Project restored"})
        else:
            return jsonify({"success": False, "error": "Could not restore project"}), 500
            
    except Exception as e:
        logger.error(f"Error in api_restore_project: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>/tasks', methods=['POST'])
@login_required
def api_add_task(project_id):
    try:
        project = Project.query.get_or_404(project_id)
        user = User.query.get(session['user_id'])

        data = request.get_json(silent=True) or {}
        task = Task(
            name=data.get('name', 'New Task'),
            project_id=project_id,
            progress=data.get('progress', 0),
            start_date=parse_date(data.get('start_date')),
            end_date=parse_date(data.get('end_date'))
        )
        db.session.add(task)
        db.session.commit()

        logger.info(f"Task added to project {project_id} by {user.email}")
        return jsonify({"success": True, "task": {"id": task.id}}), 201
    except Exception as e:
        logger.error(f"Error in api_add_task: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/tasks/<int:task_id>', methods=['PUT', 'DELETE'])
@login_required
def api_task_detail(task_id):
    try:
        task = Task.query.get_or_404(task_id)
        user = User.query.get(session['user_id'])

        if request.method == 'PUT':
            data = request.get_json(silent=True) or {}
            if 'name' in data:
                task.name = data['name']
            if 'progress' in data:
                task.progress = float(data['progress'])
            if 'start_date' in data:
                task.start_date = parse_date(data['start_date'])
            if 'end_date' in data:
                task.end_date = parse_date(data['end_date'])

            db.session.commit()
            logger.info(f"Task {task_id} updated by {user.email}")
            return jsonify({"success": True})

        elif request.method == 'DELETE':
            db.session.delete(task)
            db.session.commit()
            logger.info(f"Task {task_id} deleted by {user.email}")
            return jsonify({"success": True})
            
    except Exception as e:
        logger.error(f"Error in api_task_detail: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>/add_member', methods=['POST'])
@login_required
def api_add_project_member(project_id):
    try:
        if session.get('user_role') != 'admin':
            return jsonify({"success": False, "error": "Only admin can add members"}), 403

        project = Project.query.get_or_404(project_id)
        data = request.get_json(silent=True) or {}
        email = data.get('email', '').lower().strip()

        team_member = TeamMember.query.filter_by(email=email).first()
        if not team_member:
            return jsonify({"success": False, "error": f"User {email} not found"}), 404

        if team_member not in project.team_members:
            project.team_members.append(team_member)
            db.session.commit()
            logger.info(f"Added {email} to project {project_id}")
            return jsonify({"success": True, "message": f"Added {email} to project"})
        else:
            return jsonify({"success": False, "error": "User already in project"}), 400

    except Exception as e:
        logger.error(f"Error adding member: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/all_users', methods=['GET'])
@login_required
def api_admin_all_users():
    try:
        if session.get('user_role') != 'admin':
            return jsonify({"success": False, "error": "Admin access required"}), 403

        users = User.query.all()
        users_list = [{"id": u.id, "name": u.name, "email": u.email, "role": u.role} for u in users]
        return jsonify({"success": True, "users": users_list})
    except Exception as e:
        logger.error(f"Error in api_admin_all_users: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/tasks/<int:task_id>/media', methods=['POST'])
@login_required
def api_upload_media(task_id):
    try:
        task = Task.query.get_or_404(task_id)
        user = User.query.get(session['user_id'])

        for file_key in ['images', 'videos']:
            if file_key in request.files:
                files = request.files.getlist(file_key)
                for file in files:
                    if file and file.filename:
                        filename = secure_filename(file.filename)
                        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        file.save(path)
                        media = MediaFile(filename=filename, filepath=path, task_id=task_id)
                        db.session.add(media)

        db.session.commit()
        logger.info(f"Media uploaded to task {task_id} by {user.email}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error in api_upload_media: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/report/<int:id>')
@login_required
def report(id):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import letter
    
    p = Project.query.get_or_404(id)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = [Paragraph(f"Project: {p.name}", styles['Title'])]
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"{p.name}_report.pdf")

# ================= ERROR HANDLERS =================
@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    logger.error(f"Internal error: {e}")
    return jsonify({"success": False, "error": "Internal server error"}), 500

@app.route('/health')
def health_check():
    """Health check endpoint for Fly.io"""
    try:
        # Use text() for raw SQL expression
        db.session.execute(text('SELECT 1'))
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Not found"}), 404

# ================= MAIN =================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)