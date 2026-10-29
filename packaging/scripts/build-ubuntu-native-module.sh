#!/usr/bin/env bash
# build-ubuntu-native-module.sh -- Build the Markdown filter module against
# the NGINX package that Canonical ships in an Ubuntu LTS archive.
#
# The script must run on the matching Ubuntu release, or in a container of
# that release. It reads the configure arguments from the installed NGINX
# binary, retrieves matching source package, and compiles the module against
# that source. The result carries the same binary compatibility signature as
# the installed binary because `--with-compat` is part of the distro build and
# the source revision matches the binary revision exactly.
#
# Usage:
#   build-ubuntu-native-module.sh --suite 24.04 --arch amd64 \
#       --out dist/ubuntu-native [--install-deps] [--negative-control] \
#       [--expected-revision REV]
#
# Options:
#   --suite SUITE             Ubuntu VERSION_ID of the running system (24.04)
#   --arch ARCH               amd64 or arm64
#   --out DIR                 Output directory for the module and build info
#   --install-deps            Install build dependencies with apt-get
#   --negative-control        Also build a module without --with-compat, for
#                             the binary compatibility rejection test
#   --release                 Embed the release build identity (source commit,
#                             Rust version, feature manifest digest) instead of
#                             the development identity
#   --expected-revision REV   Fail when the installed NGINX package differs
#   --help                    Show this help
#
# Exit codes:
#   0  Module built and build information written
#   1  Any failure: wrong host, missing dependency, revision drift, or build
#      error
#
# The script never evaluates `nginx -V` output in a shell. The arguments are
# tokenized with shlex and passed as an argv array.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SUITE=""
ARCH=""
OUT_DIR=""
INSTALL_DEPS="no"
NEGATIVE_CONTROL="no"
EXPECTED_REVISION=""
RELEASE_IDENTITY="no"

usage() {
    sed -n '2,32p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [build-ubuntu-native-module] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[build-ubuntu-native-module] %s\n' "$1" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            [[ $# -ge 2 ]] || die "--suite requires a value"
            SUITE="$2"
            shift 2
            ;;
        --arch)
            [[ $# -ge 2 ]] || die "--arch requires a value"
            ARCH="$2"
            shift 2
            ;;
        --out)
            [[ $# -ge 2 ]] || die "--out requires a value"
            OUT_DIR="$2"
            shift 2
            ;;
        --expected-revision)
            [[ $# -ge 2 ]] || die "--expected-revision requires a value"
            EXPECTED_REVISION="$2"
            shift 2
            ;;
        --install-deps)
            INSTALL_DEPS="yes"
            shift
            ;;
        --negative-control)
            NEGATIVE_CONTROL="yes"
            shift
            ;;
        --release)
            RELEASE_IDENTITY="yes"
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            usage
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "${SUITE}" ]] || die "--suite is required"
[[ -n "${ARCH}" ]] || die "--arch is required"
[[ -n "${OUT_DIR}" ]] || die "--out is required"
case "${ARCH}" in
    amd64|arm64)
        ;;
    *)
        die "--arch must be amd64 or arm64"
        ;;
esac

# --------------------------------------------------------------- host checks
[[ -r /etc/os-release ]] || die "/etc/os-release is missing"
# Read the two fields instead of sourcing the file: a sourced file is opaque
# to the release-workflow scanner, which then fails closed.
DISTRO_ID="$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')"
DISTRO_VERSION_ID="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')"
[[ "${DISTRO_ID}" == "ubuntu" ]] || die "this script requires Ubuntu, found ${DISTRO_ID:-unknown}"
[[ "${DISTRO_VERSION_ID}" == "${SUITE}" ]] || die \
    "host is Ubuntu ${DISTRO_VERSION_ID:-unknown} but --suite is ${SUITE}"
case "$(uname -m)" in
    x86_64)
        [[ "${ARCH}" == "amd64" ]] || die "host architecture does not match --arch ${ARCH}"
        RUST_TARGET="x86_64-unknown-linux-gnu"
        ;;
    aarch64)
        [[ "${ARCH}" == "arm64" ]] || die "host architecture does not match --arch ${ARCH}"
        RUST_TARGET="aarch64-unknown-linux-gnu"
        ;;
    *)
        die "unsupported host architecture: $(uname -m)"
        ;;
esac

mkdir -p "${OUT_DIR}"
OUT_DIR="$(cd "${OUT_DIR}" && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ubuntu-native-module.XXXXXX")"
cleanup() {
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT HUP INT TERM

# ------------------------------------------------------------ source packages
# Ubuntu images ship deb822 sources. Append the deb-src type once so
# `apt-get source` can retrieve the source revision of the installed binary.
enable_source_repositories() {
    local sources_file="/etc/apt/sources.list.d/ubuntu.sources"
    [[ -f "${sources_file}" ]] || die "deb822 source list is missing: ${sources_file}"
    # Check for an active Types line, not a comment: the stock Ubuntu file
    # explains the deb-src option in a comment, and a substring match would
    # skip the rewrite and make `apt-get source` fail.
    if grep -qE "^Types:.*deb-src" "${sources_file}"; then
        return 0
    fi
    python3 - "${sources_file}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
blocks = path.read_text(encoding="utf-8").split("\n\n")
updated = []
for block in blocks:
    if block.strip() and "Types: deb\n" in block + "\n":
        block = block.replace("Types: deb", "Types: deb deb-src", 1)
    updated.append(block)
path.write_text("\n\n".join(updated) + "\n", encoding="utf-8")
PY
}

install_dependencies() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends \
        nginx \
        nginx-dev \
        build-essential \
        ca-certificates \
        curl \
        dpkg-dev \
        libpcre2-dev \
        zlib1g-dev \
        libssl-dev \
        libbrotli-dev \
        pkg-config \
        python3 \
        git \
        tar \
        file
}

if [[ "${INSTALL_DEPS}" == "yes" ]]; then
    install_dependencies
fi

for tool in nginx dpkg-query dpkg-source python3 cc make tar; do
    command -v "${tool}" >/dev/null 2>&1 || die "required tool is missing: ${tool}"
done

enable_source_repositories
apt-get update -qq
apt-get install -y -qq --no-install-recommends nginx-dev >/dev/null 2>&1 || \
    die "cannot install nginx-dev; check the archive sources"

INSTALLED_REVISION="$(dpkg-query -W -f='${Version}' nginx)"
ABI_PROVIDE="$(dpkg-query -W -f='${Provides}' nginx | tr ',' '\n' \
    | sed 's/^ *//' | grep '^nginx-abi-' | paste -sd, -)"
[[ -n "${ABI_PROVIDE}" ]] || die "installed nginx does not provide nginx-abi-*"
if [[ -n "${EXPECTED_REVISION}" && "${INSTALLED_REVISION}" != "${EXPECTED_REVISION}" ]]; then
    die "installed nginx is ${INSTALLED_REVISION}, release matrix records ${EXPECTED_REVISION}"
fi
info "installed nginx ${INSTALLED_REVISION} (${ABI_PROVIDE})"

# The configure line is the only reliable description of the distro build.
# Tokenize it with shlex; a shell evaluation would execute package metadata.
python3 - "${WORK_DIR}/configure-args.txt" <<'PY'
import shlex
import subprocess
import sys

result = subprocess.run(["nginx", "-V"], capture_output=True, text=True, check=False)
text = (result.stdout or "") + (result.stderr or "")
line = ""
for raw in text.splitlines():
    if "configure arguments:" in raw:
        line = raw.split("configure arguments:", 1)[1].strip()
if not line:
    raise SystemExit("ERROR: nginx -V printed no configure arguments")
args = shlex.split(line)
if "--modules-path" not in " ".join(args):
    raise SystemExit("ERROR: nginx -V printed no --modules-path")
if "--with-compat" not in args:
    raise SystemExit("ERROR: distro nginx was not built with --with-compat")
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write("\n".join(args) + "\n")
print(f"configure arguments: {len(args)} tokens")
PY

cd "${WORK_DIR}"
if ! apt-get source --download-only nginx >"${WORK_DIR}/apt-source.log" 2>&1; then
    tail -20 "${WORK_DIR}/apt-source.log" >&2
    die "apt-get source nginx failed"
fi
SOURCE_DSC="$(ls -1 nginx_*.dsc 2>/dev/null | head -1)"
[[ -n "${SOURCE_DSC}" ]] || die "no nginx source package was downloaded"
SOURCE_REVISION="$(sed -n 's/^Version: //p' "${SOURCE_DSC}" | head -1)"
if [[ "${SOURCE_REVISION}" != "${INSTALLED_REVISION}" ]]; then
    die "source package ${SOURCE_REVISION} does not match installed binary ${INSTALLED_REVISION}"
fi
SOURCE_ORIG="$(ls -1 nginx_*.orig.tar.* 2>/dev/null | head -1)"
[[ -n "${SOURCE_ORIG}" ]] || die "source package has no orig tarball"
SOURCE_SHA256="$(sha256sum "${SOURCE_ORIG}" | awk '{print $1}')"
dpkg-source -x "${SOURCE_DSC}" "${WORK_DIR}/nginx-src" >/dev/null 2>&1 || \
    die "dpkg-source could not extract ${SOURCE_DSC}"

# ---------------------------------------------------------------- Rust library
# The caller provisions the pinned toolchain with the verified installer.
# This script never downloads a compiler: a release build must not install a
# toolchain outside the reviewed path.
export PATH="${HOME}/.cargo/bin:${PATH}"
command -v rustc >/dev/null 2>&1 || die \
    "rustc is not installed; provision the pinned toolchain with packaging/scripts/install-verified-rustup.sh"
RUST_VERSION="$(rustc -Vv | sed -n 's/^release: //p')"
EXPECTED_RUST_VERSION="$(sed -n 's/^channel = "\(.*\)"/\1/p' "${REPO_ROOT}/rust-toolchain.toml")"
if [[ -n "${EXPECTED_RUST_VERSION}" && "${RUST_VERSION}" != "${EXPECTED_RUST_VERSION}" ]]; then
    die "rustc ${RUST_VERSION} does not match the pinned toolchain ${EXPECTED_RUST_VERSION}"
fi

cd "${REPO_ROOT}/components/rust-converter"
rustup target add "${RUST_TARGET}" >/dev/null 2>&1
cargo build --release --locked --target "${RUST_TARGET}" \
    --features "streaming,prune_noise_regions" \
    --config profile.release.lto=false

# --------------------------------------------------------------------- compile
# Brotli is a hard requirement on this path.  The Ubuntu package declares a
# dependency on the fixed libbrotli1, so a silent auto-detect fallback would
# publish a package that promises decoder support and does not have it.  `on`
# turns a missing decoder into a build failure.
export NGX_MARKDOWN_BROTLI_STREAMING=on

# A published distro artifact must not carry the development identity: the
# diagnostics document is how an operator proves which source commit produced
# the module.  Compute the release identity here so the caller cannot pass an
# inconsistent tuple.
if [[ "${RELEASE_IDENTITY}" == "yes" ]]; then
    RELEASE_SOURCE_SHA="$(git -c "safe.directory=${REPO_ROOT}" -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || true)"
    if [[ ! "${RELEASE_SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
        die "cannot resolve the source commit for a release build"
    fi
    RELEASE_MANIFEST="$(sed -n 's/^RELEASE_VERSION = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' \
        "${REPO_ROOT}/tools/release/matrix/normalize_matrix.py" | head -n 1)"
    if [[ ! "${RELEASE_MANIFEST}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        die "cannot resolve the release line for the feature manifest"
    fi
    FEATURE_MANIFEST="${REPO_ROOT}/artifacts/release/${RELEASE_MANIFEST}/official-build-feature-manifest.json"
    [[ -f "${FEATURE_MANIFEST}" ]] || die "feature manifest not found: ${FEATURE_MANIFEST}"
    export NGX_MARKDOWN_BUILD_KIND=release
    export NGX_MARKDOWN_SOURCE_SHA="${RELEASE_SOURCE_SHA}"
    export NGX_MARKDOWN_RUST_VERSION="${RUST_VERSION}"
    export NGX_MARKDOWN_FEATURE_MANIFEST_DIGEST="$(
        python3 - "${FEATURE_MANIFEST}" <<'PY'
import hashlib
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
print("sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest())
PY
    )"
    info "release identity ${RELEASE_SOURCE_SHA} rust ${RUST_VERSION}"
fi

cd "${WORK_DIR}/nginx-src"
NGX_ARGS_FILE="${WORK_DIR}/configure-args.txt"
NGX_ARGS=()
while IFS= read -r argument; do
    NGX_ARGS+=("${argument}")
done < "${NGX_ARGS_FILE}"
[[ "${#NGX_ARGS[@]}" -gt 0 ]] || die "configure argument list is empty"

./configure "${NGX_ARGS[@]}" \
    --add-dynamic-module="${REPO_ROOT}/components/nginx-module" \
    >"${WORK_DIR}/configure.log" 2>&1 || {
        tail -40 "${WORK_DIR}/configure.log" >&2
        die "configure failed"
    }
make -j"$(nproc)" modules >"${WORK_DIR}/make-modules.log" 2>&1 || {
    tail -60 "${WORK_DIR}/make-modules.log" >&2
    die "module build failed"
}

MODULE_PATH="${WORK_DIR}/nginx-src/objs/ngx_http_markdown_filter_module.so"
[[ -f "${MODULE_PATH}" ]] || die "module was not produced"
MODULE_SHA256="$(sha256sum "${MODULE_PATH}" | awk '{print $1}')"
BUILD_ARGS_DIGEST="sha256:$(sha256sum "${NGX_ARGS_FILE}" | awk '{print $1}')"

# ------------------------------------------------------------- decoder audit
# This path links the distribution decoder on purpose so Ubuntu security
# updates reach it.  Record the exact decoder the build used and refuse an
# artifact whose decoder support silently disappeared, because the DEB
# declares a libbrotli1 dependency that would then be a false promise.
BROTLI_VERSION="$(dpkg-query -W -f='${Version}' libbrotli1 2>/dev/null || true)"
[[ -n "${BROTLI_VERSION}" ]] || die "libbrotli1 is not installed; cannot bind the decoder"
if readelf -d "${MODULE_PATH}" 2>/dev/null | grep -Eq 'NEEDED.*libbrotli(dec|common)\.so'; then
    BROTLI_LINKAGE="dynamic"
else
    BROTLI_LINKAGE="absent"
fi
[[ "${BROTLI_LINKAGE}" == "dynamic" ]] || \
    die "module has no dynamic Brotli decoder; refusing a degraded release artifact"
info "linked decoder libbrotli1 ${BROTLI_VERSION} (${BROTLI_LINKAGE})"

install -m 0644 "${MODULE_PATH}" \
    "${OUT_DIR}/ngx_http_markdown_filter_module.so"

python3 - "${OUT_DIR}/ubuntu-native-build.json" "${MODULE_SHA256}" \
    "${BUILD_ARGS_DIGEST}" "${INSTALLED_REVISION}" "${SOURCE_SHA256}" \
    "${ABI_PROVIDE}" "${RUST_VERSION}" "${SUITE}" "${ARCH}" \
    "${BROTLI_VERSION}" "${BROTLI_LINKAGE}" <<'PY'
import json
import sys

(path, module_sha, args_digest, revision, source_sha, abi, rust, suite, arch,
 brotli_version, brotli_linkage) = sys.argv[1:]
payload = {
    "schema_version": 1,
    "source_kind": "ubuntu-default-repo",
    "distro": f"ubuntu-{suite}",
    "distro_version": suite,
    "arch": arch,
    "libc": "glibc",
    "nginx_package_revision": revision,
    "nginx_version": revision.split("-")[0],
    "abi_provide": abi,
    "source_reference": f"https://launchpad.net/ubuntu/+source/nginx/{revision}",
    "source_digest": f"sha256:{source_sha}",
    "build_args_digest": args_digest,
    "module_sha256": module_sha,
    "rust_version": rust,
    "features": ["streaming", "prune_noise_regions"],
    "brotli_version": brotli_version,
    "brotli_linkage": brotli_linkage,
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

info "module sha256=${MODULE_SHA256}"
info "build information: ${OUT_DIR}/ubuntu-native-build.json"

# -------------------------------------------------------- negative control
# A module built from the same source and arguments but WITHOUT --with-compat
# carries a narrower binary compatibility signature. NGINX must reject it with
# "not binary compatible", which proves that the positive load test really
# exercises the signature check instead of merely finding the file.
if [[ "${NEGATIVE_CONTROL}" == "yes" ]]; then
    NEG_DIR="${WORK_DIR}/negative"
    cp -a "${WORK_DIR}/nginx-src" "${NEG_DIR}"
    # The copied tree already contains the compatible module. Remove it, or
    # make sees an up-to-date target and the negative test loads the good
    # module instead of the incompatible one.
    rm -f "${NEG_DIR}/objs/ngx_http_markdown_filter_module.so"
    NEG_ARGS=()
    for argument in "${NGX_ARGS[@]}"; do
        [[ "${argument}" == "--with-compat" ]] && continue
        NEG_ARGS+=("${argument}")
    done
    (
        cd "${NEG_DIR}"
        ./configure "${NEG_ARGS[@]}" \
            --add-dynamic-module="${REPO_ROOT}/components/nginx-module" \
            >"${WORK_DIR}/configure-negative.log" 2>&1
        make -j"$(nproc)" modules >"${WORK_DIR}/make-negative.log" 2>&1
    ) || {
        tail -40 "${WORK_DIR}/configure-negative.log" >&2
        tail -40 "${WORK_DIR}/make-negative.log" >&2
        die "negative-control build failed"
    }
    NEG_MODULE="${NEG_DIR}/objs/ngx_http_markdown_filter_module.so"
    [[ -f "${NEG_MODULE}" ]] || die "negative-control module was not produced"
    install -m 0644 "${NEG_MODULE}" \
        "${OUT_DIR}/ngx_http_markdown_filter_module.incompatible.so"
    info "negative-control module: ${OUT_DIR}/ngx_http_markdown_filter_module.incompatible.so"
fi
