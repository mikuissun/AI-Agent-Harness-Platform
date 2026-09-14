from fastapi import APIRouter, Request

router = APIRouter(tags=["runs"])


@router.post("/api/tasks/{task_id}/run")
async def run_task(task_id: int, request: Request):
    return await request.app.state.runs.run(task_id)


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return request.app.state.runs.get(run_id)


@router.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, request: Request):
    return await request.app.state.runs.resume(run_id)
