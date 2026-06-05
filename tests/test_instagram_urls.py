"""Instagram URL classification acceptance tests.

Run: .venv/bin/python tests/test_instagram_urls.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.source_detection import classify_instagram_url, detect_creator_source

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name}")


# 1. post URL
r = classify_instagram_url("https://www.instagram.com/p/DIjP5aZxpdu/")
check("post: type instagram_post", r["input_type"] == "instagram_post")
check("post: shortcode extracted", r["shortcode"] == "DIjP5aZxpdu")
check("post: handle is None (not 'p')", r["handle"] is None)
check("post: should_build_creator false", r["should_build_creator"] is False)
check("post: needs_profile_resolution", r["needs_profile_resolution"] is True)

# 2. reel URL
r2 = classify_instagram_url("https://www.instagram.com/reel/ABC123/")
check("reel: type instagram_reel", r2["input_type"] == "instagram_reel")
check("reel: shortcode ABC123", r2["shortcode"] == "ABC123")
check("reel: handle is None (not 'reel')", r2["handle"] is None)

# 3. profile URL
r3 = classify_instagram_url("https://www.instagram.com/couponingwithtina/")
check("profile: type instagram_profile", r3["input_type"] == "instagram_profile")
check("profile: handle extracted", r3["handle"] == "couponingwithtina")
check("profile: should_build_creator true", r3["should_build_creator"] is True)

# 4. plain handle never assumes YouTube
d = detect_creator_source("@couponingwithtina")
check("plain handle: no primary platform", d["primary_platform"] is None)
check("plain handle: searches all platforms", d["should_search_all_platforms"] is True)

# 5. detect_creator_source on post URL keeps handle empty (no creator named 'p')
d2 = detect_creator_source("https://www.instagram.com/p/DIjP5aZxpdu/")
check("detect: post URL yields no handle", d2["normalized_handle"] == "")
check("detect: instagram details present", d2.get("instagram", {}).get("needs_profile_resolution") is True)

print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
