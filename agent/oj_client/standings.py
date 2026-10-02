"""Official account-level Performance. No scoring formula or fuzzy identity match."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from .contests import contest_identifier


def account_identity(body):
    if not isinstance(body, dict):
        return None
    user_id = body.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        return None
    username = body.get("username")
    return {"user_id": user_id, "username": username if isinstance(username, str) else None}


@dataclass(frozen=True)
class OfficialPerformance:
    official_performance: Optional[int] = None
    official_performance_status: str = "unavailable"
    official_performance_source: Optional[str] = None
    official_performance_fetched_at: Optional[str] = None
    official_performance_scope: str = "contest_account"
    official_performance_identity: Optional[dict] = None
    official_performance_endpoint_sha256: Optional[str] = None

    def __post_init__(self):
        statuses = {"unavailable", "confirmed", "identity_unresolved", "identity_ambiguous",
                    "account_not_ranked", "invalid", "pending"}
        if self.official_performance_status not in statuses:
            raise ValueError("Invalid official Performance status")
        value = self.official_performance
        if self.official_performance_status == "confirmed":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4000:
                raise ValueError("Invalid official Performance")
        elif value is not None:
            raise ValueError("Unconfirmed Performance must be null")

    def as_dict(self):
        return asdict(self)


def parse_performance(body, contest_id, identity):
    contest_id = contest_identifier(contest_id)
    source = f"GET /api/v1/contests/{contest_id}/standings#rows[].performance"
    fields = dict(official_performance_source=source,
                  official_performance_fetched_at=datetime.now(timezone.utc).isoformat(),
                  official_performance_identity=identity)
    def outcome(status, value=None):
        return OfficialPerformance(value, status, **fields)
    if (not isinstance(identity, dict) or isinstance(identity.get("user_id"), bool)
            or not isinstance(identity.get("user_id"), int) or identity["user_id"] < 1):
        return outcome("identity_unresolved")
    if (not isinstance(body, dict) or isinstance(body.get("contest_id"), bool)
            or not isinstance(body.get("contest_id"), int)
            or body.get("contest_id") != int(contest_id) or not isinstance(body.get("rows"), list)
            or len(body["rows"]) > 10000):
        return outcome("invalid")
    rows = body["rows"]
    if any(not isinstance(row, dict) or isinstance(row.get("user_id"), bool)
           or not isinstance(row.get("user_id"), int) or row["user_id"] < 1 for row in rows):
        return outcome("invalid")
    matches = [row for row in rows if row["user_id"] == identity["user_id"]]
    if len(matches) > 1:
        return outcome("identity_ambiguous")
    if not matches:
        return outcome("account_not_ranked")
    row = matches[0]
    if "performance" not in row:
        return outcome("unavailable")
    value = row["performance"]
    if value is None:
        return outcome("pending")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4000:
        return outcome("invalid")
    return outcome("confirmed", value)
