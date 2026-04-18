# migrate_db.py
from app import app, db
from models import Project, TeamMember

def run_complete_migration():
    with app.app_context():
        print("Starting complete migration...")
        
        # Step 1: Create all tables if they don't exist
        print("Creating tables if not exist...")
        db.create_all()
        print("✓ Tables ready")
        
        # Step 2: Add created_by_email column using SQLAlchemy
        print("\nChecking for created_by_email column...")
        try:
            # Check if column exists by trying to query it
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('project')]
            
            if 'created_by_email' not in columns:
                print("Adding created_by_email column...")
                db.session.execute('ALTER TABLE project ADD COLUMN created_by_email VARCHAR(255)')
                db.session.commit()
                print("✓ Column added")
            else:
                print("✓ Column already exists")
        except Exception as e:
            print(f"Note: {e}")
        
        # Step 3: Add all team members to all projects
        print("\nAdding team members to projects...")
        
        all_members = TeamMember.query.all()
        projects = Project.query.all()
        
        if not projects:
            print("No projects found. Create a project first.")
            return
        
        if not all_members:
            print("No team members found. Register some users first.")
            return
        
        added_count = 0
        for project in projects:
            for member in all_members:
                if member not in project.team_members:
                    project.team_members.append(member)
                    added_count += 1
                    print(f"✓ Added {member.email} to '{project.name}'")
        
        db.session.commit()
        print(f"\n✓ Migration complete! Added {added_count} project-member relationships.")
        
        # Step 4: Verify
        print("\nVerification:")
        for project in projects:
            print(f"Project '{project.name}' has {len(project.team_members)} members")

if __name__ == "__main__":
    run_complete_migration()