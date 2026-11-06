# Shared console settings

Operators and viewers use `/shared-state` (WebSocket), routed by nginx to the
loopback-only `airsent-shared-state` service on 9203. Signed presence tokens
identify roles. Only operators can change settings; every write revalidates the
token. Demo and SITL do not join this service.

Shared data:
- failsafe activation switches (dashboard and failsafe page)
- failsafe response drafts
- mission plans and home point
- manual preflight checklist entries (automatic entries remain telemetry-driven)
- parameter drafts (the existing Write to Vehicle UI is still a placeholder)
- automatic safety monitor toggle

Countdown synchronization remains on the existing `/sequence` service.
Aircraft status and commands remain on the existing telemetry/command relay.
Camera choice, map zoom, navigation, filters and local review/export controls
remain personal. Shared console policies/drafts are not proof of onboard
configuration and this service never sends aircraft commands.

Writes include the expected version of that setting. Conflicting edits are
rejected with the latest snapshot; the operator must review and retry. There is
no offline edit replay. A save waits for server acknowledgement; a missing
acknowledgement closes the connection and disables editing until resynchronized.
Server state is persisted atomically at `/var/lib/airsent-shared/state.json`.
Old browser-only values are not silently uploaded over shared state. If the
server has no prior value, a different existing local value is preserved under
`airsent-shared-local-backup-v1` in that browser before applying the snapshot.

Deployment:
1. Install shared_state_server.py alongside presence_auth.py in /opt/airsent.
2. Run deploy/install_shared_state.py as root on the console droplet. It installs
   a dedicated unprivileged systemd service using /etc/airsent-presence.env,
   writable only at /var/lib/airsent-shared, and adds the nginx route after backup.
3. Deploy shared-state.js, viewer-access.js, the updated HTML and failsafe scripts.
   Preserve deployment-specific console-config.js and the Jetson configuration.
4. Refresh existing pages to load the client. Look for SHARED SETTINGS · LIVE.

Verified with isolated two-operator/one-viewer sockets, denied viewer writes,
conflict rejection and restart persistence. No Jetson restart is required.
