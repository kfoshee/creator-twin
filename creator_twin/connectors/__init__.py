"""Connector registry. Every connector is optional and failure-tolerant."""


def get_connector(platform: str):
    if platform == "youtube":
        from .youtube import YouTubeConnector
        return YouTubeConnector
    if platform == "instagram":
        from .instagram import InstagramConnector
        return InstagramConnector
    if platform == "tiktok":
        from .tiktok import TikTokConnector
        return TikTokConnector
    if platform == "x":
        from .twitter_x import XConnector
        return XConnector
    if platform == "threads":
        from .threads import ThreadsConnector
        return ThreadsConnector
    if platform == "website":
        from .website import WebsiteConnector
        return WebsiteConnector
    if platform == "newsletter":
        from .newsletter import NewsletterConnector
        return NewsletterConnector
    if platform == "podcast":
        from .podcast import PodcastConnector
        return PodcastConnector
    if platform == "uploads":
        from .uploaded_files import UploadedFilesConnector
        return UploadedFilesConnector
    raise ValueError(f"Unknown platform: {platform}")


ALL_PLATFORMS = ["youtube", "instagram", "tiktok", "x", "threads",
                 "website", "newsletter", "podcast", "uploads"]
