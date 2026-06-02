from __future__ import annotations

from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

# ── 认证路由（始终注册） ──────────────────────────
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ── 开发用 Mock SSO 页面（始终注册，生产无害） ──
dev_router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


def _user_info(identity, permissions: list[str] | None = None) -> dict:
    roles = [{"name": r} for r in getattr(identity, "roles", [])]
    if not roles:
        roles = [{"name": identity.user_id}]
    result: dict = {
        "id": identity.user_id,
        "username": identity.username or identity.user_id,
        "is_active": True,
        "roles": roles,
    }
    if permissions is not None:
        result["permissions"] = permissions
    return result


@auth_router.get("/me")
async def me(request: Request):
    adapters = request.app.state.adapters
    identity = await adapters.token_verifier.verify(request)
    if identity is None or not identity.user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    perms = await adapters.permission_checker.get_permissions(identity.user_id)
    return _user_info(identity, permissions=perms)


_DEV_LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dev SSO Login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0e0e10; color: #e4e4e7; display: flex; align-items: center;
         justify-content: center; min-height: 100vh; }
  .card { background: #1a1a1e; border: 1px solid #2a2a30; border-radius: 12px;
          padding: 40px 36px; width: 360px; max-width: 90vw; }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 24px; color: #e4e4e7; }
  .btn { display: block; width: 100%; background: #27272a; border: 1px solid #3f3f46;
         border-radius: 6px; padding: 10px 12px; font-size: 14px; color: #e4e4e7;
         cursor: pointer; font-family: inherit; margin-bottom: 8px;
         transition: border-color .15s; text-align: center; text-decoration: none; }
  .btn:hover { border-color: #6366f1; }
  input { display: block; width: 100%; background: #27272a; border: 1px solid #3f3f46;
          border-radius: 6px; padding: 10px 12px; font-size: 14px; color: #e4e4e7;
          font-family: inherit; margin-bottom: 12px; outline: none; }
  input:focus { border-color: #6366f1; }
  .label { font-size: 12px; color: #a1a1aa; margin-bottom: 8px;
           text-transform: uppercase; letter-spacing: .05em; }
</style>
</head>
<body>
<div class="card">
  <h1>Dev SSO Login</h1>
  <form method="POST">
    <input type="hidden" name="redirect" value="{redirect}">
    <div class="label">User ID</div>
    <input type="text" name="user_id" value="1" placeholder="Enter user ID">
    <button class="btn" type="submit" name="role" value="1">Login as Admin</button>
    <button class="btn" type="submit" name="role" value="2">Login as Member</button>
    <button class="btn" type="submit" name="role" value="3">Login as User</button>
    <button class="btn" type="submit" name="role" value="4">Login as Scene Manager</button>
    <button class="btn" type="submit" name="role" value="5">Login as Agent Manager</button>
    <button class="btn" type="submit" name="role" value="6">Login as Tool Manager</button>
  </form>
</div>
</body>
</html>"""


def _parse_redirect(redirect: str) -> tuple[str, str]:
    """Return (origin, path+query) from a full frontend URL or a path-only redirect."""
    if redirect.startswith("http://") or redirect.startswith("https://"):
        parsed = urlparse(redirect)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        return origin, path
    return "", redirect or "/"


@dev_router.get("/login", response_class=HTMLResponse)
async def dev_login_page(request: Request, redirect: str = "/"):
    origin, _ = _parse_redirect(redirect)
    if not origin:
        host = request.headers.get("x-forwarded-host") or request.headers["host"]
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        origin = f"{proto}://{host}"
    return _DEV_LOGIN_HTML.replace("{redirect}", redirect or "/")


@dev_router.post("/login")
async def dev_login_submit(request: Request):
    form = await request.form()
    raw_user_id = form.get("user_id")
    raw_role = form.get("role")
    raw_redirect = form.get("redirect")
    user_id = str(raw_user_id or raw_role or "1")
    role = str(raw_role or "") or user_id
    redirect_value = str(raw_redirect or "/")
    uid = user_id if role == user_id else role

    origin, path = _parse_redirect(redirect_value)
    target = f"{origin}{path}"

    response = RedirectResponse(url=target, status_code=302)
    response.set_cookie(
        key="x-user-id",
        value=uid,
        httponly=True,
        samesite="lax",
        max_age=3600,
        path="/",
    )
    return response


@dev_router.get("/logout")
async def dev_logout(request: Request, redirect: str = "/"):
    origin, _ = _parse_redirect(redirect)
    if not origin:
        host = request.headers.get("x-forwarded-host") or request.headers["host"]
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        origin = f"{proto}://{host}"
    login_url = str(request.url_for("dev_login_page"))
    target = f"{login_url}?{urlencode({'redirect': origin})}"

    response = RedirectResponse(url=target, status_code=302)
    response.set_cookie(key="x-user-id", value="", httponly=True, max_age=0, path="/")
    return response
