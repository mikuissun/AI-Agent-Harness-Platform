class TaskNotFoundError(Exception):
    def __init__(self, task_id: int) -> None:
        super().__init__(f"Task {task_id} not found")
