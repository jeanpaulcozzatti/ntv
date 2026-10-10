#!/bin/zsh
# launchd entry point for NTVstreams. Loads the API key from ~/.config/ntvstreams/env
# (a file you create, mode 600, containing `export ANTHROPIC_API_KEY=...`) and runs one pipeline pass.
set -u
export PATH="/opt/homebrew/bin:/Users/jeanpaul/.local/bin:/usr/local/bin:/usr/bin:/bin"
ENV_FILE="$HOME/.config/ntvstreams/env"
if [[ -r "$ENV_FILE" ]]; then
  source "$ENV_FILE"
fi
cd /Users/jeanpaul/Dev/ntv || exit 1
exec uv run --project /Users/jeanpaul/Dev/ntv ntv run
