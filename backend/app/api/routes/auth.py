"""Public session and OIDC browser routes; no redirect destination is user supplied."""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.schemas import AuthSession
from app.auth.service import COOKIE_NAME, LOGIN_COOKIE_NAME, AuthRejected

router = APIRouter()


def _service(request: Request):
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="authentication is not configured")
    return service


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/session", response_model=AuthSession, response_model_exclude_unset=True)
def session(request: Request, response: Response):
    service = _service(request)
    principal = service.resolve(
        request.cookies.get(COOKIE_NAME),
        client_host=request.client.host if request.client else None,
    )
    body = {"mode": service.mode, "authenticated": principal is not None}
    if principal:
        body.update({"workspaceKey": principal.workspace_key,
                     "displayName": principal.display_name, "email": principal.email})
    _no_store(response)
    return body


@router.get("/api/auth/login", response_class=RedirectResponse, status_code=303, responses={
    303: {"description": "Redirect to the identity provider; sets the login-state cookie."}})
async def login(request: Request):
    service = _service(request)
    try:
        start = await service.begin_login()
    except AuthRejected as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = RedirectResponse(start.authorization_url, status_code=303)
    response.set_cookie(
        LOGIN_COOKIE_NAME, start.state, max_age=start.expires_in,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return _no_store(response)


def _callback_error(status_code: int, detail: str) -> Response:
    response = JSONResponse({"detail": detail}, status_code=status_code)
    response.delete_cookie(LOGIN_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    return _no_store(response)


@router.get("/api/auth/callback", response_class=RedirectResponse, status_code=303, responses={
    303: {"description": "Login complete: sets the session cookie and redirects home."},
    400: {"description": "The login could not be completed; the login-state cookie is cleared."}})
async def callback(request: Request):
    service = _service(request)
    states = request.query_params.getlist("state")
    codes = request.query_params.getlist("code")
    errors = request.query_params.getlist("error")
    state = request.query_params.get("state", "")
    browser_state = request.cookies.get(LOGIN_COOKIE_NAME)
    error = request.query_params.get("error")
    code = request.query_params.get("code", "")
    if (len(states) != 1 or len(codes) > 1 or len(errors) > 1
            or not service.state_matches(browser_state, state)):
        return _callback_error(400, "login could not be completed")
    if error:
        # Consume state even for provider errors so an old transaction cannot replay.
        try:
            await service.consume_failed_login(state)
        except (AttributeError, AuthRejected):
            pass
        return _callback_error(400, "identity provider login was declined")
    try:
        token = await service.complete_login(state, code)
    except AuthRejected:
        return _callback_error(400, "login could not be completed")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME, token, max_age=service.oidc.session_ttl_seconds,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    response.delete_cookie(LOGIN_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    return _no_store(response)


@router.post("/api/auth/logout", response_class=Response, status_code=204, responses={
    204: {"description": "Signed out: the session cookie is cleared."},
    403: {"description": "A same-origin request is required."}})
def logout(request: Request):
    service = _service(request)
    if service.mode == "oidc":
        origin = request.headers.get("origin")
        callback = urlsplit(service.oidc.redirect_uri)
        expected_origin = f"{callback.scheme}://{callback.netloc}"
        if not origin or origin != expected_origin:
            raise HTTPException(status_code=403, detail="same-origin logout is required")
    token = request.cookies.get(COOKIE_NAME)
    service.logout(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    return _no_store(response)
