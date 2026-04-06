from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, abort, flash, session
from flask_migrate import Migrate
from flask_mail import Mail, Message
from models import db, Project, Task, MediaFile, TeamMember
from docx import Document
from docx.shared import Inches
from io import BytesIO
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for Docker
import matplotlib.pyplot as plt
import os
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import secrets
import hashlib
import smtplib
import sys

app = Flask(__name__)

# Configuration with environment variable support for Docker
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:////app/data/projects.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', '/app/static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here-change-in-production')

# Email configuration - Use environment variables for Docker
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'dulasman5@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'ejna xnwp hear ajnu')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'your-email@gmail.com')
app.config['MAIL_MAX_EMAILS'] = None
app.config['MAIL_ASCII_ATTACHMENTS'] = False

# Allowed extensions
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('/app/data', exist_ok=True)  # Ensure data directory exists for SQLite

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
mail = Mail(app)

# Define phases for project types
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

def test_email_configuration():
    """Test if email configuration is working"""
    try:
        with app.app_context():
            msg = Message('Test Email',
                        recipients=['test@example.com'])
            msg.body = 'This is a test email from your Project Management System'
            mail.send(msg)
        return True, "Email configuration is working!"
    except Exception as e:
        return False, f"Email configuration error: {str(e)}"

def send_invitation_email(email, project_name, project_id, invite_token):
    """Send invitation email to team member"""
    with app.app_context():
        invite_link = url_for('accept_invitation', token=invite_token, _external=True)
        
        # Create HTML email content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #4361ee, #3f37c9); color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; background: #f8fafc; }}
                .button {{ display: inline-block; padding: 12px 24px; background: #4361ee; color: white; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
                .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Project Invitation</h2>
                </div>
                <div class="content">
                    <p>Hello,</p>
                    <p>You have been invited to join the project: <strong>{project_name}</strong></p>
                    <p>Click the button below to access the project and start collaborating:</p>
                    <div style="text-align: center;">
                        <a href="{invite_link}" class="button">Access Project</a>
                    </div>
                    <p>This invitation will expire in 7 days.</p>
                    <hr>
                    <p><small>If the button doesn't work, copy and paste this link into your browser:</small></p>
                    <p><small>{invite_link}</small></p>
                </div>
                <div class="footer">
                    <p>Best regards,<br>Project Management Team</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Project Invitation
        
        You have been invited to join the project: {project_name}
        
        Click the link below to access the project:
        {invite_link}
        
        This invitation will expire in 7 days.
        
        Best regards,
        Project Management Team
        """
        
        try:
            msg = Message(
                subject=f'Invitation to join project: {project_name}',
                recipients=[email],
                body=text_content,
                html=html_content
            )
            mail.send(msg)
            print(f"Email sent successfully to {email}")
            return True
        except Exception as e:
            print(f"Error sending email to {email}: {str(e)}")
            return False

@app.route('/test_email')
def test_email():
    """Test endpoint to check email configuration"""
    success, message = test_email_configuration()
    if success:
        flash('Email configuration is working!', 'success')
    else:
        flash(f'Email configuration error: {message}', 'error')
    return redirect(url_for('index'))

@app.route('/')
def index():
    # Get member ID from session if logged in
    member_id = session.get('member_id')
    if member_id:
        # Show only projects the member has access to
        member = TeamMember.query.get(member_id)
        if member:
            projects = member.projects
        else:
            projects = Project.query.all()
    else:
        projects = Project.query.all()
    
    return render_template('index.html', projects=projects, session=session)

@app.route('/add_project', methods=['POST'])
def add_project():
    name = request.form['name']
    project_type = request.form['type']
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    
    # Parse dates if provided
    start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
    end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
    
    project = Project(name=name, project_type=project_type, start_date=start_date_obj, end_date=end_date_obj)
    db.session.add(project)
    db.session.commit()

    # Create tasks for the project phases
    phases = ROAD_PHASES if project_type == "Road" else REAL_ESTATE_PHASES
    for phase in phases:
        task = Task(name=phase, progress=0.0, project_id=project.id)
        db.session.add(task)
    db.session.commit()

    flash(f'Project "{name}" created successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/invite_members/<int:project_id>', methods=['POST'])
def invite_members(project_id):
    project = Project.query.get_or_404(project_id)
    emails_input = request.form.get('emails', '')
    
    # Split emails by comma, newline, or semicolon
    import re
    emails = re.split(r'[,\n;]+', emails_input)
    emails = [email.strip().lower() for email in emails if email.strip()]
    
    if not emails:
        flash('Please enter at least one email address', 'error')
        return redirect(url_for('index'))
    
    invited_count = 0
    failed_emails = []
    
    for email in emails:
        # Validate email format
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            failed_emails.append(f"{email} (invalid format)")
            continue
        
        try:
            # Check if member already exists
            member = TeamMember.query.filter_by(email=email).first()
            if not member:
                # Create new member
                member = TeamMember(
                    email=email, 
                    name=email.split('@')[0],
                    created_at=datetime.utcnow()
                )
                db.session.add(member)
                db.session.flush()  # Get the ID without committing
            
            # Check if already added to project
            if member not in project.team_members:
                project.team_members.append(member)
                
                # Generate invitation token (expires in 7 days)
                token_data = f"{member.id}_{project.id}_{datetime.utcnow().timestamp()}"
                invite_token = hashlib.sha256(token_data.encode()).hexdigest()
                member.invite_token = invite_token
                member.token_expiry = datetime.utcnow() + timedelta(days=7)
                
                # Send email invitation
                if send_invitation_email(email, project.name, project.id, invite_token):
                    invited_count += 1
                    print(f"Invitation sent to {email}")
                else:
                    failed_emails.append(f"{email} (email sending failed)")
                    # Remove member from project if email failed
                    project.team_members.remove(member)
            else:
                failed_emails.append(f"{email} (already a member)")
                
        except Exception as e:
            print(f"Error processing {email}: {str(e)}")
            failed_emails.append(f"{email} ({str(e)})")
    
    # Commit all changes
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Database error: {str(e)}', 'error')
        return redirect(url_for('index'))
    
    # Show results
    if invited_count > 0:
        flash(f'Successfully sent {invited_count} invitation(s)!', 'success')
    
    if failed_emails:
        flash(f'Failed to send to {len(failed_emails)} email(s): {", ".join(failed_emails[:3])}', 'error')
    
    if invited_count == 0 and not failed_emails:
        flash('No valid email addresses provided', 'error')
    
    return redirect(url_for('index'))

@app.route('/accept_invitation/<token>')
def accept_invitation(token):
    # Find member with this token
    member = TeamMember.query.filter_by(invite_token=token).first()
    if not member:
        flash('Invalid or expired invitation link', 'error')
        return redirect(url_for('index'))
    
    # Check token expiry (7 days)
    if member.token_expiry and datetime.utcnow() > member.token_expiry:
        flash('Invitation has expired. Please request a new one.', 'error')
        return redirect(url_for('index'))
    
    # Log the member in
    session['member_id'] = member.id
    session['member_name'] = member.name
    session['member_email'] = member.email
    
    flash(f'Welcome {member.name}! You now have access to the projects you were invited to.', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
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
    
    # Check if member has access to this project
    member_id = session.get('member_id')
    if member_id:
        member = TeamMember.query.get(member_id)
        if member and task.project not in member.projects:
            flash('You do not have access to this project', 'error')
            return redirect(url_for('index'))
    
    if request.method == 'POST':
        task.name = request.form['name']
        task.progress = float(request.form['progress'])
        db.session.commit()
        
        # Handle media uploads
        # Handle image uploads
        if 'images' in request.files:
            images = request.files.getlist('images')
            for image in images:
                if image and allowed_file(image.filename, 'image'):
                    filename = secure_filename(f"{datetime.now().timestamp()}_{image.filename}")
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    image.save(filepath)
                    media = MediaFile(
                        filename=filename,
                        filepath=filepath,
                        file_type='image',
                        task_id=task.id
                    )
                    db.session.add(media)
        
        # Handle video uploads
        if 'videos' in request.files:
            videos = request.files.getlist('videos')
            for video in videos:
                if video and allowed_file(video.filename, 'video'):
                    filename = secure_filename(f"{datetime.now().timestamp()}_{video.filename}")
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    video.save(filepath)
                    media = MediaFile(
                        filename=filename,
                        filepath=filepath,
                        file_type='video',
                        task_id=task.id
                    )
                    db.session.add(media)
        
        db.session.commit()
        return redirect(url_for('index'))
    
    media_files = MediaFile.query.filter_by(task_id=task.id).all()
    return render_template('edit_task.html', task=task, media_files=media_files)

@app.route('/delete_task/<int:task_id>')
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    
    # Check if member has access
    member_id = session.get('member_id')
    if member_id:
        member = TeamMember.query.get(member_id)
        if member and task.project not in member.projects:
            flash('You do not have access to this project', 'error')
            return redirect(url_for('index'))
    
    # Delete associated media files
    media_files = MediaFile.query.filter_by(task_id=task_id).all()
    for media in media_files:
        if os.path.exists(media.filepath):
            os.remove(media.filepath)
        db.session.delete(media)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete_all_tasks/<int:project_id>', methods=['GET', 'POST'])
def delete_all_tasks(project_id):
    project = Project.query.get_or_404(project_id)
    
    # Check if member has access
    member_id = session.get('member_id')
    if member_id:
        member = TeamMember.query.get(member_id)
        if member and project not in member.projects:
            flash('You do not have access to this project', 'error')
            return redirect(url_for('index'))
    
    # Delete all media files for tasks in this project
    for task in project.tasks:
        media_files = MediaFile.query.filter_by(task_id=task.id).all()
        for media in media_files:
            if os.path.exists(media.filepath):
                os.remove(media.filepath)
            db.session.delete(media)
    
    # Delete all tasks associated with this project
    Task.query.filter_by(project_id=project.id).delete()
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete_media/<int:media_id>')
def delete_media(media_id):
    media = MediaFile.query.get_or_404(media_id)
    task_id = media.task_id
    
    # Check if member has access
    task = Task.query.get(task_id)
    member_id = session.get('member_id')
    if member_id and task:
        member = TeamMember.query.get(member_id)
        if member and task.project not in member.projects:
            flash('You do not have access to this project', 'error')
            return redirect(url_for('index'))
    
    if os.path.exists(media.filepath):
        os.remove(media.filepath)
    db.session.delete(media)
    db.session.commit()
    return redirect(url_for('edit_task', task_id=task_id))

@app.route('/edit_project/<int:project_id>', methods=['POST'])
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    project.name = request.form['name']
    project.project_type = request.form['type']
    project.start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date() if request.form.get('start_date') else None
    project.end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date() if request.form.get('end_date') else None
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete_project/<int:project_id>')
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    
    # Delete all media files associated with tasks in this project
    for task in project.tasks:
        media_files = MediaFile.query.filter_by(task_id=task.id).all()
        for media in media_files:
            if os.path.exists(media.filepath):
                os.remove(media.filepath)
            db.session.delete(media)
    
    # Delete all tasks (will be cascaded due to relationship)
    Task.query.filter_by(project_id=project.id).delete()
    
    # Delete the project
    db.session.delete(project)
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/project_report/<int:project_id>')
def project_report(project_id):
    project = Project.query.get_or_404(project_id)
    tasks = [{"name": t.name, "progress": t.progress, "media": MediaFile.query.filter_by(task_id=t.id).all()} for t in project.tasks]
    
    # Check if report.html exists, if not, return a simple report
    try:
        return render_template('report.html', project=project, tasks=tasks)
    except:
        # Fallback: Return a simple JSON response or redirect to generate_report
        return redirect(url_for('generate_report', project_id=project_id))

@app.route('/drone_view')
def drone_view():
    # Check if drone_view.html exists
    try:
        return render_template('drone_view.html')
    except:
        # Fallback: Return a simple message or redirect to index
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Drone View</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head>
        <body>
            <div class="container mt-5">
                <div class="alert alert-info">
                    <h4><i class="bi bi-info-circle"></i> Drone View Coming Soon</h4>
                    <p>The drone view feature is currently under development. Please check back later.</p>
                    <a href="/" class="btn btn-primary">Back to Dashboard</a>
                </div>
            </div>
        </body>
        </html>
        """

@app.route('/generate_report/<int:project_id>')
def generate_report(project_id):
    # Fetch project and tasks from DB
    project = Project.query.get_or_404(project_id)
    tasks = Task.query.filter_by(project_id=project_id).all()

    # Create Word document
    doc = Document()
    doc.add_heading(f"{project.name} - Progress Report", 0)
    doc.add_paragraph(f"Type: {project.project_type}")
    if project.start_date:
        doc.add_paragraph(f"Start Date: {project.start_date}")
    if project.end_date:
        doc.add_paragraph(f"End Date: {project.end_date}")
    doc.add_paragraph(f"Overall Completion: {project.completion:.1f}%")

    # Task Progress Section
    doc.add_heading("Task Progress", level=1)
    for t in tasks:
        doc.add_paragraph(f"{t.name}: {t.progress}%")
        
        # Add media information
        media_files = MediaFile.query.filter_by(task_id=t.id).all()
        if media_files:
            doc.add_paragraph(f"  Attached files: {len(media_files)}")
            for media in media_files:
                doc.add_paragraph(f"    - {media.filename} ({media.file_type})")

    # --- Save charts as images using Matplotlib ---
    try:
        # Task Progress Bar Chart
        plt.figure(figsize=(6, 4))
        plt.bar([t.name for t in tasks], [t.progress for t in tasks], color='steelblue')
        plt.ylabel("Progress (%)")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        img_stream1 = BytesIO()
        plt.savefig(img_stream1, format='png')
        img_stream1.seek(0)
        doc.add_picture(img_stream1, width=Inches(5))
        plt.close()

        # Completion Pie Chart
        plt.figure(figsize=(4, 4))
        plt.pie([project.completion, 100 - project.completion],
                labels=['Completed', 'Remaining'],
                autopct='%1.1f%%',
                colors=['green', 'lightgrey'])
        plt.title("Overall Completion")
        img_stream2 = BytesIO()
        plt.savefig(img_stream2, format='png')
        img_stream2.seek(0)
        doc.add_picture(img_stream2, width=Inches(4))
        plt.close()
    except Exception as e:
        print(f"Error generating charts: {e}")

    # Save Word file in memory
    word_stream = BytesIO()
    doc.save(word_stream)
    word_stream.seek(0)

    return send_file(
        word_stream,
        as_attachment=True,
        download_name=f"{project.name}_report.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

# Serve static files (for uploaded media)
@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>404 - Page Not Found</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="text-center">
                <i class="bi bi-exclamation-triangle-fill text-warning" style="font-size: 4rem;"></i>
                <h1 class="mt-3">404 - Page Not Found</h1>
                <p class="lead">The page you are looking for does not exist.</p>
                <a href="/" class="btn btn-primary">Go to Dashboard</a>
            </div>
        </div>
    </body>
    </html>
    """, 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>500 - Internal Server Error</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="text-center">
                <i class="bi bi-bug-fill text-danger" style="font-size: 4rem;"></i>
                <h1 class="mt-3">500 - Internal Server Error</h1>
                <p class="lead">Something went wrong on our end. Please try again later.</p>
                <a href="/" class="btn btn-primary">Go to Dashboard</a>
            </div>
        </div>
    </body>
    </html>
    """, 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Use environment variable for host and port
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    app.run(host=host, port=port, debug=debug)