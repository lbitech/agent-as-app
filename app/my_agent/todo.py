import os
import uuid
import sqlalchemy
from datetime import datetime
from typing import Optional, List
import json

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Enum,
    select,
    delete,
    update,
)
from sqlalchemy.orm import declarative_base, Session
from google.adk.agents.llm_agent import Agent

# --- DATABASE LOGIC ---
Base = declarative_base()
connector = None
engine = None

def get_connector():
    """Lazy initialization of connector"""
    global connector, engine
    if connector is None:
        try:
            from google.cloud.sql.connector import Connector as SQLConnector
            connector = SQLConnector()
        except Exception as e:
            print(f"[DB] Connector() failed: {type(e).__name__}: {e}")
            return None
    return connector

def get_engine():
    """Lazy initialization of engine"""
    global engine
    if engine is None:
        try:
            connector_obj = get_connector()
            if connector_obj is None:
                return None
            
            def getconn():
                db_connection_name = os.environ.get("DB_CONNECTION_NAME")
                db_user = os.environ.get("DB_USER")
                db_password = os.environ.get("DB_PASSWORD")
                db_name = os.environ.get("DB_NAME", "tasks")

                return connector_obj.connect(
                    db_connection_name,
                    "pg8000",
                    user=db_user,
                    password=db_password,
                    db=db_name,
                )

            engine = sqlalchemy.create_engine(
                "postgresql+pg8000://",
                creator=getconn,
            )
        except Exception as e:
            print(f"Failed to initialize database engine: {e}")
            return None
    return engine

class Todo(Base):
    __tablename__ = "todos"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False)
    priority = Column(
        Enum("high", "medium", "low", name="priority_levels"), nullable=False, default="medium"
    )
    due_date = Column(DateTime, nullable=True)
    status = Column(Enum("pending", "done", name="status_levels"), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    """Builds the table if it's missing."""
    engine_obj = get_engine()
    if engine_obj:
        Base.metadata.create_all(bind=engine_obj)

def add_todo(
    title: str, priority: str = "medium", due_date: Optional[str] = None
) -> dict:
    """
    Adds a new task to the list.

    Args:
        title (str): The description of the task.
        priority (str): The urgency level. Must be one of: 'high', 'medium', 'low'.
        due_date (str, optional): The due date in ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).

    Returns:
        dict: A dictionary containing the new task's ID and a status message.
    """
    init_db()
    engine_obj = get_engine()
    if not engine_obj:
        return {"id": str(uuid.uuid4()), "status": "Database not available"}
    
    with Session(engine_obj) as session:
        due = datetime.fromisoformat(due_date) if due_date else None
        item = Todo(
            title=title,
            priority=priority.lower(),
            due_date=due,
        )
        session.add(item)
        session.commit()
        return {"id": item.id, "status": f"Task added ✅"}

def list_todos(status: str = "pending") -> list:
    """
    Lists tasks from the database, optionally filtering by status.

    Args:
        status (str, optional): The status to filter by. 'pending', 'done', or 'all'.
    """
    init_db()
    engine_obj = get_engine()
    if not engine_obj:
        return []
    
    with Session(engine_obj) as session:
        query = select(Todo)
        
        s_lower = status.lower()
        if s_lower != "all":
            query = query.where(Todo.status == s_lower)

        query = query.order_by(Todo.priority, Todo.created_at)

        results = session.execute(query).scalars().all()
        return [
            {
                "id": t.id,
                "task": t.title,
                "priority": t.priority,
                "status": t.status,
            }
            for t in results
        ]

def complete_todo(task_id: str) -> str:
    """Marks a specific task as 'done'."""
    init_db()
    engine_obj = get_engine()
    if not engine_obj:
        return "Database not available"
    
    with Session(engine_obj) as session:
        session.execute(update(Todo).where(Todo.id == task_id).values(status="done"))
        session.commit()
        return f"Task {task_id} marked as done."

def delete_todo(task_id: str) -> str:
    """Permanently removes a task from the database."""
    init_db()
    engine_obj = get_engine()
    if not engine_obj:
        return "Database not available"
    
    with Session(engine_obj) as session:
        session.execute(delete(Todo).where(Todo.id == task_id))
        session.commit()
        return f"Task {task_id} deleted."

# --- TASK CREATION FORM ---
def create_task_from_form(title: str, priority: str = "medium", due_date: Optional[str] = None) -> dict:
    """
    Creates a new task with form data.
    This function receives structured form input from the user interface.
    
    Args:
        title (str): The task description (required)
        priority (str): Priority level - 'high', 'medium', or 'low' (default: 'medium')
        due_date (str): Due date in ISO format YYYY-MM-DD (optional)
    
    Returns:
        dict: Result with task ID and confirmation message
    """
    result = add_todo(
        title=title,
        priority=priority,
        due_date=due_date
    )
    return {
        "success": True,
        "task_id": result["id"],
        "message": f"Task '{title}' created successfully! (ID: {result['id']})",
        "task_details": {
            "title": title,
            "priority": priority,
            "due_date": due_date or "Not set"
        }
    }

def show_task_form() -> str:
    """
    Returns a form specification for creating a task as a JSON string.
    The frontend will parse this JSON and render it as an interactive HTML form.
    
    Returns:
        str: JSON string representing the form schema
    """
    import json
    form_spec = {
        "type": "form",
        "form_id": "create_task_form",
        "title": "Create New Task",
        "description": "Fill in the details below to create a new task",
        "fields": [
            {
                "name": "title",
                "label": "Task Title",
                "type": "text",
                "required": True,
                "placeholder": "Enter task description",
                "validation": "The task title is required"
            },
            {
                "name": "priority",
                "label": "Priority Level",
                "type": "select",
                "required": True,
                "default": "medium",
                "options": [
                    {"value": "high", "label": "High Priority"},
                    {"value": "medium", "label": "Medium Priority"},
                    {"value": "low", "label": "Low Priority"}
                ]
            },
            {
                "name": "due_date",
                "label": "Due Date",
                "type": "date",
                "required": False,
                "placeholder": "YYYY-MM-DD",
                "help_text": "Optional - leave blank if no due date"
            }
        ],
        "submit_button": "Create Task",
        "submit_tool": "create_task_from_form"
    }
    return json.dumps(form_spec)

# --- TODO SPECIALIST AGENT ---
todo_agent = Agent(
    model='gemini-2.5-flash',
    name='todo_specialist',
    description='A specialist agent that manages a structured SQL task list.',
    instruction='''
    You are a task management specialist. Help users create, view, complete, and delete tasks.
    
    **For creating tasks:**
    1. Call show_task_form() to get the form JSON
    2. Send the form JSON directly as your response - do NOT add any additional text before or after the JSON
    3. The JSON will be rendered as an interactive form in the user's browser
    4. Wait for the user to submit the form
    5. Once you receive the form data, call create_task_from_form() with the data
    6. Confirm that the task was created successfully with its ID
    
    **IMPORTANT:** When sending the form, include ONLY the JSON output from show_task_form(), with no other text.
    
    **For other actions:**
    - Use list_todos to show all tasks or filter by status (pending, done, all)
    - Use complete_todo to mark a task as finished
    - Use delete_todo to remove a task
    
    When you need a task ID you don't have, use list_todos first to find it.
    Be helpful and confirm all actions clearly.
    ''',
    tools=[show_task_form, create_task_from_form, add_todo, list_todos, complete_todo, delete_todo],
)