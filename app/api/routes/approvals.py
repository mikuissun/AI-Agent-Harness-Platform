from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("/{approval_id}")
def get_approval(approval_id: str, request: Request):
    return request.app.state.approvals.get(approval_id)


@router.post("/{approval_id}/approve")
async def approve(approval_id: str, request: Request):
    return await request.app.state.approvals.approve(approval_id)


@router.post("/{approval_id}/reject")
def reject(approval_id: str, request: Request):
    return request.app.state.approvals.reject(approval_id)
