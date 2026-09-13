PAYLOAD = {
    "title": "Fix failing tests",
    "instruction": "Analyze and fix the failing pytest tests.",
}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_task(client):
    response = client.post("/api/tasks", json=PAYLOAD)
    assert response.status_code == 201
    task = response.json()
    assert task["id"] > 0
    assert task["title"] == PAYLOAD["title"]
    assert task["instruction"] == PAYLOAD["instruction"]
    assert task["status"] == "PENDING"
    assert task["created_at"]
    assert task["updated_at"]


def test_get_task(client):
    created = client.post("/api/tasks", json=PAYLOAD).json()
    response = client.get(f"/api/tasks/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_list_tasks(client):
    empty = client.get("/api/tasks")
    assert empty.status_code == 200
    assert empty.json() == []
    first = client.post("/api/tasks", json=PAYLOAD).json()
    second = client.post("/api/tasks", json={**PAYLOAD, "title": "Second task"}).json()
    response = client.get("/api/tasks")
    assert response.status_code == 200
    assert response.json() == [first, second]


def test_task_not_found(client):
    response = client.get("/api/tasks/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Task 999 not found"}
