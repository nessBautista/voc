"""Adapters map pages, never touch storage. Network access is injected."""

import hashlib
import hmac
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import utc


class LimitReached(RuntimeError):
    pass


@dataclass
class Response:
    status: int
    body: bytes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # no unbudgeted follow-up requests


class HTTPTransport:
    def request(self, url, body, *, byte_limit, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LimitReached("time_limit")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": "sonar-lab-collector/0.1 (bounded research probe)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        try:
            response = opener.open(request, timeout=min(10, remaining))
        except urllib.error.HTTPError as error:
            response = error
        chunks = []
        size = 0
        with response:
            while True:
                if time.monotonic() >= deadline:
                    raise LimitReached("time_limit")
                chunk = response.read(min(65536, byte_limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > byte_limit:
                    raise LimitReached("response_limit")
                chunks.append(chunk)
            return Response(response.status, b"".join(chunks))


def text_or_none(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("expected text")
    return value if value.strip() else None


def label(entry, key):
    value = entry.get(key, {})
    if not isinstance(value, dict):
        raise TypeError("invalid Apple label")
    return value.get("label")


class AppleSource:
    page_size = 50
    interval = 0.5
    sort = "mostrecent"
    provider = "apple-rss"
    parser_version = "apple-rss-v1"

    def __init__(self, cfg, salt, salt_version):
        self.cfg, self.salt, self.salt_version = cfg, salt, salt_version

    def request(self, cursor):
        page = cursor or 1
        return (
            f"https://itunes.apple.com/{self.cfg.country}/rss/customerreviews/page={page}/id={self.cfg.target}/sortby=mostrecent/json",
            None,
        )

    def parse(self, response, cursor):
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        data = json.loads(response.body)
        if not isinstance(data.get("feed"), dict):
            raise TypeError("missing Apple feed")
        entries = data["feed"].get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise TypeError("invalid Apple entries")
        items = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError("invalid Apple entry")
            if "im:rating" not in entry and "im:name" in entry:
                continue  # narrowly recognise app metadata, not a malformed review
            items.append(entry)
        page = cursor or 1
        return items, page + 1 if page < 10 else None

    def record(self, raw):
        author = raw.get("author", {})
        uri = label(author, "uri") if isinstance(author, dict) else None
        match = re.search(r"/id(\d+)$", uri or "")
        author_hash = None
        if match:
            author_hash = (
                self.salt_version
                + ":"
                + hmac.new(self.salt, match[1].encode(), hashlib.sha256).hexdigest()
            )
        rating = label(raw, "im:rating")
        return {
            "source_id": label(raw, "id"),
            "updated_at": utc(label(raw, "updated")),
            "text": text_or_none(label(raw, "content")),
            "title": text_or_none(label(raw, "title")),
            "rating": int(rating) if rating is not None else None,
            "app_version": text_or_none(label(raw, "im:version")),
            "author_hash": author_hash,
        }


def play_helpers():
    # Importing the pinned third-party package changes the global TLS factory.
    # Use only its wire formatter/field specs and restore that side effect.
    previous = ssl._create_default_https_context
    try:
        from google_play_scraper.constants.element import ElementSpecs
        from google_play_scraper.constants.regex import Regex
        from google_play_scraper.constants.request import Formats
    finally:
        ssl._create_default_https_context = previous
    return ElementSpecs, Formats, Regex


class PlaySource:
    page_size = 100
    interval = 1.2
    sort = "newest"
    provider = "google-play-web"
    parser_version = "google-play-scraper-1.2.7+strict-v1"

    def __init__(self, cfg, salt, salt_version):
        self.cfg = cfg

    def request(self, cursor):
        _, formats, _ = play_helpers()
        return (
            formats.Reviews.build(lang=self.cfg.lang, country=self.cfg.country),
            formats.Reviews.build_body(
                self.cfg.target,
                2,
                min(self.page_size, self.cfg.max_items),
                "null",
                "null",
                cursor,
            ),
        )

    def parse(self, response, cursor):
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        _, _, regex = play_helpers()
        matches = regex.REVIEWS.findall(response.body.decode("utf-8"))
        if len(matches) != 1:
            raise ValueError("unrecognised Play response framing")
        frame = json.loads(matches[0])
        payload = json.loads(frame[0][2])
        if not isinstance(payload, list) or len(payload) < 2:
            raise ValueError("unrecognised Play payload")
        items = payload[0] or []
        if not isinstance(items, list):
            raise TypeError("invalid Play items")
        token_container = payload[-2]
        if token_container is None:
            token = None
        elif isinstance(token_container, list) and token_container:
            token = token_container[-1]
        else:
            raise ValueError("unknown Play continuation structure")
        if token is not None and (not isinstance(token, str) or not token):
            raise ValueError("invalid Play continuation token")
        if token is not None and token == cursor:
            raise ValueError("Play continuation token did not advance")
        return items, token

    def record(self, raw):
        specs, _, _ = play_helpers()
        # Do not use the library's naive datetime conversion.
        epoch = raw[5][0]
        if type(epoch) is not int:
            raise ValueError("missing Play epoch")
        fields = {
            k: specs.Review[k].extract_content(raw)
            for k in ("reviewId", "content", "score", "reviewCreatedVersion")
        }
        return {
            "source_id": fields["reviewId"],
            "updated_at": utc(datetime.fromtimestamp(epoch, UTC).isoformat()),
            "text": text_or_none(fields["content"]),
            "title": None,
            "rating": fields["score"],
            "app_version": text_or_none(fields["reviewCreatedVersion"]),
            "author_hash": None,
        }


ADAPTERS = {"appstore": AppleSource, "playstore": PlaySource}
