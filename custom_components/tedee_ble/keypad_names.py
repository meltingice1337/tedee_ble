"""Who used the keypad: names for the PIN and fingerprint IDs the lock reports.

A lock with a keypad reports each use as a trigger plus a small ID
(issue #16, Keypad PRO):

    trigger 0x0A, ID 0..n   - PIN, numbered in the order the PINs were created
    trigger 0x17, ID n      - fingerprint slot on a Keypad PRO / fingerprint reader

The two ID ranges are independent, so a name is looked up by method and ID.

PIN names come from the cloud's PIN list. The cloud has no fingerprint list
(the app reads it from the keypad over Bluetooth), but its activity log names
each fingerprint unlock. Those names are learned by matching a use the lock
reported to the activity entry with the same timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

METHOD_PIN = "pin"
METHOD_FINGERPRINT = "fingerprint"
METHODS = (METHOD_PIN, METHOD_FINGERPRINT)

# Accessory device types with a fingerprint reader: fingerprint module, Keypad PRO.
FINGERPRINT_READER_TYPES = {9, 10}

# Cloud activity events for an authorized use, by method. Rejected attempts
# (wrong PIN, unknown finger) never reach the lock notification stream.
ACTIVITY_EVENT_METHODS = {
    61: METHOD_PIN,  # unlock by PIN
    63: METHOD_PIN,  # pull spring by PIN
    64: METHOD_PIN,  # partially open by PIN
    65: METHOD_PIN,  # lock by keypad with PIN
    68: METHOD_PIN,  # force unlock by PIN
    76: METHOD_PIN,  # auto pull spring by PIN
    77: METHOD_FINGERPRINT,  # unlock by fingerprint
    78: METHOD_FINGERPRINT,  # force open by fingerprint
    79: METHOD_FINGERPRINT,  # partially open by fingerprint
    80: METHOD_FINGERPRINT,  # pull spring by fingerprint
    81: METHOD_FINGERPRINT,  # auto pull spring by fingerprint
}

# The lock stamps the activity entry, Home Assistant stamps the notification.
# Two fingers of one hand can follow each other within ~5 s, so the window
# stays below that and anything ambiguous is left unnamed.
MATCH_TOLERANCE_SECONDS = 3.0

FALLBACK_LABELS = {METHOD_PIN: "PIN", METHOD_FINGERPRINT: "Fingerprint"}


def fallback_name(method: str, access_id: int) -> str:
    """What to show while the name behind an ID is not known."""
    return f"{FALLBACK_LABELS[method]} {access_id}"


def _id_alias_map(items: Any) -> dict[str, str]:
    names: dict[str, str] = {}
    if not isinstance(items, list):
        return names
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id", item.get("pinId"))
        alias = item.get("alias")
        if isinstance(item_id, int) and not isinstance(item_id, bool) and alias:
            names[str(item_id)] = str(alias)
    return names


def parse_pin_list(result: Any) -> dict[str, dict[str, str]]:
    """Names from the cloud's PIN list (GET /my/Lock/{id}/pin).

    The app reads `{"pins": [{"id", "alias"}, ...]}`. A bare list and a
    `fingerprints` list are accepted too, should the cloud send them.
    """
    if isinstance(result, list):
        return {METHOD_PIN: _id_alias_map(result)}
    if not isinstance(result, dict):
        return {}
    names = {METHOD_PIN: _id_alias_map(result.get("pins"))}
    fingerprints = _id_alias_map(result.get("fingerprints"))
    if fingerprints:
        names[METHOD_FINGERPRINT] = fingerprints
    return names


def _activity_time(entry: dict) -> float | None:
    """Epoch seconds of an activity entry; the cloud sends naive UTC."""
    try:
        parsed = datetime.fromisoformat(str(entry.get("date")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def match_activity(
    observations: Iterable[tuple[float, str, int]],
    activities: Iterable[dict],
    tolerance: float = MATCH_TOLERANCE_SECONDS,
) -> dict[str, dict[str, str]]:
    """Learn names by matching reported uses to cloud activity entries.

    `observations` are (epoch seconds, method, ID) for uses the lock reported.
    A use is named only when it is the single use of its method within
    `tolerance` of an activity entry, and every such entry in range carries
    the same name. A wrong name would stick, a missing one is learned on a
    later use.
    """
    observations = list(observations)
    named: dict[str, list[tuple[float, str]]] = {m: [] for m in METHODS}
    for entry in activities:
        method = ACTIVITY_EVENT_METHODS.get(entry.get("event"))
        alias = entry.get("pinAlias") or entry.get("accessLinkName")
        when = _activity_time(entry)
        if method and alias and when is not None:
            named[method].append((when, str(alias)))

    learned: dict[str, dict[str, str]] = {}
    for when, method, access_id in observations:
        aliases = {
            alias for t, alias in named.get(method, ()) if abs(t - when) <= tolerance
        }
        if len(aliases) != 1:
            continue
        # Another ID used around the same moment could own that entry instead.
        if any(
            other_method == method
            and other_id != access_id
            and abs(other_when - when) <= 2 * tolerance
            for other_when, other_method, other_id in observations
        ):
            continue
        learned.setdefault(method, {})[str(access_id)] = aliases.pop()
    return learned
