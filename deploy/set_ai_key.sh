#!/usr/bin/env bash
# Install the Anthropic API key for the AI flight review. Run ON THE VPS:  bash deploy/set_ai_key.sh
# The key is stored in /etc/airsent/ai.env (root only) and given to the relay service — it never
# goes to the Jetson, the browser or the git repo.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi
read -r -s -p "Anthropic API key (sk-ant-…, input hidden): " KEY; echo
case "$KEY" in sk-ant-*) ;; *) echo "That doesn't look like an Anthropic API key (should start with sk-ant-)"; exit 1;; esac
read -r -p "Model [claude-opus-5-5]: " MODEL; MODEL="${MODEL:-claude-opus-5-5}"
install -d -m 700 /etc/airsent
umask 077
printf 'ANTHROPIC_API_KEY=%s\nAIRSENT_AI_MODEL=%s\n' "$KEY" "$MODEL" > /etc/airsent/ai.env
chmod 600 /etc/airsent/ai.env
install -d /etc/systemd/system/airsent-relay.service.d
printf '[Service]\nEnvironmentFile=-/etc/airsent/ai.env\n' > /etc/systemd/system/airsent-relay.service.d/ai.conf
systemctl daemon-reload
systemctl restart airsent-relay
sleep 1
# check the key works (tiny request, a fraction of a cent)
CODE=$(curl -s -o /tmp/ai_check.json -w '%{http_code}' https://api.anthropic.com/v1/messages \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply OK\"}]}" || true)
if [ "$CODE" = "200" ]; then echo "✓ Key works with $MODEL — AI review is ready on the Logs page"
else echo "✗ The API answered HTTP $CODE: $(head -c 300 /tmp/ai_check.json)"; fi
rm -f /tmp/ai_check.json
systemctl is-active --quiet airsent-relay && echo "✓ relay restarted" || echo "✗ relay not running — journalctl -u airsent-relay"
