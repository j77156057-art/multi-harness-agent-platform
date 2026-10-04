"""Append-only event fact source for MVP-02."""

from app.events.store import EventRecord, EventStore

__all__ = ["EventRecord", "EventStore"]
