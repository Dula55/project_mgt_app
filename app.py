# Memory optimization - must be at the very top
import os
import sys
import gc
import signal
import time

# Disable matplotlib debugging and reduce memory
os.environ['MPLBACKEND'] = 'Agg'
os.environ['MALLOC_ARENA_MAX'] = '2'
os.environ['OMP_NUM_THREADS'] = '1'

# Limit thread stack size
import threading
threading.stack_size(1024 * 512)  # 512KB instead of 8MB

# Import Flask and extensions
from flask import Flask, render_template, request, redirect, url_for, send_file, flash, session, jsonify
from flask_migrate import Migrate
from flask_mail import Mail, Message
from io import BytesIO
from datetime import datetime, timedelta
import secrets
import hashlib
import re
import urllib.request
from werkzeug.utils import secure_filename

# Create Flask app
app = Flask(__name__)

# Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:////app/data/projects.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 1,
    'pool_recycle': 3600,
    'pool_pre_ping': True,
}
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', '/app/static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Email configuration (optional)
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', '')

# Import models after app is configured
from models import db, Project, Task, MediaFile, TeamMember

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
mail = Mail(app)

# Allowed extensions
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('/app/data', exist_ok=True)

# Define phases
ROAD_PHASES = ["Site preparation", "Earth work", "Drainage construction", "Asphalt laying"]
REAL_ESTATE_PHASES = ["Site preparation", "Setting out and foundation",
                      "Block work", "Roofing", "Plastering and finishing"]

def allowed_file(filename, file_type='image'):
    """Check if file extension is allowed"""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if file_type == 'image':
        return ext in ALLOWED_IMAGE_EXTENSIONS
    elif file_type == 'video':
        return ext in ALLOWED_VIDEO_EXTENSIONS
    return False

def send_invitation_email(email, project_name, project_id, invite_token):
    """Send invitation email to team member"""
    if not app.config['MAIL_USERNAME'] or not app.config['MAIL_PASSWORD']:
        return True
    
    try:
        with app.app_context():
            invite_link = url_for('accept_invitation', token=invite_token, _external=True)
            msg = Message(
                subject=f'Invitation to join project: {project_name}',
                recipients=[email],
                body=f"You have been invited to join project: {project_name}\n\nClick: {invite_link}"
            )
            mail.send(msg)
            return True
    except Exception as e:
        print(f"Email error: {e}")
        return True

# Simple health check endpoint (doesn't require database)
@app.route('/health')
def health_check():
    return "OK", 200

# Root endpoint
@app.route('/')
def index():
    try:
        member_id = session.get('member_id')
        if member_id:
            member = TeamMember.query.get(member_id)
            if member:
                projects = member.projects
            else:
                projects = Project.query.all()
        else:
            projects = Project.query.all()
        
        return render_template('index.html', projects=projects, session=session)
    except Exception as e:
        app.logger.error(f"Index error: {e}")
        # Try to create tables if they don't exist
        try:
            db.create_all()
        except:
            pass
        return render_template('index.html', projects=[], session=session), 200

@app.route('/add_project', methods=['POST'])
def add_project():
    try:
        name = request.form['name']
        project_type = request.form['type']
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        
        start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        
        project = Project(name=name, project_type=project_type, start_date=start_date_obj, end_date=end_date_obj)
        db.session.add(project)
        db.session.commit()
        
        phases = ROAD_PHASES if project_type == "Road" else REAL_ESTATE_PHASES
        for phase in phases:
            task = Task(name=phase, progress=0.0, project_id=project.id)
            db.session.add(task)
        db.session.commit()
        
        flash(f'Project "{name}" created successfully!', 'success')
    except Exception as e:
        app.logger.error(f"Add project error: {e}")
        flash(f'Error creating project: {str(e)}', 'error')
    
    return redirect(url_for('index'))

@app.route('/invite_members/<int:project_id>', methods=['POST'])
def invite_members(project_id):
    project = Project.query.get_or_404(project_id)
    emails_input = request.form.get('emails', '')
    
    emails = re.split(r'[,\n;]+', emails_input)
    emails = [email.strip().lower() for email in emails if email.strip()]
    
    invited_count = 0
    for email in emails:
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            continue
        
        try:
            member = TeamMember.query.filter_by(email=email).first()
            if not member:
                member = TeamMember(email=email, name=email.split('@')[0])
                db.session.add(member)
                db.session.flush()
            
            if member not in project.team_members:
                project.team_members.append(member)
                token_data = f"{member.id}_{project.id}_{datetime.utcnow().timestamp()}"
                invite_token = hashlib.sha256(token_data.encode()).hexdigest()
                member.invite_token = invite_token
                member.token_expiry = datetime.utcnow() + timedelta(days=7)
                send_invitation_email(email, project.name, project.id, invite_token)
                invited_count += 1
        except Exception as e:
            print(f"Error inviting {email}: {e}")
    
    db.session.commit()
    
    if invited_count > 0:
        flash(f'Successfully invited {invited_count} member(s)!', 'success')
    return redirect(url_for('index'))

@app.route('/accept_invitation/<token>')
def accept_invitation(token):
    member = TeamMember.query.filter_by(invite_token=token).first()
    if not member or (member.token_expiry and datetime.utcnow() > member.token_expiry):
        flash('Invalid or expired invitation', 'error')
        return redirect(url_for('index'))
    
    session['member_id'] = member.id
    session['member_name'] = member.name
    session['member_email'] = member.email
    
    flash(f'Welcome {member.name}!', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

@app.route('/update_task/<int:task_id>', methods=['POST'])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.progress = float(request.form['progress'])
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    
    if request.method == 'POST':
        task.name = request.form['name']
        task.progress = float(request.form['progress'])
        db.session.commit()
        
        # Handle image uploads
        if 'images' in request.files:
            for image in request.files.getlist('images'):
                if image and allowed_file(image.filename, 'image'):
                    filename = secure_filename(f"{datetime.now().timestamp()}_{image.filename}")
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    image.save(filepath)
                    media = MediaFile(filename=filename, filepath=filepath, file_type='image', task_id=task.id)
                    db.session.add(media)
        
        if 'videos' in request.files:
            for video in request.files.getlist('videos'):
                if video and allowed_file(video.filename, 'video'):
                    filename = secure_filename(f"{datetime.now().timestamp()}_{video.filename}")
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    video.save(filepath)
                    media = MediaFile(filename=filename, filepath=filepath, file_type='video', task_id=task.id)
                    db.session.add(media)
        
        db.session.commit()
        return redirect(url_for('index'))
    
    media_files = MediaFile.query.filter_by(task_id=task.id).all()
    return render_template('edit_task.html', task=task, media_files=media_files)

@app.route('/delete_media/<int:media_id>')
def delete_media(media_id):
    media = MediaFile.query.get_or_404(media_id)
    task_id = media.task_id
    if os.path.exists(media.filepath):
        os.remove(media.filepath)
    db.session.delete(media)
    db.session.commit()
    return redirect(url_for('edit_task', task_id=task_id))

@app.route('/delete_task/<int:task_id>')
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    for media in MediaFile.query.filter_by(task_id=task_id).all():
        if os.path.exists(media.filepath):
            os.remove(media.filepath)
        db.session.delete(media)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete_project/<int:project_id>')
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    for task in project.tasks:
        for media in MediaFile.query.filter_by(task_id=task.id).all():
            if os.path.exists(media.filepath):
                os.remove(media.filepath)
            db.session.delete(media)
    Task.query.filter_by(project_id=project.id).delete()
    db.session.delete(project)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/generate_report/<int:project_id>')
def generate_report(project_id):
    from docx import Document
    from docx.shared import Inches
    import matplotlib.pyplot as plt
    
    project = Project.query.get_or_404(project_id)
    tasks = Task.query.filter_by(project_id=project_id).all()
    
    doc = Document()
    doc.add_heading(f"{project.name} - Progress Report", 0)
    doc.add_paragraph(f"Completion: {project.completion:.1f}%")
    
    for task in tasks:
        doc.add_paragraph(f"{task.name}: {task.progress}%")
    
    try:
        plt.figure(figsize=(6, 4))
        plt.bar([t.name[:20] for t in tasks], [t.progress for t in tasks])
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        img_stream = BytesIO()
        plt.savefig(img_stream, format='png')
        img_stream.seek(0)
        doc.add_picture(img_stream, width=Inches(5))
        plt.close()
        gc.collect()
    except Exception as e:
        print(f"Chart error: {e}")
    
    word_stream = BytesIO()
    doc.save(word_stream)
    word_stream.seek(0)
    
    return send_file(word_stream, as_attachment=True, download_name=f"{project.name}_report.docx")

@app.route('/drone_view')
def drone_view():
    try:
        return render_template('drone_view.html')
    except:
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Drone View</title></head>
        <body>
            <h1>Drone View Coming Soon</h1>
            <a href="/">Back to Dashboard</a>
        </body>
        </html>
        """

@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return "<h1>404</h1><p>Page not found</p><a href='/'>Home</a>", 404

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    return "<h1>500</h1><p>Internal server error</p><a href='/'>Home</a>", 500

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()
    gc.collect()

# Create tables when app starts
with app.app_context():
    try:
        db.create_all()
        print("Database tables created successfully")
    except Exception as e:
        print(f"Error creating tables: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=False)