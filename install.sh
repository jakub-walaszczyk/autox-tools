#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Dynamically get the absolute path of the directory where this script lives
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# 2. Configuration (Defaults to 'axt', but accepts user parameter)
ALIAS_NAME="${1:-axt}"
ZSHRC="$HOME/.zshrc"

# The exact line we want to inject
ALIAS_LINE="alias ${ALIAS_NAME}=\"uv run --project '${PROJECT_DIR}' --env-file '${PROJECT_DIR}/.env'\""

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
echo "👉 Run the following command to refresh your current terminal session:"
echo "   source ~/.zshrc"
echo "🎉 After that, you can run your tool from anywhere using: ${ALIAS_NAME}"
