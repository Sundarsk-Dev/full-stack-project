"""Prepare/check private human-review worksheets from existing captures; no network or labels."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_WORKSHEET_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 2000


class ReviewError(Exception):
    """Fixed diagnostic codes; never source content or private exception text."""


def require(condition, code):
    if not condition:
        raise ReviewError(code)


def read_bytes(path, limit):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, "file_too_large")
    return value


def catalog_at(root):
    return json.loads(read_bytes(root / "eval/source-candidates.json", 64 * 1024))


def load_capture(relative, root, catalog):
    require(isinstance(relative, str), "invalid_capture_path")
    parts = PurePosixPath(relative).parts
    require(len(parts) == 5 and parts[:3] == ("var", "eval", "sources")
            and "\\" not in relative and ".." not in parts
            and re.fullmatch(r"[0-9a-f]{32}", parts[-1]), "invalid_capture_path")
    capture = (root / relative).resolve()
    require(capture.is_relative_to(root / "var/eval/sources"), "capture_outside_sources")
    for name in ("capture.json", "raw.bin", "text.txt"):
        require((capture / name).resolve().parent == capture, "capture_file_outside_directory")
    metadata = json.loads(read_bytes(capture / "capture.json", 16 * 1024))
    candidates = {document["id"]: {**document, "family": family["id"], "split": family["split"]}
                  for family in catalog["policy_families"] for document in family["documents"]}
    expected = candidates.get(parts[3])
    require(expected is not None and metadata.get("role") == "policy", "policy_capture_required")
    require(all(metadata.get(key) == value for key, value in expected.items()), "capture_catalog_mismatch")
    raw = read_bytes(capture / "raw.bin", MAX_SOURCE_BYTES)
    text_bytes = read_bytes(capture / "text.txt", MAX_SOURCE_BYTES)
    require(sha256(raw).hexdigest() == metadata["raw_sha256"]
            and sha256(text_bytes).hexdigest() == metadata["text_sha256"], "capture_hash_mismatch")
    text = text_bytes.decode("utf-8")  # Preserve CRLF and Unicode code-point positions.
    require(text.strip() and "\ufffd" not in text, "invalid_capture_text")
    return metadata, text


def record_id(metadata, start, end):
    return f"{metadata['id']}-{metadata['text_sha256'][:16]}-{start}-{end}"


def prepare(captures, *, root=ROOT):
    root = Path(root).resolve()
    catalog = catalog_at(root)
    sources, records = {}, []
    for relative in captures:
        require(relative not in sources, "duplicate_capture")
        metadata, text = load_capture(relative, root, catalog)
        sources[relative] = {"metadata": metadata, "review": {
            "content": None, "rights": None, "effective_date": None,
            "license_reference": None, "attribution": None, "reviewer": None, "reviewed_at": None}}
        # Mechanical nonempty lines, including navigation. People select clauses and expand context.
        spans = [(line.start() + len(line.group()) - len(line.group().lstrip()),
                  line.end() - len(line.group()) + len(line.group().rstrip()))
                 for line in re.finditer(r"[^\r\n]+", text) if line.group().strip()]
        require(len(records) + len(spans) <= MAX_RECORDS, "too_many_candidates")
        for index, (start, end) in enumerate(spans):
            context_start = spans[max(0, index - 1)][0]
            context_end = spans[min(len(spans) - 1, index + 1)][1]
            records.append({"id": record_id(metadata, start, end), "capture": relative,
                            "start": start, "end": end, "quote": text[start:end],
                            "context": {"start": context_start, "end": context_end,
                                        "text": text[context_start:context_end]},
                            "selected": False, "human_review": None})
    require(sources and records, "empty_worksheet")
    return {"schema_version": 1, "purpose": "human_review_draft", "catalog_revision": catalog["revision"],
            "sources": sources, "records": records}


def check(worksheet, *, root=ROOT):
    root = Path(root).resolve()
    catalog = catalog_at(root)
    require(worksheet.get("schema_version") == 1 and worksheet.get("purpose") == "human_review_draft"
            and worksheet.get("catalog_revision") == catalog["revision"], "worksheet_revision_mismatch")
    sources, records = worksheet["sources"], worksheet["records"]
    require(isinstance(sources, dict) and 0 < len(sources) <= 32 and isinstance(records, list)
            and 0 < len(records) <= MAX_RECORDS, "invalid_worksheet_size")
    captures = {}
    for relative, source in sources.items():
        metadata, text = load_capture(relative, root, catalog)
        require(source["metadata"] == metadata, "worksheet_provenance_changed")
        captures[relative] = metadata, text
    ids, selected, duplicates = set(), [], defaultdict(list)
    for record in records:
        require(record["capture"] in captures, "unknown_record_capture")
        metadata, text = captures[record["capture"]]
        start, end = record["start"], record["end"]
        context = record["context"]
        left, right = context["start"], context["end"]
        require(all(type(value) is int for value in (start, end, left, right))
                and 0 <= left <= start < end <= right <= len(text), "invalid_offsets")
        require(record["quote"] == text[start:end] and context["text"] == text[left:right], "excerpt_mismatch")
        require(record["id"] == record_id(metadata, start, end) and record["id"] not in ids, "invalid_record_id")
        ids.add(record["id"])
        require(type(record["selected"]) is bool and (record["human_review"] is None
                or isinstance(record["human_review"], dict)), "invalid_review_fields")
        if record["selected"]:
            selected.append(metadata)
            normalized = " ".join(unicodedata.normalize("NFKC", record["quote"]).casefold().split())
            duplicates[normalized].append((metadata["split"], record["id"]))
    cross_split = [sorted(item[1] for item in group) for group in duplicates.values()
                   if len({item[0] for item in group}) > 1]
    return {"integrity": "pass", "scope": "capture_hashes_provenance_offsets_and_exact_split_duplicates",
            "captures": len(captures), "candidates": len(records), "selected_candidates": len(selected),
            "selected_families": len({item["family"] for item in selected}),
            "selected_heldout_candidates": sum(item["split"] == "heldout" for item in selected),
            "cross_split_exact_duplicates": cross_split, "human_labels_verified": False, "corpus_ready": False}


def worksheet_path(value, root):
    path = (root / value).resolve()
    require(path.is_relative_to(root / "var/eval/reviews") and path.suffix == ".json", "invalid_worksheet_path")
    return path


def save_new(worksheet, destination):
    data = (json.dumps(worksheet, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    require(len(data) <= MAX_WORKSHEET_BYTES, "worksheet_too_large")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:  # Never overwrite human edits or an earlier draft.
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare", help="Create a new unlabeled worksheet from explicit capture folders")
    build.add_argument("captures", nargs="+", help="Project-relative var/eval/sources paths using forward slashes")
    build.add_argument("--output", required=True, help="New .json path under var/eval/reviews")
    inspect = commands.add_parser("check", help="Check integrity/split screening only; never certifies human labels")
    inspect.add_argument("worksheet", help=".json path under var/eval/reviews")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            destination = worksheet_path(args.output, ROOT)
            worksheet = prepare(args.captures)
        else:
            destination = worksheet_path(args.worksheet, ROOT)
            worksheet = json.loads(read_bytes(destination, MAX_WORKSHEET_BYTES))
        report = check(worksheet)
        if args.command == "prepare":
            save_new(worksheet, destination)
        print(json.dumps(report))  # Counts and record IDs only; no source text, reviews or secrets.
        raise SystemExit(1 if report["cross_split_exact_duplicates"] else 0)
    except ReviewError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
    except FileExistsError:
        print(json.dumps({"status": "failed", "reason": "worksheet_exists_preserved"}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        print(json.dumps({"status": "failed", "reason": "invalid_or_unreadable_review_input"}))
    raise SystemExit(1)


if __name__ == "__main__": main()
