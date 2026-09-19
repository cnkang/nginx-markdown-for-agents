#!/usr/bin/env bash
# Fixture harness for the OCI-label image binding check in
# tools/ci/verify_official_nginx_docker.sh.
#
# The release gate cannot be exercised end-to-end here (no Docker daemon is
# available), so this harness sources the script's label helpers with a
# synthetic `docker image inspect` payload and asserts the accept/reject
# decision for each fixture.  The helpers under test are pure functions of
# the inspect JSON plus the MODULE_SHA / IMAGE_DIGEST / IMAGE_REFERENCE
# globals, which is exactly what the --skip-build path feeds them.
#
# Usage: bash tools/ci/test_verify_official_nginx_docker_binding.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VERIFIER="${REPO_ROOT}/tools/ci/verify_official_nginx_docker.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS: %s\n' "$1"
  return 0
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL: %s\n' "$1" >&2
  [[ -n "${2:-}" ]] && printf '      Detail: %s\n' "$2" >&2
  return 0
}

if [[ ! -f "${VERIFIER}" ]]; then
  fail "verifier script not found" "${VERIFIER}"
  exit 1
fi

# Source only the helper functions: strip the main flow by extracting the
# function definitions the test needs.  Sourcing the whole script would run
# the argument parser and exit on missing MODULE_SHA.
HELPERS_FILE="$(mktemp "${TMPDIR:-/tmp}/verifier-helpers.XXXXXX")"
trap 'rm -f "${HELPERS_FILE}"' EXIT
python3 - "${VERIFIER}" "${HELPERS_FILE}" <<'PY'
import re
import sys

source, destination = sys.argv[1], sys.argv[2]
text = open(source, encoding="utf-8").read()
wanted = ("read_oci_label", "verify_reused_image_binding")
chunks = []
for name in wanted:
    match = re.search(
        rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.M | re.S
    )
    if match is None:
        raise SystemExit(f"cannot extract function {name} from {source}")
    chunks.append(match.group(0))

labels = re.search(
    r'^OCI_LABEL_(REVISION|BASE_NAME|BASE_DIGEST)="[^"]+"\n', text, re.M
)
header = ""
for line in text.splitlines(keepends=True):
    if line.startswith("OCI_LABEL_"):
        header += line
open(destination, "w", encoding="utf-8").write(header + "\n" + "\n".join(chunks))
PY
if [[ ! -s "${HELPERS_FILE}" ]]; then
  fail "could not extract label helpers from the verifier"
  exit 1
fi
# shellcheck source=/dev/null
. "${HELPERS_FILE}"

MODULE_SHA="$(printf 'a%.0s' $(seq 1 40))"
IMAGE_DIGEST="sha256:$(printf 'b%.0s' $(seq 1 64))"
IMAGE_REFERENCE="nginx:1.31.5"
OTHER_SHA="$(printf 'c%.0s' $(seq 1 40))"
# Sentinel for the inspect-payload writer: this label is not present.
ABSENT_LABEL="__absent__"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/verifier-binding-fixtures.XXXXXX")"
trap 'rm -f "${HELPERS_FILE}"; rm -rf "${WORK_DIR}"' EXIT

write_inspect() {
  local path="$1" revision="$2" base_name="$3" base_digest="$4"
  python3 - "${path}" "${revision}" "${base_name}" "${base_digest}" <<'PY'
import json
import sys

path, revision, base_name, base_digest = sys.argv[1:5]
labels = {}
if revision != "__absent__":
    labels["org.opencontainers.image.revision"] = revision
if base_name != "__absent__":
    labels["org.opencontainers.image.base.name"] = base_name
if base_digest != "__absent__":
    labels["org.opencontainers.image.base.digest"] = base_digest
payload = [
    {
        "Id": "sha256:" + "d" * 64,
        "Config": {"Labels": labels},
    }
]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
PY
  return 0
}

expect_binding() {
  local fixture="$1" expected_rc="$2" label="$3"
  local rc=0
  verify_reused_image_binding "${fixture}" >/dev/null 2>&1 || rc=$?
  if [[ "${rc}" -eq "${expected_rc}" ]]; then
    pass "${label}: binding decision rc=${rc}"
  else
    fail "${label}: expected rc=${expected_rc}, got rc=${rc}"
  fi
  return 0
}

# Fixture 1: full OCI evidence -> binding established.
FIXTURE_OK="${WORK_DIR}/inspect-ok.json"
write_inspect "${FIXTURE_OK}" "${MODULE_SHA}" "${IMAGE_REFERENCE}" "${IMAGE_DIGEST}"
expect_binding "${FIXTURE_OK}" 0 "fixture 1 (revision+base digest match)"

# Fixture 2: base name label absent (BuildKit omits it without a registry
# name) -> still accepted, since revision + digest are the decisive evidence.
FIXTURE_NO_NAME="${WORK_DIR}/inspect-no-name.json"
write_inspect "${FIXTURE_NO_NAME}" "${MODULE_SHA}" "${ABSENT_LABEL}" "${IMAGE_DIGEST}"
expect_binding "${FIXTURE_NO_NAME}" 0 "fixture 2 (base name absent, digest matches)"

# Fixture 3: stale image with a different module revision -> reject.
FIXTURE_STALE="${WORK_DIR}/inspect-stale.json"
write_inspect "${FIXTURE_STALE}" "${OTHER_SHA}" "${IMAGE_REFERENCE}" "${IMAGE_DIGEST}"
expect_binding "${FIXTURE_STALE}" 1 "fixture 3 (revision != MODULE_SHA)"

# Fixture 4: base digest differs from the matrix row -> reject.
FIXTURE_BASE_MISMATCH="${WORK_DIR}/inspect-base-mismatch.json"
write_inspect \
  "${FIXTURE_BASE_MISMATCH}" "${MODULE_SHA}" "${IMAGE_REFERENCE}" \
  "sha256:$(printf 'e%.0s' $(seq 1 64))"
expect_binding "${FIXTURE_BASE_MISMATCH}" 1 "fixture 4 (base digest != IMAGE_DIGEST)"

# Fixture 5: locally built image without any OCI labels -> reject.
FIXTURE_LOCAL="${WORK_DIR}/inspect-no-labels.json"
write_inspect "${FIXTURE_LOCAL}" "${ABSENT_LABEL}" "${ABSENT_LABEL}" "${ABSENT_LABEL}"
expect_binding "${FIXTURE_LOCAL}" 1 "fixture 5 (no OCI labels at all)"

# Fixture 6: base name present but naming a different reference -> reject.
FIXTURE_NAME_MISMATCH="${WORK_DIR}/inspect-name-mismatch.json"
write_inspect \
  "${FIXTURE_NAME_MISMATCH}" "${MODULE_SHA}" "nginx:1.29.0" "${IMAGE_DIGEST}"
expect_binding "${FIXTURE_NAME_MISMATCH}" 1 "fixture 6 (base name != IMAGE_REFERENCE)"

# Fixture 7: malformed inspect payload -> reject (fail closed, no crash).
FIXTURE_MALFORMED="${WORK_DIR}/inspect-malformed.json"
printf '%s' '{not valid json' > "${FIXTURE_MALFORMED}"
expect_binding "${FIXTURE_MALFORMED}" 1 "fixture 7 (malformed inspect payload)"

# Fixture 8: empty payload / image absent -> reject.
FIXTURE_EMPTY="${WORK_DIR}/inspect-empty.json"
: > "${FIXTURE_EMPTY}"
expect_binding "${FIXTURE_EMPTY}" 1 "fixture 8 (empty inspect payload)"

# Fixture 9 (behavioral): the --skip-build branch must consult the label
# check before it may claim the binding, and must fail closed when the check
# fails.  Token greps would pass even if a future edit dropped or bypassed
# the call, which is exactly the regression the release finding described, so
# the branch body is extracted from the script itself (by line markers, so
# test and script cannot drift) and executed in a sandbox where the image
# inspect always succeeds and the OCI-label check always fails.
SKIP_BRANCH_BODY="$(
  python3 - "${VERIFIER}" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
header = 'if [[ "${SKIP_BUILD}" -eq 1 ]]; then'
start = text.find(header)
end = text.find('else\n  echo "==> Building image from', start)
if start < 0 or end <= start:
    raise SystemExit(0)
body = text[start + len(header):end]
# The header line is dropped so the body is a self-contained statement list;
# if the branch was rewritten to a shape this fixture cannot execute, print
# nothing and let the fixture fail closed.
if not body.startswith("\n"):
    raise SystemExit(0)
print(body, end="")
PY
)"
if [[ -z "${SKIP_BRANCH_BODY}" ]]; then
  fail "fixture 9: could not isolate the --skip-build branch body"
else
  SANDBOX="${WORK_DIR}/skip-branch-sandbox.sh"
  python3 - "${SANDBOX}" "${WORK_DIR}" "${SKIP_BRANCH_BODY}" <<'PY'
import sys

path, work_dir, body = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "w", encoding="utf-8") as handle:
    handle.write(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        "# Sandbox for the --skip-build branch body extracted from\n"
        "# tools/ci/verify_official_nginx_docker.sh (fixture 9).\n"
        "SKIP_BUILD=1\n"
        'IMAGE_NAME="sandbox-binding-check:tag"\n'
        f'TMP_DIR="{work_dir}"\n'
        'ARTIFACT_DIR=""\n'
        'ALLOW_UNVERIFIED_IMAGE_BINDING="${ALLOW_UNVERIFIED_IMAGE_BINDING:-0}"\n'
        "IMAGE_BINDING_ESTABLISHED=0\n"
        'IMAGE_BINDING_SOURCE=""\n'
        "# `docker image inspect` always succeeds and the OCI-label check always\n"
        "# fails, so the branch must decide from that failure alone.\n"
        "docker() { return 0; }\n"
        "verify_reused_image_binding() { return 1; }\n"
        "trap 'printf \"BINDING_ESTABLISHED=%s\\n\" \"${IMAGE_BINDING_ESTABLISHED}\"' EXIT\n"
        "\n" + body
    )
PY
  run_skip_branch_sandbox() {
    local allow_value="$1"
    local tag="$2"
    SANDBOX_RC=0
    SANDBOX_OUT="${WORK_DIR}/skip-branch-${tag}.out"
    SANDBOX_ERR="${WORK_DIR}/skip-branch-${tag}.err"
    ALLOW_UNVERIFIED_IMAGE_BINDING="${allow_value}" \
      bash "${SANDBOX}" >"${SANDBOX_OUT}" 2>"${SANDBOX_ERR}" || SANDBOX_RC=$?
    return 0
  }

  # Fail path (default): the label check fails, so the branch must exit 1,
  # explain the unestablished binding, and leave the claim unset.
  run_skip_branch_sandbox 0 "failclosed"
  if [[ "${SANDBOX_RC}" -eq 1 ]]; then
    pass "fixture 9: failed label check exits 1 (fail closed)"
  else
    fail "fixture 9: failed label check must exit 1" \
      "rc=${SANDBOX_RC}; stderr: $(head -3 "${SANDBOX_ERR}" | tr '\n' ' ')"
  fi
  if grep -qF 'Image binding established from OCI labels' \
    "${SANDBOX_OUT}" "${SANDBOX_ERR}"; then
    fail "fixture 9: fail path claimed the binding was established"
  else
    pass "fixture 9: fail path does not claim the binding was established"
  fi
  if grep -q '^BINDING_ESTABLISHED=0$' "${SANDBOX_OUT}"; then
    pass "fixture 9: fail path leaves IMAGE_BINDING_ESTABLISHED=0"
  else
    fail "fixture 9: fail path changed IMAGE_BINDING_ESTABLISHED" \
      "sandbox stdout: $(tr '\n' ' ' <"${SANDBOX_OUT}")"
  fi
  if grep -q 'Image binding not established' "${SANDBOX_ERR}"; then
    pass "fixture 9: fail path explains the unestablished binding"
  else
    fail "fixture 9: fail path does not explain the unestablished binding"
  fi

  # Skip path (ALLOW_UNVERIFIED_IMAGE_BINDING=1): the run may continue, but
  # the skip must not fabricate verification — the claim stays unproduced and
  # IMAGE_BINDING_ESTABLISHED stays 0.
  run_skip_branch_sandbox 1 "allowed"
  if [[ "${SANDBOX_RC}" -eq 0 ]]; then
    pass "fixture 9: ALLOW_UNVERIFIED_IMAGE_BINDING=1 continues (rc=0)"
  else
    fail "fixture 9: ALLOW_UNVERIFIED_IMAGE_BINDING=1 must continue" \
      "rc=${SANDBOX_RC}; stderr: $(head -3 "${SANDBOX_ERR}" | tr '\n' ' ')"
  fi
  if grep -qF 'Image binding established from OCI labels' \
    "${SANDBOX_OUT}" "${SANDBOX_ERR}"; then
    fail "fixture 9: skip path fabricated a verification claim"
  else
    pass "fixture 9: skip path does not fabricate a verification claim"
  fi
  if grep -q '^BINDING_ESTABLISHED=0$' "${SANDBOX_OUT}"; then
    pass "fixture 9: skip path leaves IMAGE_BINDING_ESTABLISHED=0"
  else
    fail "fixture 9: skip path changed IMAGE_BINDING_ESTABLISHED" \
      "sandbox stdout: $(tr '\n' ' ' <"${SANDBOX_OUT}")"
  fi
  if grep -q 'continuing because --allow-unverified-image-binding was given' \
    "${SANDBOX_ERR}"; then
    pass "fixture 9: skip path warns that the binding is unverified"
  else
    fail "fixture 9: skip path is missing the unverified-binding warning"
  fi
fi

# Fixture 10 (structural): the binding must not be established before the
# label check, i.e. the initial value stays 0 until verified.
if grep -q '^IMAGE_BINDING_ESTABLISHED=0$' "${VERIFIER}"; then
  pass "fixture 10: IMAGE_BINDING_ESTABLISHED starts unset (fail closed by default)"
else
  fail "fixture 10: IMAGE_BINDING_ESTABLISHED does not default to 0"
fi

# Fixture 11 (structural): the build path must self-attest the same OCI labels
# the --skip-build path reads back, so an image built by this script carries
# its matrix binding and can be verified on reuse.
if grep -qF -- '--label "${OCI_LABEL_REVISION}=${MODULE_SHA}"' "${VERIFIER}" \
  && grep -qF -- '--label "${OCI_LABEL_BASE_NAME}=${IMAGE_REFERENCE}"' "${VERIFIER}" \
  && grep -qF -- '--label "${OCI_LABEL_BASE_DIGEST}=${IMAGE_DIGEST}"' "${VERIFIER}"; then
  pass "fixture 11: build path stamps the OCI binding labels (self-attestation)"
else
  fail "fixture 11: build path does not stamp the OCI binding labels"
fi

# Fixture 12 (structural): the official-nginx-docker workflow builds the image
# it later verifies with --skip-build, so it must stamp the same labels;
# otherwise the verify step fails closed by design (release gate turns red).
DOCKER_WORKFLOW="${REPO_ROOT}/.github/workflows/official-nginx-docker.yml"
if [[ -f "${DOCKER_WORKFLOW}" ]] \
  && grep -qF 'org.opencontainers.image.revision=${{ steps.source.outputs.sha }}' "${DOCKER_WORKFLOW}" \
  && grep -qF 'org.opencontainers.image.base.name=${{ matrix.image_ref }}' "${DOCKER_WORKFLOW}" \
  && grep -qF 'org.opencontainers.image.base.digest=${{ matrix.image_digest }}' "${DOCKER_WORKFLOW}"; then
  pass "fixture 12: official Docker workflow stamps the OCI binding labels on the built image"
else
  fail "fixture 12: official Docker workflow build is missing the OCI binding labels"
fi

echo
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  exit 1
fi
exit 0
