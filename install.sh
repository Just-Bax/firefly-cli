#!/usr/bin/env sh
# Installs the firefly CLI on macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/Just-Bax/firefly-cli/master/install.sh | sh
#
# Nothing needs to be installed first. uv is fetched if missing and brings its
# own Python, so the machine never needs a system Python.

set -eu

REPO="Just-Bax/firefly-cli"
BRANCH="master"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok() { printf '\033[32m    %s\033[0m\n' "$1"; }
warn() { printf '\033[33m    %s\033[0m\n' "$1"; }

printf '\nfirefly - Adobe Firefly from the command line\n\n'

export PATH="$HOME/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
    step "uv already installed"
else
    step "Installing uv (provides Python, nothing else needed)"
    curl -fsSL https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv installed but is not on PATH. Open a new terminal and re-run." >&2
    exit 1
fi

step "Installing firefly"
# The zip avoids needing git on the machine.
uv tool install --force --python 3.12 \
    "firefly-cli @ https://github.com/${REPO}/archive/refs/heads/${BRANCH}.zip"

uv tool update-shell >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"

step "Downloading the sign-in browser (about 150MB, one time)"
if ! firefly setup; then
    warn 'Skipped. Run "firefly setup" later.'
fi

printf '\n'
ok "Installed."
printf '\n  Open a NEW terminal, then run:\n'
printf '    firefly login\n'
printf '    firefly image "a red fox in snow"\n\n'
