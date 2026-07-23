"""Chat API router: message handling, SSE streaming, session management."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from chat.models import ChatMessage, ChatRequest, ChatResponse, PairRequest, SSEEvent
from chat.intent import Intent, parse_intent
from chat.session import session_manager
from chat.sse import sse_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# These will be set by api.py when wiring up
_connected_extensions = None
_extension_bridges = None
_session_extension_map = None
_get_llm_provider = None


def configure(connected_extensions, extension_bridges, session_extension_map, get_llm_provider):
    """Called by api.py to inject shared state."""
    global _connected_extensions, _extension_bridges, _session_extension_map, _get_llm_provider
    _connected_extensions = connected_extensions
    _extension_bridges = extension_bridges
    _session_extension_map = session_extension_map
    _get_llm_provider = get_llm_provider


def _bridge_for_session(session_id: str):
    """Return the ExtensionBridge paired with this chat session, or None.

    Looks up the session's paired extension (set via POST /api/chat/pair)
    rather than picking an arbitrary connected extension — with more than one
    extension connected to a shared deployment, "arbitrary" means a user's
    audit can silently run in someone else's browser.
    """
    ext_session_id = (_session_extension_map or {}).get(session_id)
    if not ext_session_id:
        return None
    if ext_session_id not in (_connected_extensions or {}):
        return None  # paired extension exists but isn't currently connected
    return (_extension_bridges or {}).get(ext_session_id)


@router.post("/message", response_model=ChatResponse)
async def send_message(request: ChatRequest):
    """Handle a chat message. Parse intent and respond or start audit."""
    session_id = request.session_id
    user_msg = request.message.strip()

    # Store user message
    session_manager.add_message(ChatMessage(
        session_id=session_id,
        role="user",
        content=user_msg,
    ))

    intent, url = parse_intent(user_msg)

    if intent == Intent.GREETING:
        response_text = (
            "Hi! I can audit any website for accessibility, SEO, performance, "
            "and broken links. Just paste a URL to get started."
        )
        session_manager.add_message(ChatMessage(
            session_id=session_id, role="assistant", content=response_text,
        ))
        return ChatResponse(
            session_id=session_id, intent="greeting", response=response_text,
        )

    if intent == Intent.HELP:
        response_text = (
            "I can audit websites for:\n"
            "- **Accessibility** (WCAG compliance via axe-core)\n"
            "- **SEO** (meta tags, headings, structured data)\n"
            "- **Performance** (page weight, load time, image sizes)\n"
            "- **Broken links**\n\n"
            "Just type a URL like: `audit https://example.com`\n\n"
            "Make sure the Chrome extension is installed and connected."
        )
        session_manager.add_message(ChatMessage(
            session_id=session_id, role="assistant", content=response_text,
        ))
        return ChatResponse(
            session_id=session_id, intent="help", response=response_text,
        )

    if intent == Intent.AUDIT and url:
        # Check THIS session has a paired, currently-connected extension —
        # not just "some extension is connected somewhere" (which, on a
        # shared deployment with multiple users, could be someone else's).
        if _bridge_for_session(session_id) is None:
            is_paired = session_id in (_session_extension_map or {})
            response_text = (
                "Your paired extension isn't currently connected. Open Chrome "
                "with the extension installed and try again."
                if is_paired else
                "No extension paired with this session yet. Open the extension "
                "popup, enter this session's pairing code (shown in the chat UI), "
                "and try again."
            )
            session_manager.add_message(ChatMessage(
                session_id=session_id, role="assistant", content=response_text,
                message_type="error",
            ))
            return ChatResponse(
                session_id=session_id, intent="audit", response=response_text,
            )

        # Start audit in background
        audit_id = f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        response_text = f"Starting audit of {url}..."

        session_manager.add_message(ChatMessage(
            session_id=session_id, role="assistant", content=response_text,
            message_type="audit_progress",
            metadata={"audit_id": audit_id, "url": url},
        ))

        # Launch background audit task
        asyncio.create_task(_run_audit(session_id, audit_id, url))

        return ChatResponse(
            session_id=session_id,
            intent="audit",
            response=response_text,
            audit_id=audit_id,
        )

    # Unknown intent
    response_text = (
        "I'm not sure what you mean. Try pasting a URL to audit, "
        "or type 'help' to see what I can do."
    )
    session_manager.add_message(ChatMessage(
        session_id=session_id, role="assistant", content=response_text,
    ))
    return ChatResponse(
        session_id=session_id, intent="unknown", response=response_text,
    )


async def _run_audit(session_id: str, audit_id: str, url: str):
    """Background task: run audit and emit SSE events."""
    from ai.agent.orchestrator import AuditOrchestrator
    from ai.agent.state import AuditSession
    from ai.agent.ws_bridge import ExtensionBridge

    try:
        # Use the extension paired with THIS chat session (see /api/chat/pair),
        # never an arbitrary connected extension — with multiple users sharing
        # a deployed backend, "arbitrary" can mean someone else's browser.
        bridge = _bridge_for_session(session_id)

        if not bridge:
            await session_manager.emit_event(session_id, SSEEvent(
                event="error",
                data={"message": "No paired, connected extension available for this session"},
            ))
            await session_manager.emit_done(session_id)
            return

        llm = _get_llm_provider() if _get_llm_provider else None

        audit_session = AuditSession(
            session_id=audit_id,
            chat_session_id=session_id,
            target_url=url,
        )

        async def progress_callback(event_type: str, message: str, data: dict):
            try:
                await session_manager.emit_event(session_id, SSEEvent(
                    event=event_type,
                    data={"message": message, **data},
                ))
            except Exception as cb_err:
                logger.error(f"Progress callback failed: {cb_err}", exc_info=True)

        orchestrator = AuditOrchestrator(llm=llm)
        result = await orchestrator.run_audit(
            bridge=bridge,
            target_url=url,
            session=audit_session,
            progress_callback=progress_callback,
        )

        # Store full results for REST retrieval
        logger.info(f"Storing audit result for session {session_id}")
        try:
            result_dict = result.to_dict()
            session_manager.store_audit_result(session_id, result_dict)
            logger.info(f"Audit result stored successfully ({len(str(result_dict))} chars)")
        except Exception as store_err:
            logger.error(f"Failed to store audit result: {store_err}", exc_info=True)

    except Exception as e:
        logger.error(f"Audit background task failed: {e}", exc_info=True)
        try:
            await session_manager.emit_event(session_id, SSEEvent(
                event="error",
                data={"message": f"Audit failed: {e}"},
            ))
        except Exception:
            logger.error("Failed to emit error event after task failure", exc_info=True)

    finally:
        try:
            await session_manager.emit_done(session_id)
            logger.info(f"SSE stream closed for session {session_id}")
        except Exception as done_err:
            logger.error(f"Failed to emit done: {done_err}", exc_info=True)


@router.get("/stream/{session_id}")
async def stream_events(session_id: str):
    """SSE endpoint. Client connects once on page load to receive all events."""
    return StreamingResponse(
        sse_stream(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get chat history and audit results for a session."""
    messages = session_manager.get_messages(session_id)
    result = session_manager.get_audit_result(session_id)
    return {
        "session_id": session_id,
        "messages": [m.model_dump() for m in messages],
        "audit_result": result,
    }


@router.get("/sessions/{session_id}/results")
async def get_results(session_id: str):
    """Get full audit results JSON for download."""
    result = session_manager.get_audit_result(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="No audit results found for this session")
    return result


@router.get("/sessions/{session_id}/pdf")
async def download_pdf(session_id: str):
    """Generate and download a PDF of the QA audit card."""
    result = session_manager.get_audit_result(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="No audit results found for this session")

    qa_card = result.get("qa_card")
    if not qa_card:
        raise HTTPException(status_code=404, detail="No QA card data available for this session")

    from pdf.generator import generate_audit_pdf

    pdf_bytes = generate_audit_pdf(qa_card)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="qa_audit_{session_id}.pdf"',
        },
    )


@router.get("/sessions/{session_id}/pptx")
async def download_pptx(session_id: str):
    """Generate and download a PPTX deck containing all audit screenshots."""
    result = session_manager.get_audit_result(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="No audit results found for this session")

    from ppt.generator import generate_audit_pptx

    desktop_results = result.get("desktop_results", [])
    mobile_results = result.get("mobile_results", [])
    vdp_screenshot = result.get("vdp_screenshot")
    target_url = result.get("target_url", "")

    pptx_bytes = generate_audit_pptx(
        target_url=target_url,
        desktop_results=desktop_results,
        mobile_results=mobile_results,
        vdp_screenshot=vdp_screenshot,
        contact_form_screenshot=result.get("contact_form_screenshot"),
        trade_value_screenshot=result.get("trade_value_screenshot"),
        site_summary=result.get("site_summary", ""),
        overall_score=result.get("overall_score", 0.0),
        overall_grade=result.get("overall_grade", ""),
        recommendations=result.get("recommendations", []),
        sprint_metrics={
            "broken_links_all": result.get("broken_links_all", []),
            "broken_images_all": result.get("broken_images_all", []),
            "oversized_images_all": result.get("oversized_images_all", []),
            "js_errors_all": result.get("js_errors_all", []),
            "page_load_times": result.get("page_load_times", []),
        },
    )

    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f'attachment; filename="site_audit_{safe_name}.pptx"',
        },
    )


@router.get("/extension-status")
async def extension_status(session_id: Optional[str] = None):
    """Check extension connectivity.

    Always returns the global connected/count fields (useful for admin
    visibility on a shared deployment). When `session_id` is provided, also
    reports whether THIS session specifically has a paired extension and
    whether that paired extension is currently connected — this is what the
    UI should actually gate "ready to audit" on, since with multiple users
    the global count can be >0 while nobody has paired with you yet.
    """
    connected = bool(_connected_extensions and len(_connected_extensions) > 0)
    result = {
        "connected": connected,
        "count": len(_connected_extensions) if _connected_extensions else 0,
    }
    if session_id:
        ext_session_id = (_session_extension_map or {}).get(session_id)
        result["paired"] = ext_session_id is not None
        result["paired_and_connected"] = _bridge_for_session(session_id) is not None
    return result


@router.post("/pair")
async def pair_session(req: PairRequest):
    """Pair a chat/UI session with a specific extension session.

    Called by the extension (not the UI) once the user copies their chat
    session's pairing code into the extension popup. After this, audits
    started from that chat session are routed to this specific extension —
    see _bridge_for_session — instead of an arbitrary connected one.
    """
    if not req.session_id or not req.ext_session_id:
        raise HTTPException(status_code=400, detail="session_id and ext_session_id are required")

    if _session_extension_map is None:
        raise HTTPException(status_code=500, detail="Pairing state not configured")

    _session_extension_map[req.session_id] = req.ext_session_id
    logger.info(f"Paired chat session {req.session_id} -> extension {req.ext_session_id}")

    return {
        "paired": True,
        "session_id": req.session_id,
        "ext_session_id": req.ext_session_id,
        "extension_connected": req.ext_session_id in (_connected_extensions or {}),
    }
