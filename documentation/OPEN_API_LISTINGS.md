# L-Manager Open API v1 - Listings Create

This guide documents the external Open API for creating local listings with workspace credentials.

## Scope

- Version: `v1`
- Endpoint scope: **Create listing only**
- Result: listing is created in local database as `draft`
- PropertyFinder publishing is **not** part of this endpoint

## Credential model

- Credentials are managed per workspace by workspace admins/system admins.
- Multiple credentials are allowed per workspace.
- Every credential has:
  - `key_id` (public, used in `X-API-Key`)
  - `secret` (private, shown once at create/regenerate)
  - status (`active`, `inactive`, `revoked`, `expired`)
  - per-credential rate limit (default `60 req/min`)

## Authentication headers

Every Open API request must include:

- `X-API-Key: <key_id>`
- `X-API-Secret: <secret>`

Optional:

- `X-Request-Id: <your-trace-id>`

## Endpoint

### `POST /api/open/v1/listings`

Creates a local listing in the workspace bound to the credential.

### Required payload fields

- `reference`
- `offering_type`
- `property_type`
- `category`
- `price`
- one of:
  - `title_en`
  - `title_ar`

### Optional fields (common)

- `description_en`, `description_ar`
- `assigned_agent` (email)
- `assigned_to_id` (must belong to same workspace)
- any other local listing fields supported by internal model

## Behavior details

- Listing status is forced to `draft`.
- `reference` must be unique within the credential workspace.
- `assigned_to_id` is validated against workspace membership.
- If `assigned_agent` is omitted, system applies workspace default agent email.

## Example request (cURL)

```bash
curl -X POST "https://l-manager.up.railway.app/api/open/v1/listings" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wsk_xxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "X-API-Secret: wss_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "X-Request-Id: partner-crm-0001" \
  -d '{
    "reference": "CRM-20260228-0001",
    "offering_type": "sale",
    "property_type": "apartment",
    "category": "residential",
    "price": 1750000,
    "title_en": "Two Bedroom in Marina",
    "description_en": "Generated from partner CRM",
    "assigned_agent": "agent@example.com"
  }'
```

## Success response (`201`)

```json
{
  "success": true,
  "data": {
    "id": 123,
    "reference": "CRM-20260228-0001",
    "status": "draft"
  },
  "meta": {
    "workspace_id": 1
  },
  "request_id": "partner-crm-0001"
}
```

## Error response format

All errors return:

```json
{
  "success": false,
  "code": "validation_error",
  "error": "Payload validation failed.",
  "request_id": "oa_abc123...",
  "details": {}
}
```

## Error matrix

- `401 invalid_credentials`
  - Missing `X-API-Key`/`X-API-Secret`
  - Invalid key/secret
- `403 credential_inactive`
  - Credential inactive
- `403 credential_revoked`
  - Credential revoked
- `403 credential_expired`
  - Credential expired
- `403 insufficient_scope`
  - Scope does not include `listings:create`
- `409 duplicate_reference`
  - Same `reference` already exists in workspace
- `422 validation_error`
  - Missing/invalid fields in payload
- `429 rate_limited`
  - Per credential rate limit exceeded
- `500 internal_error`
  - Unexpected server error

## Rate limiting

- Default: `60 requests/minute` per credential.
- On `429`, retry after `Retry-After` header seconds.

## Security practices

- Store secret in a vault, never in client-side code.
- Rotate credential secret if leaked.
- Revoke unused credentials.
- Use separate credentials per integration partner/environment.

## Credential management APIs (session-authenticated)

Workspace admin/system admin can call:

- `GET /api/workspaces/<workspace_id>/open-api/credentials`
- `POST /api/workspaces/<workspace_id>/open-api/credentials`
- `POST /api/workspaces/<workspace_id>/open-api/credentials/<credential_id>/revoke`
- `POST /api/workspaces/<workspace_id>/open-api/credentials/<credential_id>/regenerate-secret`

## OpenAPI spec

- Machine-readable spec: `GET /api/open/v1/spec`
- In-app docs page: `GET /open-api/docs`
