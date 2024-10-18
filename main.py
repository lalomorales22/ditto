import os
import sys
import json
import importlib
import traceback
from flask import Flask, Blueprint, request, send_from_directory, render_template_string, jsonify, flash, redirect, url_for
from threading import Thread
from time import sleep
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from litellm import completion, supports_function_calling

# Configuration
MODEL_NAME = os.environ.get('LITELLM_MODEL', 'gpt-4o')  # Default model; can be swapped easily

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Change this to a random secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app_builder.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Initialize LoginManager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

LOG_FILE = "flask_app_builder_log.json"

# Directory paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
ROUTES_DIR = os.path.join(BASE_DIR, 'routes')
PROJECTS_DIR = os.path.join(BASE_DIR, 'projects')

# Initialize progress tracking
progress = {
    "status": "idle",
    "iteration": 0,
    "max_iterations": 50,
    "output": "",
    "completed": False
}

# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    projects = db.relationship('Project', backref='owner', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# Project model
class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    versions = db.relationship('ProjectVersion', backref='project', lazy='dynamic')
    collaborators = db.relationship('ProjectCollaborator', backref='project', lazy='dynamic')

# Project Version model for version control
class ProjectVersion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    changes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Project Collaborator model for collaboration features
class ProjectCollaborator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    permission = db.Column(db.String(20), nullable=False)  # e.g., 'read', 'write', 'admin'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Helper functions
def create_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)
        if path == ROUTES_DIR:
            create_file(os.path.join(ROUTES_DIR, '__init__.py'), '')
        return f"Created directory: {path}"
    return f"Directory already exists: {path}"

def create_file(path, content):
    try:
        with open(path, 'x') as f:
            f.write(content)
        return f"Created file: {path}"
    except FileExistsError:
        with open(path, 'w') as f:
            f.write(content)
        return f"Updated file: {path}"
    except Exception as e:
        return f"Error creating/updating file {path}: {e}"

def update_file(path, content):
    try:
        with open(path, 'w') as f:
            f.write(content)
        return f"Updated file: {path}"
    except Exception as e:
        return f"Error updating file {path}: {e}"

def fetch_code(file_path):
    try:
        with open(file_path, 'r') as f:
            code = f.read()
        return code
    except Exception as e:
        return f"Error fetching code from {file_path}: {e}"

def load_routes():
    try:
        if BASE_DIR not in sys.path:
            sys.path.append(BASE_DIR)
        for filename in os.listdir(ROUTES_DIR):
            if filename.endswith('.py') and filename != '__init__.py':
                module_name = filename[:-3]
                module_path = f'routes.{module_name}'
                try:
                    if module_path in sys.modules:
                        importlib.reload(sys.modules[module_path])
                    else:
                        importlib.import_module(module_path)
                    module = sys.modules.get(module_path)
                    if module:
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if isinstance(attr, Blueprint):
                                app.register_blueprint(attr)
                except Exception as e:
                    print(f"Error importing module {module_path}: {e}")
                    continue
        print("Routes loaded successfully.")
        return "Routes loaded successfully."
    except Exception as e:
        print(f"Error in load_routes: {e}")
        return f"Error loading routes: {e}"

def task_completed():
    progress["status"] = "completed"
    progress["completed"] = True
    return "Task marked as completed."

# Initialize necessary directories
create_directory(TEMPLATES_DIR)
create_directory(STATIC_DIR)
create_directory(ROUTES_DIR)
create_directory(PROJECTS_DIR)

# Load routes once at initiation
load_routes()

# Function to log history to file
def log_to_file(history_dict):
    try:
        with open(LOG_FILE, 'w') as log_file:
            json.dump(history_dict, log_file, indent=4)
    except Exception as e:
        pass  # Silent fail

# Routes
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template_string('''
        <h1>Flask App Builder</h1>
        <a href="{{ url_for('login') }}">Login</a> | <a href="{{ url_for('register') }}">Register</a>
    ''')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter((User.username == username) | (User.email == email)).first()
        if user:
            flash('Username or email already exists')
            return redirect(url_for('register'))
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('Registered successfully')
        return redirect(url_for('login'))
    return render_template_string('''
        <h1>Register</h1>
        <form method="post">
            <input type="text" name="username" placeholder="Username" required><br>
            <input type="email" name="email" placeholder="Email" required><br>
            <input type="password" name="password" placeholder="Password" required><br>
            <input type="submit" value="Register">
        </form>
        <a href="{{ url_for('login') }}">Already have an account? Login</a>
    ''')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template_string('''
        <h1>Login</h1>
        <form method="post">
            <input type="text" name="username" placeholder="Username" required><br>
            <input type="password" name="password" placeholder="Password" required><br>
            <input type="submit" value="Login">
        </form>
        <a href="{{ url_for('register') }}">Don't have an account? Register</a>
    ''')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    projects = Project.query.filter_by(user_id=current_user.id).all()
    return render_template_string('''
        <h1>Dashboard</h1>
        <h2>Your Projects</h2>
        <ul>
        {% for project in projects %}
            <li>
                <a href="{{ url_for('project_detail', project_id=project.id) }}">{{ project.name }}</a>
                (Created: {{ project.created_at.strftime('%Y-%m-%d %H:%M:%S') }})
            </li>
        {% endfor %}
        </ul>
        <a href="{{ url_for('create_project') }}">Create New Project</a><br>
        <a href="{{ url_for('logout') }}">Logout</a>
    ''', projects=projects)

@app.route('/create_project', methods=['GET', 'POST'])
@login_required
def create_project():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        new_project = Project(name=name, description=description, user_id=current_user.id)
        db.session.add(new_project)
        db.session.commit()
        flash('Project created successfully')
        return redirect(url_for('dashboard'))
    return render_template_string('''
        <h1>Create Project</h1>
        <form method="post">
            <input type="text" name="name" placeholder="Project Name" required><br>
            <textarea name="description" placeholder="Project Description"></textarea><br>
            <input type="submit" value="Create Project">
        </form>
        <a href="{{ url_for('dashboard') }}">Back to Dashboard</a>
    ''')

@app.route('/project/<int:project_id>')
@login_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not project.collaborators.filter_by(user_id=current_user.id).first():
        flash('You do not have permission to view this project')
        return redirect(url_for('dashboard'))
    return render_template_string('''
        <h1>{{ project.name }}</h1>
        <p>{{ project.description }}</p>
        <h2>Files</h2>
        <ul>
        {% for file in project_files %}
            <li><a href="{{ url_for('edit_file', project_id=project.id, filename=file) }}">{{ file }}</a></li>
        {% endfor %}
        </ul>
        <a href="{{ url_for('create_file', project_id=project.id) }}">Create New File</a><br>
        <a href="{{ url_for('project_settings', project_id=project.id) }}">Project Settings</a><br>
        <a href="{{ url_for('dashboard') }}">Back to Dashboard</a>
    ''', project=project, project_files=os.listdir(os.path.join(PROJECTS_DIR, str(project.id))))

@app.route('/project/<int:project_id>/create_file', methods=['GET', 'POST'])
@login_required
def create_file(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not project.collaborators.filter_by(user_id=current_user.id, permission='write').first():
        flash('You do not have permission to create files in this project')
        return redirect(url_for('project_detail', project_id=project_id))
    if request.method == 'POST':
        filename = request.form.get('filename')
        content = request.form.get('content')
        file_path = os.path.join(PROJECTS_DIR, str(project.id), filename)
        create_file(file_path, content)
        flash('File created successfully')
        return redirect(url_for('project_detail', project_id=project_id))
    return render_template_string('''
        <h1>Create File</h1>
        <form method="post">
            <input type="text" name="filename" placeholder="Filename" required><br>
            <textarea name="content" placeholder="File Content"></textarea><br>
            <input type="submit" value="Create File">
        </form>
        <a href="{{ url_for('project_detail', project_id=project_id) }}">Back to Project</a>
    ''', project_id=project_id)

@app.route('/project/<int:project_id>/edit/<path:filename>', methods=['GET', 'POST'])
@login_required
def edit_file(project_id, filename):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not project.collaborators.filter_by(user_id=current_user.id, permission='write').first():
        flash('You do not have permission to edit files in this project')
        return redirect(url_for('project_detail', project_id=project_id))
    file_path = os.path.join(PROJECTS_DIR, str(project.id), filename)
    if request.method == 'POST':
        content = request.form.get('content')
        update_file(file_path, content)
        flash('File updated successfully')
        return redirect(url_for('project_detail', project_id=project_id))
    content = fetch_code(file_path)
    return render_template_string('''
        <h1>Edit File: {{ filename }}</h1>
        <form method="post">
            <textarea name="content" rows="20" cols="80">{{ content }}</textarea><br>
            <input type="submit" value="Save Changes">
        </form>
        <a href="{{ url_for('project_detail', project_id=project_id) }}">Back to Project</a>
    ''', filename=filename, content=content, project_id=project_id)

@app.route('/project/<int:project_id>/settings')
@login_required
def project_settings(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id:
        flash('You do not have permission to access project settings')
        return redirect(url_for('project_detail', project_id=project_id))
    collaborators = ProjectCollaborator.query.filter_by(project_id=project_id).all()
    return render_template_string('''
        <h1>Project Settings: {{ project.name }}</h1>
        <h2>Collaborators</h2>
        <ul>
        {% for collaborator in collaborators %}
            <li>
                {{ collaborator.user.username }} ({{ collaborator.permission }})
                <a href="{{ url_for('remove_collaborator', project_id=project.id, collaborator_id=collaborator.id) }}">Remove</a>
            </li>
        {% endfor %}
        </ul>
        <h3>Add Collaborator</h3>
        <form action="{{ url_for('add_collaborator', project_id=project.id) }}" method="post">
            <input type="text" name="username" placeholder="Username" required>
            <select name="permission">
                <option value="read">Read</option>
                <option value="write">Write</option>
                <option value="admin">Admin</option>
            </select>
            <input type="submit" value="Add Collaborator">
        </form>
        <h2>Version History</h2>
        <ul>
        {% for version in project.versions %}
            <li>
                Version {{ version.version_number }} 
                ({{ version.created_at.strftime('%Y-%m-%d %H:%M:%S') }})
                <a href="{{ url_for('view_version', project_id=project.id, version_id=version.id) }}">View</a>
            </li>
        {% endfor %}
        </ul>
        <a href="{{ url_for('project_detail', project_id=project.id) }}">Back to Project</a>
    ''', project=project, collaborators=collaborators)

@app.route('/project/<int:project_id>/add_collaborator', methods=['POST'])
@login_required
def add_collaborator(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id:
        flash('You do not have permission to add collaborators')
        return redirect(url_for('project_settings', project_id=project_id))
    username = request.form.get('username')
    permission = request.form.get('permission')
    user = User.query.filter_by(username=username).first()
    if not user:
        flash('User not found')
    elif ProjectCollaborator.query.filter_by(project_id=project_id, user_id=user.id).first():
        flash('User is already a collaborator')
    else:
        collaborator = ProjectCollaborator(project_id=project_id, user_id=user.id, permission=permission)
        db.session.add(collaborator)
        db.session.commit()
        flash('Collaborator added successfully')
    return redirect(url_for('project_settings', project_id=project_id))

@app.route('/project/<int:project_id>/remove_collaborator/<int:collaborator_id>')
@login_required
def remove_collaborator(project_id, collaborator_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id:
        flash('You do not have permission to remove collaborators')
        return redirect(url_for('project_settings', project_id=project_id))
    collaborator = ProjectCollaborator.query.get_or_404(collaborator_id)
    db.session.delete(collaborator)
    db.session.commit()
    flash('Collaborator removed successfully')
    return redirect(url_for('project_settings', project_id=project_id))

@app.route('/project/<int:project_id>/version/<int:version_id>')
@login_required
def view_version(project_id, version_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not project.collaborators.filter_by(user_id=current_user.id).first():
        flash('You do not have permission to view this project version')
        return redirect(url_for('dashboard'))
    version = ProjectVersion.query.get_or_404(version_id)
    return render_template_string('''
        <h1>{{ project.name }} - Version {{ version.version_number }}</h1>
        <p>Created at: {{ version.created_at.strftime('%Y-%m-%d %H:%M:%S') }}</p>
        <h2>Changes</h2>
        <pre>{{ version.changes }}</pre>
        <a href="{{ url_for('project_settings', project_id=project.id) }}">Back to Project Settings</a>
    ''', project=project, version=version)

@app.route('/project/<int:project_id>/generate', methods=['GET', 'POST'])
@login_required
def generate_app(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not project.collaborators.filter_by(user_id=current_user.id, permission='write').first():
        flash('You do not have permission to generate app for this project')
        return redirect(url_for('project_detail', project_id=project_id))
    if request.method == 'POST':
        user_input = request.form.get('user_input')
        thread = Thread(target=run_main_loop, args=(user_input, project_id))
        thread.start()
        return redirect(url_for('view_progress', project_id=project_id))
    return render_template_string('''
        <h1>Generate App for {{ project.name }}</h1>
        <form method="post">
            <textarea name="user_input" rows="10" cols="80" placeholder="Describe the app you want to create..."></textarea><br>
            <input type="submit" value="Generate App">
        </form>
        <a href="{{ url_for('project_detail', project_id=project.id) }}">Back to Project</a>
    ''', project=project)

@app.route('/project/<int:project_id>/progress')
@login_required
def view_progress(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not project.collaborators.filter_by(user_id=current_user.id).first():
        flash('You do not have permission to view this project\'s progress')
        return redirect(url_for('dashboard'))
    return render_template_string('''
        <h1>Generation Progress for {{ project.name }}</h1>
        <pre id="progress">{{ progress_output }}</pre>
        <script>
            setInterval(function() {
                fetch('/progress')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('progress').innerHTML = data.output;
                    if (data.completed) {
                        document.getElementById('refresh-btn').style.display = 'block';
                    }
                });
            }, 2000);
        </script>
        <button id="refresh-btn" style="display:none;" onclick="location.href='{{ url_for('project_detail', project_id=project.id) }}';">View Generated App</button>
    ''', project=project, progress_output=progress["output"])

@app.route('/progress')
def get_progress():
    return jsonify(progress)

# Available functions for the LLM
available_functions = {
    "create_directory": create_directory,
    "create_file": create_file,
    "update_file": update_file,
    "fetch_code": fetch_code,
    "task_completed": task_completed
}

# Define the tools for function calling
tools = [
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Creates a new directory at the specified path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path to create."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Creates or updates a file at the specified path with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to create or update."
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_file",
            "description": "Updates an existing file at the specified path with the new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to update."
                    },
                    "content": {
                        "type": "string",
                        "description": "The new content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_code",
            "description": "Retrieves the code from the specified file path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The file path to fetch the code from."
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_completed",
            "description": "Indicates that the assistant has completed the task.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]

def run_main_loop(user_input, project_id):
    project = Project.query.get(project_id)
    project_path = os.path.join(PROJECTS_DIR, str(project_id))
    
    history_dict = {
        "iterations": []
    }

    if not supports_function_calling(MODEL_NAME):
        progress["status"] = "error"
        progress["output"] = "Model does not support function calling."
        progress["completed"] = True
        return "Model does not support function calling."

    max_iterations = progress["max_iterations"]
    iteration = 0

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Flask developer tasked with building a complete, production-ready Flask application based on the user's description. "
                "Before coding, carefully plan out all the files, routes, templates, and static assets needed. "
                f"Follow these steps:\n"
                "1. **Understand the Requirements**: Analyze the user's input to fully understand the application's functionality and features.\n"
                "2. **Plan the Application Structure**: List all the routes, templates, and static files that need to be created. Consider how they interact.\n"
                "3. **Implement Step by Step**: For each component, use the provided tools to create directories, files, and write code. Ensure each step is thoroughly completed before moving on.\n"
                "4. **Review and Refine**: Use `fetch_code` to review the code you've written. Update files if necessary using `update_file`.\n"
                "5. **Ensure Completeness**: Do not leave any placeholders or incomplete code. All functions, routes, and templates must be fully implemented and ready for production.\n"
                "6. **Finalize**: Once everything is complete and thoroughly tested, call `task_completed()` to finish.\n\n"
                "Constraints and Notes:\n"
                f"- The application files must be structured within the project directory: {project_path}\n"
                "- Routes should be modular and placed inside a 'routes' subdirectory as separate Python files.\n"
                "- The `index.html` served from the 'templates' subdirectory is the entry point of the app. Update it appropriately if additional templates are created.\n"
                "- Do not use placeholders like 'Content goes here'. All code should be complete and functional.\n"
                "- Do not ask the user for additional input; infer any necessary details to complete the application.\n"
                "- Ensure all routes are properly linked and that templates include necessary CSS and JS files.\n"
                "- Handle any errors internally and attempt to resolve them before proceeding.\n\n"
                "Available Tools:\n"
                "- `create_directory(path)`: Create a new directory.\n"
                "- `create_file(path, content)`: Create or overwrite a file with content.\n"
                "- `update_file(path, content)`: Update an existing file with new content.\n"
                "- `fetch_code(file_path)`: Retrieve the code from a file for review.\n"
                "- `task_completed()`: Call this when the application is fully built and ready.\n\n"
                "Remember to think carefully at each step, ensuring the application is complete, functional, and meets the user's requirements."
            )
        },
        {"role": "user", "content": user_input},
        {"role": "system", "content": f"History:\n{json.dumps(history_dict, indent=2)}"}
    ]

    output = ""

    while iteration < max_iterations:
        progress["iteration"] = iteration + 1
        current_iteration = {
            "iteration": iteration + 1,
            "actions": [],
            "llm_responses": [],
            "tool_results": [],
            "errors": []
        }
        history_dict['iterations'].append(current_iteration)

        try:
            response = completion(
                model=MODEL_NAME,
                messages=messages,
                tools=tools,
                tool_choice="auto"
            )

            if not response.choices[0].message:
                error = response.get('error', 'Unknown error')
                current_iteration['errors'].append({'action': 'llm_completion', 'error': error})
                log_to_file(history_dict)
                sleep(5)
                iteration += 1
                continue

            response_message = response.choices[0].message
            content = response_message.content or ""
            current_iteration['llm_responses'].append(content)

            output += f"\n<h2>Iteration {iteration + 1}:</h2>\n"

            tool_calls = response_message.tool_calls

            if tool_calls:
                output += "<strong>Tool Call:</strong>\n<p>" + content + "</p>\n"
                messages.append(response_message)

                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    function_to_call = available_functions.get(function_name)

                    if not function_to_call:
                        error_message = f"Tool '{function_name}' is not available."
                        current_iteration['errors'].append({
                            'action': f'tool_call_{function_name}',
                            'error': error_message,
                            'traceback': 'No traceback available.'
                        })
                        continue

                    try:
                        function_args = json.loads(tool_call.function.arguments)
                        
                        # Adjust file paths to be within the project directory
                        if 'path' in function_args:
                            function_args['path'] = os.path.join(project_path, function_args['path'])
                        elif 'file_path' in function_args:
                            function_args['file_path'] = os.path.join(project_path, function_args['file_path'])

                        function_response = function_to_call(**function_args)

                        current_iteration['tool_results'].append({
                            'tool': function_name,
                            'result': function_response
                        })

                        output += f"<strong>Tool Result ({function_name}):</strong>\n<p>{function_response}</p>\n"

                        messages.append(
                            {"tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": function_response}
                        )

                        if function_name == "task_completed":
                            progress["status"] = "completed"
                            progress["completed"] = True
                            output += "\n<h2>COMPLETE</h2>\n"
                            progress["output"] = output
                            log_to_file(history_dict)
                            
                            # Create a new version for the project
                            new_version = ProjectVersion(
                                project_id=project.id,
                                version_number=project.versions.count() + 1,
                                changes=f"AI-generated app based on input: {user_input[:100]}..."
                            )
                            db.session.add(new_version)
                            db.session.commit()
                            
                            return output

                    except Exception as tool_error:
                        error_message = f"Error executing {function_name}: {tool_error}"
                        current_iteration['errors'].append({
                            'action': f'tool_call_{function_name}',
                            'error': error_message,
                            'traceback': traceback.format_exc()
                        })

                second_response = completion(
                    model=MODEL_NAME,
                    messages=messages
                )
                if second_response.choices and second_response.choices[0].message:
                    second_response_message = second_response.choices[0].message
                    content = second_response_message.content or ""
                    current_iteration['llm_responses'].append(content)
                    output += "<strong>LLM Response:</strong>\n<p>" + content + "</p>\n"
                    messages.append(second_response_message)
                else:
                    error = second_response.get('error', 'Unknown error in second LLM response.')
                    current_iteration['errors'].append({'action': 'second_llm_completion', 'error': error})

            else:
                output += "<strong>LLM Response:</strong>\n<p>" + content + "</p>\n"
                messages.append(response_message)

            progress["output"] = output

        except Exception as e:
            error = str(e)
            current_iteration['errors'].append({
                'action': 'main_loop',
                'error': error,
                'traceback': traceback.format_exc()
            })

        iteration += 1
        log_to_file(history_dict)
        sleep(2)

    if iteration >= max_iterations:
        progress["status"] = "completed"

    progress["completed"] = True
    progress["status"] = "completed"

    return output

# Initialize the database
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
