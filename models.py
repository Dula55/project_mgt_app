from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import sqlite3
import os

db = SQLAlchemy()

# Association table for many-to-many relationship between Project and TeamMember
project_members = db.Table('project_members',
    db.Column('project_id', db.Integer, db.ForeignKey('project.id'), primary_key=True),
    db.Column('member_id', db.Integer, db.ForeignKey('team_member.id'), primary_key=True)
)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    project_type = db.Column(db.String(50), nullable=False)  # Road / Real Estate
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tasks = db.relationship('Task', backref='project', lazy=True, cascade='all, delete-orphan')
    team_members = db.relationship('TeamMember', secondary=project_members, back_populates='projects', lazy='dynamic')

    @property
    def completion(self):
        if not self.tasks:
            return 0
        return sum(task.progress for task in self.tasks) / len(self.tasks)
    
    @property
    def duration_days(self):
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days
        return None

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    progress = db.Column(db.Float, default=0.0)  # percentage
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    media_files = db.relationship('MediaFile', backref='task', lazy=True, cascade='all, delete-orphan')

class MediaFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)  # 'image' or 'video'
    uploaded_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)

class TeamMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    invite_token = db.Column(db.String(255), nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    projects = db.relationship('Project', secondary=project_members, back_populates='team_members')