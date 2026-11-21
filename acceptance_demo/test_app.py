import pytest
from app import app, conn

@pytest.fixture
def client():
    conn.execute('DELETE FROM tasks')
    conn.commit()
    with app.test_client() as client:
        yield client

def test_get_tasks(client):
    response = client.get('/tasks')
    assert response.status_code == 200
    assert response.json == []

def test_create_task(client):
    response = client.post('/tasks', json={'title': 'Test Task', 'description': 'Test Description'}, content_type='application/json')
    assert response.status_code == 201
    assert response.json['title'] == 'Test Task'
    assert response.json['description'] == 'Test Description'

def test_get_task(client):
    response = client.post('/tasks', json={'title': 'Test Task', 'description': 'Test Description'}, content_type='application/json')
    task_id = response.json['id']
    response = client.get(f'/tasks/{task_id}')
    assert response.status_code == 200
    assert response.json['id'] == task_id
    assert response.json['title'] == 'Test Task'
    assert response.json['description'] == 'Test Description'

def test_update_task(client):
    response = client.post('/tasks', json={'title': 'Test Task', 'description': 'Test Description'}, content_type='application/json')
    task_id = response.json['id']
    response = client.put(f'/tasks/{task_id}', json={'title': 'Updated Task', 'description': 'Updated Description'}, content_type='application/json')
    assert response.status_code == 200
    assert response.json['id'] == task_id
    assert response.json['title'] == 'Updated Task'
    assert response.json['description'] == 'Updated Description'

def test_delete_task(client):
    response = client.post('/tasks', json={'title': 'Test Task', 'description': 'Test Description'}, content_type='application/json')
    task_id = response.json['id']
    response = client.delete(f'/tasks/{task_id}')
    assert response.status_code == 200
    assert response.json['message'] == 'Task deleted successfully'

def test_invalid_task(client):
    response = client.get('/tasks/999')
    assert response.status_code == 404
    assert response.json['error'] == 'Task not found'

def test_missing_title(client):
    response = client.post('/tasks', json={'description': 'Test Description'}, content_type='application/json')
    assert response.status_code == 400
    assert response.json['error'] == 'Title is required'
