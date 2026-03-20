"""Admin page and test trigger. Requires is_admin=1 in user_account."""
import asyncio
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.market_hours import is_india_market_open
from icici_breeze_backend.app.auth.context import (
    ACCESS_TOKEN_COOKIE,
    CREDENTIAL_FULL_SECRET_COOKIE,
    ICICI_BROKER_TOKEN_COOKIE,
    get_request_context,
    get_request_context_or_redirect,
    RequestContext,
)
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend

router = APIRouter()
breeze = processor()

# Output file for test results (relative to project root)
E2E_OUTPUT_FILE = "logs/last_e2e_output.txt"
E2E_STATUS_FILE = "logs/last_e2e_status.txt"
_test_process: asyncio.subprocess.Process | None = None
_harness_running = False


def get_user_is_admin(user_id: str) -> bool:
    """Check if user has is_admin=1 in user_account."""
    db_path = cfg.DATA_PATH + "db.sqlite3"
    if not os.path.isfile(db_path):
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT is_admin FROM user_account WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            return row is not None and row[0] == 1
    except sqlite3.OperationalError:
        # Column may not exist if migration not run
        return False


def get_common_template_vars(ctx: RequestContext) -> dict:
    """Return common template vars (e.g. is_admin, API usage) for navbar and shared UI. Theme is client-side."""
    from icici_breeze_backend.app.services.api_usage import get_usage_for_display
    base = {"is_admin": get_user_is_admin(ctx.user_id), "display_theme": "dark"}
    base.update(get_usage_for_display(ctx.user_id))
    return base


def require_admin(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
    """Dependency: require is_admin=1 in user_account."""
    if not get_user_is_admin(ctx.user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return ctx


@router.get("")
async def serve_admin(
    _: RequestContext = Depends(get_request_context_or_redirect),
    __: RequestContext = Depends(require_admin),
):
    return redirect_to_frontend("/admin")


@router.get("/data")
async def admin_data(
    ctx: RequestContext = Depends(require_admin),
):
    user_id = ctx.user_id
    customer = breeze.get_customer_details(user_id)
    if customer is None:
        customer = {"Status": 400, "Error": "Not available", "Success": {"idirect_user_name": "—"}}
    elif customer.get("Status") != 200:
        customer = {"Status": 400, "Error": customer.get("Error", ""), "Success": {"idirect_user_name": "—"}}

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin.get("Status") != 200:
        margin = {
            "Status": 400,
            "Error": margin.get("Error", ""),
            "Success": {
                "last_refresh": "—",
                "actual_margin_ute": 0,
                "cash_limit": 0,
                "actual_margin_avl": 0,
                "target_margin_free": 0,
                "limits": 0,
            },
        }
    extra = get_common_template_vars(ctx)
    return JSONResponse(
        {
            "customer": customer,
            "margin": margin,
            "is_admin": True,
            **extra,
        }
    )


@router.post("/tests/run")
async def run_tests(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    _: RequestContext = Depends(require_admin),
):
    """Start tests in background. Suite: api, playwright, or full (default: playwright)."""
    global _test_process, _harness_running
    if _harness_running or (_test_process is not None and _test_process.returncode is None):
        return JSONResponse(status_code=409, content={"status": "running", "detail": "Tests already in progress"})

    suite = request.query_params.get("suite", "playwright")
    if suite not in ("api", "playwright", "full"):
        suite = "playwright"

    override = (
        request.query_params.get("override_market_hours") == "1"
        or os.environ.get("INTEGRATION_SKIP_MARKET_HOURS") == "1"
    )
    run_mode = request.query_params.get("run_mode", "plain")

    # All tests restricted to non-market hours unless override (same as Playwright)
    if is_india_market_open() and not override:
        raise HTTPException(
            status_code=403,
            detail="Integration tests blocked during market hours (9:15 AM - 3:30 PM IST). "
            "Use override_market_hours=1 or set INTEGRATION_SKIP_MARKET_HOURS=1 to override.",
        )

    # Margin and session apply only to playwright (or full's playwright phase)
    needs_playwright = suite in ("playwright", "full")
    if needs_playwright:
        E2E_MIN_MARGIN_LACS = 30
        E2E_MIN_MARGIN_RUPEES = E2E_MIN_MARGIN_LACS * 100_000
        margin = breeze.get_margin_situation(ctx.user_id, target_margin_ute=100)
        if margin.get("Status") != 200:
            raise HTTPException(
                status_code=400,
                detail="Unable to fetch margin. Cannot start E2E tests.",
            )
        actual_margin_avl = margin.get("Success", {}).get("actual_margin_avl", 0) or 0
        if actual_margin_avl < E2E_MIN_MARGIN_RUPEES:
            lacs = actual_margin_avl / 100_000
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient margin for testing. Available: ₹{lacs:.1f}L. Minimum required: ₹{E2E_MIN_MARGIN_LACS}L.",
            )

    # Extract session from logged-in user (user logs in -> Admin -> Run tests)
    access_token = (request.cookies.get(ACCESS_TOKEN_COOKIE) or (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()).strip()
    broker_token = (ctx.broker_token or "").strip()
    full_secret_cookie = (request.cookies.get(CREDENTIAL_FULL_SECRET_COOKIE) or "").strip()
    if needs_playwright and (not access_token or not broker_token):
        raise HTTPException(status_code=400, detail="Session required; log in and try again")

    from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
    handler = JWTHandler(secret_key=cfg.JWT_SECRET or "", access_token_expire_minutes=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = handler.create_access_token(ctx.user_id, ctx.username, google_id=ctx.google_id, roles=ctx.roles)

    root = Path(__file__).resolve().parent.parent.parent.parent
    output_path = root / E2E_OUTPUT_FILE
    status_path = root / E2E_STATUS_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["INTEGRATION_SKIP_MARKET_HOURS"] = "1" if override else "0"
    base_url = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
    env["E2E_BASE_URL"] = base_url
    if needs_playwright:
        env["E2E_PRE_AUTH_ACCESS_TOKEN"] = access_token
        env["E2E_PRE_AUTH_BROKER_TOKEN"] = broker_token
        if full_secret_cookie:
            env["E2E_PRE_AUTH_FULL_SECRET"] = full_secret_cookie
        if os.environ.get("E2E_RATE_LIMIT_BYPASS_SECRET"):
            env["E2E_RATE_LIMIT_BYPASS_SECRET"] = os.environ["E2E_RATE_LIMIT_BYPASS_SECRET"]

    python_bin = root / "fapi_venv" / "bin" / "python"
    if not python_bin.exists():
        python_bin = Path("python3")

    # Playwright suite: run all e2e tests except those that exercise Google OAuth
    # (marked with @pytest.mark.google_oauth). This ensures Admin-triggered runs
    # never hit the Google consent UI, even indirectly.
    pytest_playwright_args = ["tests/e2e/", "--browser", "webkit", "--headed", "-v", "--durations=0", "-m", "not google_oauth"]
    if run_mode == "trace":
        pytest_playwright_args.extend(["--tracing", "on"])
    elif run_mode == "video":
        pytest_playwright_args.extend(["--video", "on"])

    async def _run():
        global _test_process, _harness_running
        _harness_running = True
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("Tests started...\n")
        with open(status_path, "w", encoding="utf-8") as f:
            f.write("running")
        try:
            if suite == "api":
                _test_process = await asyncio.create_subprocess_exec(
                    str(python_bin), "-m", "pytest", "tests/functional/", "-v", "--durations=0",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(root),
                    env=env,
                )
                raw = await _test_process.communicate()
                stdout = raw[0] if isinstance(raw, (tuple, list)) and len(raw) >= 1 else b""
                out_text = (stdout or b"").decode("utf-8", errors="replace")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(out_text)
                with open(status_path, "w", encoding="utf-8") as f:
                    f.write("completed" if _test_process.returncode == 0 else "failed")
            elif suite == "playwright":
                _test_process = await asyncio.create_subprocess_exec(
                    str(python_bin), "-m", "pytest", *pytest_playwright_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(root),
                    env=env,
                )
                raw = await _test_process.communicate()
                stdout = raw[0] if isinstance(raw, (tuple, list)) and len(raw) >= 1 else b""
                out_text = (stdout or b"").decode("utf-8", errors="replace")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(out_text)
                with open(status_path, "w", encoding="utf-8") as f:
                    f.write("completed" if _test_process.returncode == 0 else "failed")
            else:  # full
                try:
                    # Phase 1: API
                    proc1 = await asyncio.create_subprocess_exec(
                        str(python_bin), "-m", "pytest", "tests/functional/", "-v", "--durations=0",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=str(root),
                        env=env,
                    )
                    raw1 = await proc1.communicate()
                    stdout1 = raw1[0] if isinstance(raw1, (tuple, list)) and len(raw1) >= 1 else b""
                    out1 = (stdout1 or b"").decode("utf-8", errors="replace")
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(out1)
                    if proc1.returncode != 0:
                        with open(status_path, "w", encoding="utf-8") as f:
                            f.write("failed")
                        exit_path = root / (E2E_STATUS_FILE + ".exit")
                        with open(exit_path, "w", encoding="utf-8") as f:
                            f.write(str(proc1.returncode or -1))
                        return
                    # Phase 2: Playwright
                    with open(output_path, "a", encoding="utf-8") as f:
                        f.write("\n\n=== PLAYWRIGHT SUITE ===\n\n")
                    proc2 = await asyncio.create_subprocess_exec(
                        str(python_bin), "-m", "pytest", *pytest_playwright_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=str(root),
                        env=env,
                    )
                    raw2 = await proc2.communicate()
                    stdout2 = raw2[0] if isinstance(raw2, (tuple, list)) and len(raw2) >= 1 else b""
                    out2 = (stdout2 or b"").decode("utf-8", errors="replace")
                    with open(output_path, "a", encoding="utf-8") as f:
                        f.write(out2)
                    with open(status_path, "w", encoding="utf-8") as f:
                        f.write("completed" if proc2.returncode == 0 else "failed")
                    exit_path = root / (E2E_STATUS_FILE + ".exit")
                    with open(exit_path, "w", encoding="utf-8") as f:
                        f.write(str(proc2.returncode or -1))
                    _test_process = proc2  # so fall-through exit_path write has a valid process
                finally:
                    pass  # _harness_running cleared in outer finally
            exit_path = root / (E2E_STATUS_FILE + ".exit")
            with open(exit_path, "w", encoding="utf-8") as f:
                f.write(str(_test_process.returncode or -1))
        except Exception as e:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"Error: {e}\n")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write("failed")
            exit_path = root / (E2E_STATUS_FILE + ".exit")
            with open(exit_path, "w", encoding="utf-8") as f:
                f.write("-1")
        finally:
            _test_process = None
            _harness_running = False

    asyncio.create_task(_run())
    response = JSONResponse(content={"job_id": "e2e", "status": "started", "suite": suite})
    if needs_playwright:
        response.set_cookie(
            key=ACCESS_TOKEN_COOKIE,
            value=access_token,
            httponly=True,
            secure=cfg.COOKIE_SECURE,
            samesite="lax",
            max_age=60 * 60,
            path="/",
        )
    return response


@router.get("/tests/status")
async def get_tests_status(
    _: RequestContext = Depends(require_admin),
):
    """Return last test run status and output."""
    root = Path(__file__).resolve().parent.parent.parent.parent
    output_path = root / E2E_OUTPUT_FILE
    status_path = root / E2E_STATUS_FILE
    exit_path = root / (E2E_STATUS_FILE + ".exit")

    global _test_process, _harness_running
    if _harness_running or (_test_process is not None and _test_process.returncode is None):
        output = ""
        if output_path.exists():
            output = output_path.read_text(encoding="utf-8", errors="replace")
        return JSONResponse(content={"status": "running", "output": output, "exit_code": None})

    status = "idle"
    if status_path.exists():
        status = status_path.read_text(encoding="utf-8").strip() or "idle"

    exit_code = None
    if exit_path.exists():
        try:
            exit_code = int(exit_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pass

    output = ""
    if output_path.exists():
        output = output_path.read_text(encoding="utf-8", errors="replace")

    return JSONResponse(content={"status": status, "output": output, "exit_code": exit_code})
