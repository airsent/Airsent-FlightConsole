#!/bin/bash
# Keep cellular (any enx* iface) as preferred default route; fall back to WiFi if it dies.
while true; do
  IF=$(ls /sys/class/net | grep ^enx | head -1)
  if [ -n "$IF" ]; then
    GW=$(ip route show dev "$IF" | awk '/^default/{print $3; exit}')
    GW=${GW:-192.168.225.1}
    if ping -I "$IF" -c 1 -W 2 1.1.1.1 >/dev/null 2>&1; then
      ip route show default dev "$IF" | grep -q "metric 50" || ip route replace default via "$GW" dev "$IF" metric 50
    else
      ip route del default via "$GW" dev "$IF" metric 50 2>/dev/null
    fi
  fi
  sleep 5
done
