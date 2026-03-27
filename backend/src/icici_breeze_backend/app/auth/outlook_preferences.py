"""Per-user storage for market outlook RSS feeds and prompt template."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

from icici_breeze_backend.app.core.config import DATA_PATH, USERS_DB
from icici_breeze_backend.app.domain.outlook_defaults import (
    DEFAULT_OUTLOOK_FEEDS,
    DEFAULT_OUTLOOK_PROMPT_TEMPLATE,
    DEFAULT_OUTLOOK_SYSTEM_PROMPT,
)


@dataclass
class OutlookFeedConfig:
    name: str
    url: str


@dataclass
class OutlookPreferences:
    user_id: str
    feeds: list[OutlookFeedConfig]
    prompt_template: str
    system_prompt: str
    using_default_feeds: bool
    using_default_prompt: bool
    using_default_system_prompt: bool


class OutlookPreferencesManager:
    def __init__(self):
        self._db_path = DATA_PATH + USERS_DB

    def _parse_feeds_json(self, raw: Optional[str]) -> list[OutlookFeedConfig]:
        if not raw:
            return [OutlookFeedConfig(name=n, url=u) for n, u in DEFAULT_OUTLOOK_FEEDS]
        try:
            items = json.loads(raw)
        except Exception:
            return [OutlookFeedConfig(name=n, url=u) for n, u in DEFAULT_OUTLOOK_FEEDS]
        out: list[OutlookFeedConfig] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if not name or not url:
                continue
            out.append(OutlookFeedConfig(name=name, url=url))
        return out or [OutlookFeedConfig(name=n, url=u) for n, u in DEFAULT_OUTLOOK_FEEDS]

    def get(self, user_id: str) -> OutlookPreferences:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT feeds_json, prompt_template, system_prompt
                FROM user_outlook_preferences
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return OutlookPreferences(
                user_id=user_id,
                feeds=[OutlookFeedConfig(name=n, url=u) for n, u in DEFAULT_OUTLOOK_FEEDS],
                prompt_template=DEFAULT_OUTLOOK_PROMPT_TEMPLATE,
                system_prompt=DEFAULT_OUTLOOK_SYSTEM_PROMPT,
                using_default_feeds=True,
                using_default_prompt=True,
                using_default_system_prompt=True,
            )
        feeds = self._parse_feeds_json(row[0])
        prompt = str(row[1] or "").strip() or DEFAULT_OUTLOOK_PROMPT_TEMPLATE
        system_prompt = str(row[2] or "").strip() or DEFAULT_OUTLOOK_SYSTEM_PROMPT
        using_default_feeds = not bool(str(row[0] or "").strip())
        using_default_prompt = not bool(str(row[1] or "").strip())
        using_default_system_prompt = not bool(str(row[2] or "").strip())
        return OutlookPreferences(
            user_id=user_id,
            feeds=feeds,
            prompt_template=prompt,
            system_prompt=system_prompt,
            using_default_feeds=using_default_feeds,
            using_default_prompt=using_default_prompt,
            using_default_system_prompt=using_default_system_prompt,
        )

    def upsert(
        self,
        *,
        user_id: str,
        feeds: list[dict[str, str]],
        prompt_template: str,
        system_prompt: str,
    ) -> None:
        clean_feeds: list[dict[str, str]] = []
        for f in feeds:
            name = str((f or {}).get("name") or "").strip()
            url = str((f or {}).get("url") or "").strip()
            if not name or not url:
                continue
            clean_feeds.append({"name": name, "url": url})
        prompt = str(prompt_template or "").strip()
        system = str(system_prompt or "").strip()
        feeds_json = json.dumps(clean_feeds) if clean_feeds else None
        prompt_db = prompt if prompt else None
        system_db = system if system else None
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO user_outlook_preferences(user_id, feeds_json, prompt_template, system_prompt, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    feeds_json=excluded.feeds_json,
                    prompt_template=excluded.prompt_template,
                    system_prompt=excluded.system_prompt,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, feeds_json, prompt_db, system_db),
            )
            conn.commit()

    def reset(self, *, user_id: str, reset_feeds: bool, reset_prompt: bool, reset_system_prompt: bool) -> None:
        with sqlite3.connect(self._db_path) as conn:
            if reset_feeds and reset_prompt and reset_system_prompt:
                conn.execute("DELETE FROM user_outlook_preferences WHERE user_id = ?", (user_id,))
            elif reset_feeds and not reset_prompt and not reset_system_prompt:
                conn.execute(
                    """
                    INSERT INTO user_outlook_preferences(user_id, feeds_json, prompt_template, system_prompt, created_at, updated_at)
                    VALUES (?, NULL, (SELECT prompt_template FROM user_outlook_preferences WHERE user_id = ?), (SELECT system_prompt FROM user_outlook_preferences WHERE user_id = ?), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        feeds_json=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, user_id, user_id),
                )
            elif reset_prompt and not reset_feeds and not reset_system_prompt:
                conn.execute(
                    """
                    INSERT INTO user_outlook_preferences(user_id, feeds_json, prompt_template, system_prompt, created_at, updated_at)
                    VALUES (?, (SELECT feeds_json FROM user_outlook_preferences WHERE user_id = ?), NULL, (SELECT system_prompt FROM user_outlook_preferences WHERE user_id = ?), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        prompt_template=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, user_id, user_id),
                )
            elif reset_system_prompt and not reset_feeds and not reset_prompt:
                conn.execute(
                    """
                    INSERT INTO user_outlook_preferences(user_id, feeds_json, prompt_template, system_prompt, created_at, updated_at)
                    VALUES (?, (SELECT feeds_json FROM user_outlook_preferences WHERE user_id = ?), (SELECT prompt_template FROM user_outlook_preferences WHERE user_id = ?), NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        system_prompt=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, user_id, user_id),
                )
            else:
                current = self.get(user_id)
                self.upsert(
                    user_id=user_id,
                    feeds=[{"name": f.name, "url": f.url} for f in ([] if reset_feeds else current.feeds)],
                    prompt_template="" if reset_prompt else current.prompt_template,
                    system_prompt="" if reset_system_prompt else current.system_prompt,
                )
                return
            conn.commit()
