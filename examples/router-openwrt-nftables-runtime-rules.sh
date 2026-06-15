#!/bin/sh
# router-openwrt-nftables-runtime-rules.sh
#
# Apply runtime DNAT + MASQUERADE rules using nftables syntax.
# Required for OpenWrt 22.03+ which defaults to the nftables firewall
# backend (fw4).
#
# These rules are NOT persistent -- they are lost on reboot.
# Use for initial testing only. See docs/router-openwrt-glinet.md for
# persistent configuration guidance.
#
# Usage:
#   sh router-openwrt-nftables-runtime-rules.sh
#
# Rollback:
#   Manually remove rules or reboot the router. You can also delete by
#   handle using the output of `nft -a list ruleset | grep tuya-psk-bridge`.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration -- edit these values for your setup
# ---------------------------------------------------------------------------

# IP address of the Tuya device on your LAN
DEVICE_IP="<DEVICE_IP>"

# Human-readable name used in rule comments (for identification)
DEVICE_NAME="<DEVICE_NAME>"

# IP address of the host running tuya-psk-bridge (usually your HA server)
BRIDGE_IP="<BRIDGE_IP>"

# Port the bridge listens on for PSK-encrypted MQTT (default: 8886)
MQTT_PSK_PORT="${MQTT_PSK_PORT:-8886}"

# The destination port the Tuya device sends to (usually 443 for TLS)
HTTPS_PORT="${HTTPS_PORT:-443}"

# ---------------------------------------------------------------------------
# Determine the OpenWrt fw4 chain names
# ---------------------------------------------------------------------------
# fw4 creates separate nftables tables per hook. The relevant chains are:
#   - nat: prerouting (input hook, for DNAT)
#   - nat: postrouting (output hook, for MASQUERADE)
#
# On fw4 these typically live in the "inet fw4" table under chains:
#   - prerouting_lan_rule  (match traffic arriving on lan)
#   - postrouting_lan_rule (match traffic leaving on lan)

NFT_TABLE="inet fw4"

# Verify the table exists
if ! nft list tables 2>/dev/null | grep -q "$NFT_TABLE"; then
  echo "ERROR: nftables table '${NFT_TABLE}' not found."
  echo "Are you running on an OpenWrt system with fw4?"
  echo "Available tables:"
  nft list tables 2>/dev/null || echo "  (none)"
  exit 1
fi

# ---------------------------------------------------------------------------
# Apply DNAT rule (prerouting)
# ---------------------------------------------------------------------------
# Rewrites the destination of packets from DEVICE_IP:443 to BRIDGE_IP:MQTT_PSK_PORT

nft add rule inet fw4 prerouting_lan_rule \
  ip saddr "$DEVICE_IP" \
  tcp dport "$HTTPS_PORT" \
  dnat to "$BRIDGE_IP:$MQTT_PSK_PORT" \
  comment "\"tuya-psk-bridge-${DEVICE_NAME}-dnat\""

echo "DNAT rule added: ${DEVICE_IP}:${HTTPS_PORT} -> ${BRIDGE_IP}:${MQTT_PSK_PORT}"

# ---------------------------------------------------------------------------
# Apply MASQUERADE rule (postrouting)
# ---------------------------------------------------------------------------
# Rewrites the source address of return packets so the Tuya device accepts them

nft add rule inet fw4 postrouting_lan_rule \
  ip saddr "$DEVICE_IP" \
  ip daddr "$BRIDGE_IP" \
  tcp dport "$MQTT_PSK_PORT" \
  masquerade \
  comment "\"tuya-psk-bridge-${DEVICE_NAME}-snat\""

echo "MASQUERADE rule added for ${DEVICE_IP} -> ${BRIDGE_IP}:${MQTT_PSK_PORT}"

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

echo ""
echo "--- Verifying rules ---"

if nft list ruleset 2>/dev/null | grep -q "tuya-psk-bridge-${DEVICE_NAME}"; then
  echo "OK: Bridge rules are active."
  echo ""
  nft -a list ruleset | grep "tuya-psk-bridge-${DEVICE_NAME}"
  echo ""
  echo "To remove rules later, use the handle numbers above:"
  echo "  nft delete rule inet fw4 prerouting_lan_rule handle <N>"
  echo "  nft delete rule inet fw4 postrouting_lan_rule handle <N>"
else
  echo "WARNING: No bridge rules found. Check that nft and fw4 are available."
  exit 1
fi
