from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "user"
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default="team_member")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # One-to-one relationship with TeamMember
    team_member = db.relationship("TeamMember", backref="user_account", uselist=False, cascade="all, delete-orphan")
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    project_type = db.Column(db.String(50), default="General", nullable=False, index=True)

    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    
    created_by_email = db.Column(db.String(255), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tasks = db.relationship(
        "Task",
        backref="project",
        cascade="all, delete-orphan",
        lazy=True,
    )

    team_members = db.relationship(
        "TeamMember",
        secondary="project_team",
        backref="projects",
        lazy="subquery",
    )

    @property
    def completion(self):
        if not self.tasks:
            return 0.0
        return sum((t.progress or 0) for t in self.tasks) / len(self.tasks)

    @property
    def duration_days(self):
        if self.start_date and self.end_date:
            return max(0, (self.end_date - self.start_date).days)
        return 0


class TeamMember(db.Model):
    __tablename__ = "team_member"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    
    # Link to User account
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=True)

    invite_token = db.Column(db.String(255), nullable=True, index=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    invited_by_id = db.Column(db.Integer, db.ForeignKey("team_member.id"), nullable=True)
    invited_by = db.relationship(
        "TeamMember",
        remote_side=[id],
        backref="invited_members",
    )


class Task(db.Model):
    __tablename__ = "task"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    progress = db.Column(db.Float, default=0.0, nullable=False)

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False,
        index=True,
    )

    activity_description = db.Column(db.String(500), nullable=True)
    dependencies = db.Column(db.String(500), nullable=True)
    task_category = db.Column(db.String(100), nullable=True, index=True)

    start_date = db.Column(db.Date, nullable=True, index=True)
    end_date = db.Column(db.Date, nullable=True)
    duration_days = db.Column(db.Integer, nullable=True)

    planned_cost = db.Column(db.Float, default=0.0, nullable=False)
    actual_cost = db.Column(db.Float, default=0.0, nullable=False)

    assigned_to_id = db.Column(db.Integer, db.ForeignKey("team_member.id"), nullable=True, index=True)
    assigned_to = db.relationship(
        "TeamMember",
        foreign_keys=[assigned_to_id],
        backref=db.backref("assigned_tasks", lazy="dynamic"),
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    media_files = db.relationship(
        "MediaFile",
        backref="task",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def auto_schedule(self):
        if self.start_date and self.duration_days:
            from datetime import timedelta
            self.end_date = self.start_date + timedelta(days=self.duration_days)

    def __repr__(self):
        return f"<Task {self.name}>"


class MediaFile(db.Model):
    __tablename__ = "media_file"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=True)
    filepath = db.Column(db.String(500), nullable=True)
    file_type = db.Column(db.String(20), nullable=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey("task.id"),
        nullable=False,
        index=True,
    )
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


project_team = db.Table(
    "project_team",
    db.Column("project_id", db.Integer, db.ForeignKey("project.id"), primary_key=True),
    db.Column("member_id", db.Integer, db.ForeignKey("team_member.id"), primary_key=True),
)