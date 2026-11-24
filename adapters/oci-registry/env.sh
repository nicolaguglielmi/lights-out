#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# oci-registry env.sh: validates generic remote OCI registry settings,
# refuses static credential/key files inside $WORKLOAD, handles optional
# OCI_AUTH_CMD / Bearer challenge auth safely (never leaking tokens in argv),
# and configures cosign keyless OIDC or KMS/key verification helpers.
set -eu

OCI_REGISTRY_REPO=${OCI_REGISTRY_REPO:-}
OCI_REGISTRY_SCHEME=${OCI_REGISTRY_SCHEME:-}
OCI_AUTH_CMD=${OCI_AUTH_CMD:-}
READY_TIMEOUT=${READY_TIMEOUT:-60}

case "$OCI_REGISTRY_REPO" in
  ''|replace-me)
    printf 'oci-registry: OCI_REGISTRY_REPO is empty or placeholder (expected <registry>/<repo>)\n' >&2
    exit 1 ;;
  *://* | *@* | /* | */ | *//* )
    printf 'oci-registry: OCI_REGISTRY_REPO=%s must be <registry>/<repo> without scheme, userinfo, or trailing slash\n' "$OCI_REGISTRY_REPO" >&2
    exit 1 ;;
  *[[:space:]]*)
    printf 'oci-registry: OCI_REGISTRY_REPO must not contain whitespace\n' >&2
    exit 1 ;;
  */*) : ;;
  *)
    printf 'oci-registry: OCI_REGISTRY_REPO=%s must include <registry>/<repo>\n' "$OCI_REGISTRY_REPO" >&2
    exit 1 ;;
esac

OCI_REGISTRY_HOST=${OCI_REGISTRY_REPO%%/*}
OCI_REGISTRY_PATH=${OCI_REGISTRY_REPO#*/}
if [ -z "$OCI_REGISTRY_HOST" ] || [ -z "$OCI_REGISTRY_PATH" ]; then
  printf 'oci-registry: OCI_REGISTRY_REPO=%s has empty host or repository path\n' "$OCI_REGISTRY_REPO" >&2
  exit 1
fi

if [ -z "$OCI_REGISTRY_SCHEME" ]; then
  case "$OCI_REGISTRY_HOST" in
    localhost | localhost:* | 127.0.0.1 | 127.0.0.1:* | '[::1]' | '[::1]:*')
      OCI_REGISTRY_SCHEME=http ;;
    *)
      OCI_REGISTRY_SCHEME=https ;;
  esac
fi
case "$OCI_REGISTRY_SCHEME" in
  http|https) ;;
  *)
    printf 'oci-registry: OCI_REGISTRY_SCHEME=%s must be http or https\n' "$OCI_REGISTRY_SCHEME" >&2
    exit 1 ;;
esac

OCI_REGISTRY_URL="$OCI_REGISTRY_SCHEME://$OCI_REGISTRY_HOST"
OCI_ALLOW_HTTP=0
if [ "$OCI_REGISTRY_SCHEME" = "http" ] || [ "${OCI_ALLOW_HTTP_REGISTRY:-}" = "1" ]; then
  OCI_ALLOW_HTTP=1
fi

# Validate keyless OIDC policy consistency: never allow one half without the other.
_has_id=""
_has_iss=""
if [ -n "${COSIGN_CERT_IDENTITY_REGEXP:-}" ] || [ -n "${COSIGN_CERT_IDENTITY:-}" ]; then
  _has_id=1
fi
if [ -n "${COSIGN_CERT_OIDC_ISSUER:-}" ] || [ -n "${COSIGN_CERT_OIDC_ISSUER_REGEXP:-}" ]; then
  _has_iss=1
fi
if [ -n "$_has_id" ] && [ -z "$_has_iss" ]; then
  printf 'oci-registry: COSIGN_CERT_IDENTITY* requires COSIGN_CERT_OIDC_ISSUER or COSIGN_CERT_OIDC_ISSUER_REGEXP\n' >&2
  exit 1
fi
if [ -n "$_has_iss" ] && [ -z "$_has_id" ]; then
  printf 'oci-registry: COSIGN_CERT_OIDC_ISSUER* requires COSIGN_CERT_IDENTITY_REGEXP or COSIGN_CERT_IDENTITY\n' >&2
  exit 1
fi

check_no_static_keys_in_workload() {
  _wl=${WORKLOAD:-}
  [ -n "$_wl" ] && [ -d "$_wl" ] || return 0
  _found=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name .crew -o -name .gates-out -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f \( \
      -path '*/.aws/credentials' -o -path '*/.aws/config' -o \
      -name '*credentials*.json' -o -name '*service-account*.json' -o -name '*service_account*.json' -o \
      -name '*-key.json' -o -name 'cosign.key' -o -name '*.key' -o \
      -name '*.pem' -o -name '*.p12' -o -name '*.pfx' -o \
      -name 'hcloud-token*' -o -name '*kubeconfig*' \
    \) -print 2>/dev/null | head -n 1 || true)
  if [ -n "$_found" ]; then
    printf 'oci-registry: static credential/key file refused in WORKLOAD: %s\n' "$_found" >&2
    exit 1
  fi
  _json_hit=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name .crew -o -name .gates-out -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f -name '*.json' ! -name 'package-lock.json' -print 2>/dev/null | while IFS= read -r _jf; do
      if grep -Eq '"type"[[:space:]]*:[[:space:]]*"service_account"|"private_key_id"[[:space:]]*:|"client_secret"[[:space:]]*:|"clientSecret"[[:space:]]*:|"aws_secret_access_key"[[:space:]]*:' "$_jf" 2>/dev/null; then
        printf '%s\n' "$_jf"
        break
      fi
    done || true)
  if [ -n "$_json_hit" ]; then
    printf 'oci-registry: static credential JSON file refused in WORKLOAD: %s\n' "$_json_hit" >&2
    exit 1
  fi
  if [ -n "${COSIGN_KEY:-}" ] && [ -f "${COSIGN_KEY}" ]; then
    _wl_abs=$(CDPATH='' cd -- "$_wl" && pwd -P)
    _key_dir=$(CDPATH='' cd -- "$(dirname -- "$COSIGN_KEY")" && pwd -P)
    case "$_key_dir/" in
      "$_wl_abs/"*)
        printf 'oci-registry: COSIGN_KEY must not live inside WORKLOAD (%s)\n' "$COSIGN_KEY" >&2
        exit 1 ;;
    esac
  fi
}

check_no_static_keys_in_workload

_OCI_BEARER_TOKEN=${OCI_REGISTRY_TOKEN:-}

oci_auth() {
  if [ -z "$OCI_AUTH_CMD" ]; then
    return 0
  fi
  _auth_out=$(sh -c "$OCI_AUTH_CMD") || {
    printf 'oci-registry: OCI_AUTH_CMD failed\n' >&2
    return 1
  }
  if [ -n "$_auth_out" ]; then
    if printf '%s' "$_auth_out" | grep -Eq '^[^[:space:]]+$'; then
      if [ -z "$_OCI_BEARER_TOKEN" ]; then
        _OCI_BEARER_TOKEN=$_auth_out
      fi
    else
      printf '%s\n' "$_auth_out" >&2
    fi
  fi
  return 0
}

oci_url_encode() {
  printf '%s' "$1" | jq -Rr @uri
}

oci_exchange_bearer() {
  _www=$1
  _realm=$(printf '%s' "$_www" | sed -n 's/.*realm="\([^"]*\)".*/\1/p')
  [ -n "$_realm" ] || return 1
  _service=$(printf '%s' "$_www" | sed -n 's/.*service="\([^"]*\)".*/\1/p')
  _scope=$(printf '%s' "$_www" | sed -n 's/.*scope="\([^"]*\)".*/\1/p')
  _q=""
  [ -z "$_service" ] || _q="${_q}${_q:+&}service=$(oci_url_encode "$_service")"
  [ -z "$_scope" ] || _q="${_q}${_q:+&}scope=$(oci_url_encode "$_scope")"
  _tok_url="$_realm${_q:+?$_q}"
  _tok_hdr=""
  if [ -n "$_OCI_BEARER_TOKEN" ]; then
    _tok_hdr=$(mktemp)
    chmod 600 "$_tok_hdr"
    printf 'Authorization: Bearer %s\n' "$_OCI_BEARER_TOKEN" > "$_tok_hdr"
    _tok_json=$(curl -fsS --connect-timeout 2 --max-time 10 -H @"$_tok_hdr" "$_tok_url" 2>/dev/null) || _tok_json=""
    rm -f "$_tok_hdr"
  else
    _tok_json=$(curl -fsS --connect-timeout 2 --max-time 10 "$_tok_url" 2>/dev/null) || _tok_json=""
  fi
  [ -n "$_tok_json" ] || return 1
  _exchanged=$(printf '%s' "$_tok_json" | jq -r '.token // .access_token // empty' 2>/dev/null) || _exchanged=""
  [ -n "$_exchanged" ] || return 1
  _OCI_BEARER_TOKEN=$_exchanged
  return 0
}

# oci_curl METHOD URL OUT_BODY OUT_HEADERS [ACCEPT]
# Returns HTTP status code on stdout (000 on network error); never passes tokens in argv.
oci_curl() {
  _method=$1 _url=$2 _body=$3 _hdrs=$4 _accept=${5:-}
  _attempt=0
  while [ "$_attempt" -lt 2 ]; do
    _auth_file=""
    if [ -n "$_OCI_BEARER_TOKEN" ]; then
      _auth_file=$(mktemp)
      chmod 600 "$_auth_file"
      printf 'Authorization: Bearer %s\n' "$_OCI_BEARER_TOKEN" > "$_auth_file"
    fi
    if [ "$_method" = "HEAD" ]; then
      set -- -sS --connect-timeout 2 --max-time 15 --head -D "$_hdrs" -o /dev/null -w '%{http_code}'
    else
      set -- -sS --connect-timeout 2 --max-time 15 -X "$_method" -D "$_hdrs" -o "$_body" -w '%{http_code}'
    fi
    [ -z "$_accept" ] || set -- "$@" -H "Accept: $_accept"
    [ -z "$_auth_file" ] || set -- "$@" -H @"$_auth_file"
    _code=$(curl "$@" "$_url" 2>/dev/null) || _code=000
    [ -z "$_auth_file" ] || rm -f "$_auth_file"
    if [ "$_code" = "401" ] && [ "$_attempt" -eq 0 ]; then
      _www=$(tr -d '\r' < "$_hdrs" | awk 'tolower($0) ~ /^www-authenticate:/ { sub(/^[^:]*:[[:space:]]*/, ""); print; exit }')
      if [ -n "$_www" ] && oci_exchange_bearer "$_www"; then
        _attempt=$((_attempt + 1))
        continue
      fi
    fi
    printf '%s' "$_code"
    return 0
  done
}

oci_resolve_digest() {
  _tag=$1
  _tmp_dir=$(mktemp -d)
  _accept='application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json'
  _url="$OCI_REGISTRY_URL/v2/$OCI_REGISTRY_PATH/manifests/$_tag"
  _code=$(oci_curl HEAD "$_url" "$_tmp_dir/body" "$_tmp_dir/hdrs" "$_accept")
  if [ "$_code" = "405" ] || { [ "$_code" = "200" ] && ! grep -qi '^docker-content-digest:' "$_tmp_dir/hdrs"; }; then
    _code=$(oci_curl GET "$_url" "$_tmp_dir/body" "$_tmp_dir/hdrs" "$_accept")
  fi
  if [ "$_code" != "200" ]; then
    rm -rf "$_tmp_dir"
    return 1
  fi
  _digest=$(tr -d '\r' < "$_tmp_dir/hdrs" | awk -F': ' 'tolower($1) == "docker-content-digest" { print $2; exit }')
  if [ -z "$_digest" ] && [ -s "$_tmp_dir/body" ]; then
    _digest="sha256:$(sha256sum "$_tmp_dir/body" | awk '{ print $1 }')"
  fi
  rm -rf "$_tmp_dir"
  printf '%s' "$_digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || return 1
  printf '%s' "$_digest"
}

cosign_append_tlog_sign_flags() {
  if [ "${COSIGN_TLOG_UPLOAD:-}" = "false" ] \
     || { [ "$OCI_ALLOW_HTTP" = "1" ] && [ -n "${COSIGN_KEY:-}${COSIGN_KMS_KEY:-}" ]; }; then
    return 0
  fi
  return 1
}

cosign_sign_image() {
  _ref=$1
  set -- sign
  [ "$OCI_ALLOW_HTTP" != "1" ] || set -- "$@" --allow-http-registry
  if [ -n "${COSIGN_KMS_KEY:-}" ]; then
    set -- "$@" --key "$COSIGN_KMS_KEY"
  elif [ -n "${COSIGN_KEY:-}" ]; then
    set -- "$@" --key "$COSIGN_KEY"
    export COSIGN_PASSWORD="${COSIGN_PASSWORD:-}"
  elif [ -n "${COSIGN_IDENTITY_TOKEN:-}" ]; then
    set -- "$@" --identity-token "$COSIGN_IDENTITY_TOKEN"
  fi
  if cosign_append_tlog_sign_flags; then
    set -- "$@" --tlog-upload=false --use-signing-config=false
  fi
  set -- "$@" --yes "$_ref"
  cosign "$@" >&2
}

cosign_attest_spdx() {
  _pred=$1 _ref=$2
  set -- attest --type spdxjson --predicate "$_pred"
  [ "$OCI_ALLOW_HTTP" != "1" ] || set -- "$@" --allow-http-registry
  if [ -n "${COSIGN_KMS_KEY:-}" ]; then
    set -- "$@" --key "$COSIGN_KMS_KEY"
  elif [ -n "${COSIGN_KEY:-}" ]; then
    set -- "$@" --key "$COSIGN_KEY"
    export COSIGN_PASSWORD="${COSIGN_PASSWORD:-}"
  elif [ -n "${COSIGN_IDENTITY_TOKEN:-}" ]; then
    set -- "$@" --identity-token "$COSIGN_IDENTITY_TOKEN"
  fi
  if cosign_append_tlog_sign_flags; then
    set -- "$@" --tlog-upload=false --use-signing-config=false
  fi
  set -- "$@" --yes "$_ref"
  cosign "$@" >&2
}

cosign_append_verify_flags() {
  _sub=$1 _ref=$2
  shift 2
  set -- "$_sub" "$@"
  [ "$OCI_ALLOW_HTTP" != "1" ] || set -- "$@" --allow-http-registry
  if [ -n "${COSIGN_KMS_KEY:-}" ]; then
    set -- "$@" --key "$COSIGN_KMS_KEY"
  elif [ -n "${COSIGN_PUB_KEY:-}" ]; then
    set -- "$@" --key "$COSIGN_PUB_KEY"
  elif [ -n "${COSIGN_KEY:-}" ]; then
    _vkey=$COSIGN_KEY
    case "$_vkey" in
      *.key) [ ! -f "${_vkey%.key}.pub" ] || _vkey="${_vkey%.key}.pub" ;;
    esac
    set -- "$@" --key "$_vkey"
  elif [ -n "${COSIGN_CERT_IDENTITY_REGEXP:-}" ] || [ -n "${COSIGN_CERT_IDENTITY:-}" ]; then
    if [ -n "${COSIGN_CERT_IDENTITY_REGEXP:-}" ]; then
      set -- "$@" --certificate-identity-regexp "$COSIGN_CERT_IDENTITY_REGEXP"
    else
      set -- "$@" --certificate-identity "$COSIGN_CERT_IDENTITY"
    fi
    if [ -n "${COSIGN_CERT_OIDC_ISSUER:-}" ]; then
      set -- "$@" --certificate-oidc-issuer "$COSIGN_CERT_OIDC_ISSUER"
    else
      set -- "$@" --certificate-oidc-issuer-regexp "$COSIGN_CERT_OIDC_ISSUER_REGEXP"
    fi
  else
    printf 'oci-registry: verification policy unset; set COSIGN_CERT_IDENTITY_REGEXP + COSIGN_CERT_OIDC_ISSUER or COSIGN_KMS_KEY/COSIGN_KEY\n' >&2
    return 1
  fi
  if [ "${COSIGN_INSECURE_IGNORE_TLOG:-}" = "1" ] \
     || [ "${COSIGN_TLOG_UPLOAD:-}" = "false" ] \
     || { [ "$OCI_ALLOW_HTTP" = "1" ] && [ -n "${COSIGN_KEY:-}${COSIGN_PUB_KEY:-}" ]; }; then
    set -- "$@" --insecure-ignore-tlog
  fi
  cosign "$@" "$_ref" >&2
}

cosign_verify_image() {
  cosign_append_verify_flags verify "$1"
}

cosign_verify_attestation_spdx() {
  cosign_append_verify_flags verify-attestation "$1" --type spdxjson
}
