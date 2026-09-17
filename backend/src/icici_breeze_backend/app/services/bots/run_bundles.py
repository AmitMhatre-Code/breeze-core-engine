"""Collapse the run log into bundles for the Activity table.

A scanning bot writes a row every couple of minutes, so a day of identical `skipped` verdicts
buried the one run that did something. Bundling keeps every row (a bundle expands back into
them) while the table shows one line per stretch of unchanged behaviour.

Mirrored by `frontend/src/lib/bot-run-bundles.ts`, which bundles ranges of up to 7 days in the
browser; ranges beyond that are bundled here so a month never ships as tens of thousands of
rows. The two must produce the same bundles for the same runs.
"""
from __future__ import annotations

from typing import Optional

from icici_breeze_backend.app.domain.bots import BotRunBundle, BotRunRecord


def _reason_key(run: BotRunRecord) -> str:
    return run.reason_code or run.reason_text or ""


def bundle_audit_log(bundle: BotRunBundle) -> Optional[str]:
    """The bundle row's download. Live runs share one file per bot per day, so the latest run's
    link is every member's; a backtest's trail is its own, so only a lone backtest gets one."""
    if bundle.trigger == "backtest" and bundle.count > 1:
        return None
    return bundle.latest.audit_log


def bundle_runs(runs_newest_first: list[BotRunRecord]) -> list[BotRunBundle]:
    """Bundle runs given newest first (as `repo.list_runs` returns them).

    Runs are walked oldest first; each bot keeps one open bundle, which closes when that bot's
    next run differs in day, trigger or status. Bundles come back ordered by their latest run's
    position in the input, i.e. newest first with the repository's tie-break.
    """
    # (position of latest run in input, members oldest -> newest)
    groups: list[tuple[int, list[BotRunRecord]]] = []
    open_group: dict[str, int] = {}
    for pos in range(len(runs_newest_first) - 1, -1, -1):
        run = runs_newest_first[pos]
        day = (run.started_at or "")[:10]
        idx = open_group.get(run.bot_type)
        if idx is not None:
            last = groups[idx][1][-1]
            if (
                (last.started_at or "")[:10] == day
                and last.trigger == run.trigger
                and last.status == run.status
            ):
                groups[idx][1].append(run)
                groups[idx] = (pos, groups[idx][1])
                continue
        open_group[run.bot_type] = len(groups)
        groups.append((pos, [run]))

    groups.sort(key=lambda g: g[0])
    bundles: list[BotRunBundle] = []
    for _pos, members in groups:
        first, latest = members[0], members[-1]
        bundle = BotRunBundle(
            bot_type=latest.bot_type,
            trigger=latest.trigger,
            status=latest.status,
            date=(latest.started_at or "")[:10],
            count=len(members),
            first_started_at=first.started_at,
            last_started_at=latest.started_at,
            latest=latest,
            distinct_reasons=max(1, len({_reason_key(r) for r in members})),
        )
        bundle.audit_log = bundle_audit_log(bundle)
        bundles.append(bundle)
    return bundles
