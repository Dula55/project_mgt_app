import sqlite3
import os

# Path to your database file
db_path = 'instance/projects.db'

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if created_at column exists
        cursor.execute("PRAGMA table_info(project)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'created_at' not in columns:
            print("Adding created_at column to project table...")
            cursor.execute("ALTER TABLE project ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            print("Column added successfully!")
        
        # Check if team_member table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_member'")
        if not cursor.fetchone():
            print("Creating team_member table...")
            cursor.execute("""
                CREATE TABLE team_member (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(120) NOT NULL UNIQUE,
                    invite_token VARCHAR(255),
                    token_expiry TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("team_member table created!")
        
        # Check if project_members association table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_members'")
        if not cursor.fetchone():
            print("Creating project_members association table...")
            cursor.execute("""
                CREATE TABLE project_members (
                    project_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    PRIMARY KEY (project_id, member_id),
                    FOREIGN KEY(project_id) REFERENCES project (id),
                    FOREIGN KEY(member_id) REFERENCES team_member (id)
                )
            """)
            print("project_members table created!")
        
        conn.commit()
        print("Database updated successfully!")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        conn.close()
else:
    print(f"Database file not found at {db_path}")
    print("Run the app first to create the database, or delete the existing one.")