"""AI engine.

Retrieval is hybrid: pgvector cosine similarity on embeddings (semantic) blended with pg_trgm similarity on
subjects (lexical, catches error codes and product names). Everything works without an external LLM; when one
is configured it adds summaries, draft replies and richer triage, always behind PII redaction.

All functions take the caller's tenant-bound ``Session``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ..db.base import utcnow
from ..db.models import Category, Interaction, KnowledgeArticle, State, Team, Ticket
from ..services.visibility import visible_ticket_criteria
from ..settings import settings
from .providers import LLMUnavailable, complete, complete_json, embed_text, llm_enabled
from .redaction import redact

VEC_WEIGHT, LEX_WEIGHT = 0.8, 0.2
DONE = ["resolved", "closed", "cancelled"]


def match_band(score):
    """Human label + 0-100 strength, calibrated to the active embedder's thresholds (raw cosine scores differ a
    lot between the local and neural embedders)."""
    lo, hi = settings.ai_similar_min_score, settings.ai_duplicate_threshold
    strength = max(0, min(100, round(100 * (score - lo) / max(hi - lo, 1e-6))))
    label = "Very close" if score >= hi else "Strong" if strength >= 55 else "Partial" if strength >= 25 else "Weak"
    return label, strength


def ticket_text(t) -> str:
    parts = [t.subject, t.subject, t.description or ""]
    if t.category_id:
        parts.append(str(t.category))
    if t.ci_id:
        parts.append(f"{t.ci.name} {t.ci.type_label} {t.ci.tech_stack}")
    if t.resolution_notes:
        parts.append(t.resolution_notes)
    return "\n".join(parts)


def embed_ticket(session: Session, t):
    t.embedding = embed_text(ticket_text(t))
    session.flush()
    return t.embedding


def embed_article(session: Session, a) -> None:
    a.embedding = embed_text(f"{a.title}\n{a.title}\n{a.body}")
    session.flush()


def similar_tickets(session: Session, tenant_id, *, ticket=None, text=None, limit=5, user=None, resolved_only=False,
                    open_only=False, account=None, min_score=None):
    min_score = settings.ai_similar_min_score if min_score is None else min_score
    if ticket is not None:
        vec = ticket.embedding if ticket.embedding is not None else embed_ticket(session, ticket)
        subject = ticket.subject
    else:
        vec, subject = embed_text(text or ""), (text or "")[:300]
    distance = Ticket.embedding.cosine_distance(list(vec))
    lex = func.similarity(Ticket.subject, subject)
    stmt = (select(Ticket, distance.label("distance"), lex.label("lex"))
            .join(State, State.id == Ticket.state_id)
            .options(joinedload(Ticket.state), joinedload(Ticket.priority), joinedload(Ticket.category),
                     joinedload(Ticket.account), joinedload(Ticket.team))
            .where(Ticket.tenant_id == tenant_id, Ticket.embedding.is_not(None)))
    if ticket is not None:
        stmt = stmt.where(Ticket.id != ticket.id)
    if resolved_only:
        stmt = stmt.where(State.category.in_(["resolved", "closed"]))
    if open_only:
        stmt = stmt.where(State.category.notin_(DONE))
    if account is not None:
        stmt = stmt.where(or_(Ticket.account_id == account.id, Ticket.account_id.is_(None)))
    if user is not None:
        stmt = stmt.where(visible_ticket_criteria(session, user))
    stmt = stmt.order_by(distance).limit(max(limit * 6, 30))
    scored = []
    for c, dist, lx in session.execute(stmt).unique().all():
        score = VEC_WEIGHT * (1 - float(dist)) + LEX_WEIGHT * float(lx or 0)
        if score >= min_score:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [{
        "id": c.id, "number": c.number, "subject": c.subject, "score": round(s, 3),
        "match": match_band(s)[0], "strength": match_band(s)[1],
        "state": c.state.name, "state_category": c.state.category,
        "priority": c.priority.key if c.priority else None,
        "category": str(c.category) if c.category_id else None,
        "account": c.account.name if c.account_id else None,
        "resolution_notes": (c.resolution_notes or "")[:600],
        "resolved_at": c.resolved_at, "created_at": c.created_at,
    } for s, c in scored[:limit]]


def similar_articles(session: Session, tenant_id, text, *, limit=4, public_only=False, account=None, min_score=None):
    min_score = settings.ai_similar_min_score if min_score is None else min_score
    vec = embed_text(text)
    distance = KnowledgeArticle.embedding.cosine_distance(list(vec))
    lex = func.similarity(KnowledgeArticle.title, text[:300])
    stmt = (select(KnowledgeArticle, distance.label("distance"), lex.label("lex"))
            .where(KnowledgeArticle.tenant_id == tenant_id, KnowledgeArticle.status == "published",
                   KnowledgeArticle.embedding.is_not(None)))
    if public_only:
        stmt = stmt.where(KnowledgeArticle.visibility == "public")
    if account is not None:
        stmt = stmt.where(or_(KnowledgeArticle.account_id == account.id, KnowledgeArticle.account_id.is_(None)))
    stmt = stmt.order_by(distance).limit(limit * 4)
    out = []
    for a, dist, lx in session.execute(stmt).all():
        score = VEC_WEIGHT * (1 - float(dist)) + LEX_WEIGHT * float(lx or 0)
        if score >= min_score:
            out.append({"id": a.id, "title": a.title, "score": round(score, 3), "match": match_band(score)[0],
                        "visibility": a.visibility, "excerpt": a.body[:280]})
    return sorted(out, key=lambda x: -x["score"])[:limit]


def detect_duplicates(session: Session, ticket):
    since = utcnow() - timedelta(days=14)
    hits = [h for h in similar_tickets(session, ticket.tenant_id, ticket=ticket, limit=3, open_only=True,
                                       account=ticket.account if ticket.account_id else None)
            if h["score"] >= settings.ai_duplicate_threshold and h["created_at"] >= since]
    return [{"id": h["id"], "number": h["number"], "subject": h["subject"], "score": h["score"], "match": h["match"]}
            for h in hits]


def knn_triage(session: Session, ticket):
    """Suggest category / team / impact / urgency by weighted vote of similar tickets."""
    neighbours = similar_tickets(session, ticket.tenant_id, ticket=ticket, limit=8)
    if not neighbours:
        return {}
    rows = {t.id: t for t in session.scalars(select(Ticket).where(Ticket.id.in_([n["id"] for n in neighbours])))}
    votes: dict = defaultdict(lambda: defaultdict(float))
    total = 0.0
    for n in neighbours:
        t, w = rows.get(n["id"]), n["score"] ** 2
        if not t:
            continue
        total += w
        for field in ("category_id", "team_id", "impact", "urgency"):
            v = getattr(t, field)
            if v is not None:
                votes[field][v] += w
    out = {"basis": "similar_tickets", "neighbours": [n["number"] for n in neighbours[:5]]}
    conf = []
    for field, counter in votes.items():
        best, w = max(counter.items(), key=lambda kv: kv[1])
        out[field] = best
        conf.append(w / total if total else 0)
    out["confidence"] = round(min(conf) if conf else 0, 2)
    return out


def llm_triage(session: Session, ticket):
    cats = {str(c): c.id for c in session.scalars(select(Category).where(Category.tenant_id == ticket.tenant_id))}
    teams = {t.name: t.id for t in session.scalars(select(Team).where(Team.tenant_id == ticket.tenant_id))}
    examples = similar_tickets(session, ticket.tenant_id, ticket=ticket, limit=4, resolved_only=True)
    ex_txt = "\n".join(f"- {e['subject']} -> category={e['category']}" for e in examples) or "(none)"
    text = ticket_text(ticket)
    if settings.ai_redact_pii:
        text = redact(text)
    data = complete_json(
        "You are an ITIL service desk triage assistant. Classify IT support tickets.",
        f"Categories: {list(cats)}\nTeams: {list(teams)}\n"
        "Impact 1=enterprise-wide 2=department 3=team 4=single user. Urgency 1=work stopped 2=high 3=medium 4=low.\n"
        f"Similar past tickets:\n{ex_txt}\n\nTicket:\n{text[:6000]}\n\n"
        'Return {"category": one of categories or null, "team": one of teams or null, "impact": 1-4, '
        '"urgency": 1-4, "sentiment": "calm|frustrated|angry", "summary": "one sentence", "confidence": 0-1}',
    )
    return {
        "basis": "llm", "category_id": cats.get(data.get("category")), "team_id": teams.get(data.get("team")),
        "impact": int(data["impact"]) if str(data.get("impact", "")).isdigit() else None,
        "urgency": int(data["urgency"]) if str(data.get("urgency", "")).isdigit() else None,
        "sentiment": data.get("sentiment"), "summary": data.get("summary"),
        "confidence": float(data.get("confidence", 0.5)),
    }


def triage(session: Session, ticket):
    if llm_enabled():
        try:
            return llm_triage(session, ticket)
        except Exception:  # noqa: BLE001 - fall back to the offline vote
            pass
    return knn_triage(session, ticket)


def _interactions(session: Session, ticket, *, kinds=None, limit=None, newest_first=True):
    stmt = select(Interaction).options(joinedload(Interaction.author)).where(Interaction.ticket_id == ticket.id)
    if kinds:
        stmt = stmt.where(Interaction.kind.in_(kinds))
    stmt = stmt.order_by(Interaction.created_at.desc() if newest_first else Interaction.created_at)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).unique())


def _thread(session: Session, ticket, include_internal=True, limit=30):
    lines = [f"Subject: {ticket.subject}", f"Description: {ticket.description}"]
    rows = _interactions(session, ticket, kinds=None if include_internal else ["public"], limit=limit)
    for it in reversed(rows):
        who = "Agent" if (it.author and it.author.is_agent) else ("System" if it.kind == "system" else "Customer")
        lines.append(f"[{who}{' note' if it.kind == 'internal' else ''}] {it.body}")
    text = "\n".join(lines)
    return redact(text) if settings.ai_redact_pii else text


def summarize(session: Session, ticket):
    try:
        return complete("You summarise IT support tickets for the next agent picking them up. Be concise and factual.",
                        f"{_thread(session, ticket)[:12000]}\n\nWrite: 1) the problem in one sentence, 2) what has been "
                        "tried, 3) current status and the next step. Max 90 words.", 300).strip()
    except LLMUnavailable:
        last = next(iter(_interactions(session, ticket, kinds=["public", "internal"], limit=1)), None)
        count = session.scalar(select(func.count()).select_from(Interaction).where(Interaction.ticket_id == ticket.id))
        first = (ticket.description or ticket.subject).strip().split("\n")[0][:240]
        return (f"Problem: {first}\nStatus: {ticket.state.name}, priority {ticket.priority.key if ticket.priority else '-'}"
                f", {count} updates." + (f"\nLatest: {last.body[:240]}" if last else ""))


def draft_reply(session: Session, ticket, user=None):
    kb = similar_articles(session, ticket.tenant_id, ticket_text(ticket), limit=2)
    past = similar_tickets(session, ticket.tenant_id, ticket=ticket, limit=3, resolved_only=True)
    context = "\n".join([f"KB: {a['title']}: {a['excerpt']}" for a in kb] +
                        [f"Past fix ({p['number']}): {p['resolution_notes']}" for p in past if p["resolution_notes"]])
    name = ticket.requester.first_name if ticket.requester and ticket.requester.first_name else "there"
    try:
        text = complete(
            "You draft replies for IT support agents. Polite, specific, no promises you can't keep. Plain text.",
            f"{_thread(session, ticket, include_internal=False)[:8000]}\n\nRelevant knowledge:\n{redact(context)[:4000]}\n\n"
            f"Draft the agent's next reply to the customer (greet them as {name}). Max 140 words.", 400)
        return {"draft": text.strip(), "sources": [a["title"] for a in kb] + [p["number"] for p in past]}
    except LLMUnavailable:
        if past and past[0]["resolution_notes"]:
            body = (f"Hi {name},\n\nThanks for the details. This looks similar to an issue we resolved before "
                    f"({past[0]['number']}). The fix then was:\n\n{past[0]['resolution_notes']}\n\n"
                    "Could you try this and let us know if it helps?\n\nBest regards")
        elif kb:
            body = (f"Hi {name},\n\nThanks for reaching out. This guide should help: \"{kb[0]['title']}\".\n\n"
                    f"{kb[0]['excerpt']}\n\nLet us know if the issue persists.\n\nBest regards")
        else:
            body = (f"Hi {name},\n\nThanks for reporting this. We're looking into it and will update you shortly. "
                    "Could you share any error messages or screenshots and when it started?\n\nBest regards")
        return {"draft": body, "sources": [p["number"] for p in past[:1]] or [a["title"] for a in kb[:1]]}


def generate_article_draft(session: Session, ticket):
    """Generate a structured draft Knowledge Base article from a resolved ticket. Uses AI if available (with PII
    redaction and the configured provider) and falls back to a safe template if AI is unavailable or fails."""
    if llm_enabled():
        try:
            thread_text = _thread(session, ticket, include_internal=False)
            res_notes = ticket.resolution_notes or ""
            if settings.ai_redact_pii and res_notes:
                res_notes = redact(res_notes)
            system = (
                "You are an IT knowledge base author. Turn the resolved ticket into a general, reusable "
                "troubleshooting knowledge base article in Markdown.\n"
                "Rules:\n"
                "- Do NOT invent causes, commands, or details not supported by the ticket.\n"
                "- Do NOT include customer names, emails, phone numbers, credentials, or customer-specific identifiers.\n"
                "- If the cause is unknown or unverified, state 'Not documented' or omit it.\n"
                "- Focus on generalizable symptoms, root cause, and concrete resolution steps."
            )
            prompt = (
                f"Ticket Number: {ticket.number}\n"
                f"Subject: {redact(ticket.subject) if settings.ai_redact_pii else ticket.subject}\n"
                f"Category: {ticket.category}\n"
                f"Resolution Notes:\n{res_notes or 'Not provided'}\n\n"
                f"Ticket Discussion:\n{thread_text[:6000]}\n\n"
                "Return a JSON object with two fields:\n"
                '- "title": concise, generalized article title\n'
                '- "body": structured markdown article with ## Problem, ## Symptoms, ## Cause, ## Resolution, ## Verification (if known)'
            )
            data = complete_json(system, prompt, max_tokens=1000)
            title = (data.get("title") or ticket.subject).strip()[:300]
            body = (data.get("body") or "").strip()
            if title and body and len(body) >= 40:
                body += f"\n\n_Source: {ticket.number}_"
                return {"title": title, "body": body, "method": "ai"}
        except Exception:  # noqa: BLE001
            pass

    body = (f"## Symptoms\n{ticket.description}\n\n## Cause\n_To be confirmed_\n\n## Resolution\n"
            f"{ticket.resolution_notes or '_Describe the fix_'}\n\n_Source: {ticket.number}_")
    return {"title": ticket.subject, "body": body, "method": "template"}
