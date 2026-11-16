#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-harness-shape: the engine-free half of every CLI harness's entry
# requirement (docs/adr/0041 §9, §11); the engine half is `./go
# harness-conformance`. A CLI harness is an adapters/<name>/ with run.sh,
# Containerfile and a pin, package.json or package-lock.json, or release.env:
# discovered, never listed. Each one must have exactly one pin:
#   - an npm package: package.json pinning every dependency to an exact
#     version (no ^, no ~, no range) and a package-lock.json that agrees: the
#     root entry asks for the same version and node_modules/<package> installs
#     it;
#   - or a release binary (docs/adr/0050 §2): release.env with exactly
#     VERSION, an exact version, and per architecture (AMD64, ARM64) URL_<arch>,
#     an https URL that names VERSION as a path segment, and SHA256_<arch>, 64
#     lowercase hex digits;
#   - thinking.env with <PROVIDER>_LOW, _MEDIUM and _HIGH, the provider
#     upper-cased (docs/adr/0040 §2), for every provider the harness runs;
#   - conformance.env, and every conformance.<provider>.env beside it (a
#     harness may run more than one provider, docs/adr/0092 §2; discovered by
#     glob), each with exactly PROVIDER, MODEL, KEY_VAR, THINKING_PATH and
#     EGRESS (the fifth, docs/adr/0046 §4), or those and BASE_URL; a provider
#     among gemini, anthropic, openai and zai (a provider may have more than one
#     harness since docs/adr/0050 §1, AGENT_HARNESS chooses; and each harness
#     file has its own: a further file is named for its PROVIDER, which differs
#     from every other file's of that harness), and a KEY_VAR that .env.example
#     names. EGRESS is the only host:port the egress proxy opens for the
#     harness besides the workload's own: one or more `name:port`, a lowercase
#     DNS name with a dot, no IP literal, no `*`, a port from 1 to 65535
#     (egress_flaw in ci/lib.sh). BASE_URL, the vendor endpoint the adapter
#     points the CLI at for that provider, is `https://<host>/...` where
#     `<host>:443` is exactly the file's EGRESS (so a single host, and the
#     proxy opens it);
#   - hooks/guard.sh byte-identical to adapters/gemini-cli/hooks/guard.sh,
#     the reference: each image's build context is its own directory, so the
#     guard is copied, and `cmp` is what stops three lists drifting (§11).
# The root compose.yaml must define a service named like each harness, and a
# workload's compose fragment (app/, workloads/*/.lights-out/) that defines
# one harness service defines all of them (§11): a fragment that gives
# Postgres to gemini-cli alone would leave the next harness without it.
#
# A check that cannot fail is not one: shape_flaw judges a tree rooted at its
# argument, and after judging this one the script judges copies of a good tree
# with one defect each, every one of which must be named.
set -eu
name=harness-shape
. ci/lib.sh

conf_get() {  # FILE KEY
  grep "^$2=" "$1" 2>/dev/null | head -n1 | cut -d= -f2-
}

# shape_flaw ROOT: the first thing wrong with the CLI harnesses under ROOT, as
# one line on stdout, or nothing. It never calls nogo (it runs in $(...)).
shape_flaw() {
  root=$1
  harnesses=""
  for d in "$root"/adapters/*/; do
    [ -d "$d" ] || continue
    h=$(basename "$d")
    a=$root/adapters/$h
    if [ -f "$a/run.sh" ] && [ -f "$a/Containerfile" ] && { [ -f "$a/package.json" ] || [ -f "$a/package-lock.json" ] || [ -f "$a/release.env" ]; }; then
      harnesses="$harnesses $h"
    fi
  done
  if [ -z "$harnesses" ]; then
    printf 'no CLI harness under adapters/ (a directory with run.sh, Containerfile and package.json or release.env)'
    return
  fi
  ref=$root/adapters/gemini-cli/hooks/guard.sh
  if [ ! -f "$ref" ]; then
    printf 'adapters/gemini-cli/hooks/guard.sh, the reference guard, is missing'
    return
  fi

  for h in $harnesses; do
    a=$root/adapters/$h

    # A release binary: one version, one digest per architecture.
    if [ -f "$a/release.env" ]; then
      if [ -f "$a/package.json" ] || [ -f "$a/package-lock.json" ]; then
        printf '%s: release.env beside package.json or package-lock.json; a harness has one pin (docs/adr/0050 §2)' "$h"
        return
      fi
      rel=$a/release.env
      keys=$(grep -vE '^[[:space:]]*(#|$)' "$rel" | cut -d= -f1 | sort | tr '\n' ' ')
      if [ "$keys" != "SHA256_AMD64 SHA256_ARM64 URL_AMD64 URL_ARM64 VERSION " ]; then
        printf '%s: release.env has the keys [%s], want exactly VERSION URL_AMD64 SHA256_AMD64 URL_ARM64 SHA256_ARM64' "$h" "${keys% }"
        return
      fi
      version=$(conf_get "$rel" VERSION)
      if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$'; then
        printf '%s: release.env VERSION is "%s", not an exact version' "$h" "$version"
        return
      fi
      for arch in AMD64 ARM64; do
        url=$(conf_get "$rel" "URL_$arch")
        case "$url" in
          https://*/"$version"/*) ;;
          https://*) printf '%s: release.env URL_%s does not name VERSION %s as a path segment' "$h" "$arch" "$version"; return ;;
          *) printf '%s: release.env URL_%s is not an https URL' "$h" "$arch"; return ;;
        esac
        if ! conf_get "$rel" "SHA256_$arch" | grep -Eq '^[0-9a-f]{64}$'; then
          printf '%s: release.env SHA256_%s is not a sha256 (64 lowercase hex digits)' "$h" "$arch"
          return
        fi
      done
    fi

    # An npm package: the version, pinned and locked.
    if [ ! -f "$a/release.env" ]; then
      if [ ! -f "$a/package.json" ]; then printf '%s: package.json is missing' "$h"; return; fi
      if [ ! -f "$a/package-lock.json" ]; then printf '%s: package-lock.json is missing' "$h"; return; fi
      pin=$(jq -r --slurpfile lock "$a/package-lock.json" '
        ($lock[0].packages // {}) as $p
        | ([($p[""].dependencies // {}), ($p[""].optionalDependencies // {}), ($p[""].devDependencies // {})] | add // {}) as $root
        | ([(.dependencies // {}), (.optionalDependencies // {}), (.devDependencies // {})] | add // {}) as $d
        | if ($d | length) == 0 then "package.json pins no dependency"
          else
            [ $d | to_entries[] | . as $e
              | if ($e.value | test("^[0-9]+\\.[0-9]+\\.[0-9]+([-+][0-9A-Za-z.-]+)?$") | not)
                  then "\($e.key) is \($e.value | tojson) in package.json, not an exact version"
                elif ($root[$e.key] // null) != $e.value
                  then "package-lock.json asks \($root[$e.key] // "nothing" | tostring) of \($e.key), package.json pins \($e.value)"
                elif ($p["node_modules/" + $e.key].version // null) != $e.value
                  then "package-lock.json installs \($p["node_modules/" + $e.key].version // "nothing" | tostring) of \($e.key), package.json pins \($e.value)"
                else empty end
            ] | .[0] // empty
          end' "$a/package.json" 2>/dev/null) || { printf '%s: package.json or package-lock.json is not JSON' "$h"; return; }
      if [ -n "$pin" ]; then printf '%s: %s' "$h" "$pin"; return; fi
    fi

    # The facts the suite reads: conformance.env, the first provider, and each
    # conformance.<provider>.env beside it (docs/adr/0092 §2), in that order.
    if [ ! -f "$a/conformance.env" ]; then printf '%s: conformance.env is missing' "$h"; return; fi
    seen=""
    for cf in "$a/conformance.env" "$a"/conformance.*.env; do
      [ -f "$cf" ] || continue
      cn=${cf##*/}
      keys=$(grep -vE '^[[:space:]]*(#|$)' "$cf" | cut -d= -f1 | sort | tr '\n' ' ')
      case "$keys" in
        "EGRESS KEY_VAR MODEL PROVIDER THINKING_PATH "|"BASE_URL EGRESS KEY_VAR MODEL PROVIDER THINKING_PATH ") ;;
        *) printf '%s: %s has the keys [%s], want exactly PROVIDER MODEL KEY_VAR THINKING_PATH EGRESS, and BASE_URL besides if it has one' "$h" "$cn" "${keys% }"; return ;;
      esac
      for k in PROVIDER MODEL KEY_VAR THINKING_PATH EGRESS; do
        if [ -z "$(conf_get "$cf" "$k")" ]; then printf '%s: %s has an empty %s' "$h" "$cn" "$k"; return; fi
      done
      provider=$(conf_get "$cf" PROVIDER)
      case "$provider" in
        gemini|anthropic|openai|zai) ;;
        *) printf '%s: %s PROVIDER is %s, want gemini, anthropic, openai or zai' "$h" "$cn" "$provider"; return ;;
      esac
      # A further file is named for its provider, and no two files of a harness share one.
      if [ "$cn" != conformance.env ] && [ "$cn" != "conformance.$provider.env" ]; then
        printf '%s: %s has PROVIDER %s, so it must be named conformance.%s.env (docs/adr/0092 §2)' "$h" "$cn" "$provider" "$provider"
        return
      fi
      case " $seen " in
        *" $provider "*) printf '%s: %s has PROVIDER %s, which another conformance file of %s has: one file per provider (docs/adr/0092 §2)' "$h" "$cn" "$provider" "$h"; return ;;
      esac
      seen="$seen $provider"
      key_var=$(conf_get "$cf" KEY_VAR)
      if ! printf '%s' "$key_var" | grep -Eq '^[A-Z][A-Z0-9_]*$'; then
        printf '%s: %s KEY_VAR %s is not a variable name' "$h" "$cn" "$key_var"
        return
      fi
      if ! grep -q "^$key_var=" "$root/.env.example" 2>/dev/null; then
        printf '%s: %s KEY_VAR %s is not named in .env.example' "$h" "$cn" "$key_var"
        return
      fi
      if ! conf_get "$cf" THINKING_PATH | grep -q '^\.'; then
        printf '%s: %s THINKING_PATH is not a jq path (it starts with a dot)' "$h" "$cn"
        return
      fi
      egress_bad=$(egress_flaw "$(conf_get "$cf" EGRESS)")
      if [ -n "$egress_bad" ]; then
        printf '%s: %s EGRESS: %s (docs/adr/0046 §4: lowercase name.tld:port, no IP, no *)' "$h" "$cn" "$egress_bad"
        return
      fi
      # BASE_URL: https://<host>/..., and <host>:443 is exactly EGRESS, the one host the proxy opens.
      if grep -q '^BASE_URL=' "$cf"; then
        url=$(conf_get "$cf" BASE_URL)
        case "$url" in
          https://*/*) url_host=${url#https://}; url_host=${url_host%%/*} ;;
          *) printf '%s: %s BASE_URL "%s" is not https://<host>/... (docs/adr/0092 §2)' "$h" "$cn" "$url"; return ;;
        esac
        if [ "$url_host:443" != "$(conf_get "$cf" EGRESS)" ]; then
          printf '%s: %s BASE_URL names the host %s, and its EGRESS is %s: want exactly %s:443, the one host the proxy opens (docs/adr/0092 §2)' "$h" "$cn" "$url_host" "$(conf_get "$cf" EGRESS)" "$url_host"
          return
        fi
      fi

      # The thinking map: three levels for its provider.
      prefix=$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')
      if [ ! -f "$a/thinking.env" ]; then printf '%s: thinking.env is missing' "$h"; return; fi
      for level in LOW MEDIUM HIGH; do
        if [ -z "$(conf_get "$a/thinking.env" "${prefix}_$level")" ]; then
          printf '%s: thinking.env has no %s_%s= line' "$h" "$prefix" "$level"
          return
        fi
      done
    done

    # The guard, copied and identical.
    if [ ! -f "$a/hooks/guard.sh" ]; then printf '%s: hooks/guard.sh is missing' "$h"; return; fi
    if ! cmp -s "$a/hooks/guard.sh" "$ref"; then
      printf '%s: hooks/guard.sh differs from adapters/gemini-cli/hooks/guard.sh; the guard is one file, copied (docs/adr/0041 §11)' "$h"
      return
    fi
  done

  # Compose: the root file runs each harness by its name; a fragment that names
  # one names all of them.
  for h in $harnesses; do
    if ! grep -Eq "^  $h:([[:space:]]|\$)" "$root/compose.yaml" 2>/dev/null; then
      printf 'compose.yaml defines no service named %s, and run.sh runs it by that name' "$h"
      return
    fi
  done
  for f in "$root"/app/compose.yaml "$root"/workloads/*/.lights-out/compose.yaml; do
    [ -f "$f" ] || continue
    have=""
    missing=""
    for h in $harnesses; do
      if grep -Eq "^  $h:([[:space:]]|\$)" "$f"; then have="$have $h"; else missing="$missing $h"; fi
    done
    if [ -n "$have" ] && [ -n "$missing" ]; then
      printf '%s defines a worker service for%s but not for%s (docs/adr/0041 §11)' "${f#"$root"/}" "$have" "$missing"
      return
    fi
  done
}

flaw=$(shape_flaw .)
[ -z "$flaw" ] || nogo "$flaw"

# --- the check must be able to fail ---------------------------------------------
# A good tree with three harnesses (the real gemini-cli, a copy of it that
# speaks another provider, and the real antigravity-cli as bin-cli: a release
# pin, and a second harness on gemini), then one defect at a time. Each must be
# named; a defect that goes unnoticed turns this check into a NO-GO of its own.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

good=$work/good
mkdir -p "$good/adapters" "$good/app" "$good/workloads/m/.lights-out"
cp -R adapters/gemini-cli "$good/adapters/gemini-cli"
cp -R adapters/gemini-cli "$good/adapters/other-cli"
cp -R adapters/antigravity-cli "$good/adapters/bin-cli"
cp .env.example "$good/.env.example"
# The fourth provider's key, named in the good tree's example whatever the real one holds.
printf 'ZAI_API_KEY=replace-me\n' >> "$good/.env.example"
o=$good/adapters/other-cli
grep -v '^PROVIDER=\|^KEY_VAR=\|^EGRESS=' "$o/conformance.env" > "$o/conformance.env.new"
printf 'PROVIDER=anthropic\nKEY_VAR=ANTHROPIC_API_KEY\nEGRESS=api.other.example:443 cdn.other.example:8443\n' >> "$o/conformance.env.new"
mv "$o/conformance.env.new" "$o/conformance.env"
# other-cli runs a second provider (docs/adr/0092 §2): its own file, with a BASE_URL on its one host.
grep -v '^PROVIDER=\|^KEY_VAR=\|^EGRESS=' "$o/conformance.env" > "$o/conformance.zai.env"
printf 'PROVIDER=zai\nKEY_VAR=ZAI_API_KEY\nEGRESS=api.zother.example:443\nBASE_URL=https://api.zother.example/api/anthropic\n' >> "$o/conformance.zai.env"
printf 'ANTHROPIC_LOW=low\nANTHROPIC_MEDIUM=medium\nANTHROPIC_HIGH=high\nZAI_LOW=low\nZAI_MEDIUM=high\nZAI_HIGH=max\n' > "$o/thinking.env"
for f in compose.yaml app/compose.yaml workloads/m/.lights-out/compose.yaml; do
  printf 'services:\n  gemini-cli:\n    image: x\n  other-cli: &worker\n    image: x\n  bin-cli: *worker\n' > "$good/$f"
done

control=$(shape_flaw "$good")
[ -z "$control" ] || nogo "the self-check's good tree is not judged good: $control"

# expect_flaw NAME WANT: the tree $bad, built by the caller, must be refused
# with a message containing WANT.
bad=$work/bad
expect_flaw() {
  got=$(shape_flaw "$bad")
  case "$got" in
    *"$2"*) ;;
    "") nogo "ci/harness-shape.sh cannot fail: it accepted the defect '$1'" ;;
    *) nogo "ci/harness-shape.sh named the wrong problem for '$1'" "wanted a message with: $2" "got: $got" ;;
  esac
}
fresh() { rm -rf "$bad"; cp -R "$good" "$bad"; }
drop_line() {  # FILE ERE: FILE without its lines matching ERE
  grep -Ev "$2" "$1" > "$1.new" || true
  mv "$1.new" "$1"
}
set_conf() {  # KEY VALUE: other-cli's conformance.env with KEY=VALUE, the key's old line replaced
  drop_line "$bad/adapters/other-cli/conformance.env" "^$1="
  printf '%s=%s\n' "$1" "$2" >> "$bad/adapters/other-cli/conformance.env"
}

fresh
jq '.dependencies |= with_entries(.value = "^" + .value)' "$bad/adapters/other-cli/package.json" > "$bad/p.new"
mv "$bad/p.new" "$bad/adapters/other-cli/package.json"
expect_flaw "a ^ range in package.json" "not an exact version"

fresh
jq '.dependencies |= with_entries(.value = "~1.2.3")' "$bad/adapters/other-cli/package.json" > "$bad/p.new"
mv "$bad/p.new" "$bad/adapters/other-cli/package.json"
expect_flaw "a ~ range in package.json" "not an exact version"

fresh
jq '.packages[""].dependencies |= with_entries(.value = "9.9.9")' "$bad/adapters/other-cli/package-lock.json" > "$bad/p.new"
mv "$bad/p.new" "$bad/adapters/other-cli/package-lock.json"
expect_flaw "a lock that asks for another version" "package-lock.json asks"

fresh
jq '.packages |= with_entries(select(.key | startswith("node_modules/") | not))' "$bad/adapters/other-cli/package-lock.json" > "$bad/p.new"
mv "$bad/p.new" "$bad/adapters/other-cli/package-lock.json"
expect_flaw "a lock that installs nothing" "package-lock.json installs"

fresh
rm "$bad/adapters/other-cli/package-lock.json"
expect_flaw "no lockfile" "package-lock.json is missing"

fresh
drop_line "$bad/adapters/other-cli/thinking.env" '^ANTHROPIC_HIGH='
expect_flaw "a thinking map without HIGH" "ANTHROPIC_HIGH"

fresh
printf 'GEMINI_LOW=LOW\nGEMINI_MEDIUM=MEDIUM\nGEMINI_HIGH=HIGH\n' > "$bad/adapters/other-cli/thinking.env"
expect_flaw "a thinking map for another provider" "ANTHROPIC_LOW"

fresh
printf 'EXTRA=1\n' >> "$bad/adapters/other-cli/conformance.env"
expect_flaw "a sixth conformance.env key" "conformance.env has the keys"

fresh
drop_line "$bad/adapters/other-cli/conformance.env" '^THINKING_PATH='
expect_flaw "a conformance.env without THINKING_PATH" "conformance.env has the keys"

fresh
drop_line "$bad/adapters/other-cli/conformance.env" '^EGRESS='
expect_flaw "a conformance.env without EGRESS" "conformance.env has the keys"

fresh
set_conf EGRESS ""
expect_flaw "an empty EGRESS" "conformance.env has an empty EGRESS"

# EGRESS's shape (docs/adr/0046 §4), one planted defect per rule, each named; the last
# one hides the bad entry behind a good one, because every entry of the list is judged.
fresh
set_conf EGRESS "1.1.1.1:443"
expect_flaw "an IP literal in EGRESS" "is an IP literal"

fresh
set_conf EGRESS "*.example.com:443"
expect_flaw "a wildcard in EGRESS" "has a wildcard"

fresh
set_conf EGRESS "api.other.example:0"
expect_flaw "port 0 in EGRESS" "port outside 1-65535"

fresh
set_conf EGRESS "api.other.example:65536"
expect_flaw "port 65536 in EGRESS" "port outside 1-65535"

fresh
set_conf EGRESS "api.other.example"
expect_flaw "an EGRESS entry with no port" "has no port"

fresh
set_conf EGRESS "api.other.example:https"
expect_flaw "a port that is not a number in EGRESS" "port that is not a number"

fresh
set_conf EGRESS "[::1]:443"
expect_flaw "an IPv6 literal in EGRESS" "more than one colon"

fresh
set_conf EGRESS "API.other.example:443"
expect_flaw "an uppercase name in EGRESS" "is not lowercase"

fresh
set_conf EGRESS "localhost:443"
expect_flaw "a name with no dot in EGRESS" "name with no dot"

fresh
set_conf EGRESS "-a.other.example:443"
expect_flaw "a name that starts with a hyphen in EGRESS" "is not a DNS name"

fresh
set_conf EGRESS "api_z.other.example:443"
expect_flaw "a character outside a DNS name in EGRESS" "is not a DNS name"

fresh
set_conf EGRESS "api..other.example:443"
expect_flaw "an empty label in EGRESS" "is not a DNS name"

fresh
set_conf EGRESS "api.other.example:443 10.0.0.5:80"
expect_flaw "a good entry followed by an IP literal in EGRESS" "is an IP literal"

fresh
rm "$bad/adapters/other-cli/conformance.env"
expect_flaw "no conformance.env" "conformance.env is missing"

fresh
set_conf PROVIDER mistral
expect_flaw "a provider outside the four" "want gemini, anthropic, openai or zai"

# A harness's further provider files (docs/adr/0092 §2): other-cli's conformance.zai.env, which
# the good tree has and shape_flaw judged good above, broken one rule at a time. Every rule
# above holds for it too; the messages name the file.
set_zai() {  # KEY VALUE: other-cli's conformance.zai.env with KEY=VALUE, the key's old line replaced
  drop_line "$bad/adapters/other-cli/conformance.zai.env" "^$1="
  printf '%s=%s\n' "$1" "$2" >> "$bad/adapters/other-cli/conformance.zai.env"
}
zf=$bad/adapters/other-cli

fresh
set_zai PROVIDER mistral
expect_flaw "a provider outside the four in a further file" "conformance.zai.env PROVIDER is mistral"

fresh
mv "$zf/conformance.zai.env" "$zf/conformance.glm.env"
expect_flaw "a provider file named for something else than its provider" "conformance.glm.env has PROVIDER zai, so it must be named conformance.zai.env"

fresh
cp "$zf/conformance.env" "$zf/conformance.anthropic.env"
expect_flaw "a second file for the provider conformance.env has" "conformance.anthropic.env has PROVIDER anthropic, which another conformance file of other-cli has"

fresh
cp "$zf/conformance.zai.env" "$zf/conformance.openai.env"
expect_flaw "a second file for a provider under another provider's name" "conformance.openai.env has PROVIDER zai, so it must be named conformance.zai.env"

fresh
printf 'EXTRA=1\n' >> "$zf/conformance.zai.env"
expect_flaw "a sixth key that is not BASE_URL" "conformance.zai.env has the keys"

fresh
set_zai KEY_VAR NOT_IN_THE_EXAMPLE
expect_flaw "a further file's key variable .env.example does not name" "conformance.zai.env KEY_VAR NOT_IN_THE_EXAMPLE is not named in .env.example"

fresh
set_zai EGRESS "1.1.1.1:443"
expect_flaw "an IP literal in a further file's EGRESS" "conformance.zai.env EGRESS: '1.1.1.1:443' is an IP literal"

fresh
drop_line "$zf/thinking.env" '^ZAI_HIGH='
expect_flaw "no thinking map for a further provider" "thinking.env has no ZAI_HIGH= line"

fresh
set_zai BASE_URL "https://api.elsewhere.example/api/anthropic"
expect_flaw "a BASE_URL host that is not the file's EGRESS host" "conformance.zai.env BASE_URL names the host api.elsewhere.example"

fresh
set_conf BASE_URL "https://api.elsewhere.example/v1"
expect_flaw "a BASE_URL host that is not EGRESS's, in conformance.env" "conformance.env BASE_URL names the host api.elsewhere.example"

fresh
set_zai BASE_URL "https://api.zother.example:8443/api/anthropic"
expect_flaw "a BASE_URL with a port other than EGRESS's 443" "conformance.zai.env BASE_URL names the host api.zother.example:8443"

fresh
set_zai EGRESS "api.zother.example:443 cdn.zother.example:443"
expect_flaw "a BASE_URL beside an EGRESS of more than one host" "conformance.zai.env BASE_URL names the host api.zother.example, and its EGRESS is api.zother.example:443 cdn.zother.example:443"

fresh
set_zai BASE_URL "http://api.zother.example/api/anthropic"
expect_flaw "a BASE_URL over http" "conformance.zai.env BASE_URL \"http://api.zother.example/api/anthropic\" is not https://<host>/..."

fresh
set_zai BASE_URL "https://api.zother.example"
expect_flaw "a BASE_URL with no path" "conformance.zai.env BASE_URL \"https://api.zother.example\" is not https://<host>/..."

fresh
set_zai BASE_URL ""
expect_flaw "an empty BASE_URL" "conformance.zai.env BASE_URL \"\" is not https://<host>/..."

# The release pin (docs/adr/0050 §2), on bin-cli.
set_rel() {  # KEY VALUE: bin-cli's release.env with KEY=VALUE, the key's old line replaced
  drop_line "$bad/adapters/bin-cli/release.env" "^$1="
  printf '%s=%s\n' "$1" "$2" >> "$bad/adapters/bin-cli/release.env"
}
rel_get() { conf_get "$good/adapters/bin-cli/release.env" "$1"; }

fresh
set_rel VERSION 1.5
expect_flaw "a release VERSION that is not exact" "not an exact version"

fresh
set_rel SHA256_ARM64 "$(rel_get SHA256_ARM64 | cut -c1-40)"
expect_flaw "a truncated digest" "SHA256_ARM64 is not a sha256"

fresh
set_rel SHA256_AMD64 "$(rel_get SHA256_AMD64 | tr '[:lower:]' '[:upper:]')"
expect_flaw "an uppercase digest" "SHA256_AMD64 is not a sha256"

fresh
set_rel URL_AMD64 "$(rel_get URL_AMD64 | sed "s|/$(rel_get VERSION)/|/latest/|")"
expect_flaw "a release URL that does not name the version" "does not name VERSION"

fresh
set_rel URL_ARM64 "$(rel_get URL_ARM64 | sed 's|^https:|http:|')"
expect_flaw "a release URL over http" "URL_ARM64 is not an https URL"

fresh
drop_line "$bad/adapters/bin-cli/release.env" '^URL_ARM64='
expect_flaw "a release pin without one architecture" "release.env has the keys"

fresh
cp "$good/adapters/gemini-cli/package.json" "$bad/adapters/bin-cli/package.json"
expect_flaw "a release pin beside a package pin" "a harness has one pin"

fresh
grep -v '^KEY_VAR=' "$bad/adapters/other-cli/conformance.env" > "$bad/c.new"
printf 'KEY_VAR=NOT_IN_THE_EXAMPLE\n' >> "$bad/c.new"
mv "$bad/c.new" "$bad/adapters/other-cli/conformance.env"
expect_flaw "a key variable .env.example does not name" "not named in .env.example"

fresh
printf '# drift\n' >> "$bad/adapters/other-cli/hooks/guard.sh"
expect_flaw "a guard that drifted" "hooks/guard.sh differs"

fresh
rm "$bad/adapters/other-cli/hooks/guard.sh"
expect_flaw "no guard" "hooks/guard.sh is missing"

fresh
drop_line "$bad/app/compose.yaml" '^  other-cli:'
expect_flaw "a workload fragment that names one harness of two" "defines a worker service for"

fresh
drop_line "$bad/workloads/m/.lights-out/compose.yaml" '^  gemini-cli:'
expect_flaw "an attached-style fragment that names one harness of two" "defines a worker service for"

fresh
drop_line "$bad/compose.yaml" '^  other-cli:'
expect_flaw "a root compose.yaml without a harness's service" "compose.yaml defines no service named other-cli"

go
