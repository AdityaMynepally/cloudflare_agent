"""Chat API router: message handling, SSE streaming, session management."""

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from chat.models import ChatMessage, ChatRequest, ChatResponse, SSEEvent
from chat.intent import Intent, parse_intent
from chat.session import session_manager
from chat.sse import sse_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# These will be set by api.py when wiring up
_connected_extensions = None
_extension_bridges = None
_get_llm_provider = None


def configure(connected_extensions, extension_bridges, get_llm_provider):
    """Called by api.py to inject shared state."""
    global _connected_extensions, _extension_bridges, _get_llm_provider
    _connected_extensions = connected_extensions
    _extension_bridges = extension_bridges
    _get_llm_provider = get_llm_provider


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
        # Check extension is connected
        if not _connected_extensions or len(_connected_extensions) == 0:
            response_text = (
                "No Chrome extension connected. Please install and connect "
                "the Web Audit extension, then try again."
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
        # Pick first connected extension
        ext_session_id = next(iter(_connected_extensions))
        bridge = _extension_bridges.get(ext_session_id)

        if not bridge:
            await session_manager.emit_event(session_id, SSEEvent(
                event="error",
                data={"message": "Extension bridge not available"},
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
async def extension_status():
    """Check if any Chrome extension is connected."""
    connected = bool(_connected_extensions and len(_connected_extensions) > 0)
    return {
        "connected": connected,
        "count": len(_connected_extensions) if _connected_extensions else 0,
    }
