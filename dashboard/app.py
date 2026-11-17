"""Local portfolio dashboard; importing this module never opens a journal."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from jsonschema import Draft202012Validator, FormatChecker, ValidationError as SchemaValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from dashboard.assets import verify_frontend


ROOT = Path(__file__).resolve().parent
def create_app(clock=None, static_directory=None, serve_frontend=True,
               intelligence_database=None, intelligence_demo=False, analysis_controls_enabled=False, analysis_profiles=(), investment_database=None,
               investment_recording_enabled=False, awareness_profile=None,
               monitoring_profile=None, monitoring_cache=None, monitoring_reviews=None,
               monitoring_workflow_enabled=False, setup_workspace=None, setup_enabled=False):
    if setup_enabled and setup_workspace is None:
        raise ValueError("Setup requires a selected workspace")
    if setup_workspace is not None and (
            investment_database is None or intelligence_demo
            or Path(investment_database).absolute() !=
            Path(setup_workspace).absolute() / "investment.sqlite3"):
        raise ValueError("Setup requires the workspace's fixed investment journal path")
    if awareness_profile is not None and (investment_database is None or intelligence_database is None or intelligence_demo):
        raise ValueError("Portfolio awareness requires explicit investment and intelligence stores")
    if monitoring_profile is not None and investment_database is None:
        raise ValueError("Monitoring profile requires an explicit investment store")
    if monitoring_cache is not None and monitoring_profile is None:
        raise ValueError("Monitoring cache requires an explicit monitoring profile")
    if monitoring_reviews is not None and (
            investment_database is None or monitoring_profile is None):
        raise ValueError("Monitoring reviews require explicit investment and monitoring stores")
    if monitoring_workflow_enabled and monitoring_reviews is None:
        raise ValueError("Monitoring workflow actions require an explicitly selected review store")
    if monitoring_reviews is not None and (
            Path(monitoring_reviews).resolve() == Path(investment_database).resolve()
            or monitoring_cache is not None
            and Path(monitoring_reviews).resolve() == Path(monitoring_cache).resolve()):
        raise ValueError("Monitoring review, cache and investment stores must be separate")
    if serve_frontend:
        static_directory = verify_frontend(static_directory)
    clock = clock or (lambda: datetime.now(timezone.utc))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    setup = None
    setup_validator = None
    if setup_workspace is not None:
        from dashboard.investment_setup import InvestmentSetup
        setup = InvestmentSetup(Path(setup_workspace).absolute(), setup_enabled, clock())
        setup_validator = Draft202012Validator(
            json.loads((ROOT / "setup.schema.json").read_text(encoding="utf-8")),
            format_checker=FormatChecker())
    intelligence = None
    investment_recording = None
    monitoring_workflow = None
    if investment_recording_enabled:
        if investment_database is None or intelligence_demo:
            raise ValueError("Investment recording requires a selected recorded investment store")
        from dashboard.investment_recording import RecordingSessions
        investment_recording = RecordingSessions(
            investment_database, clock)
    if monitoring_workflow_enabled:
        from dashboard.monitoring_workflow import MonitoringWorkflowSessions
        monitoring_workflow = MonitoringWorkflowSessions()
    if intelligence_database is not None or intelligence_demo:
        if intelligence_demo and (investment_database is not None or intelligence_database is not None):
            raise ValueError("Synthetic intelligence must not bind operational storage")
        from dashboard.intelligence import IntelligenceWorkspace
        intelligence = IntelligenceWorkspace(
            intelligence_database, analysis_profiles, analysis_controls_enabled, intelligence_demo)
    mutation_paths = {
        "/api/v1/intelligence/controls",
        "/api/investment/v1/recording/preview",
        "/api/investment/v1/recording/confirm",
        "/api/investment/v1/monitoring-workflow/sync",
        "/api/investment/v1/monitoring-workflow/action",
        "/api/investment/v1/monitoring-workflow/observation/preview",
        "/api/investment/v1/monitoring-workflow/observation/confirm",
        "/api/investment/v1/setup/preview",
        "/api/investment/v1/setup/recovery-preview",
        "/api/investment/v1/setup/confirm",
    }
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def error(code, status=None):
        return JSONResponse(status_code=status or 400, content={
            "error": code, "message": "The dashboard request is invalid.",
        })

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        origin = request.headers.get("origin")
        if hostname not in ("127.0.0.1", "localhost"):
            response = error("invalid_request", 400)
        elif origin and origin != f"{request.url.scheme}://{host}":
            response = error("invalid_request", 403)
        elif request.method not in ("GET", "HEAD") and not (
                request.method == "POST" and request.url.path in mutation_paths and (
                    (intelligence and intelligence.controls_enabled and
                     request.url.path.startswith("/api/v1/intelligence/")) or
                    (investment_recording and
                     request.url.path.startswith("/api/investment/v1/recording/")) or
                    (monitoring_workflow and
                     request.url.path.startswith("/api/investment/v1/monitoring-workflow/")) or
                    (setup and setup.security is not None and
                     request.url.path.startswith("/api/investment/v1/setup/")))):
            response = error("invalid_request", 405)
        elif request.method == "POST" and (origin != f"{request.url.scheme}://{host}" or request.headers.get("sec-fetch-site") not in (None,"same-origin")):
            response = error("invalid_request", 403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error("invalid_request")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error("invalid_request", exc.status_code)

    @app.get("/api/v1/settings")
    def settings():
        if setup is not None:
            status = setup.status(clock())
            if status["state"] != "ready":
                return {"mode": "recorded", "workspace": "setup",
                        "setup_state": status["state"]}
        return {"mode": "recorded" if investment_database is not None else "demo",
                "workspace": "investment" if investment_database is not None else "demo"}

    @app.get("/api/investment/v1/snapshot")
    def investment_snapshot():
        from investment import InvestmentError
        from investment.service import snapshot

        try:
            if investment_database is not None:
                report = snapshot(investment_database, now=clock())
            else:
                from dashboard.investment_demo import demo_opening_snapshot
                report = demo_opening_snapshot(now=clock())
            schema = json.loads((ROOT.parent / "investment" / "response-v2.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
            return report
        except (InvestmentError, OSError) as exc:
            return JSONResponse(status_code=503, content={"error": "investment_unavailable", "message": str(exc)})
        except SchemaValidationError:
            return JSONResponse(status_code=409, content={"error": "investment_invalid", "message": "Investment report failed schema validation."})

    @app.get("/api/investment/v1/monitoring")
    def investment_monitoring():
        from investment import InvestmentError
        from investment.monitoring import monitoring_snapshot
        try:
            if monitoring_profile is not None:
                result = monitoring_snapshot(investment_database, monitoring_profile, monitoring_cache, clock())
            elif investment_database is not None:
                result = {"enabled": False}
            else:
                from dashboard.investment_demo import demo_monitoring_snapshot
                result = demo_monitoring_snapshot(clock())
            schema = json.loads((ROOT.parent / "investment" / "monitoring.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
            return result
        except (InvestmentError, OSError):
            return JSONResponse(status_code=503, content={"error": "portfolio_monitoring_unavailable"})
        except SchemaValidationError:
            return JSONResponse(status_code=409, content={"error": "portfolio_monitoring_invalid"})

    @app.get("/api/investment/v{version}/awareness")
    def investment_awareness(version: int):
        if version not in (1, 2):
            raise HTTPException(status_code=404)
        if awareness_profile is None:
            return {"enabled": False}
        from investment import InvestmentError
        from investment.awareness import awareness_snapshot
        try:
            result = awareness_snapshot(investment_database, intelligence_database, awareness_profile, clock(), version)
            schema = json.loads((ROOT.parent / "investment" / "awareness.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
            return result
        except (InvestmentError, OSError):
            return JSONResponse(status_code=503, content={"error": "portfolio_awareness_unavailable"})
        except SchemaValidationError:
            return JSONResponse(status_code=409, content={"error": "portfolio_awareness_invalid"})

    @app.get("/api/investment/v1/recording/session")
    def investment_recording_session(request: Request):
        if investment_recording is None:
            return {"enabled": False, "csrf": None,
                    "accounts": [], "products": [], "latest_correctable_event": None,
                    "correctable_events": [], "current_balances": {},
                    "current_holdings": [], "opened": False}
        from dashboard.investment_recording import RecordingError

        try:
            result = investment_recording.session_payload(
                request.cookies.get("investment_recording_session"), clock())
        except RecordingError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.code})
        token = result.pop("_token")
        response = JSONResponse(result)
        response.set_cookie(
            "investment_recording_session", token, httponly=True, samesite="strict",
            secure=request.url.scheme == "https",
            path="/api/investment/v1/recording", max_age=900)
        return response

    @app.get("/api/v1/intelligence")
    def intelligence_snapshot():
        if intelligence is None:
            return JSONResponse(status_code=503,content={"error":"intelligence_not_configured"})
        try:
            return intelligence.snapshot()
        except Exception as exc:
            return JSONResponse(status_code=503,content={"error":"intelligence_unavailable",
                "diagnostic":getattr(exc,"code",type(exc).__name__) if intelligence.demo else None})

    @app.get("/api/v1/intelligence/session")
    def intelligence_session(request:Request):
        if intelligence is None or not intelligence.controls_enabled:
            return {"enabled":False,"csrf":None}
        token,record=intelligence.security.session(request.cookies.get("analysis_session"),clock(),create=True)
        response=JSONResponse({"enabled":True,"csrf":record["csrf"]})
        response.set_cookie("analysis_session",token,httponly=True,samesite="strict",secure=request.url.scheme=="https",path="/api/v1/intelligence",max_age=900)
        return response

    async def bounded_json(request, maximum=4096, strict=False):
        if request.headers.get("content-type","").split(";",1)[0]!="application/json":
            raise ValueError("JSON only")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > maximum:
                raise ValueError("Command too large")
        if strict:
            from investment.store import parse_json
            return parse_json(bytes(body).decode("utf-8"))
        return json.loads(bytes(body))

    def setup_response(payload):
        try:
            setup_validator.validate(payload)
        except SchemaValidationError:
            return JSONResponse(status_code=409, content={"error": "setup_response_invalid"})
        return JSONResponse(payload)

    @app.get("/api/investment/v1/setup/session")
    def investment_setup_session(request: Request):
        if setup is None:
            return {"enabled": False, "csrf": None,
                    "state": "blocked", "workspace": "",
                    "message": "Setup is not selected. Restart with the default workspace or --workspace."}
        from dashboard.monitoring_workflow import WorkflowError
        from investment import InvestmentError
        try:
            payload = setup.session_payload(request.cookies.get("investment_setup_session"), clock())
            token = payload.pop("_token", None)
            response = setup_response(payload)
            if token is not None:
                response.set_cookie(
                    "investment_setup_session", token, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https",
                    path="/api/investment/v1/setup", max_age=900)
            return response
        except (WorkflowError, InvestmentError, OSError):
            return JSONResponse(status_code=503, content={"error": "setup_unavailable"})

    def setup_authorized(request):
        return bool(setup and setup.authorize(
            request.cookies.get("investment_setup_session"),
            request.headers.get("x-csrf-token"), clock()))

    @app.post("/api/investment/v1/setup/preview")
    @app.post("/api/investment/v1/setup/recovery-preview")
    async def investment_setup_preview(request: Request):
        from dashboard.monitoring_workflow import WorkflowError
        from investment import InvestmentError
        if not setup_authorized(request):
            return JSONResponse(status_code=403, content={"error": "setup_authorization_required"})
        try:
            payload = await bounded_json(request, maximum=1048576, strict=True)
            token = request.cookies.get("investment_setup_session")
            if request.url.path.endswith("/recovery-preview"):
                if payload != {}:
                    return JSONResponse(status_code=400, content={"error": "invalid_request"})
                result = setup.recover(token, clock())
            else:
                if type(payload) is not dict or set(payload) != {"request_id", "profile", "opening"}:
                    return JSONResponse(status_code=400, content={"error": "invalid_request"})
                result = setup.preview(
                    token, payload["request_id"], payload["profile"], payload["opening"], clock())
            return setup_response(result)
        except WorkflowError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.code})
        except InvestmentError as exc:
            return JSONResponse(status_code=409, content={"error": "setup_rejected", "message": str(exc)})
        except (ValueError, RecursionError):
            return JSONResponse(status_code=400, content={"error": "invalid_request"})

    @app.post("/api/investment/v1/setup/confirm")
    async def investment_setup_confirm(request: Request):
        from dashboard.monitoring_workflow import WorkflowError
        from investment import InvestmentError
        if not setup_authorized(request):
            return JSONResponse(status_code=403, content={"error": "setup_authorization_required"})
        try:
            payload = await bounded_json(request, strict=True)
            if (type(payload) is not dict or set(payload) != {"preview_id"}
                    or not isinstance(payload["preview_id"], str)
                    or not 1 <= len(payload["preview_id"]) <= 256):
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            return setup_response(setup.confirm(
                request.cookies.get("investment_setup_session"), payload["preview_id"], clock()))
        except WorkflowError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.code})
        except InvestmentError as exc:
            return JSONResponse(status_code=409, content={"error": "setup_rejected", "message": str(exc)})
        except (ValueError, RecursionError):
            return JSONResponse(status_code=400, content={"error": "invalid_request"})

    def workflow_sources():
        from investment.monitoring import project, validate_profile
        from investment.monitoring_reviews import monitoring_cache_binding
        from investment.monitoring_store import read_cache
        from investment.store import canonical, digest, load_json, read_with_clock

        now = clock()
        ledger, now = read_with_clock(investment_database, now)
        profile = load_json(monitoring_profile)
        validate_profile(profile, ledger, now)
        cache = read_cache(monitoring_cache, profile, now) if monitoring_cache is not None else None
        cache_binding = None
        if cache is not None:
            cache_binding = monitoring_cache_binding(
                monitoring_cache, cache["instance_id"])
        report = project(ledger, profile, now, cache)
        identity = {
            "portfolio_id": ledger.spec["portfolio_id"],
            "profile_hash": digest(canonical(profile)),
            "cache_binding": cache_binding,
        }
        return {
            "now": now, "ledger": ledger, "profile": profile, "cache": cache,
            "report": report, "identity": identity,
            "profile_hash": identity["profile_hash"],
        }

    @app.get("/api/investment/v1/monitoring/reviews")
    def monitoring_reviews_inbox():
        if monitoring_reviews is None:
            return {"enabled": False}
        from investment import InvestmentError
        from investment.monitoring_reviews import review_inbox, review_sync_is_current
        try:
            sources = workflow_sources()
            inbox = review_inbox(monitoring_reviews, sources["identity"])
            try:
                inbox["sync_current"] = review_sync_is_current(
                    monitoring_reviews, sources["report"], sources["identity"]["portfolio_id"],
                    sources["profile_hash"], sources["now"],
                    sources["identity"]["cache_binding"])
                inbox["source_available"] = True
            except InvestmentError:
                inbox["sync_current"] = False
                inbox["source_available"] = False
            return {"enabled": True, **inbox}
        except (InvestmentError, OSError):
            return JSONResponse(status_code=409, content={"error": "monitoring_reviews_unavailable"})

    @app.get("/api/investment/v1/monitoring-workflow/session")
    def monitoring_workflow_session(request: Request):
        if monitoring_workflow is None:
            return {"enabled": False, "csrf": None,
                    "accounts": [], "cache_enabled": False}
        from dashboard.monitoring_workflow import WorkflowError
        from investment import InvestmentError
        try:
            result = monitoring_workflow.session_payload(
                request.cookies.get("investment_monitoring_session"), clock())
            token = result.pop("_token")
            accounts, cache_enabled = [], monitoring_cache is not None
            sources = workflow_sources()
            accounts = [{"account_id": item["account_id"], "provider": item["provider"]}
                        for item in sources["profile"]["accounts"]]
            result.update({"accounts": accounts, "cache_enabled": cache_enabled,
                           "review_enabled": monitoring_reviews is not None})
            response = JSONResponse(result)
            response.set_cookie(
                "investment_monitoring_session", token, httponly=True, samesite="strict",
                secure=request.url.scheme == "https",
                path="/api/investment/v1/monitoring-workflow", max_age=900)
            return response
        except (WorkflowError, InvestmentError, OSError) as exc:
            status = getattr(exc, "status", 503)
            return JSONResponse(status_code=status, content={"error": "monitoring_workflow_unavailable"})

    def workflow_authorized(request):
        return bool(monitoring_workflow and monitoring_workflow.authorize(
            request.cookies.get("investment_monitoring_session"),
            request.headers.get("x-csrf-token"), clock()))

    @app.post("/api/investment/v1/monitoring-workflow/sync")
    async def monitoring_workflow_sync(request: Request):
        if monitoring_reviews is None:
            return JSONResponse(status_code=503, content={"error": "monitoring_reviews_disabled"})
        if not workflow_authorized(request):
            return JSONResponse(status_code=403, content={"error": "monitoring_workflow_unauthorized"})
        from investment import InvestmentError
        from investment.monitoring_reviews import sync_review_findings
        try:
            payload = await bounded_json(request)
            if type(payload) is not dict or payload:
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            sources = workflow_sources()
            result = sync_review_findings(
                monitoring_reviews, sources["report"], sources["identity"]["portfolio_id"],
                sources["profile_hash"], sources["now"],
                sources["identity"]["cache_binding"])
            return result
        except (ValueError, InvestmentError, OSError):
            return JSONResponse(status_code=409, content={"error": "monitoring_sync_rejected"})

    @app.post("/api/investment/v1/monitoring-workflow/action")
    async def monitoring_workflow_action(request: Request):
        if monitoring_reviews is None:
            return JSONResponse(status_code=503, content={"error": "monitoring_reviews_disabled"})
        if not workflow_authorized(request):
            return JSONResponse(status_code=403, content={"error": "monitoring_workflow_unauthorized"})
        from investment import InvestmentError
        from investment.monitoring_reviews import review_sync_is_current, update_review
        try:
            payload = await bounded_json(request)
            required = {"case_id", "request_id", "expected_revision", "action", "note"}
            if type(payload) is not dict or set(payload) != required:
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            sources = workflow_sources()
            if not review_sync_is_current(
                    monitoring_reviews, sources["report"], sources["identity"]["portfolio_id"],
                    sources["profile_hash"], sources["now"],
                    sources["identity"]["cache_binding"]):
                return JSONResponse(status_code=409, content={"error": "stale_review_sync"})
            result = update_review(
                monitoring_reviews, payload["case_id"], payload["request_id"],
                payload["expected_revision"], payload["action"], payload["note"],
                sources["now"], expected_identity=sources["identity"])
            return result
        except (ValueError, InvestmentError, OSError):
            return JSONResponse(status_code=409, content={"error": "monitoring_action_rejected"})

    @app.post("/api/investment/v1/monitoring-workflow/observation/preview")
    async def monitoring_observation_preview(request: Request):
        if monitoring_cache is None:
            return JSONResponse(status_code=503, content={"error": "monitoring_cache_disabled"})
        if not workflow_authorized(request):
            return JSONResponse(status_code=403, content={"error": "monitoring_workflow_unauthorized"})
        from dashboard.monitoring_workflow import (
            WorkflowError, request_fingerprint,
        )
        from investment import InvestmentError
        from investment.core import explicit_clock, iso, text
        from investment.monitoring import validate_observation
        from investment.reconciliation import preview_observations
        from investment.monitoring_store import MAX_ATTEMPT_BYTES
        from investment.store import canonical
        import secrets

        token = request.cookies.get("investment_monitoring_session")
        try:
            payload = await bounded_json(request, 2 * 1024 * 1024)
            if (type(payload) is not dict
                    or set(payload) != {"request_id", "mapping_observed_at", "observation"}):
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            request_id = text(payload["request_id"])
            if len(request_id) > 128:
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            fingerprint = request_fingerprint(payload)
            prior = monitoring_workflow.existing_preview(token, request_id, fingerprint, clock())
            if prior is not None:
                return prior
            sources = workflow_sources()
            if sources["cache"] is None or sources["cache"]["instance_id"] is None:
                return JSONResponse(status_code=409, content={"error": "identified_monitoring_cache_required"})
            now = sources["now"]
            observation = payload["observation"]
            if type(observation) is not dict:
                return JSONResponse(status_code=400, content={"error": "invalid_observation"})
            validate_observation(observation, now)
            mapping_at = explicit_clock(payload["mapping_observed_at"])
            if mapping_at > now:
                return JSONResponse(status_code=409, content={"error": "future_mapping_evidence"})
            mapping = next((item for item in sources["profile"]["accounts"]
                            if item["account_id"] == observation["account_id"]), None)
            if mapping is None or mapping["provider"] != observation["provider"]:
                return JSONResponse(status_code=409, content={"error": "observation_account_mismatch"})
            mapping_at = max(
                mapping_at, explicit_clock(sources["profile"]["observed_at"]),
                explicit_clock(observation.get(
                    "mapping_available_at", sources["profile"]["observed_at"])))
            if explicit_clock(observation["available_at"]) > now:
                return JSONResponse(status_code=409, content={"error": "future_observation"})
            existing_attempt = next(
                (item for item in sources["cache"]["attempts"] if item["id"] == request_id),
                None)
            for retained in sources["cache"]["attempts"]:
                retained_observation = retained["observation"]
                if (retained_observation is not None
                        and retained["account_id"] == observation["account_id"]
                        and retained["provider"] == observation["provider"]
                        and retained_observation["id"] == observation["id"]
                        and canonical(retained_observation) != canonical(observation)):
                    return JSONResponse(status_code=409, content={"error": "observation_revision_conflict"})
            if existing_attempt is not None:
                if (existing_attempt["status"] != "success"
                        or canonical(existing_attempt["observation"]) != canonical(observation)):
                    return JSONResponse(status_code=409, content={"error": "attempt_id_conflict"})
                attempt = existing_attempt
            else:
                attempt = {
                    "id": request_id, "account_id": observation["account_id"],
                    "provider": observation["provider"], "attempted_at": iso(now),
                    "status": "success", "error_code": None, "observation": observation,
                }
            if len(canonical(attempt).encode("utf-8")) > MAX_ATTEMPT_BYTES:
                return JSONResponse(status_code=413, content={"error": "observation_too_large"})
            reconciliation = {
                "status": "unavailable",
                "reason": "GBP source clock is required for reconciliation; no FX or clock is inferred.",
            }
            if observation["currency"] == "GBP" and observation["observed_at"] is not None:
                account_spec = next(item for item in sources["ledger"].spec["accounts"]
                                    if item["id"] == observation["account_id"])
                staged = {
                    "schema_version": 1,
                    "portfolio_id": sources["identity"]["portfolio_id"],
                    "currency": "GBP", "source": observation["provider"],
                    "evidence": observation["evidence"],
                    "mapping_observed_at": iso(mapping_at),
                    "account_mappings": [{
                        "source_account": observation["account_id"],
                        "account_id": observation["account_id"],
                        "account_binding": account_spec["binding"],
                        "evidence": "Bound to the selected local monitoring account profile",
                    }],
                    "product_mappings": observation.get("product_mappings", []),
                    "accounts": [{
                        "source_account": observation["account_id"],
                        "observed_at": observation["observed_at"],
                        "available_at": observation["available_at"],
                        "cash_coverage": observation["coverage"]
                        if observation["cash"] is not None else "unknown",
                        "holdings_coverage": observation["coverage"],
                        "coverage_evidence": observation["evidence"],
                        "cash_gbp": observation["cash"],
                        "holdings": observation["holdings"],
                    }],
                }
                reconciliation = preview_observations(
                    investment_database, staged, mapping["max_age_seconds"], now)
                if reconciliation["revision"] != sources["ledger"].revision:
                    return JSONResponse(status_code=409, content={"error": "investment_revision_changed"})
            preview_id = secrets.token_urlsafe(32)
            response = {
                "preview_id": preview_id, "request_id": request_id,
                "journal_revision": sources["ledger"].revision,
                "profile_hash": sources["profile_hash"],
                "cache_revision": sources["cache"]["revision"],
                "cache_instance_id": sources["cache"]["instance_id"],
                "observation": observation, "reconciliation": reconciliation,
                "already_staged": existing_attempt is not None,
                "expires_at": (now + monitoring_workflow.PREVIEW_TTL)
                .isoformat().replace("+00:00", "Z"),
                "stages_only_to": "monitoring_cache",
                "investment_journal_changed": False,
            }
            monitoring_workflow.remember_preview(
                token, request_id, fingerprint,
                {"preview_id": preview_id, "response": response,
                 "attempt": attempt, "source_revision": sources["ledger"].revision,
                 "profile_hash": sources["profile_hash"],
                 "cache_revision": sources["cache"]["revision"],
                 "cache_instance_id": sources["cache"]["instance_id"],
                 "expires": now + monitoring_workflow.PREVIEW_TTL, "receipt": None},
                now)
            return response
        except WorkflowError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.code})
        except (ValueError, InvestmentError, OSError, KeyError, TypeError):
            return JSONResponse(status_code=409, content={"error": "observation_preview_rejected"})

    @app.post("/api/investment/v1/monitoring-workflow/observation/confirm")
    async def monitoring_observation_confirm(request: Request):
        if monitoring_cache is None:
            return JSONResponse(status_code=503, content={"error": "monitoring_cache_disabled"})
        if not workflow_authorized(request):
            return JSONResponse(status_code=403, content={"error": "monitoring_workflow_unauthorized"})
        from dashboard.monitoring_workflow import WorkflowError
        from investment import InvestmentError
        from investment.monitoring_store import append_attempt

        token = request.cookies.get("investment_monitoring_session")
        try:
            payload = await bounded_json(request)
            if type(payload) is not dict or set(payload) != {"preview_id"}:
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            pending = monitoring_workflow.pending_preview(
                token, payload["preview_id"], clock())
            if pending["receipt"] is not None:
                return pending["receipt"]
            sources = workflow_sources()
            if (sources["ledger"].revision != pending["source_revision"]
                    or sources["profile_hash"] != pending["profile_hash"]
                    or sources["cache"] is None
                    or sources["cache"]["revision"] != pending["cache_revision"]
                    or sources["cache"]["instance_id"] != pending["cache_instance_id"]):
                return JSONResponse(status_code=409, content={"error": "stale_observation_preview"})
            revision = append_attempt(
                monitoring_cache, sources["profile"], pending["attempt"], sources["now"],
                expected_instance_id=pending["cache_instance_id"],
                expected_revision=pending["cache_revision"])
            receipt = {
                "staged": True, "cache_revision": revision,
                "stages_only_to": "monitoring_cache", "investment_journal_changed": False,
            }
            return monitoring_workflow.set_receipt(token, payload["preview_id"], receipt, clock())
        except WorkflowError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.code})
        except (ValueError, InvestmentError, OSError):
            return JSONResponse(status_code=409, content={"error": "observation_confirmation_rejected"})

    @app.post("/api/investment/v1/recording/preview")
    async def investment_recording_preview(request: Request):
        if investment_recording is None:
            return JSONResponse(status_code=503, content={"error": "recording_disabled"})
        from dashboard.investment_recording import RecordingError

        if not investment_recording.authorize(
                request.cookies.get("investment_recording_session"),
                request.headers.get("x-csrf-token"), clock()):
            return JSONResponse(status_code=403, content={"error": "recording_unauthorized"})
        try:
            payload = await bounded_json(request, 2 * 1024 * 1024)
            if type(payload) is not dict or set(payload) != {"request_id", "event"}:
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            result = investment_recording.preview(
                request.cookies.get("investment_recording_session"),
                payload["request_id"], payload["event"], clock())
            schema = json.loads(
                (ROOT.parent / "investment" / "response-v2.schema.json").read_text(
                    encoding="utf-8"))
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            validator.validate(result["before"])
            validator.validate(result["after"])
            return result
        except RecordingError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.code})
        except (ValueError, SchemaValidationError, OSError):
            return JSONResponse(status_code=409, content={"error": "preview_rejected"})

    @app.post("/api/investment/v1/recording/confirm")
    async def investment_recording_confirm(request: Request):
        if investment_recording is None:
            return JSONResponse(status_code=503, content={"error": "recording_disabled"})
        from dashboard.investment_recording import RecordingError

        if not investment_recording.authorize(
                request.cookies.get("investment_recording_session"),
                request.headers.get("x-csrf-token"), clock()):
            return JSONResponse(status_code=403, content={"error": "recording_unauthorized"})
        try:
            payload = await bounded_json(request)
            if type(payload) is not dict or set(payload) != {"preview_id"}:
                return JSONResponse(status_code=400, content={"error": "invalid_request"})
            return investment_recording.confirm(
                request.cookies.get("investment_recording_session"),
                payload["preview_id"], clock())
        except RecordingError as exc:
            return JSONResponse(status_code=exc.status, content={
                "error": exc.code, "retryable": exc.status >= 500})
        except ValueError:
            return JSONResponse(status_code=409, content={"error": "confirmation_rejected"})

    @app.post("/api/v1/intelligence/controls")
    async def intelligence_controls(request:Request):
        if not intelligence.security.authorize(request.cookies.get("analysis_session"),request.headers.get("x-csrf-token"),clock()):
            return error("invalid_request",403)
        try:
            from market_intelligence.store import encode
            receipt=intelligence.enqueue(await bounded_json(request),clock())
            return JSONResponse(status_code=202,content=json.loads(encode(receipt)))
        except Exception:
            return error("invalid_request",409)

    if serve_frontend:
        app.mount("/assets", StaticFiles(directory=static_directory / "assets"), name="assets")

        @app.get("/")
        @app.get("/intelligence")
        @app.get("/investment")
        @app.get("/investment/record")
        @app.get("/investment/reviews")
        @app.get("/investment-guide")
        @app.get("/setup")
        def index():
            return FileResponse(static_directory / "index.html")

    return app