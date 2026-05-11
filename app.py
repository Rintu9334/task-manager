import os
import sqlite3
import pandas as pd
import numpy as np
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'taskmanager_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

DATABASE = 'tasks.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    # Tasks table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT DEFAULT 'Normal',
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

# Initialize DB
init_db()

# --- Auth Middleware ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route("/")
@login_required
def dashboard():
    return render_template("index.html", username=session.get('username'), active_tab='dashboard')

@app.route("/tasks_page")
@login_required
def tasks_page():
    return render_template("index.html", username=session.get('username'), active_tab='tasks')

@app.route("/analytics_page")
@login_required
def analytics_page():
    return render_template("index.html", username=session.get('username'), active_tab='analytics')

@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        hashed_pw = generate_password_hash(password)
        try:
            conn = get_db_connection()
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_pw))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return "Username already exists!", 400
    return render_template("register.html")

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        return "Invalid credentials!", 401
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- Production API Endpoints ---

@app.route("/add_task", methods=['POST'])
@login_required
def add_task():
    try:
        data = request.get_json() if request.is_json else request.form
        title = data.get('title')
        description = data.get('description', '')
        priority = data.get('priority', 'Normal')
        user_id = session['user_id']
        
        if not title:
            return jsonify({"error": "Title is required"}), 400
            
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO tasks (user_id, title, description, priority, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, title, description, priority, 'Pending'))
        conn.commit()
        conn.close()
        
        socketio.emit('update_tasks')
        return jsonify({"message": "Task added"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_tasks", methods=['GET'])
@login_required
def get_tasks():
    try:
        user_id = session['user_id']
        status = request.args.get('status')
        search = request.args.get('search')
        
        conn = get_db_connection()
        query = "SELECT * FROM tasks WHERE user_id = ?"
        params = [user_id]
        
        if status and status != 'All':
            query += " AND status = ?"
            params.append(status)
        if search:
            query += " AND title LIKE ?"
            params.append(f"%{search}%")
            
        query += " ORDER BY id DESC"
        tasks = conn.execute(query, params).fetchall()
        conn.close()
        return jsonify([dict(t) for t in tasks]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/complete_task/<int:id>", methods=['POST'])
@login_required
def complete_task(id):
    try:
        user_id = session['user_id']
        conn = get_db_connection()
        conn.execute('UPDATE tasks SET status = ? WHERE id = ? AND user_id = ?', ('Completed', id, user_id))
        conn.commit()
        conn.close()
        socketio.emit('update_tasks')
        return jsonify({"message": "Task completed"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/delete_task/<int:id>", methods=['POST'])
@login_required
def delete_task(id):
    try:
        user_id = session['user_id']
        conn = get_db_connection()
        conn.execute('DELETE FROM tasks WHERE id = ? AND user_id = ?', (id, user_id))
        conn.commit()
        conn.close()
        socketio.emit('update_tasks')
        return jsonify({"message": "Task deleted"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analytics", methods=['GET'])
@login_required
def analytics():
    try:
        user_id = session['user_id']
        conn = get_db_connection()
        df = pd.read_sql_query("SELECT status FROM tasks WHERE user_id = ?", conn, params=(user_id,))
        conn.close()
        if df.empty:
            return jsonify({"total": 0, "pending": 0, "completed": 0, "efficiency": 0}), 200
        total = len(df)
        completed = int(np.sum(df['status'] == 'Completed'))
        pending = total - completed
        efficiency = round((completed / total) * 100, 1)
        return jsonify({"total": total, "pending": pending, "completed": completed, "efficiency": efficiency}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
