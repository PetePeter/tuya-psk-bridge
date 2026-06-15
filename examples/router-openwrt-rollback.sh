#!/bin/sh
# router-openwrt-rollback.sh
#
# Remove runtime DNAT + MASQUERADE rules from an OpenWrt / GL.iNet router.
# Matches rules by the exact comment tag used during creation.
#
# These commands are safe to run even if the rules have already been removed
# or were never applied -- iptables will simply report no match and exit 0.
#
# Usage:
#   sh router-openwrt-rollback.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration -- must match the values used when applying the rules
# ---------------------------------------------------------------------------

DEVICE_IP="<DEVICE_IP>"
DEVICE_NAME="<DEVICE_NAME>"
BRIDGE_IP="<BRIDGE_IP>"
MQTT_PSK_PORT="${MQTT_PSK_PORT:-8886}"
HTTPS_PORT="${HTTPS_PORT:-443}"

# ---------------------------------------------------------------------------
# Remove DNAT rule
# ---------------------------------------------------------------------------

if iptables -t nat -S | grep -q "tuya-psk-bridge-${DEVICE_NAME}-dnat"; then
  iptables -t nat -D prerouting_lan_rule \
    -s "$DEVICE_IP" \
    -p tcp --dport "$HTTPS_PORT" \
    -j DNAT --to-destination "$BRIDGE_IP:$MQTT_PSK_PORT" \
    -m comment --comment "tuya-psk-bridge-${DEVICE_NAME}-dnat"
  echo "DNAT rule removed for ${DEVICE_NAME}"
else
  echo "DNAT rule for ${DEVICE_NAME} not found (already removed or never applied)"
fi

# ---------------------------------------------------------------------------
# Remove MASQUERADE rule
# ---------------------------------------------------------------------------

if iptables -t nat -S | grep -q "tuya-psk-bridge-${DEVICE_NAME}-snat"; then
  iptables -t nat -D postrouting_lan_rule \
    -s "$DEVICE_IP" \
    -d "$BRIDGE_IP" \
    -p tcp --dport "$MQTT_PSK_PORT" \
    -j MASQUERADE \
    -m comment --comment "tuya-psk-bridge-${DEVICE_NAME}-snat"
  echo "MASQUERADE rule removed for ${DEVICE_NAME}"
else
  echo "MASQUERADE rule for ${DEVICE_NAME} not found (already removed or never applied)"
fi

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

echo ""
echo "--- Verifying removal ---"

if iptables -t nat -S | grep -q "tuya-psk-bridge-${DEVICE_NAME}"; then
  echo "WARNING: Some bridge rules are still present."
  echo ""
  iptables -t nat -S | grep "tuya-psk-bridge-${DEVICE_NAME}"
else
  echo "OK: All bridge rules for ${DEVICE_NAME} have been removed."
fi
