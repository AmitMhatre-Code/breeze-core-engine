#!/usr/bin/env bash
# Resolve portal DRM Docker build args for ghcr-publish (required for every production image).
# Public key: explicit PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_B64 / _PEM, else derived from
# PORTAL_HEARTBEAT_JWT_PRIVATE_KEY_B64 (same secret as breeze-saas-portal aws-deploy).
set -euo pipefail

_api_base="${CONSOLE_API_PUBLIC_BASE_URL:-}"
_private_b64="${PORTAL_HEARTBEAT_JWT_PRIVATE_KEY_B64:-}"
_public_b64="${PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_B64:-}"
_public_pem="${PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM:-}"

if [ -z "$_api_base" ]; then
  echo "::error::CONSOLE_API_PUBLIC_BASE_URL is required (repo var or secret). Production images must ship with portal DRM enabled." >&2
  exit 1
fi

allowed_host="$(python3 -c "from urllib.parse import urlparse; print(urlparse('''${_api_base}''').hostname or '')")"
if [ -z "$allowed_host" ]; then
  echo "::error::CONSOLE_API_PUBLIC_BASE_URL must be a valid URL with a hostname (got: ${_api_base})." >&2
  exit 1
fi

if [ -n "$_public_b64" ]; then
  :
elif [ -n "$_public_pem" ]; then
  _public_b64="$(printf '%s\n' "$_public_pem" | base64 | tr -d '\n')"
elif [ -n "$_private_b64" ]; then
  _private_pem="$(printf '%s' "$_private_b64" | base64 -d)"
  _public_pem="$(printf '%s\n' "$_private_pem" | openssl ec -pubout 2>/dev/null)"
  _public_b64="$(printf '%s\n' "$_public_pem" | base64 | tr -d '\n')"
else
  echo "::error::Set PORTAL_HEARTBEAT_JWT_PRIVATE_KEY_B64 (preferred; same secret as breeze-saas-portal) or PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_B64 / _PEM." >&2
  exit 1
fi

# Validate derived/explicit public key decodes to a PEM public key.
if ! printf '%s' "$_public_b64" | base64 -d | openssl pkey -pubin -noout >/dev/null 2>&1; then
  echo "::error::Resolved portal heartbeat public key is not a valid ES256 public PEM." >&2
  exit 1
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "allowed_hosts<<EOF"
    echo "$allowed_host"
    echo "EOF"
    echo "public_key_b64<<EOF"
    echo "$_public_b64"
    echo "EOF"
  } >> "$GITHUB_OUTPUT"
else
  echo "allowed_hosts=$allowed_host"
  echo "public_key_b64=$_public_b64"
fi
