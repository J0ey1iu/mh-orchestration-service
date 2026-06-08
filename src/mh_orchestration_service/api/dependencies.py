from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from mh_orchestration_service.api.auth import get_user_id as _verify_user


async def get_current_user(request: Request) -> str:
    """FastAPI Depends-compatible: returns authenticated user ID from the request."""
    return await _verify_user(request)


async def get_current_permissions(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[str]:
    """FastAPI Depends-compatible: returns permission list for the current user."""
    adapters = request.app.state.adapters
    return await adapters.permission_checker.get_permissions(user_id)


async def verify_m2m_request(request: Request) -> str:
    """FastAPI Depends-compatible: authenticates M2M callers (tools, agent runs)."""
    adapters = request.app.state.adapters
    app_id = await adapters.m2m_auth_provider.authenticate(request)
    if app_id is None:
        raise HTTPException(status_code=401, detail="M2M authentication required")
    return app_id


def require_permission(permission: str):
    """FastAPI Depends factory: require a specific permission for the current user.

    Usage::

        @router.get("/scenarios")
        async def list_scenarios(
            user_id: str = Depends(require_permission("manage:scene:*")),
        ):
            ...
    """

    async def _check(
        request: Request,
        user_id: str = Depends(get_current_user),
    ) -> str:
        adapters = request.app.state.adapters
        ok = await adapters.permission_checker.check(user_id, permission)
        if not ok:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {permission}",
            )
        return user_id

    return _check


async def resolve_request_identity(request: Request) -> str:
    """Try user auth first, fall back to M2M auth.

    Returns the authenticated identity (user_id or app_id).
    Raises 401 only if both user auth and M2M auth fail.
    """
    try:
        return await _verify_user(request)
    except HTTPException:
        pass
    adapters = request.app.state.adapters
    app_id = await adapters.m2m_auth_provider.authenticate(request)
    if app_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return app_id


async def resolve_request_permissions(
    request: Request,
    identity: str = Depends(resolve_request_identity),
) -> list[str]:
    """Get permissions for the resolved identity (user or M2M app)."""
    adapters = request.app.state.adapters
    return await adapters.permission_checker.get_permissions(identity)
