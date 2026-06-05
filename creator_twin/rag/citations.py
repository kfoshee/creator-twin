"""Citation formatting: every answer can expose platform, title, URL,
source_type and confidence per supporting chunk."""


def format_sources(chunks: list, limit: int = 6) -> list:
    out, seen = [], set()
    for c in chunks[:limit * 2]:
        meta = c.get("metadata", {})
        key = (c["source_type"], meta.get("title", ""), c.get("platform"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "platform": c.get("platform") or meta.get("platform", ""),
            "title": meta.get("title", ""),
            "url": meta.get("url", ""),
            "chunk_type": c["chunk_type"],
            "source_type": c["source_type"],
            "confidence": c["confidence"],
            "excerpt": c["text"][:160],
        })
        if len(out) >= limit:
            break
    return out


def context_block(chunks: list) -> str:
    return "\n\n".join(
        f"[chunk {i+1} | platform={c.get('platform') or 'general'} | {c['chunk_type']} | "
        f"source={c['source_type']} | confidence={c['confidence']}]\n{c['text'][:900]}"
        for i, c in enumerate(chunks))
