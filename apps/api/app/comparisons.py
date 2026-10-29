"""Saved comparisons: exact alignment, bounded tentative similarity, no interpretation calls."""
from collections import defaultdict, deque
from itertools import islice
import re

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from .db import timestamp
from .models import Analysis, Comparison, Document, DocumentVersion, User
from .reports import owned_analysis, report_data
from .scoring import APPLICABLE, PROFILE_REVISION

MAX_SIMILARITY_PAIRS = 4096
DIFF_REVISION = "exact-token-dice-v1"


def basis(analysis, version, document):
    required = {"extractor", "segmenter", "rules", "library", "rubric"}
    if (not required <= analysis.engine.keys() or analysis.profile_revision != PROFILE_REVISION
            or analysis.engine["extractor"] != version.extractor_version):
        raise HTTPException(409, "Saved settings are incomplete. Reanalyze both captures with common settings first.")
    ai = None
    if analysis.effective_mode == "hybrid":
        identities = {(p.get("provider"), p.get("model"), p.get("prompt_revision")) for p in (analysis.ai_provenance or [])}
        if len(identities) != 1 or not all(next(iter(identities))):
            raise HTTPException(409, "Hybrid provenance is incomplete. Reanalyze with common settings and consent.")
        ai = list(next(iter(identities)))
    elif analysis.effective_mode != "rules_only":
        raise HTTPException(409, "The saved review mode is unsupported.")
    return {"document_type": document.document_type, "groups": sorted(APPLICABLE[document.document_type]),
        "engine": analysis.engine, "effective_mode": analysis.effective_mode, "ai": ai, "profile_revision": analysis.profile_revision}


def interpretation(clause):
    return (clause["substantive"], clause["unrated_reason"] if not clause["findings"] else None,
            sorted({(f["category"], f["severity"]) for f in clause["findings"]}))


def align(left, right):
    """Every passage is represented once. Truncated similarity never approves meaning."""
    buckets = defaultdict(deque)
    for index, clause in enumerate(right): buckets[clause["text"]].append(index)
    matched, used = {}, set()
    for index, clause in enumerate(left):
        if buckets[clause["text"]]:
            target = buckets[clause["text"]].popleft()
            matched[index] = (target, None)
            used.add(target)
    missing_left = [i for i in range(len(left)) if i not in matched]
    missing_right = [j for j in range(len(right)) if j not in used]
    tokens = lambda text: set(islice((m.group().lower() for m in re.finditer(r"\w+", text[:2048])), 128))
    left_tokens = {i: tokens(left[i]["text"]) for i in missing_left}
    right_tokens = {j: tokens(right[j]["text"]) for j in missing_right}
    edges, pairs = [], 0
    for i in missing_left:
        for j in missing_right:
            if pairs >= MAX_SIMILARITY_PAIRS: break
            pairs += 1
            a, b = left_tokens[i], right_tokens[j]
            similarity = 2 * len(a & b) / (len(a) + len(b)) if a and b else 0
            if similarity >= .6: edges.append((-similarity, i, j))
        if pairs >= MAX_SIMILARITY_PAIRS: break
    for negative, i, j in sorted(edges):
        if i not in matched and j not in used:
            matched[i] = (j, round(-negative, 3)); used.add(j)
    rows = []
    for i, a in enumerate(left):
        j, similarity = matched.get(i, (None, None))
        b = right[j] if j is not None else None
        rows.append({"left": a["id"], "right": b["id"] if b else None,
            "text_change": "removed" if b is None else "unchanged" if a["text"] == b["text"] else "modified",
            "interpretation_changed": b is not None and interpretation(a) != interpretation(b), "similarity": similarity})
    rows.extend({"left": None, "right": b["id"], "text_change": "added", "interpretation_changed": False, "similarity": None}
                for j, b in enumerate(right) if j not in used)
    return rows, {"similarity_pairs": pairs, "similarity_limited": len(missing_left) * len(missing_right) > pairs,
                  "similarity_sample_characters": 2048, "similarity_sample_tokens": 128}


def create_comparison(db, owner_id, left_id, right_id, kind, profile):
    if left_id == right_id: raise HTTPException(422, "Choose two different saved reports.")
    # INSERT's owner FK also locks this row. Match enqueue/deletion before docs.
    db.execute(select(User.id).where(User.id == owner_id).with_for_update())
    left, lv, ld = owned_analysis(db, owner_id, left_id)
    right, rv, rd = owned_analysis(db, owner_id, right_id)
    # Serialize against tombstoning in a deterministic order; reports themselves are immutable.
    docs = db.scalars(select(Document).where(Document.id.in_([ld.id, rd.id]), Document.owner_id == owner_id)
                      .order_by(Document.id).with_for_update().execution_options(populate_existing=True)).all()
    if len(docs) != len({ld.id, rd.id}) or any(d.tombstoned for d in docs): raise HTTPException(404, "Report not found.")
    if (kind == "versions") != (ld.id == rd.id):
        raise HTTPException(422, "Version comparisons need one document; policy comparisons need two documents.")
    common = basis(left, lv, ld)
    if common != basis(right, rv, rd):
        raise HTTPException(409, "Reports use different types, engines or review modes/models. Reanalyze with common settings and required consent first.")
    reports = [report_data(db, owner_id, value) for value in (left_id, right_id)]
    if any(profile not in r["score_profiles"] for r in reports):
        raise HTTPException(409, "This scoring profile is unavailable for these saved reports.")
    rows, limits = align(reports[0]["clauses"], reports[1]["clauses"])
    summary = {"source_text_changed": lv.text != rv.text,
        "text_changes": sum(r["text_change"] != "unchanged" for r in rows),
        "interpretation_changes": sum(r["interpretation_changed"] for r in rows)}
    sides = [{"id": r["id"], "document_id": r["document_id"], "title": r["title"], "version_number": r["version_number"],
        "revision_number": r["revision_number"], "requested_mode": r["requested_mode"], "effective_mode": r["effective_mode"],
        "degradation_message": r["degradation_message"], "score": r["score_profiles"][profile]["score"]} for r in reports]
    row = Comparison(owner_id=owner_id, left_id=left_id, right_id=right_id, kind=kind, profile=profile,
        data={"basis": common, "diff_revision": DIFF_REVISION, "left": sides[0], "right": sides[1],
              "summary": summary, "limits": limits, "passages": rows})
    db.add(row); db.commit()
    return {"comparison_id": row.id}


def comparison_data(db, owner_id, comparison_id, limit=20, offset=0, changes_only=False):
    row = db.scalar(select(Comparison).where(Comparison.id == comparison_id, Comparison.owner_id == owner_id))
    if row is None: raise HTTPException(404, "Comparison not found.")
    reports = [report_data(db, owner_id, value) for value in (row.left_id, row.right_id)]
    clauses = [{c["id"]: c for c in r["clauses"]} for r in reports]
    passages = row.data["passages"]
    if changes_only: passages = [p for p in passages if p["text_change"] != "unchanged" or p["interpretation_changed"]]
    visible = []
    for passage in passages[offset:offset + limit]:
        item = {**passage}
        for index, side in enumerate(("left", "right")):
            clause = clauses[index].get(passage[side])
            item[side] = {k: clause[k] for k in ("id", "text", "start", "end", "substantive", "unrated_reason", "findings")} if clause else None
        visible.append(item)
    return {"id": row.id, "kind": row.kind, "profile": row.profile, "created_at": timestamp(row.created_at),
        **{k: v for k, v in row.data.items() if k != "passages"}, "passages": visible,
        "total": len(passages), "limit": limit, "offset": offset}


def comparison_history(db, owner_id, analysis_id, limit, offset):
    owned_analysis(db, owner_id, analysis_id)
    visible = select(Analysis.id).join(DocumentVersion, Analysis.version_id == DocumentVersion.id).join(Document).where(
        Analysis.owner_id == owner_id, Document.owner_id == owner_id, Document.tombstoned.is_(False))
    query = select(Comparison).where(Comparison.owner_id == owner_id, Comparison.left_id.in_(visible), Comparison.right_id.in_(visible),
        or_(Comparison.left_id == analysis_id, Comparison.right_id == analysis_id))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Comparison.created_at.desc(), Comparison.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [{"id": r.id, "created_at": timestamp(r.created_at), "profile": r.profile,
        "left_title": r.data["left"]["title"], "right_title": r.data["right"]["title"]} for r in rows], "total": total, "limit": limit, "offset": offset}
