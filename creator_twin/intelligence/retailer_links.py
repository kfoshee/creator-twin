"""Retailer search links — the app does the comparison legwork, not the user."""
import urllib.parse


def _q(query):
    return urllib.parse.quote_plus((query or "").strip())


def amazon_search_url(query):
    return f"https://www.amazon.com/s?k={_q(query)}"


def target_search_url(query):
    return f"https://www.target.com/s?searchTerm={_q(query)}"


def walmart_search_url(query):
    return f"https://www.walmart.com/search?q={_q(query)}"


def cvs_search_url(query):
    return f"https://www.cvs.com/search?searchTerm={_q(query)}"


def walgreens_search_url(query):
    return f"https://www.walgreens.com/search/results.jsp?Ntt={_q(query)}"


def google_shopping_search_url(query):
    return f"https://www.google.com/search?tbm=shop&q={_q(query)}"
