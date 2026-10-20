"""Source-only extraction of explicit block infobox fields.

Missing fields never imply default drops. Numeric hardness is not a tool tier.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path


BLOCK_NAMES = {"block", "block2", "infobox block", "infobox/block"}
MECHANISM_TEMPLATE_NAMES = BLOCK_NAMES | {"crafting", "smelting"}


def is_block_template(name: str) -> bool:
    return name.casefold().replace("_", " ").strip() in BLOCK_NAMES


def parser_version() -> str:
    dependency = Path(__file__).with_name("gold_claims.py")
    return hashlib.sha256(Path(__file__).read_bytes() + dependency.read_bytes()).hexdigest()[:8]


@lru_cache(maxsize=1)
def _name_resolver():
    from corpus.gold_claims import NameResolver

    return NameResolver()


def is_mechanism_template(name: str) -> bool:
    return name.casefold().replace("_", " ").strip() in MECHANISM_TEMPLATE_NAMES


def tool_payload(raw: str) -> dict:
    from corpus.gold_claims import normalize_display

    cleaned = normalize_display(raw).casefold()
    match = re.search(r"\b(?:(wooden|wood|stone|iron|diamond|netherite|golden|gold)\s+)?(pickaxe|axe|shovel|hoe|shears|sword)\b", cleaned)
    payload = {"raw_tool": raw}
    if match:
        payload["tool_class"] = match.group(2)
        if match.group(1):
            payload["minimum_tool_material"] = match.group(1)
    elif cleaned in {"any", "none", "hand", "by hand"}:
        payload["tool_class"] = "any"
    return payload


def parse_block_fields(body: str, subject: str, resolver) -> list[dict]:
    from corpus.gold_claims import normalize_display, parse_template, split_top_level

    name, _, named = parse_template(body)
    if not is_block_template(name) or not subject:
        return []
    base = {"extractor": "template", "deterministic": True, "template_name": name}
    results = []
    conditions = tool_payload(named["tool"]) if named.get("tool") else {}
    for field in ("drops", "drop"):
        raw = named.get(field, "")
        if not raw:
            continue
        drops, variants = [], []
        for value in split_top_level(raw, ";"):
            for piece in re.split(r"<br\s*/?>|\n\s*\*", value, flags=re.I):
                clean = normalize_display(piece).strip()
                if not clean:
                    continue
                own = re.match(r"^itself\b", clean, re.I)
                links = re.findall(r"\[\[([^]|]+)(?:\|[^]]+)?\]\]", piece)
                item = subject if own else resolver.resolve(clean)
                if item is None and links:
                    # An item link followed by a condition must not consume tool links.
                    item = resolver.resolve(links[0])
                if item:
                    drops.append(item)
                variants.append({"raw": piece.strip(), "item": item})
        if not drops:
            continue
        drop_conditions = dict(conditions)
        inline_tool = tool_payload(raw)
        if "tool_class" in inline_tool:
            drop_conditions["drop_tool"] = inline_tool
        payload = {**base, "drops": list(dict.fromkeys(drops)), "raw_drops": raw,
                   "variants": variants, "conditions": drop_conditions}
        # Preserve version/enchantment alternatives, rather than flattening them
        # into an unconditional claim consumed as an ordinary drop producer.
        if re.search(r"silk touch|\{\{(?:only|in|since|until)|\b(?:java|bedrock|before|after)\b", raw, re.I):
            payload["conditional_variants"] = True
        results.append({"claim_type": "drop", "subject_item": subject, "payload": payload})
    if conditions:
        results.append({"claim_type": "tool_requirement", "subject_item": subject,
                        "payload": {**base, **conditions}})
    elif named.get("hardness") and re.search(r"\b(?:requires?|needs?|at least)\b", normalize_display(named["hardness"]), re.I):
        requirement = tool_payload(named["hardness"])
        if "tool_class" in requirement:
            results.append({"claim_type": "tool_requirement", "subject_item": subject,
                            "payload": {**base, **requirement, "field": "hardness"}})
    return results


def extract_block_claims(text: str, page_title: str, resolver=None) -> list[dict]:
    from corpus.gold_claims import NameResolver, iter_templates, parse_template, split_top_level

    resolver = resolver or NameResolver()
    subject = resolver.resolve(page_title)
    history = re.search(r"^==+\s*History\s*==+\s*$", text, re.I | re.M)
    claims = []
    for offset, body in iter_templates(text):
        name, _, named = parse_template(body)
        if not is_block_template(name):
            continue
        subjects = {subject} if subject else set()
        if not subjects:
            for key, value in named.items():
                if re.fullmatch(r"(?:invimage|group)\d*", key):
                    subjects.update(item for part in split_top_level(value, ";") if (item := resolver.resolve(part)))
        for item in sorted(subjects):
            for claim in parse_block_fields(body, item, resolver):
                claims.append({**claim, "tense": "historical" if history and offset >= history.start() else "current",
                               "polarity": "positive", "span_text": text[offset:offset + len(body) + 4]})
    return claims


def is_template_chunk(text: str) -> bool:
    from corpus.gold_claims import iter_templates, parse_template

    templates = list(iter_templates(text))
    return bool(len(templates) == 1 and templates[0][0] == 0
                and len(templates[0][1]) + 4 == len(text)
                and is_mechanism_template(parse_template(templates[0][1])[0]))


def public_template_claims(
    text: str, page_title: str, chunk_id: str, versions=()
) -> list[dict]:
    from corpus.build_claims_cache import public_claim
    from corpus.gold_claims import (
        iter_templates,
        parse_crafting_variants,
        parse_smelting_variants,
        parse_template,
    )

    templates = list(iter_templates(text))
    if len(templates) != 1:
        return []
    _, body = templates[0]
    name, positional, named = parse_template(body)
    if is_block_template(name):
        bases = extract_block_claims(text, page_title)
    else:
        resolver = _name_resolver()
        bases = []
        source_versions = tuple(versions) or (None,)
        if name == "crafting":
            for version in source_versions:
                for output, payload in parse_crafting_variants(
                    positional, named, resolver, version
                ):
                    bases.append({
                        "claim_type": "recipe",
                        "subject_item": output,
                        "payload": payload,
                        "polarity": "positive",
                        "tense": "current",
                        "span_text": text,
                    })
        elif name == "smelting":
            for output, payload in parse_smelting_variants(positional, named, resolver):
                bases.append({
                    "claim_type": "smelting",
                    "subject_item": output,
                    "payload": payload,
                    "polarity": "positive",
                    "tense": "current",
                    "span_text": text,
                })
    unique = {}
    for base in bases:
        base = {
            **base,
            "payload": {
                **base["payload"],
                "extractor": "template",
                "deterministic": True,
            },
        }
        key = (base["claim_type"], base["subject_item"], repr(base["payload"]))
        unique[key] = public_claim(base, chunk_id, parser_version())
    return list(unique.values())


def public_prose_drop_claims(
    text: str, page_title: str, chunk_id: str, subject_items: list[str]
) -> list[dict]:
    """Parse only explicit current-tense drop sentences from rendered prose."""
    from corpus.build_claims_cache import public_claim
    resolver = _name_resolver()
    claims = []
    sentences = re.findall(r"(?:^|(?<=\.))\s*[^.]*\bdrops?\b[^.]*\.", text, re.I)
    for raw_sentence in sentences:
        sentence = raw_sentence.strip()
        lower = sentence.casefold()
        if "historical claim:" in lower or "drops nothing" in lower:
            continue
        tail_match = re.search(r"\bdrops?\b(.*)", sentence, re.I)
        if not tail_match:
            continue
        before_drop = sentence[:tail_match.start()].casefold()
        if re.search(
            r"(?:do|does|did|can|could|will|would)\s+not\s*$|"
            r"(?:no longer|never|cannot|can't)\s*$|"
            r"(?:chance|probability)\s+to\s*$",
            before_drop,
        ):
            continue
        title_words = re.findall(r"[a-z0-9]+", page_title.casefold())
        if title_words:
            title_pattern = r"\b" + r"\s+".join(
                re.escape(word) for word in title_words[:-1]
            )
            if len(title_words) > 1:
                title_pattern += r"\s+"
            title_pattern += re.escape(title_words[-1]) + r"s?\b"
            if not re.search(title_pattern, before_drop):
                continue
        tail = tail_match.group(1)
        if re.search(r"\b(?:itself|themselves)\b", tail, re.I):
            pairs = [(subject, subject) for subject in subject_items]
        else:
            normalized_tail = re.sub(r"[^a-z0-9_ -]+", " ", tail.casefold())
            candidates = []
            words = normalized_tail.split()
            for start in range(len(words)):
                for width in range(min(5, len(words) - start), 0, -1):
                    alias = " ".join(words[start:start + width])
                    identifier = resolver.aliases.get(alias)
                    if identifier:
                        candidates.append((start, -width, identifier))
                        break
            outputs = []
            for _, _, identifier in sorted(candidates):
                if identifier not in outputs:
                    outputs.append(identifier)
            pairs = [(subject, output) for subject in subject_items for output in outputs[:2]]
        for subject, output in pairs:
            claims.append(public_claim({
                "claim_type": "drop",
                "subject_item": subject,
                "payload": {"drops": [output], "silk_touch": False,
                            "extractor": "deterministic_prose"},
                "polarity": "positive",
                "tense": "current",
                "span_text": sentence,
            }, chunk_id, parser_version()))
    return claims
