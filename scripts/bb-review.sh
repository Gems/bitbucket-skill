#!/bin/bash
#
# Bitbucket PR batch code review tool for Claude Code
#

# bb-review.sh - Batch code review for Bitbucket PRs
# Usage: bb-review.sh <pr-id> <comments-file>
#
# Comments file format (TSV - tab separated):
# file_path<TAB>line_number<TAB>comment_text
#
# Example comments file:
# app/code/Vendor/Module/Model/Example.php	45	Use dependency injection here
# src/Controller/Api.php	120	Add try/catch for exception handling
#
# Requires: BB_WORKSPACE, BB_REPO, BB_USER, BB_APP_PASSWORD environment variables
# Or reads bitbucket.config relative to this skill's installation path

set -e

PR_ID="$1"
COMMENTS_FILE="$2"

if [[ -z "$PR_ID" || -z "$COMMENTS_FILE" ]]; then
    echo "Usage: bb-review.sh <pr-id> <comments-file>"
    echo ""
    echo "Comments file format (TSV):"
    echo "  file_path<TAB>line_number<TAB>comment_text"
    echo ""
    echo "Example:"
    echo "  echo -e 'src/Model.php\\t45\\tUse DI here' > comments.tsv"
    echo "  bb-review.sh 42 comments.tsv"
    exit 1
fi

if [[ ! -f "$COMMENTS_FILE" ]]; then
    echo "Error: Comments file not found: $COMMENTS_FILE"
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

if [[ -z "$BB_WORKSPACE" || -z "$BB_USER" || -z "$BB_APP_PASSWORD" ]]; then
    echo "Error: Missing Bitbucket credentials"
    echo "Set BB_WORKSPACE, BB_USER, BB_APP_PASSWORD environment variables"
    echo "Or create bitbucket.config beside the skill or at its agent root"
    exit 1
fi

API_URL="https://api.bitbucket.org/2.0/repositories/${BB_WORKSPACE}/${BB_REPO}/pullrequests/${PR_ID}/comments"

echo "Adding review comments to PR #${PR_ID}..."
echo ""

SUCCESS_COUNT=0
FAIL_COUNT=0

while IFS=$'\t' read -r FILE_PATH LINE_NUMBER COMMENT_TEXT || [[ -n "$FILE_PATH" ]]; do
    # Skip empty lines and comments
    [[ -z "$FILE_PATH" || "$FILE_PATH" == \#* ]] && continue
    
    # Escape special characters in comment
    ESCAPED_COMMENT=$(echo "$COMMENT_TEXT" | sed 's/"/\\"/g' | sed 's/\\/\\\\/g')
    
    JSON_PAYLOAD=$(cat <<EOF
{
  "content": {"raw": "${ESCAPED_COMMENT}"},
  "inline": {"path": "${FILE_PATH}", "to": ${LINE_NUMBER}}
}
EOF
)

    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -u "${BB_USER}:${BB_APP_PASSWORD}" \
        -H "Content-Type: application/json" \
        "$API_URL" \
        -d "$JSON_PAYLOAD" 2>/dev/null)

    HTTP_CODE=$(echo "$RESPONSE" | tail -n1)

    if [[ "$HTTP_CODE" -ge 200 && "$HTTP_CODE" -lt 300 ]]; then
        echo "✓ ${FILE_PATH}:${LINE_NUMBER}"
        ((SUCCESS_COUNT++))
    else
        echo "✗ ${FILE_PATH}:${LINE_NUMBER} (HTTP ${HTTP_CODE})"
        ((FAIL_COUNT++))
    fi
    
    # Rate limiting - small delay between requests
    sleep 0.5

done < "$COMMENTS_FILE"

echo ""
echo "Done: ${SUCCESS_COUNT} succeeded, ${FAIL_COUNT} failed"

[[ $FAIL_COUNT -gt 0 ]] && exit 1 || exit 0
