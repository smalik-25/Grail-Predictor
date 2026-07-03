"""Reddit mention volume via PRAW, r/rickowens and similar brand subs.

Access: the official Reddit API through PRAW, free tier, well within rate
limits. The cleanest source in the whole ingestion layer: real API, real
terms, no gray area. Mention matching against specific pieces happens
downstream in resolution/features; ingestion just captures posts faithfully.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ingestion.base import has_env, live_mode, load_fixture

logger = logging.getLogger(__name__)

PLATFORM = "social"

DEFAULT_SUBREDDITS: tuple[str, ...] = (
    "rickowens",
    "fashionreps",  # noise source, but early hype often shows here first
    "japanesestreetwear",
    "Undercoverism",
    "grailed",
)


@dataclass(frozen=True)
class SocialMention:
    """One Reddit post from a watched sub. Comment-level volume comes later."""

    subreddit: str
    post_id: str
    title: str
    url: str | None
    created_date: str  # ISO date
    score: int
    num_comments: int


class RedditClient:
    """Yields SocialMention records, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode() and has_env(
            "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"
        )

    def mentions(self, subreddits: tuple[str, ...] = DEFAULT_SUBREDDITS) -> Iterator[SocialMention]:
        if not self.live:
            logger.info("social: fixture mode (INGEST_LIVE unset or Reddit creds missing)")
            for row in load_fixture(PLATFORM, self._fixture_dir):
                yield SocialMention(
                    subreddit=row["subreddit"],
                    post_id=row["post_id"],
                    title=row["title"],
                    url=row.get("url"),
                    created_date=row["created_date"],
                    score=int(row.get("score", 0)),
                    num_comments=int(row.get("num_comments", 0)),
                )
            return
        yield from self._live_mentions(subreddits)

    def _live_mentions(self, subreddits: tuple[str, ...]) -> Iterator[SocialMention]:
        import datetime
        import os

        import praw

        reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
        )
        for name in subreddits:
            try:
                for post in reddit.subreddit(name).new(limit=100):
                    yield SocialMention(
                        subreddit=name,
                        post_id=post.id,
                        title=post.title,
                        url=f"https://www.reddit.com{post.permalink}",
                        created_date=datetime.datetime.fromtimestamp(
                            post.created_utc, tz=datetime.timezone.utc
                        ).date().isoformat(),
                        score=post.score,
                        num_comments=post.num_comments,
                    )
            except Exception as exc:  # noqa: BLE001 - praw raises many API exception types
                logger.warning("social: subreddit %r failed (%s); continuing", name, exc)
