"""Answer guidance: how the twin should phrase product takes for THIS creator.

Generated once during the starter build (Gemini call 2 inside
starter_taste_model) and stored on the fingerprint under 'answer_guidance'.
This module is the read path with a deterministic fallback — zero LLM calls.
"""
DEFAULT_GUIDANCE = {
    "voice_rules": ["First person, direct, practical"],
    "price_rules": ["Lead with value for money", "Never invent a price you did not verify"],
    "creator_specific_take_rules": ["Ground every take in what this creator actually covers"],
    "retailer_rules": ["Point to the store cards below instead of telling people to go check"],
    "answer_do": ["Give a clear verdict", "Offer one concrete alternative when passing"],
    "answer_dont": ["No em dashes", "No firsthand-testing claims", "No generic filler"],
}


def get_answer_guidance(creator_id: str) -> dict:
    """Returns the stored guidance dict, falling back to safe defaults."""
    from .creator_fingerprint import get_fingerprint
    profile = get_fingerprint(creator_id) or {}
    g = profile.get("answer_guidance") or {}
    if not any(g.get(k) for k in DEFAULT_GUIDANCE):
        # legacy fingerprints kept these flat
        g = {"voice_rules": profile.get("product_take_rules") or [],
             "price_rules": profile.get("price_value_rules") or [],
             "creator_specific_take_rules": profile.get("repeated_advice") or [],
             "retailer_rules": profile.get("retailer_rules") or [],
             "answer_do": profile.get("answer_do") or [],
             "answer_dont": profile.get("boundaries_and_disallowed_claims") or []}
    return {k: (g.get(k) or DEFAULT_GUIDANCE[k]) for k in DEFAULT_GUIDANCE}


def guidance_block(creator_id: str, max_chars: int = 700) -> str:
    """Compact text block for the chat prompt."""
    g = get_answer_guidance(creator_id)
    lines = []
    for label, key in (("Voice", "voice_rules"), ("Price", "price_rules"),
                       ("Takes", "creator_specific_take_rules"), ("Stores", "retailer_rules"),
                       ("Do", "answer_do"), ("Don't", "answer_dont")):
        vals = [v for v in g[key][:3] if v]
        if vals:
            lines.append(f"{label}: " + "; ".join(vals))
    return "\n".join(lines)[:max_chars]
