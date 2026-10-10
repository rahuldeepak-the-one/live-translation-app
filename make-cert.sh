#!/bin/bash
# ============================================================
# Self-signed certificate for the LAN server.
#
# WHY: it is OPTIONAL now. This used to be mandatory — Chrome grants
# getUserMedia only in a secure context and a plain http:// LAN address is not
# one, so without a cert the browser capture page silently did nothing. Capture
# left the browser (./feed-mic.py), so the only thing left on this side is
# keeping the caption stream off the clear on a shared venue WiFi. See tls.py.
#
# Chrome IGNORES the Common Name and reads subjectAltName ONLY, so the cert
# must name every address a phone might dial. The laptop's IP changes with the
# venue's WiFi, so the SAN list is built from this machine's live addresses
# rather than hardcoded — re-run this script after joining a new network.
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="$SCRIPT_DIR/certs"
CRT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"

mkdir -p "$CERT_DIR"

# Every real (non-virtual) IPv4 on this machine, via the same filter the
# startup banner uses — so the cert names exactly the addresses we hand out.
IPS=$(cd "$SCRIPT_DIR" && python3 -c "
import netinfo
out = netinfo._run('ip', '-o', '-4', 'addr', 'show')
for _, ip in netinfo.physical_ips(out):
    print(ip)
")

SAN="DNS:localhost,IP:127.0.0.1"
for ip in $IPS; do SAN="$SAN,IP:$ip"; done

echo "🔐 Generating self-signed certificate"
echo "   valid for: $SAN"

openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" -out "$CRT" \
    -days 825 \
    -subj "/CN=live-translation-laptop" \
    -addext "subjectAltName=$SAN" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" \
    2>/dev/null

# The key is a secret even on a church LAN: anyone who reads it can impersonate
# the server to every phone that trusted it.
chmod 600 "$KEY"
chmod 644 "$CRT"

echo "   key:  $KEY"
echo "   cert: $CRT"
echo ""
echo "⚠️  Self-signed, so every phone sees Chrome's 'Your connection is not"
echo "    private' page ONCE. Tap Advanced -> Proceed. No public CA will issue"
echo "    for a private IP, so there is no way to avoid this."
