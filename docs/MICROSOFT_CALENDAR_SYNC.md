# Microsoft Calendar Intelligence

Client360 synchronizes recent and upcoming Microsoft 365 meetings through the
existing delegated OAuth connection. The background scheduler runs calendar and
mail synchronization independently every 15 minutes.

## Matching and timeline publishing

- Organizer and attendee email addresses are normalized before matching.
- Email addresses linked to more than one person are treated as ambiguous and
  remain in review instead of being matched automatically.
- The connected Microsoft account is excluded from client matching.
- Known addresses publish `calendar_event` records to each person's timeline.
- Timeline external IDs are derived from the Microsoft event ID and person ID.
  The timeline service upserts this key, so repeated synchronization updates the
  existing meeting instead of creating duplicates.
- Metadata includes organizer, attendees, response states, start and end values,
  location, Teams join URL, Outlook web link, and the connected user's response.

## Unmatched review

Unknown organizer or attendee addresses are queued in
`microsoft_unmatched_calendar_attendees`. Client360 never creates contacts from
calendar data automatically. Reviewers can match a queue item to an existing
person or ignore it at `/microsoft365/calendar-review`.
If a later sync finds a canonical email match, the pending review item closes
automatically.

## Manual synchronization

For controlled testing, send a POST request to:

```text
/microsoft365/calendar/sync?days_back=30&days_forward=90
```

Both window parameters are bounded to 365 days. The response reports reviewed,
matched, unmatched, cancelled, and published counts.

## Deployment

Apply the database migration before starting the updated application:

```text
alembic upgrade head
```

The Microsoft OAuth consent must include `Calendars.ReadWrite`, which is already
part of Client360's delegated scope configuration.
