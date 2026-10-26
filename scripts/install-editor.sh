#!/bin/sh
# Install official, checksum-pinned editor tools without touching Python.
# Usage: sh scripts/install-editor.sh [ABSOLUTE_PREFIX [binaries|tree-sitter]]
# Default: /opt, binaries (Neovim and Node/npm). The tree-sitter mode requires
# Cargo 1.90+ and a C/C++ compiler; Docker supplies these in a separate builder.
# A temporary prefix keeps all installed files outside system directories.
set -eu

prefix=${1:-/opt}
mode=${2:-binaries}
if [ "$#" -gt 2 ]; then
    printf '%s\n' 'Usage: install-editor.sh [ABSOLUTE_PREFIX [binaries|tree-sitter]]' >&2
    exit 2
fi
case "$prefix" in
    /*) ;;
    *) printf '%s\n' 'The installation prefix must be an absolute path.' >&2; exit 2 ;;
esac
case "$mode" in
    binaries|tree-sitter) ;;
    *) printf 'Unknown installation mode: %s\n' "$mode" >&2; exit 2 ;;
esac
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'Editor downloads support Linux only.' >&2
    exit 2
fi

# Published asset digests:
# https://api.github.com/repos/neovim/neovim/releases/tags/v0.12.6
# https://nodejs.org/dist/v22.23.4/SHASUMS256.txt
case "$(uname -m)" in
    aarch64|arm64)
        nvim_arch=arm64
        node_arch=arm64
        nvim_sha=8f1f64a0bdb97247034038c3823c6cbad5bdf9ecd5751b85494b71c3ee04c815
        node_sha=f7a1e486dbc8b286847815b477d4fb5e9fb963ed4489c3e1588853d685f589f9
        ;;
    x86_64|amd64)
        nvim_arch=x86_64
        node_arch=x64
        nvim_sha=474430d53e6264f6d6dd18db42d6dc9df3a1b56ca9e88a325bbf860e1a811d87
        node_sha=b8492284c76a54baf58d61668dd0e41ee0fc98adabf9503d30f05d08f55b4406
        ;;
    *) printf 'Unsupported Linux architecture: %s\n' "$(uname -m)" >&2; exit 2 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM

download() {
    curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' \
        --output "$work/$1" "$2"
    printf '%s  %s\n' "$3" "$work/$1" | sha256sum --check --status
}

if [ "$mode" = tree-sitter ]; then
    # Official Linux release binaries need glibc 2.39, newer than Bookworm's
    # 2.36. Build the published crate against Bookworm instead. The checksum is
    # published at https://index.crates.io/tr/ee/tree-sitter-cli.
    # Cargo.lock pins dependencies and Cargo verifies their registry checksums.
    download tree-sitter.crate \
        https://static.crates.io/crates/tree-sitter-cli/tree-sitter-cli-0.27.1.crate \
        a2dcd60558068c1b09a8ad52eeb8ef04cecace4789d162b0bfb57760cc623b37
    tar -xzf "$work/tree-sitter.crate" -C "$work"
    CARGO_HOME="$work/cargo" CARGO_TARGET_DIR="$work/target" \
        cargo install --locked --no-default-features \
        --path "$work/tree-sitter-cli-0.27.1" --root "$prefix/tree-sitter"
else
    download nvim.tar.gz \
        "https://github.com/neovim/neovim/releases/download/v0.12.6/nvim-linux-$nvim_arch.tar.gz" \
        "$nvim_sha"
    download node.tar.gz \
        "https://nodejs.org/dist/v22.23.4/node-v22.23.4-linux-$node_arch.tar.gz" \
        "$node_sha"
    mkdir -p "$prefix/nvim" "$prefix/node"
    tar -xzf "$work/nvim.tar.gz" --strip-components=1 -C "$prefix/nvim"
    tar -xzf "$work/node.tar.gz" --strip-components=1 -C "$prefix/node"
fi
