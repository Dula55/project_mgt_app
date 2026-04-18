# ================= PRODUCTION-GRADE app.py =================
# Fully rebuilt: stable, scalable, Fly.io + local compatible
# Includes: auth, projects, tasks, media, reports, scheduler, APIs

import os
import gc
import re
import secrets
import hashlib
import logging
from io import BytesIO
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, flash, session, jsonify, send_from_directory
)
from flask_mail import Mail, Message
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Optional scheduler
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
def get_database_url():
    url = os.getenv("DATABASE_URL")

    if not url or ".internal" in url:
        logger.warning("Using SQLite (local mode)")
        return "sqlite:///projects.db"

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url

app.config.update(
    SQLALCHEMY_DATABASE_URI=get_database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
    UPLOAD_FOLDER="static/uploads",
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
)

# Ensure upload directory exists
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

# Create tables if they don't exist
with app.app_context():
    db.create_all()

# ================= SCHEDULER =================
scheduler = BackgroundScheduler() if BackgroundScheduler else None

# ================= HELPERS =================
def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def parse_date(val):
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

# ================= AUTH =================
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].lower()
        name = request.form['name']
        password = request.form['password']

        if User.query.filter_by(email=email).first():
            flash("User exists")
            return redirect(url_for('login'))

        user = User(email=email, name=name,
                    password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()  # Get user.id
        
        # Check if TeamMember already exists
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
        email = request.form['email'].lower()
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_email'] = user.email
            session['user_role'] = getattr(user, 'role', 'team_member')
            return redirect(url_for('dashboard'))

        flash("Invalid credentials")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ================= DASHBOARD =================
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else 'login')

@app.route('/admin')
@login_required
def admin():
    return render_template('admin.html')


@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    # Get or create TeamMember for this user
    tm = TeamMember.query.filter_by(email=user.email).first()
    if not tm:
        tm = TeamMember(email=user.email, name=user.name, user_id=user.id)
        db.session.add(tm)
        db.session.commit()
    
    projects = tm.projects if tm else []
    return render_template('index.html', projects=projects)

# ================= PROJECT =================
@app.route('/add_project', methods=['POST'])
@login_required
def add_project():
    name = request.form['name']
    p = Project(name=name)
    db.session.add(p)

    user = User.query.get(session['user_id'])
    tm = TeamMember.query.filter_by(email=user.email).first()
    if tm:
        p.team_members.append(tm)

    db.session.commit()
    return redirect(url_for('dashboard'))

# ================= TASK =================
@app.route('/add_task/<int:project_id>', methods=['POST'])
@login_required
def add_task(project_id):
    name = request.form['name']
    task = Task(name=name, project_id=project_id)
    db.session.add(task)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/update_task/<int:id>', methods=['POST'])
@login_required
def update_task(id):
    t = Task.query.get_or_404(id)
    t.progress = float(request.form.get('progress', t.progress))
    db.session.commit()
    return redirect(url_for('dashboard'))

# ================= MEDIA =================
@app.route('/upload/<int:task_id>', methods=['POST'])
@login_required
def upload(task_id):
    file = request.files['file']
    filename = secure_filename(file.filename)
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)

    media = MediaFile(filename=filename, filepath=path, task_id=task_id)
    db.session.add(media)
    db.session.commit()

    return redirect(url_for('dashboard'))

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ================= REPORT =================
def generate_pdf(project):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=16, spaceAfter=30)
    
    story = []
    story.append(Paragraph(f"Project Report: {project.name}", title_style))
    story.append(Spacer(1, 12))
    
    # Project info
    story.append(Paragraph(f"Type: {getattr(project, 'project_type', 'General')}", styles['Normal']))
    story.append(Paragraph(f"Completion: {project.completion:.1f}%", styles['Normal']))
    story.append(Spacer(1, 20))
    
    # Tasks table
    if project.tasks:
        data = [['Task Name', 'Progress', 'Start Date', 'End Date']]
        for task in project.tasks:
            data.append([
                task.name,
                f"{task.progress:.1f}%",
                task.start_date.strftime("%Y-%m-%d") if task.start_date else 'N/A',
                task.end_date.strftime("%Y-%m-%d") if task.end_date else 'N/A'
            ])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        story.append(table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer

@app.route('/report/<int:id>')
@login_required
def report(id):
    p = Project.query.get_or_404(id)
    pdf = generate_pdf(p)
    return send_file(pdf, as_attachment=True,
                     download_name=f"project_{p.name}_report.pdf")

# ================= API =================
@app.route('/api/projects', methods=['GET', 'POST'])
@login_required
def api_projects():
    if request.method == 'GET':
        try:
            user = User.query.get(session['user_id'])
            tm = TeamMember.query.filter_by(email=user.email).first()
            projects = tm.projects if tm else []
            projects_list = []
            for p in projects:
                tasks_list = []
                for task in p.tasks:
                    # FIXED: Use 'media_files' instead of 'media'
                    media_list = [{"id": m.id, "filename": m.filename, "filepath": m.filepath} for m in task.media_files]
                    tasks_list.append({
                        "id": task.id,
                        "name": task.name,
                        "progress": task.progress,
                        "start_date": task.start_date.strftime("%Y-%m-%d") if task.start_date else None,
                        "end_date": task.end_date.strftime("%Y-%m-%d") if task.end_date else None,
                        "media": media_list
                    })
                
                # Calculate project completion
                completion = 0
                if tasks_list:
                    completion = sum(t.get('progress', 0) for t in tasks_list) // len(tasks_list)
                
                projects_list.append({
                    "id": p.id,
                    "name": p.name,
                    "project_type": getattr(p, 'project_type', 'General'),
                    "start_date": p.start_date.strftime("%Y-%m-%d") if p.start_date else None,
                    "end_date": p.end_date.strftime("%Y-%m-%d") if p.end_date else None,
                    "completion": completion,
                    "tasks": tasks_list
                })
            
            return jsonify({"success": True, "projects": projects_list})
        except Exception as e:
            logger.error(f"Error in api_projects GET: {e}")
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            name = data.get('name')
            project_type = data.get('project_type') or data.get('type', 'General')
            start_date = parse_date(data.get('start_date'))
            end_date = parse_date(data.get('end_date'))
            
            if not name:
                return jsonify({"success": False, "error": "Project name required"}), 400
            
            project = Project(
                name=name,
                project_type=project_type,
                start_date=start_date,
                end_date=end_date
            )
            db.session.add(project)
            db.session.flush()
            
            user = User.query.get(session['user_id'])
            tm = TeamMember.query.filter_by(email=user.email).first()
            if tm:
                project.team_members.append(tm)
            
            db.session.commit()
            
            return jsonify({"success": True, "project": {"id": project.id, "name": project.name}}), 201
        except Exception as e:
            logger.error(f"Error in api_projects POST: {e}")
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>', methods=['PUT', 'DELETE'])
@login_required
def api_project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    
    if request.method == 'PUT':
        try:
            data = request.get_json()
            if 'name' in data:
                project.name = data['name']
            if 'project_type' in data:
                project.project_type = data['project_type']
            if 'start_date' in data:
                project.start_date = parse_date(data['start_date'])
            if 'end_date' in data:
                project.end_date = parse_date(data['end_date'])
            
            db.session.commit()
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error in api_project_detail PUT: {e}")
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500
    
    elif request.method == 'DELETE':
        try:
            db.session.delete(project)
            db.session.commit()
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error in api_project_detail DELETE: {e}")
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/projects/<int:project_id>/tasks', methods=['POST'])
@login_required
def api_add_task(project_id):
    try:
        project = Project.query.get_or_404(project_id)
        data = request.get_json()
        
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
        logger.error(f"Error in api_add_task: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/tasks/<int:task_id>', methods=['PUT', 'DELETE'])
@login_required
def api_task_detail(task_id):
    task = Task.query.get_or_404(task_id)
    
    if request.method == 'PUT':
        try:
            data = request.get_json()
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
        except Exception as e:
            logger.error(f"Error in api_task_detail PUT: {e}")
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500
    
    elif request.method == 'DELETE':
        try:
            db.session.delete(task)
            db.session.commit()
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error in api_task_detail DELETE: {e}")
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/tasks/<int:task_id>/media', methods=['POST'])
@login_required
def api_upload_media(task_id):
    try:
        task = Task.query.get_or_404(task_id)
        
        if 'images' in request.files:
            files = request.files.getlist('images')
            for file in files:
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(path)
                    media = MediaFile(filename=filename, filepath=path, task_id=task_id)
                    db.session.add(media)
        
        if 'videos' in request.files:
            files = request.files.getlist('videos')
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
        logger.error(f"Error in api_upload_media: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

# ================= SCHEDULER JOB =================
def weekly_reports():
    with app.app_context():
        projects = Project.query.all()
        for p in projects:
            try:
                pdf = generate_pdf(p)
                recipients = [m.email for m in p.team_members if m.email]
                if recipients:
                    msg = Message(
                        subject=f"Weekly Report: {p.name}",
                        recipients=recipients
                    )
                    msg.attach(f"report_{p.name}.pdf", "application/pdf", pdf.getvalue())
                    mail.send(msg)
            except Exception as e:
                logger.error(f"Error sending weekly report for project {p.id}: {e}")

# ================= ERROR =================
@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    logger.error(f"Internal server error: {e}")
    return jsonify({"success": False, "error": "Internal server error"}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Resource not found"}), 404

# ================= MAIN =================
if __name__ == '__main__':
    if scheduler and not scheduler.running:
        scheduler.add_job(weekly_reports, 'cron', day_of_week='mon', hour=8)
        scheduler.start()

    app.run(debug=True, host='0.0.0.0', port=5000)