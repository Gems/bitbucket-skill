#!/bin/bash
#
# Bitbucket PR inline comment tool for Claude Code
#

# bb-comment.sh - Add inline comments to Bitbucket PRs
# Usage: bb-comment.sh <pr-id> <file-path> <line-number> "comment text"
#
# Requires: BB_WORKSPACE, BB_REPO, BB_USER, BB_APP_PASSWORD environment variables
# Or reads bitbucket.config relative to this skill's installation path

set -e

PR_ID="$1"
FILE_PATH="$2"
LINE_NUMBER="$3"
COMMENT_TEXT="$4"

if [[ -z "$PR_ID" || -z "$FILE_PATH" || -z "$LINE_NUMBER" || -z "$COMMENT_TEXT" ]]; then
    echo "Usage: bb-comment.sh <pr-id> <file-path> <line-number> \"comment text\""
    echo ""
    echo "Examples:"
    echo "  bb-comment.sh 42 app/code/Vendor/Module/Model/Example.php 45 \"Use dependency injection here\""
    echo "  bb-comment.sh 42 src/Controller/Api.php 120 \"Add try/catch for exception handling\""
    exit 1
fi

# Read config beside the skill, then fall back to the containing agent root.
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
SKILL_CONFIG="$SCRIPT_DIR/../bitbucket.config"
ROOT_CONFIG="$SCRIPT_DIR/../../../bitbucket.config"
CONFIG_FILE="$SKILL_CONFIG"
[[ ! -f "$CONFIG_FILE" ]] && CONFIG_FILE="$ROOT_CONFIG"
if [[ -f "$CONFIG_FILE" ]]; then
    [[ -z "$BB_USER" ]] && BB_USER=$(jq -r '.username // empty' "$CONFIG_FILE" 2>/dev/null || true)
    [[ -z "$BB_APP_PASSWORD" ]] && BB_APP_PASSWORD=$(jq -r '.app_password // empty' "$CONFIG_FILE" 2>/dev/null || true)
    [[ -z "$BB_WORKSPACE" ]] && BB_WORKSPACE=$(jq -r '.workspace // empty' "$CONFIG_FILE" 2>/dev/null || true)
    [[ -z "$BB_REPO" ]] && BB_REPO=$(jq -r '.repo_slug // empty' "$CONFIG_FILE" 2>/dev/null || true)
fi

# Extract workspace and repo from git remote if not set
if [[ -z "$BB_WORKSPACE" || -z "$BB_REPO" ]]; then
    GIT_REMOTE=$(git remote get-url origin 2>/dev/null || true)
    if [[ -n "$GIT_REMOTE" ]]; then
        # Handle both SSH (git@bitbucket.org:workspace/repo.git) and HTTPS formats
        if [[ "$GIT_REMOTE" =~ bitbucket.org[:/]([^/]+)/([^/.]+) ]]; then
            [[ -z "$BB_WORKSPACE" ]] && BB_WORKSPACE="${BASH_REMATCH[1]}"
            [[ -z "$BB_REPO" ]] && BB_REPO="${BASH_REMATCH[2]}"
        fi
    fi
fi

# Validate required variables
if [[ -z "$BB_WORKSPACE" || -z "$BB_USER" || -z "$BB_APP_PASSWORD" ]]; then
    echo "Error: Missing Bitbucket credentials"
    echo "Set BB_WORKSPACE, BB_USER, BB_APP_PASSWORD environment variables"
    echo "Or create bitbucket.config beside the skill or at its agent root"
    exit 1
fi


API_URL="https://api.bitbucket.org/2.0/repositories/${BB_WORKSPACE}/${BB_REPO}/pullrequests/${PR_ID}/comments"

# Create JSON payload
JSON_PAYLOAD=$(cat <<EOF
{
  "content": {
    "raw": "${COMMENT_TEXT}"
  },
  "inline": {
    "path": "${FILE_PATH}",
    "to": ${LINE_NUMBER}
  }
}
EOF
)

# Make API request
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    -u "${BB_USER}:${BB_APP_PASSWORD}" \
    -H "Content-Type: application/json" \
    "$API_URL" \
    -d "$JSON_PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" -ge 200 && "$HTTP_CODE" -lt 300 ]]; then
    echo "✓ Comment added to PR #${PR_ID}"
    echo "  File: ${FILE_PATH}:${LINE_NUMBER}"
else
    echo "✗ Failed to add comment (HTTP ${HTTP_CODE})"
    echo "$BODY" | jq -r '.error.message // .' 2>/dev/null || echo "$BODY"
    exit 1
fi
