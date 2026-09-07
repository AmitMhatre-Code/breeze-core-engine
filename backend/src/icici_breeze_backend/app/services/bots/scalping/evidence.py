"""The paper-evidence gate: a scalper may only go `live` on settings it has already traded.

Build-order step 9's precondition used to be a sentence in a document -- "a full session of
paper evidence on the production instance, which no code can assert" -- guarded by a
hardcoded `LIVE_DISPATCH_ENABLED = False` that a developer flipped by hand. That is a
precondition only in the sense that someone remembered it.

This module makes it a real one. Two halves, and the split matters:

* **The gate is objective and minimal.** A scalper becomes eligible for `live` once one
  paper session run has *completed a trading day* carrying the exact settings it would trade
  with. Not a cycle count -- a quiet day that produced two signals is still a day of evidence
  about those settings, and picking a number would substitute an invented threshold for the
  user's judgement.
* **The judgement is the user's, on real numbers.** `PaperEvidence` carries what those
  sessions actually did -- cycles, closed cycles, net P&L, friction, how each day ended --
  and the confirmation dialog puts it in front of them at the moment they arm. The gate says
  "you have evidence"; the user says "it is good enough".

**Why a config fingerprint rather than a flag.** Evidence is only evidence for the settings
that produced it. A user who paper-traded a 9-period EMA for a week and then moved to 21 has
no evidence for what the bot will now do. Hashing the material config and recording it on the
run means editing any parameter that moves the P&L invalidates the evidence *automatically* --
there is no "reset the gate" bookkeeping anyone can forget, because the count is a join on a
value that just changed.

Materiality is an **allowlist of exclusions, not inclusions**: everything in the config is
material unless named here. A field added later is therefore gated by default, which is the
right way round for a thing that decides whether real orders may be placed unattended.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


# Dotted paths into a scalper config that do NOT change what the bot would do with money.
#
# `mode` is the thing being gated -- including it would mean paper evidence never matched a
# live bot's hash and the gate could never open.
#
# `risk.api_budget_reserve_calls` is operational: it decides how much of the shared ICICI
# per-minute budget scalping must leave for the dashboard and a manual square-off. It can
# stop the bot entering, but it changes neither the signal nor the size nor the exit, so
# re-proving a week of paper for it would be friction with nothing behind it.
NON_MATERIAL_PATHS = frozenset({"mode", "risk.api_budget_reserve_calls"})


def _strip(node: Any, prefix: str = "") -> Any:
    """Drop the non-material paths, depth-first, leaving everything else untouched."""
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key in sorted(node):
        path = f"{prefix}{key}"
        if path in NON_MATERIAL_PATHS:
            continue
        out[key] = _strip(node[key], f"{path}.")
    return out


def material_config_hash(bot_type: str, config: Any) -> str:
    """Fingerprint the settings that decide what this bot does with money.

    The config is normalised through its pydantic model **before** hashing, so a blob that
    omits a defaulted field hashes identically to one that spells the default out. Without
    that, a user who opened the Signal tab and saved without changing anything would silently
    void their own evidence -- the most confusing possible behaviour for a gate.

    Falls back to hashing the raw blob if the model rejects it. A config that will not
    validate is not going live anyway (the PATCH path refuses it first), and returning a
    hash that never matches is the fail-closed answer.
    """
    from icici_breeze_backend.app.repositories.bots import _CONFIG_MODEL

    raw = config if isinstance(config, dict) else getattr(config, "__dict__", {}) or {}
    try:
        model = _CONFIG_MODEL[bot_type]
        normalised = model(**raw).model_dump(mode="json")
    except Exception:  # noqa: BLE001 -- see docstring: unhashable config must not open a gate
        _logger.debug("evidence: config did not normalise for %s", bot_type, exc_info=True)
        normalised = raw
    canonical = json.dumps(_strip(normalised), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EvidenceSession:
    """One completed paper trading day on the settings in question."""

    run_id: str
    trading_day: str
    reason_code: Optional[str]
    reason_text: Optional[str]
    cycles: int
    closed_cycles: int
    wins: int
    losses: int
    net_pnl: float
    friction: float


@dataclass(frozen=True)
class PaperEvidence:
    """What the user is being asked to judge, plus whether there is anything to judge."""

    config_hash: str
    sessions: list[EvidenceSession] = field(default_factory=list)

    @property
    def unlocked(self) -> bool:
        """One completed paper trading day on these exact settings is the whole bar."""
        return len(self.sessions) > 0

    @property
    def days(self) -> int:
        return len(self.sessions)

    @property
    def cycles(self) -> int:
        return sum(s.cycles for s in self.sessions)

    @property
    def closed_cycles(self) -> int:
        return sum(s.closed_cycles for s in self.sessions)

    @property
    def net_pnl(self) -> float:
        return round(sum(s.net_pnl for s in self.sessions), 2)

    @property
    def friction(self) -> float:
        return round(sum(s.friction for s in self.sessions), 2)

    @property
    def wins(self) -> int:
        return sum(s.wins for s in self.sessions)

    @property
    def losses(self) -> int:
        return sum(s.losses for s in self.sessions)


def gather(user_id: str, bot_type: str, config: Any) -> PaperEvidence:
    """Every completed paper trading day this bot has run on its *current* settings."""
    from icici_breeze_backend.app.repositories import bots as repo

    config_hash = material_config_hash(bot_type, config)
    try:
        sessions = repo.completed_paper_sessions(user_id, bot_type, config_hash)
    except Exception:  # noqa: BLE001 -- a gate that cannot read its evidence stays shut
        _logger.exception("evidence: could not read paper sessions for %s", bot_type)
        sessions = []
    return PaperEvidence(config_hash=config_hash, sessions=sessions)


def refusal_text(bot_type: str) -> str:
    """Why `live` was refused, in the user's terms rather than the gate's."""
    del bot_type
    return (
        "This bot has not yet completed a full paper trading day on these settings. "
        "Leave it in Paper for one trading day, then review what it did before switching "
        "to Live. Changing any setting that affects its P&L starts that over."
    )
