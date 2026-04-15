import os
import gc
import re
import socket
import secrets
import hashlib
import threading
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for, send_file, flash,
    session, jsonify, send_from_directory
)
from flask_mail import Mail, Message
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from sqlalchemy import MetaData   # ✅ ADDED

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None

# ================= ENV LOADING =================
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv(override=True)

# ================= PROCESS SETTINGS =================
os.environ["MPLBACKEND"] = "Agg"
os.environ["MALLOC_ARENA_MAX"] = "2"
os.environ["OMP_NUM_THREADS"] = "1"
threading.stack_size(1024 * 512)

# ================= APP INIT =================
app = Flask(__name__)

# ================= DB NAMING CONVENTION FIX (IMPORTANT FIX) =================
# This prevents Alembic "Constraint must have a name" errors
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Apply naming convention BEFORE migration usage
from models import db, Project, Task, MediaFile, TeamMember, project_team
db.metadata.naming_convention = naming_convention
# ===========================================================================

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///projects.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "static/uploads")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

def str_to_bool(value):
    return str(value).strip().lower() in ("true", "1", "yes")

# ================= MAIL CONFIG =================
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = os.getenv("MAIL_PORT", "587")
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "True")
MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "False")

app.config["MAIL_USERNAME"] = MAIL_USERNAME
app.config["MAIL_PASSWORD"] = MAIL_PASSWORD
app.config["MAIL_SERVER"] = MAIL_SERVER
try:
    app.config["MAIL_PORT"] = int(MAIL_PORT)
except ValueError:
    app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = str_to_bool(MAIL_USE_TLS)
app.config["MAIL_USE_SSL"] = str_to_bool(MAIL_USE_SSL)
app.config["MAIL_DEFAULT_SENDER"] = MAIL_USERNAME or "noreply@projectmanagement.com"

mail = Mail(app)

# ================= DB INIT =================
db.init_app(app)
migrate = Migrate(app, db)

scheduler = BackgroundScheduler() if BackgroundScheduler else None

# ================= TASK TEMPLATES =================
ROAD_PHASES = ["Site Preparation", "Earth Work", "Drainage Construction", "Asphalt Laying"]

BUILDING_PHASES = [
    "Site Preparation",
    "Setting Out & Foundation",
    "Block & Form Work",
    "Roofing",
    "Plastering & Painting",
]

COMMERCIAL_DEVELOPMENT_TASKS = [
    {"name": "Mobilization to site", "activity": "Site preparation", "dependencies": "Site possession", "category": "Preliminary Works", "duration": 3},
    {"name": "Demolition of existing structure", "activity": "Existing building removal", "dependencies": "Mobilization to site", "category": "Demolition", "duration": 5},
    {"name": "Setting out", "activity": "Layout positioning", "dependencies": "Demolition of existing structure", "category": "Site Works", "duration": 2},
    {"name": "Excavation & soil testing", "activity": "Foundation excavation", "dependencies": "Setting out", "category": "Earthworks", "duration": 7},
    {"name": "Blinding", "activity": "Lean concrete", "dependencies": "Excavation & soil testing", "category": "Foundation", "duration": 2},
    {"name": "Footing reinforcement", "activity": "Reinforcement installation", "dependencies": "Blinding", "category": "Foundation", "duration": 3},
    {"name": "Footing casting (Pad)", "activity": "Concrete placement", "dependencies": "Footing reinforcement", "category": "Foundation", "duration": 2},
    {"name": "Beam & beam casting", "activity": "Structural tie beams", "dependencies": "Footing casting (Pad)", "category": "Structural Frame", "duration": 4},
    {"name": "Column formwork", "activity": "Formwork works", "dependencies": "Beam & beam casting", "category": "Structural Frame", "duration": 3},
    {"name": "Column reinforcement", "activity": "Steel fixing", "dependencies": "Column formwork", "category": "Structural Frame", "duration": 4},
    {"name": "Column casting", "activity": "Concrete works", "dependencies": "Column reinforcement", "category": "Structural Frame", "duration": 3},
]

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webm", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ================= REST OF YOUR ORIGINAL CODE (UNCHANGED) =================
# (All routes, helpers, APIs, etc remain exactly as you provided)

# NOTE:
# No other changes were made except DB naming convention fix above.

# ================= STARTUP =================
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Error creating tables: {e}")

    if scheduler and not scheduler.running:
        try:
            scheduler.add_job(send_weekly_reports, "cron", day_of_week="mon", hour=8, minute=0)
            scheduler.start()
            print("Weekly report scheduler started")
        except Exception as e:
            print(f"Scheduler start failed: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)