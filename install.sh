#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Dynamically get the absolute path of the directory where this script lives
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# 2. Configuration (Defaults to 'axt', but accepts user parameter)
ALIAS_NAME="${1:-axt}"
ZSHRC="$HOME/.zshrc"

# The exact line we want to inject.
#
# The alias pins two project files so the CLI resolves identically from any
# directory, not just the project root:
#   • AUTOX_CONFIG  -> the .autox.yaml profile config (honoured by find_config
#                      as a fallback; a local .autox.yaml still takes priority).
#   • --env-file    -> the .env variables (uv loads them into the environment).
# AUTOX_CONFIG is set unconditionally: find_config ignores it gracefully if the
# file does not exist yet, so it "just works" the moment you run 'axt config init'.
ALIAS_LINE="alias ${ALIAS_NAME}=\"AUTOX_CONFIG='${PROJECT_DIR}/.autox.yaml' uv run --project '${PROJECT_DIR}' --env-file '${PROJECT_DIR}/.env'\""

echo "🚀 Preparing to install alias '${ALIAS_NAME}' for CLI autox-tools..."
echo "📍 Project Directory detected at: ${PROJECT_DIR}"

# Ensure ~/.zshrc exists
touch "$ZSHRC"

# 3. Clean up any existing alias with the same name to avoid cluttering your .zshrc
if grep -q "alias ${ALIAS_NAME}=" "$ZSHRC"; then
    echo "🔄 Existing alias for '${ALIAS_NAME}' found. Updating it..."
    # Cross-platform safe way to remove the old alias line
    grep -v "alias ${ALIAS_NAME}=" "$ZSHRC" > "${ZSHRC}.tmp" && mv "${ZSHRC}.tmp" "$ZSHRC"
fi

# 4. Append the new alias definition
echo "$ALIAS_LINE" >> "$ZSHRC"

echo "✨ Success! The alias has been added to ${ZSHRC}."
echo "🔗 It pins both '.env' and '.autox.yaml' from ${PROJECT_DIR},"
echo "   so profiles and env vars resolve the same from any directory."
echo "👉 Run the following command to refresh your current terminal session:"
echo "   source ~/.zshrc"
echo "🎉 After that, you can run your tool from anywhere using: ${ALIAS_NAME}"
