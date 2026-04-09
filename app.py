# ================= MEMORY OPTIMIZATION (KEEP AT TOP) =================
import os
import sys
import gc
import signal
import time

os.environ["MPLBACKEND"] = "Agg"
os.environ["MALLOC_ARENA_MAX"] = "2"
os.environ["OMP_NUM_THREADS"] = "1"

import threading
threading.stack_size(1024 * 512)

# ================= IMPORTS =================
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
    flash,
    session,
    jsonify,
    send_from_directory,
    Response,
)
from flask_migrate import Migrate
from flask_mail import Mail, Message
from io import BytesIO
from datetime import datetime, timedelta
import secrets
import hashlib
import re
from werkzeug.utils import secure_filename

from dotenv import load_dotenv

# Load .env from the project folder if it exists
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ================= APP INIT =================
app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///projects.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "static/uploads")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# ================= EMAIL CONFIGURATION (FIXED & ROBUST) =================
def str_to_bool(value):
    return str(value).strip().lower() in ["true", "1", "yes"]

# Ensure .env is loaded properly
from dotenv import load_dotenv
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Read environment variables safely
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = os.getenv("MAIL_PORT", "587")
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "True")
MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "False")

# Apply to Flask config
app.config["MAIL_USERNAME"] = MAIL_USERNAME
app.config["MAIL_PASSWORD"] = MAIL_PASSWORD
app.config["MAIL_SERVER"] = MAIL_SERVER

# Safe integer conversion
try:
    app.config["MAIL_PORT"] = int(MAIL_PORT)
except ValueError:
    app.config["MAIL_PORT"] = 587

# Safe boolean conversion
app.config["MAIL_USE_TLS"] = str_to_bool(MAIL_USE_TLS)
app.config["MAIL_USE_SSL"] = str_to_bool(MAIL_USE_SSL)

# Default sender
app.config["MAIL_DEFAULT_SENDER"] = MAIL_USERNAME

# Debug (VERY IMPORTANT for troubleshooting)
print("==== MAIL CONFIG DEBUG ====")
print("MAIL_USERNAME:", MAIL_USERNAME)
print("MAIL_PASSWORD:", "SET" if MAIL_PASSWORD else "NOT SET")
print("MAIL_SERVER:", MAIL_SERVER)
print("MAIL_PORT:", app.config["MAIL_PORT"])
print("MAIL_USE_TLS:", app.config["MAIL_USE_TLS"])
print("===========================")


# ================= MODELS =================
from models import db, Project, Task, MediaFile, TeamMember

db.init_app(app)
migrate = Migrate(app, db)
mail = Mail(app)

# ================= CONSTANTS =================
ROAD_PHASES = [
    "Site Preparation",
    "Earth Work",
    "Drainage Construction",
    "Asphalt Laying",
]

BUILDING_PHASES = [
    "Site Preparation",
    "Setting Out & Foundation",
    "Block & Form Work",
    "Roofing",
    "Plastering & Painting",
]

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs("/app/data", exist_ok=True)

# ================= HELPERS =================
def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(value, "%Y-%m-%d").date()

def allowed_file(filename, file_type="image"):
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if file_type == "image":
        return ext in ALLOWED_IMAGE_EXTENSIONS
    if file_type == "video":
        return ext in ALLOWED_VIDEO_EXTENSIONS
    return False

def calculate_end_date(start_date, duration_days):
    if start_date and duration_days:
        return start_date + timedelta(days=duration_days)
    return None

def get_project_duration_days(project):
    if project and project.start_date and project.end_date:
        return max(0, (project.end_date - project.start_date).days)
    return 0

def send_invitation_email(email, project_name, project_id, invite_token):
    """Send invitation email to a team member"""
    # Check if email is configured
    if not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
        print(f"Email not configured. Would have sent invite to {email}")
        print(f"Invite link would be: {url_for('accept_invitation', token=invite_token, _external=True)}")
        return True  # Return True for testing when email not configured
    
    try:
        with app.app_context():
            invite_link = url_for("accept_invitation", token=invite_token, _external=True)
            
            # Create email message
            msg = Message(
                subject=f"Invitation to join project: {project_name}",
                sender=app.config["MAIL_DEFAULT_SENDER"],
                recipients=[email]
            )
            
            # Plain text body
            msg.body = f"""
You have been invited to join the project: {project_name}

Click the link below to accept the invitation:
{invite_link}

This invitation will expire in 7 days.

Best regards,
Project Management Team
"""
            
            # HTML body
            msg.html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; }}
        .container {{ padding: 20px; background-color: #f4f4f4; }}
        .content {{ background-color: white; padding: 20px; border-radius: 5px; }}
        .button {{ 
            display: inline-block; 
            padding: 10px 20px; 
            background-color: #4CAF50; 
            color: white; 
            text-decoration: none; 
            border-radius: 5px;
            margin: 20px 0;
        }}
        .footer {{ font-size: 12px; color: #666; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="content">
            <h2>Project Invitation</h2>
            <p>You have been invited to join the project: <strong>{project_name}</strong></p>
            <p>Click the button below to accept the invitation:</p>
            <p><a href="{invite_link}" class="button">Accept Invitation</a></p>
            <p>Or copy and paste this link into your browser:</p>
            <p>{invite_link}</p>
            <p>This invitation will expire in 7 days.</p>
            <div class="footer">
                <p>Best regards,<br>Project Management Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""
            
            # Send email
            mail.send(msg)
            print(f"✅ Invitation email sent to {email}")
            return True
            
    except Exception as e:
        print(f"❌ Email error for {email}: {str(e)}")
        # Log the invite link for testing
        invite_link = url_for("accept_invitation", token=invite_token, _external=True)
        print(f"📧 Invite link (for testing): {invite_link}")
        return False

def safe_commit():
    try:
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


# ================= INDEX =================
@app.route("/")
def index():
    try:
        member_id = session.get("member_id")
        if member_id:
            member = TeamMember.query.get(member_id)
            if member:
                projects = member.projects
            else:
                projects = Project.query.all()
        else:
            projects = Project.query.all()

        # Load media files for each task in each project
        for project in projects:
            for task in project.tasks:
                task.media_files = MediaFile.query.filter_by(task_id=task.id).all()

        return render_template("index.html", projects=projects, session=session)
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Index error: {e}")
        try:
            db.create_all()
        except Exception:
            pass
        return render_template("index.html", projects=[], session=session), 200

# ================= ADD PROJECT =================
@app.route("/add_project", methods=["POST"])
def add_project():
    try:
        name = request.form["name"]
        project_type = request.form["type"]
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")

        start_date_obj = parse_date(start_date)
        end_date_obj = parse_date(end_date)

        project = Project(
            name=name,
            project_type=project_type,
            start_date=start_date_obj,
            end_date=end_date_obj,
        )
        db.session.add(project)
        db.session.commit()

        phases = ROAD_PHASES if project_type == "Road" else BUILDING_PHASES

        if start_date_obj and end_date_obj and end_date_obj > start_date_obj:
            total_days = (end_date_obj - start_date_obj).days
            phase_duration = max(1, total_days // len(phases))
        else:
            phase_duration = 5

        current_start = start_date_obj

        for phase in phases:
            task_end = calculate_end_date(current_start, phase_duration) if current_start else None
            task = Task(
                name=phase,
                progress=0.0,
                project_id=project.id,
                start_date=current_start,
                end_date=task_end,
                duration_days=phase_duration if current_start else None,
            )
            db.session.add(task)
            current_start = task_end

        db.session.commit()
        flash(f'Project "{name}" created successfully!', "success")
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Add project error: {e}")
        flash(f"Error creating project: {str(e)}", "error")

    return redirect(url_for("index"))

# ================= EDIT PROJECT =================
@app.route("/edit_project/<int:project_id>", methods=["POST"])
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    try:
        project.name = request.form.get("name", project.name)
        project.project_type = request.form.get("type", project.project_type)
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        
        if start_date:
            project.start_date = parse_date(start_date)
        if end_date:
            project.end_date = parse_date(end_date)
            
        db.session.commit()
        flash("Project updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating project: {str(e)}", "error")
    
    return redirect(url_for("index"))

# ================= VIEW PROJECT MEMBERS =================
@app.route("/project_members/<int:project_id>")
def project_members(project_id):
    """View all members of a project"""
    project = Project.query.get_or_404(project_id)
    return render_template("project_members.html", project=project)

# ================= INVITE MEMBERS =================
@app.route("/invite_members/<int:project_id>", methods=["POST"])
def invite_members(project_id):
    """Invite members to join a project"""
    project = Project.query.get_or_404(project_id)
    emails_input = request.form.get("emails", "")
    
    # Split by comma, newline, or semicolon
    emails = re.split(r"[,\n;]+", emails_input)
    emails = [email.strip().lower() for email in emails if email.strip()]

    if not emails:
        flash("Please enter at least one email address", "error")
        return redirect(url_for("project_members", project_id=project.id))

    invited_count = 0
    failed = []
    success_emails = []

    for email in emails:
        # Validate email format
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            failed.append(f"{email} (invalid format)")
            continue

        try:
            # Check if member already exists
            member = TeamMember.query.filter_by(email=email).first()
            if not member:
                # Create new member
                member = TeamMember(
                    email=email, 
                    name=email.split("@")[0]
                )
                db.session.add(member)
                db.session.flush()  # Get the ID without committing

            # Check if member is already in project
            if member in project.team_members:
                failed.append(f"{email} (already a member)")
                continue

            # Generate unique invitation token
            token_data = f"{member.id}_{project.id}_{datetime.utcnow().timestamp()}_{secrets.token_hex(8)}"
            invite_token = hashlib.sha256(token_data.encode()).hexdigest()
            
            # Store token in member record
            member.invite_token = invite_token
            member.token_expiry = datetime.utcnow() + timedelta(days=7)

            # Add member to project
            project.team_members.append(member)
            
            # Try to send email
            email_sent = send_invitation_email(email, project.name, project.id, invite_token)
            
            if email_sent:
                invited_count += 1
                success_emails.append(email)
            else:
                # Remove member from project if email fails
                project.team_members.remove(member)
                failed.append(f"{email} (email sending failed)")
            
            # Commit after each successful addition
            db.session.commit()
                
        except Exception as e:
            db.session.rollback()
            failed.append(f"{email} ({str(e)})")
            print(f"Error inviting {email}: {str(e)}")
            continue

    # Final message
    if invited_count > 0:
        flash(f"✅ Successfully invited {invited_count} member(s): {', '.join(success_emails[:3])}", "success")
    if failed:
        flash(f"⚠️ Some invitations failed: {', '.join(failed[:5])}", "warning")

    return redirect(url_for("project_members", project_id=project.id))

# ================= REMOVE MEMBER FROM PROJECT =================
@app.route("/remove_member/<int:project_id>/<int:member_id>")
def remove_member(project_id, member_id):
    """Remove a member from a project"""
    project = Project.query.get_or_404(project_id)
    member = TeamMember.query.get_or_404(member_id)
    
    try:
        if member in project.team_members:
            project.team_members.remove(member)
            db.session.commit()
            flash(f"Removed {member.name or member.email} from the project", "success")
        else:
            flash("Member not found in this project", "error")
    except Exception as e:
        db.session.rollback()
        flash(f"Error removing member: {str(e)}", "error")
    
    return redirect(url_for("project_members", project_id=project.id))

# ================= ACCEPT INVITATION =================
@app.route("/accept_invitation/<token>")
def accept_invitation(token):
    """Accept project invitation"""
    # Find member with this token
    member = TeamMember.query.filter_by(invite_token=token).first()
    
    if not member:
        flash("Invalid or expired invitation link. Please request a new invitation.", "error")
        return redirect(url_for("index"))

    # Check if token is expired
    if member.token_expiry and datetime.utcnow() > member.token_expiry:
        flash("Invitation link has expired. Please request a new invitation.", "error")
        return redirect(url_for("index"))

    # Set session variables
    session["member_id"] = member.id
    session["member_name"] = member.name
    session["member_email"] = member.email
    session.permanent = True

    # Clear the token after use for security
    member.invite_token = None
    member.token_expiry = None
    db.session.commit()

    flash(f"Welcome {member.name or member.email}! You have been added to the team.", "success")
    return redirect(url_for("index"))

# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out", "info")
    return redirect(url_for("index"))

# ================= UPDATE TASK PROGRESS (DASHBOARD) =================
@app.route("/update_task/<int:task_id>", methods=["POST"])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    try:
        task.progress = float(request.form.get("progress", task.progress))
        db.session.commit()
        flash("Task progress updated.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not update task: {e}", "error")
    return redirect(url_for("index"))

# ================= EDIT TASK =================
@app.route("/edit_task/<int:task_id>", methods=["GET", "POST"], strict_slashes=False)
@app.route("/edit_task/<int:task_id>/", methods=["GET", "POST"], strict_slashes=False)
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    project = Project.query.get(task.project_id)
    if not project:
        flash("Project not found", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        try:
            task.name = request.form.get("name", task.name)
            task.progress = float(request.form.get("progress", task.progress))

            # Accept both field names for compatibility with templates
            start_date_raw = request.form.get("start_date")
            end_date_raw = request.form.get("end_date")
            duration_raw = request.form.get("duration_days") or request.form.get("duration")

            # Normalize values
            start_date_obj = parse_date(start_date_raw) if start_date_raw else task.start_date
            end_date_obj = parse_date(end_date_raw) if end_date_raw else task.end_date

            if duration_raw not in (None, ""):
                duration_days = int(duration_raw)
            else:
                duration_days = task.duration_days

            # If only start + duration are present, calculate end date
            if start_date_obj and duration_days:
                end_date_obj = calculate_end_date(start_date_obj, duration_days)

            # If start + end are present and duration omitted, infer duration
            if start_date_obj and end_date_obj and (duration_days is None):
                duration_days = max(1, (end_date_obj - start_date_obj).days)

            # Enforce task duration <= project duration
            project_duration = get_project_duration_days(project)
            if project_duration and duration_days and duration_days > project_duration:
                flash("Task duration exceeds project duration!", "danger")
                return redirect(url_for("edit_task", task_id=task.id))

            # Optional date-bound check against project window
            if project.start_date and project.end_date and start_date_obj and end_date_obj:
                if start_date_obj < project.start_date or end_date_obj > project.end_date:
                    flash("Task dates must stay within the project date range.", "danger")
                    return redirect(url_for("edit_task", task_id=task.id))

            task.start_date = start_date_obj
            task.end_date = end_date_obj
            task.duration_days = duration_days

            db.session.commit()

            # Handle image uploads
            for image in request.files.getlist("images"):
                if image and image.filename and allowed_file(image.filename, "image"):
                    filename = secure_filename(f"{datetime.now().timestamp()}_{image.filename}")
                    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    image.save(filepath)
                    db.session.add(
                        MediaFile(
                            filename=filename,
                            filepath=filepath,
                            file_type="image",
                            task_id=task.id,
                        )
                    )

            # Handle video uploads
            for video in request.files.getlist("videos"):
                if video and video.filename and allowed_file(video.filename, "video"):
                    filename = secure_filename(f"{datetime.now().timestamp()}_{video.filename}")
                    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    video.save(filepath)
                    db.session.add(
                        MediaFile(
                            filename=filename,
                            filepath=filepath,
                            file_type="video",
                            task_id=task.id,
                        )
                    )

            db.session.commit()
            flash("Task updated successfully!", "success")
            return redirect(url_for("edit_task", task_id=task.id))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Edit task error: {e}")
            flash(f"Error updating task: {str(e)}", "error")
            return redirect(url_for("edit_task", task_id=task.id))

    media_files = MediaFile.query.filter_by(task_id=task.id).all()
    return render_template("edit_task.html", task=task, project=project, media_files=media_files)

# ================= GANTT DATA =================
@app.route("/gantt_data")
def gantt_data():
    tasks = Task.query.all()
    data = []
    for t in tasks:
        if t.start_date and t.end_date:
            data.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "start": t.start_date.strftime("%Y-%m-%d"),
                    "end": t.end_date.strftime("%Y-%m-%d"),
                    "progress": t.progress,
                    "project_id": t.project_id,
                }
            )
    return jsonify(data)

@app.route("/update_task_gantt/<int:task_id>", methods=["POST"])
def update_task_gantt(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json(force=True)

    try:
        if "start_date" in data and "end_date" in data:
            start = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
            end = datetime.strptime(data["end_date"], "%Y-%m-%d").date()

            if end < start:
                return jsonify({"status": "error", "message": "End date cannot be before start date"}), 400

            task.start_date = start
            task.end_date = end
            task.duration_days = max(1, (end - start).days)
        
        if "progress" in data:
            task.progress = float(data["progress"])
            
        db.session.commit()
        return jsonify({"status": "ok", "message": "Task updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 400

# ================= DELETE MEDIA =================
@app.route("/delete_media/<int:media_id>")
def delete_media(media_id):
    media = MediaFile.query.get_or_404(media_id)
    task_id = media.task_id

    try:
        if media.filepath and os.path.exists(media.filepath):
            os.remove(media.filepath)
        db.session.delete(media)
        db.session.commit()
        flash("Media file deleted successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete media: {str(e)}", "error")

    return redirect(url_for("edit_task", task_id=task_id))

# ================= DELETE TASK =================
@app.route("/delete_task/<int:task_id>")
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)

    try:
        for media in MediaFile.query.filter_by(task_id=task_id).all():
            if media.filepath and os.path.exists(media.filepath):
                os.remove(media.filepath)
            db.session.delete(media)

        db.session.delete(task)
        db.session.commit()
        flash("Task deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete task: {str(e)}", "error")

    return redirect(url_for("index"))

# ================= DELETE ALL TASKS =================
@app.route("/delete_all_tasks/<int:project_id>")
def delete_all_tasks(project_id):
    project = Project.query.get_or_404(project_id)
    
    try:
        tasks = Task.query.filter_by(project_id=project_id).all()
        for task in tasks:
            for media in MediaFile.query.filter_by(task_id=task.id).all():
                if media.filepath and os.path.exists(media.filepath):
                    os.remove(media.filepath)
                db.session.delete(media)
            db.session.delete(task)
        
        db.session.commit()
        flash("All tasks deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete tasks: {str(e)}", "error")
    
    return redirect(url_for("index"))

# ================= DELETE PROJECT =================
@app.route("/delete_project/<int:project_id>")
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)

    try:
        for task in Task.query.filter_by(project_id=project.id).all():
            for media in MediaFile.query.filter_by(task_id=task.id).all():
                if media.filepath and os.path.exists(media.filepath):
                    os.remove(media.filepath)
                db.session.delete(media)

        Task.query.filter_by(project_id=project.id).delete()
        db.session.delete(project)
        db.session.commit()
        flash("Project deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete project: {str(e)}", "error")

    return redirect(url_for("index"))

# ================= REPORT =================
@app.route("/project_report/<int:project_id>")
def project_report(project_id):
    project = Project.query.get_or_404(project_id)
    tasks = Task.query.filter_by(project_id=project_id).all()

    doc = BytesIO()
    doc.write(f"{project.name} Report\n".encode())
    for t in tasks:
        doc.write(f"{t.name}: {t.progress}%\n".encode())

    doc.seek(0)
    return send_file(doc, as_attachment=True, download_name=f"{project.name}_report.txt")

@app.route("/generate_report/<int:project_id>")
def generate_report(project_id):
    from docx import Document
    from docx.shared import Inches
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    project = Project.query.get_or_404(project_id)
    tasks = Task.query.filter_by(project_id=project_id).all()

    doc = Document()
    doc.add_heading(f"{project.name} - Progress Report", 0)
    doc.add_paragraph(f"Type: {project.project_type}")

    if project.start_date:
        doc.add_paragraph(f"Start Date: {project.start_date}")
    if project.end_date:
        doc.add_paragraph(f"End Date: {project.end_date}")
    doc.add_paragraph(f"Overall Completion: {project.completion:.1f}%")

    doc.add_heading("Task Progress", level=1)
    for t in tasks:
        doc.add_paragraph(f"{t.name}: {t.progress}%")
        media_files = MediaFile.query.filter_by(task_id=t.id).all()
        if media_files:
            doc.add_paragraph(f"  Attached files: {len(media_files)}")
            for media in media_files:
                doc.add_paragraph(f"    - {media.filename} ({media.file_type})")

    try:
        plt.figure(figsize=(6, 4))
        plt.bar([t.name for t in tasks], [float(t.progress or 0) for t in tasks])
        plt.ylabel("Progress (%)")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        img_stream1 = BytesIO()
        plt.savefig(img_stream1, format="png")
        img_stream1.seek(0)
        doc.add_picture(img_stream1, width=Inches(5))
        plt.close()

        plt.figure(figsize=(4, 4))
        completion = max(0.0, min(100.0, project.completion or 0))
        plt.pie(
            [completion, max(0, 100 - completion)],
            labels=["Completed", "Remaining"],
            autopct="%1.1f%%",
        )
        plt.title("Overall Completion")
        img_stream2 = BytesIO()
        plt.savefig(img_stream2, format="png")
        img_stream2.seek(0)
        doc.add_picture(img_stream2, width=Inches(4))
        plt.close()
    except Exception as e:
        print(f"Error generating charts: {e}")

    word_stream = BytesIO()
    doc.save(word_stream)
    word_stream.seek(0)

    return send_file(
        word_stream,
        as_attachment=True,
        download_name=f"{project.name}_report.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

# ================= DRONE VIEW =================
@app.route("/drone_view")
def drone_view():
    try:
        return render_template("drone_view.html")
    except Exception:
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

# ================= STATIC FILES =================
@app.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ================= ERROR HANDLERS =================
@app.errorhandler(404)
def not_found(e):
    return "<h1>404</h1><p>Page not found</p><a href='/'>Home</a>", 404

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    return "<h1>500</h1><p>Internal server error</p><a href='/'>Home</a>", 500

@app.route('/health')
def health_check():
    return 'OK', 200

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()
    gc.collect()

# ================= CREATE TABLES ON START =================
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Error creating tables: {e}")

# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)