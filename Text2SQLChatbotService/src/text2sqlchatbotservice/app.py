"""FastAPI app factory + uvicorn launcher."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from text2sqlchatbotservice.agent import build_chat_agent
from text2sqlchatbotservice.api.routers import chat as chat_router
from text2sqlchatbotservice.config import get_settings
from text2sqlchatbotservice.guardrails import BedrockGuardrail
from text2sqlchatbotservice.memory import SessionRegistry
from text2sqlchatbotservice.semantic import SemanticCompiler, load_catalog_index
from text2sqlchatbotservice.snowflake import SnowflakeClient
from text2sqlchatbotservice.tools import build_tools

logger = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).resolve().parent / "semantic" / "catalog"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app.state.settings = settings
    app.state.sf_client = SnowflakeClient(settings)
    app.state.compiler = SemanticCompiler(manifest_dir=CATALOG_DIR)
    app.state.catalog = load_catalog_index(str(CATALOG_DIR))
    app.state.guardrail = BedrockGuardrail(
        guardrail_id=settings.bedrock_guardrail_id,
        version=settings.bedrock_guardrail_version,
        region=settings.aws_region,
    )
    app.state.tools = build_tools(
        settings=settings,
        compiler=app.state.compiler,
        catalog=app.state.catalog,
        sf_client=app.state.sf_client,
    )

    def _restore_agent(session_id: str):
        # Strands' FileSessionManager hydrates messages from disk when an Agent
        # is constructed with a previously-used session_id. We rebuild the same
        # Agent factory call the create_session route uses.
        return build_chat_agent(
            settings=settings,
            session_id=session_id,
            tools=app.state.tools,
        )

    app.state.sessions = SessionRegistry(
        session_dir=settings.session_dir,
        restore=_restore_agent,
    )

    # Warm the MetricFlow engine in a BACKGROUND thread. Parsing the
    # ~7,700-metric manifest takes ~20s (longer on shared CPU). Doing it
    # synchronously here would block uvicorn from binding the port, and hosts
    # like Render kill a deploy that doesn't open its port fast enough
    # ("No open ports detected"). Running it off-thread lets the port bind
    # immediately; the first query either races the warm-up or triggers the
    # lazy build itself (SemanticCompiler._load_engine is idempotent + cheap
    # once cached). Either way it's off the synchronous startup path.
    def _warm() -> None:
        import time as _time
        try:
            _t0 = _time.monotonic()
            app.state.compiler.warm()
            logger.info("metricflow engine warmed in %.1fs", _time.monotonic() - _t0)
        except Exception:
            logger.exception("metricflow warm-up failed (will retry lazily on first query)")

    threading.Thread(target=_warm, name="metricflow-warmup", daemon=True).start()

    logger.info(
        "startup ok — provider=%s guardrail=%s auth=%s db=%s catalog=%d metrics",
        settings.llm_provider,
        "on" if app.state.guardrail.enabled else "off-shadow",
        "on" if settings.api_key else "OFF",
        settings.snowflake_database,
        len(app.state.catalog),
    )
    if not settings.api_key:
        logger.warning(
            "API_KEY is not set — the /chat API is UNAUTHENTICATED. "
            "Set API_KEY in .env before exposing this service publicly."
        )
    try:
        yield
    finally:
        app.state.sf_client.close()
        logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Text2SQL Census Chatbot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat_router.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Serve the built UI (single origin) when a dist dir is configured & exists.
    # Mounted last so /chat and /health take precedence; html=True makes the
    # SPA fall back to index.html for client-side routes.
    if settings.ui_dist_dir:
        dist = Path(settings.ui_dist_dir)
        if dist.is_dir():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
            logger.info("serving UI from %s", dist)
        else:
            logger.warning("UI_DIST_DIR=%s not found — serving API only", dist)

    return app


app = create_app()


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "text2sqlchatbotservice.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
