# ================= PRODUCTION-GRADE app.py =================
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

    # Local/dev fallback
    if not raw_url:
        logger.warning("No DATABASE_URL found, using SQLite fallback for local development.")
        return "sqlite:///projects.db"

    # Normalize old Heroku-style URL
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    parsed = urlparse(raw_url)
    host = parsed.hostname or ""
    running_on_fly = bool(os.getenv("FLY_APP_NAME"))

    # Fly internal hostnames only work inside Fly.io
    if host.endswith(".internal") and not running_on_fly:
        logger.warning(
            "DATABASE_URL points to a Fly.io internal host, but the app is running locally. "
            "Using SQLite fallback instead."
        )
        return "sqlite:///projects.db"

    # If the host cannot be resolved locally, fall back to SQLite
    if parsed.scheme.startswith("postgres") and host and not _host_resolves(host) and not running_on_fly:
        logger.warning(
            f"Database host '{host}' cannot be resolved locally. Using SQLite fallback instead."
        )
        return "sqlite:///projects.db"

    safe_host = host or "unknown-host"
    logger.info(f"Using database host: {safe_host}")
    return raw_url

app.config.update(
    SQLALCHEMY_DATABASE_URI=get_database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={
        "pool_pre_ping": True,
        "pool_recycle": 300,
    },
    SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
    UPLOAD_FOLDER="static/uploads",
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True if os.getenv("FLY_APP_NAME") else False,
)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

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

with app.app_context():
    # Keep startup from dying on DB issues during local development
    try:
        db.create_all()
    except Exception as e:
        logger.exception("Database initialization failed")
        raise

    if not User.query.filter_by(role="admin").first():
        first_user = User.query.first()
        if first_user:
            first_user.role = "admin"
            db.session.commit()
            logger.info(f"Set {first_user.email} as admin")

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
@app.route('/api/check_team_member', methods=['GET'])
@login_required
def api_check_team_member():
    """Debug endpoint to check team member status"""
    try:
        user = User.query.get(session['user_id'])
        tm = get_or_create_team_member(user)
        
        # Get all projects this user is associated with
        projects_via_membership = list(tm.projects) if hasattr(tm, 'projects') else []
        projects_created = Project.query.filter_by(created_by_email=user.email).all() if hasattr(Project, 'created_by_email') else []
        
        return jsonify({
            "success": True,
            "user_email": user.email,
            "user_role": session.get('user_role'),
            "team_member_id": tm.id,
            "projects_as_member": [p.id for p in projects_via_membership],
            "projects_created": [p.id for p in projects_created],
            "total_projects_visible": len(set(projects_via_membership + projects_created))
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/my_projects', methods=['GET'])
@login_required
def api_my_projects():
    """Get projects for the current user with proper visibility"""
    try:
        user = User.query.get(session['user_id'])
        tm = get_or_create_team_member(user)
        
        include_trashed = request.args.get('include_trashed', '0') == '1'
        include_shared = request.args.get('include_shared', '1') == '1'
        user_role = session.get('user_role', 'team_member')
        
        logger.info(f"api_my_projects called by {user.email} (role={user_role}, include_trashed={include_trashed})")
        
        # Get all projects for this user
        all_projects = []
        
        if user_role == 'admin':
            # Admin sees ALL projects
            all_projects = Project.query.all()
            logger.info(f"Admin fetching all {len(all_projects)} projects from database")
        else:
            # Team member sees projects where they're a member OR they created
            projects_set = set()
            
            # Method 1: Projects where user is a team member
            if hasattr(tm, 'projects'):
                member_projects = list(tm.projects)
                projects_set.update(member_projects)
                logger.info(f"User is member of {len(member_projects)} projects")
            
            # Method 2: Projects created by this user
            if hasattr(Project, 'created_by_email'):
                created_projects = Project.query.filter_by(created_by_email=user.email).all()
                projects_set.update(created_projects)
                logger.info(f"User created {len(created_projects)} projects")
            
            # Method 3: Also check via team_members relationship directly
            direct_member_projects = Project.query.join(Project.team_members).filter(TeamMember.id == tm.id).all()
            projects_set.update(direct_member_projects)
            logger.info(f"User is direct member of {len(direct_member_projects)} projects")
            
            all_projects = list(projects_set)
            logger.info(f"Total unique projects for {user.email}: {len(all_projects)}")
        
        # Separate active and trashed
        active_projects = get_active_projects(all_projects)
        trashed_projects = get_trashed_projects(all_projects) if include_trashed else []
        
        logger.info(f"Found {len(active_projects)} active, {len(trashed_projects)} trashed projects")
        
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
            tm = get_or_create_team_member(user)
            user_role = session.get('user_role', 'team_member')

            logger.info(f"Creating project '{name}' for user {user.email} (role={user_role})")

            project = Project(
                name=name,
                project_type=project_type,
                start_date=start_date,
                end_date=end_date
            )

            # Add created_by if model supports it
            if hasattr(project, 'created_by_email'):
                project.created_by_email = user.email
                logger.info(f"Set created_by_email to {user.email}")

            db.session.add(project)
            db.session.flush()

            # CRITICAL FIX: Add creator as team member FIRST
            if tm not in project.team_members:
                project.team_members.append(tm)
                logger.info(f"Added creator {user.email} to project team")

            # If admin is creating, add ALL team members to project
            if user_role == 'admin':
                all_members = TeamMember.query.all()
                for member in all_members:
                    if member not in project.team_members:
                        project.team_members.append(member)
                        logger.info(f"Added team member {member.email} to project")
                logger.info(f"Admin project created with {len(project.team_members)} total members")

            db.session.commit()
            
            logger.info(f"Project '{name}' (ID={project.id}) created successfully with {len(project.team_members)} members")

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
        tm = get_or_create_team_member(user)
        user_role = session.get('user_role', 'team_member')

        # Check access
        has_access = (user_role == 'admin' or tm in project.team_members)
        if not has_access:
            return jsonify({"success": False, "error": "Access denied"}), 403

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
        tm = get_or_create_team_member(user)
        user_role = session.get('user_role', 'team_member')

        has_access = (user_role == 'admin' or tm in project.team_members)
        if not has_access:
            return jsonify({"success": False, "error": "Access denied"}), 403

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
        tm = get_or_create_team_member(user)
        user_role = session.get('user_role', 'team_member')

        has_access = (user_role == 'admin' or tm in project.team_members)
        if not has_access:
            return jsonify({"success": False, "error": "Access denied"}), 403

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
        tm = get_or_create_team_member(user)
        user_role = session.get('user_role', 'team_member')

        has_access = (user_role == 'admin' or tm in task.project.team_members)
        if not has_access:
            return jsonify({"success": False, "error": "Access denied"}), 403

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
            return jsonify({"success": True})

        elif request.method == 'DELETE':
            db.session.delete(task)
            db.session.commit()
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
        tm = get_or_create_team_member(user)
        user_role = session.get('user_role', 'team_member')

        has_access = (user_role == 'admin' or tm in task.project.team_members)
        if not has_access:
            return jsonify({"success": False, "error": "Access denied"}), 403

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

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Not found"}), 404

# ================= MAIN =================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)