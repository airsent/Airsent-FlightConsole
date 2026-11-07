# Shared console event history

The real command relay journals operator requests, forwarding failures, Jetson replies, channel availability, and fresh observed aircraft mode/armed transitions in SQLite at `/var/lib/airsent-events/events.sqlite3`. Backend replies acknowledge backend processing; only telemetry establishes aircraft state. No recorded command is replayed.

`/events` proxies to loopback 9105 on the relay. Signed operator/viewer sessions can read the same durable history. Only operators can submit labelled console notes, with UUID deduplication. Demo and simulation sessions cannot read/write this real-aircraft journal. The dashboard reconnects and restores history automatically; CSV exports all history received from the server. Pause affects only the local display; the reset icon resumes history and does not erase saved records.

Flight-mode and armed indicators reflect telemetry. Clicking a command no longer locally blinks the mode or claims the aircraft changed mode. Use the ARM/DISARM command control to request an armed-state change; the status dropdown cannot locally fabricate state.

Deployment: install `event_journal.py` in `/opt/airsent`, `deploy/relay.py` as `/root/relay.py`, and create the `/events` nginx websocket route. Deploy `script.js`, `shared-events.js`, `telemetry.js`, and `dashboard.html` together. Back up the SQLite database with SQLite's backup API when adding it to operational backups. History begins at installation; browser-only events from before installation cannot be recovered.

Validation: `tests/test_event_journal.py`, `tests/test_command_access.py`, and `tests/relay_events_integration.py` (requires websockets). The integration test binds ephemeral loopback ports and a fake command target. Never use live arm/takeoff commands as synchronization tests.
