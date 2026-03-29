"""
core/correlation/rules/network_rules.py
=======================================
Correlation rules detecting network-based causality patterns.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from core.correlation.rules.base_rule import CorrelationRule
from models.timeline_event import TimelineEvent
from utils.logger import get_logger

log = get_logger(__name__)


class NetworkToggleBeforeActivityRule(CorrelationRule):
    """
    Correlates a network disconnected state quickly followed by app usage.
    Network OFF -> APP_OPENED within 60s.
    """
    NAME = "NetworkToggleBeforeActivity"
    DESCRIPTION = "Links network disable events to subsequent app usage as potential offline activity pattern."

    _WINDOW = timedelta(seconds=60)

    def apply(self, timeline: list[TimelineEvent]) -> int:
        groups_created = 0
        n = len(timeline)
        for i, event in enumerate(timeline):
            if event.event_type not in ("WIFI_DISCONNECTED", "NETWORK_DISCONNECTED", "AIRPLANE_MODE_ON"):
                continue
            for j in range(i + 1, n):
                candidate = timeline[j]
                if candidate.timestamp is None or event.timestamp is None:
                    continue
                gap = candidate.timestamp - event.timestamp
                if gap > self._WINDOW:
                    break
                if candidate.event_type in ("APP_OPENED", "ACTIVITY_RESUMED"):
                    corr_id = str(uuid.uuid4())
                    event.promote_to("CORRELATED")
                    event.link_correlation(corr_id, candidate.event_id)
                    candidate.promote_to("CORRELATED")
                    candidate.link_correlation(corr_id, event.event_id)
                    
                    candidate.severity = "IMPORTANT"
                    candidate.reason = "Possible Offline Activity Pattern: Network disabled shortly before app usage."
                    
                    groups_created += 1
                    break
        return groups_created


class NetworkAfterUnlockRule(CorrelationRule):
    """
    Correlates device unlock followed by immediate network activity.
    KEYGUARD_HIDDEN -> NETWORK_CONNECTED or WIFI_CONNECTED within 10s.
    """
    NAME = "NetworkAfterUnlock"
    DESCRIPTION = "Links device unlock to immediate network activity establishing a post-unlock network check."

    _WINDOW = timedelta(seconds=10)

    def apply(self, timeline: list[TimelineEvent]) -> int:
        groups_created = 0
        n = len(timeline)
        for i, event in enumerate(timeline):
            if event.event_type != "KEYGUARD_HIDDEN":
                continue
            for j in range(i + 1, n):
                candidate = timeline[j]
                if candidate.timestamp is None or event.timestamp is None:
                    continue
                gap = candidate.timestamp - event.timestamp
                if gap > self._WINDOW:
                    break
                if candidate.event_type in ("WIFI_CONNECTED", "NETWORK_CONNECTED", "AIRPLANE_MODE_OFF"):
                    corr_id = str(uuid.uuid4())
                    event.promote_to("CORRELATED")
                    event.link_correlation(corr_id, candidate.event_id)
                    candidate.promote_to("CORRELATED")
                    candidate.link_correlation(corr_id, event.event_id)
                    
                    candidate.severity = "IMPORTANT"
                    candidate.reason = "Post-Unlock Network Check: Immediate network activity detected after device unlock."
                    
                    groups_created += 1
                    break
        return groups_created
