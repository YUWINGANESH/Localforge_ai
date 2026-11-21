from flask import Flask, request, jsonify, abort
import sqlite3

app = Flask(__name__)

# Initialize the database and create the 'tasks' table
conn = sqlite3.connect('tasks.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS tasks
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT)''')
conn.commit()

@app.route('/', methods=['GET'])
def index():
    return app.send_static_file('index.html')

@app.route('/tasks', methods=['GET'])
def get_tasks():
    cursor.execute('SELECT * FROM tasks')
    tasks = [{'id': row[0], 'title': row[1], 'description': row[2]} for row in cursor.fetchall()]
    return jsonify(tasks)

@app.route('/tasks', methods=['POST'])
def create_task():
    data = request.get_json()
    title = data.get('title')
    desc = data.get('description')
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    cursor.execute('INSERT INTO tasks (title, description) VALUES (?, ?)', (title, desc))
    task_id = cursor.lastrowid
    conn.commit()
    return jsonify({'id': task_id, 'title': title, 'description': desc}), 201

@app.route('/tasks/<int:id>', methods=['GET'])
def get_task(id):
    cursor.execute('SELECT * FROM tasks WHERE id = ?', (id,))
    task = cursor.fetchone()
    if task:
        return jsonify({'id': task[0], 'title': task[1], 'description': task[2]})
    else:
        return jsonify({'error': 'Task not found'}), 404

@app.route('/tasks/<int:id>', methods=['PUT'])
def update_task(id):
    data = request.get_json()
    title = data.get('title')
    desc = data.get('description')
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    cursor.execute('UPDATE tasks SET title = ?, description = ? WHERE id = ?', (title, desc, id))
    if cursor.rowcount == 0:
        return jsonify({'error': 'Task not found'}), 404
    conn.commit()
    return jsonify({'id': id, 'title': title, 'description': desc}), 200

@app.route('/tasks/<int:id>', methods=['DELETE'])
def delete_task(id):
    cursor.execute('DELETE FROM tasks WHERE id = ?', (id,))
    if cursor.rowcount == 0:
        return jsonify({'error': 'Task not found'}), 404
    conn.commit()
    return jsonify({'message': 'Task deleted successfully'}), 200

if __name__ == '__main__':
    app.run(debug=True)
