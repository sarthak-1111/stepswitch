#!/bin/sh
# Stepswitch installer for Linux and macOS.
#
# Without sudo, and without editing shell profiles, it installs:
#   1. uv (the pinned version below), unless a uv is already installed. Its official installer
#      is downloaded, checked against the pinned SHA-256 below, then run with UV_NO_MODIFY_PATH=1.
#   2. Stepswitch, as a uv tool (`uv tool install`). uv downloads a Python build first when no
#      Python 3.11 or newer is installed.
#   3. What tasks use offline: `ssw prefetch` downloads the tokenizer files.
# Every download is announced first. Nothing else is installed: models come later, with
# `ssw models pull`.
#
# Settings, as environment variables:
#   STEPSWITCH_PACKAGE       what to install (default: stepswitch==STEPSWITCH_VERSION; a wheel
#                            file works too)
#   STEPSWITCH_NO_PREFETCH   1 skips `ssw prefetch`
#   UV_INSTALL_DIR           where a new uv goes (the uv installer's default: ~/.local/bin)
# Options: --dry-run prints the plan and changes nothing; --help.
set -eu

UV_VERSION="0.13.1"
UV_INSTALLER_SHA256="7c482d409673a53f4d9bd34ae95c9166be3b8d89ca82f27dd7bca3cab457afff"
STEPSWITCH_VERSION="0.1.0"

say() {
    printf 'stepswitch-install: %s\n' "$*"
}

fail() {
    say "error: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Stepswitch installer: uv, then Stepswitch as a uv tool, then `ssw prefetch`.

Usage: sh install.sh [--dry-run]

  --dry-run   print the plan and change nothing

Environment variables:
  STEPSWITCH_PACKAGE       what to install (default: the pinned stepswitch release; a wheel
                           file works too)
  STEPSWITCH_NO_PREFETCH   1 skips `ssw prefetch`
  UV_INSTALL_DIR           where a new uv goes (default: ~/.local/bin)
EOF
}

download() {  # url file
    if command -v curl >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only -q -O "$2" "$1"
    else
        fail "curl or wget is needed to download $1"
    fi
}

sha256_of() {  # file
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        fail "sha256sum or shasum is needed to check the uv installer"
    fi
}

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for dir in "${UV_INSTALL_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin"; do
        if [ -n "$dir" ] && [ -x "$dir/uv" ]; then
            printf '%s\n' "$dir/uv"
            return 0
        fi
    done
    return 1
}

install_uv() {
    url="https://astral.sh/uv/$UV_VERSION/install.sh"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    say "downloading the uv $UV_VERSION installer from $url"
    download "$url" "$tmp/uv-install.sh"
    actual=$(sha256_of "$tmp/uv-install.sh")
    [ "$actual" = "$UV_INSTALLER_SHA256" ] ||
        fail "the uv installer's SHA-256 is $actual, not the pinned $UV_INSTALLER_SHA256"
    UV_NO_MODIFY_PATH=1 sh "$tmp/uv-install.sh" --quiet
    rm -rf "$tmp"
    trap - EXIT
}

main() {
    dry_run=0
    for arg in "$@"; do
        case "$arg" in
            --dry-run) dry_run=1 ;;
            -h | --help)
                usage
                return 0
                ;;
            *) fail "unknown option: $arg (see --help)" ;;
        esac
    done
    package="${STEPSWITCH_PACKAGE:-stepswitch==$STEPSWITCH_VERSION}"
    prefetch=1
    [ "${STEPSWITCH_NO_PREFETCH:-0}" = 1 ] && prefetch=0

    if uv=$(find_uv); then
        say "uv: $uv"
    else
        uv=""
        say "uv: $UV_VERSION from https://astral.sh/uv/$UV_VERSION/install.sh (UV_NO_MODIFY_PATH=1)"
    fi
    say "Stepswitch: uv tool install $package"
    [ "$prefetch" = 1 ] && say "then: ssw prefetch (the tokenizer files)"
    if [ "$dry_run" = 1 ]; then
        say "dry run: nothing was changed"
        return 0
    fi

    if [ -z "$uv" ]; then
        install_uv
        uv=$(find_uv) || fail "uv was installed but cannot be found; set UV_INSTALL_DIR"
    fi
    "$uv" tool install "$package"
    bin=$("$uv" tool dir --bin)
    ssw="$bin/ssw"
    [ -x "$ssw" ] || fail "ssw is not in $bin after the install"
    "$ssw" --version
    if [ "$prefetch" = 1 ]; then
        "$ssw" prefetch || say "warning: ssw prefetch did not finish; run it again later"
    fi
    case ":$PATH:" in
        *":$bin:"*) ;;
        *) say "add $bin to your PATH, or run: $uv tool update-shell" ;;
    esac
    say "done. Next, in your repository: ssw init, ssw models pull --tier cpu, ssw run \"<task>\""
}

main "$@"
