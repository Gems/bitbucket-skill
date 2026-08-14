# Bitbucket REST API Reference

Reference for direct API calls when bb-cli doesn't cover specific use cases.

## Base URL

```
https://api.bitbucket.org/2.0
```

## Authentication

Use email and Bitbucket API Token with Basic Auth:
```bash
curl -u "email@example.com:bitbucket_api_token" https://api.bitbucket.org/2.0/...
```

> **Note:** Use Bitbucket API Token (not Atlassian API Token). See [CONFIG.md](../CONFIG.md) for setup instructions.

## Pull Requests API

### Create PR
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests" \
  -d '{
    "title": "PR Title",
    "source": {"branch": {"name": "feature-branch"}},
    "destination": {"branch": {"name": "develop"}},
    "description": "PR description",
    "close_source_branch": true,
    "reviewers": [{"uuid": "{reviewer-uuid}"}]
  }'
```

### List PRs
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests?state=OPEN"
```

### Get PR
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}"
```

### Merge PR
```bash
curl -X POST -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/merge"
```

### Approve PR
```bash
curl -X POST -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/approve"
```

### Request Changes
```bash
curl -X POST -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/request-changes"
```

### Decline PR
```bash
curl -X POST -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/decline"
```

## Comments API

### Add General Comment
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments" \
  -d '{"content": {"raw": "Comment text"}}'
```

### Add Inline Comment (specific line)
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments" \
  -d '{
    "content": {"raw": "Line-specific comment"},
    "inline": {
      "path": "app/code/Vendor/Module/Model/Example.php",
      "to": 45
    }
  }'
```

### List Comments
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments"
```

## Pipelines API

### Trigger Pipeline
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/" \
  -d '{
    "target": {
      "type": "pipeline_ref_target",
      "ref_type": "branch",
      "ref_name": "develop"
    }
  }'
```

### Trigger Custom Pipeline
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/" \
  -d '{
    "target": {
      "type": "pipeline_ref_target",
      "ref_type": "branch",
      "ref_name": "main",
      "selector": {
        "type": "custom",
        "pattern": "deploy-production"
      }
    }
  }'
```

### Get Pipeline Status
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/{pipeline_uuid}"
```

The same path also resolves a build number — the `#1333` shown in the UI:
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/1333"
```

### List Pipelines
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/?sort=-created_on"
```

## Environments API

### List Environments
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/environments/"
```

### List Environment Variables
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/deployments_config/environments/{env_uuid}/variables"
```

### Create Variable
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/deployments_config/environments/{env_uuid}/variables" \
  -d '{
    "key": "VAR_NAME",
    "value": "var_value",
    "secured": true
  }'
```

## Branches API

### List Branches
```bash
curl -u "$USER:$PASS" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/refs/branches"
```

### Create Branch
```bash
curl -X POST -u "$USER:$PASS" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/refs/branches" \
  -d '{
    "name": "feature/new-branch",
    "target": {"hash": "commit-hash-or-branch-name"}
  }'
```

## Error Handling

Common HTTP status codes:
- `200` - Success
- `201` - Created
- `400` - Bad request (check payload)
- `401` - Unauthorized (check credentials)
- `403` - Forbidden (check permissions)
- `404` - Not found (check workspace/repo/PR ID)
- `409` - Conflict (e.g., merge conflicts)
- `429` - Rate limited

## Pagination

API responses are paginated. Use `next` link from response:
```bash
# First page
curl -u "$USER:$PASS" "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests"

# Response includes:
# "next": "https://api.bitbucket.org/2.0/repositories/.../pullrequests?page=2"
```

## Useful jq Filters

```bash
# Get PR IDs
curl ... | jq '.values[].id'

# Get PR titles and states
curl ... | jq '.values[] | {id, title, state}'

# Get latest pipeline UUID
curl ... | jq '.values[0].uuid'
```
