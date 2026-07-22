"""Scheduling & Meeting Management platform (Phase D.19).

The authoritative domain for scheduling METADATA — meetings/appointments (the Calendar Event),
reusable meeting templates, bookable resources/rooms, attendees, resource bookings, reminders,
follow-ups, availability, recurrence metadata, and an append-only audit ledger. It coordinates the
firm's calendar while preserving ownership boundaries: it references people/households/
organizations, opportunities, annual reviews, communications, workflow, advisor work, documents,
compliance, business owner plans, the timeline, and Microsoft 365 — but is **never a source of
truth for business records**. It reuses the existing Microsoft 365 calendar sync, notification
ledger, and Communications for transport (metadata only — no calendar provider is implemented).
Approved lifecycle events flow to the shared Activity Timeline; Analytics consumes scheduling
statistics (Scheduling never depends on Analytics).
"""
