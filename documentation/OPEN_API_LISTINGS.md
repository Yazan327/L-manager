# L-Manager Open API v1.1 - Listings Create

## Changelog (v1.1)

- Expanded create payload docs to include all supported listing fields.
- Added compatibility alias map (PF/camelCase input support).
- Clarified that listing create is always `draft` (status is forced).

## Scope

- Version: `v1.1`
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

Required:

- `X-API-Key: <key_id>`
- `X-API-Secret: <secret>`

Optional:

- `X-Request-Id: <your-trace-id>`

## Endpoint

### `POST /api/open/v1/listings`

Creates a local listing in the workspace bound to the credential.

## Required payload fields

- `reference`
- `offering_type`
- `property_type`
- `category`
- `price`
- at least one title:
  - `title_en`, or
  - `title_ar`, or
  - `title.en` / `title.ar` alias format

## Supported canonical fields

### Core
- `reference`, `emirate`, `city`, `location`, `location_id`
- `category`, `offering_type`, `property_type`

### Specifications
- `bedrooms`, `bathrooms`, `size`
- `furnishing_type`, `project_status`
- `parking_slots`, `floor_number`, `unit_number`

### Pricing
- `price`, `downpayment`, `rent_frequency`

### Content
- `title_en`, `title_ar`
- `description_en`, `description_ar`

### Media
- `images`, `original_images`
- `video_tour`, `video_360`
- `amenities`

### Assignment / Ownership
- `assigned_agent` (email or PF profile id string)
- `assigned_to_id` (workspace user id)
- `owner_id`, `owner_name`, `developer`, `permit_number`, `available_from`

### Compatibility input
- `status` is accepted but ignored; server stores `draft`.

## Enum guidance

- `offering_type`: `sale`, `rent`
- `category`: `residential`, `commercial`
- `rent_frequency`: `yearly`, `monthly`, `weekly`, `daily`

## Alias compatibility map (accepted)

- `uaeEmirate` -> `emirate`
- `type` -> `property_type`
- `furnishingType` -> `furnishing_type`
- `projectStatus` -> `project_status`
- `parkingSlots` -> `parking_slots`
- `floorNumber` -> `floor_number`
- `unitNumber` -> `unit_number`
- `ownerName` -> `owner_name`
- `availableFrom` -> `available_from`
- `assignedTo.id` -> `assigned_agent`
- `location.id` -> `location_id`
- `price.type + price.amounts` -> `offering_type/rent_frequency/price`
- `title.en|ar` -> `title_en|title_ar`
- `description.en|ar` -> `description_en|description_ar`
- `media.images/videos` -> `images/video_tour/video_360`
- `compliance.listingAdvertisementNumber` -> `permit_number`

## Behavior details

- Listing status is always forced to `draft`.
- `reference` must be unique within the credential workspace.
- `assigned_to_id` is validated against workspace members.
- If `assigned_agent` is omitted, workspace default agent email is applied.

## Example request (canonical)

```bash
curl -X POST "https://l-manager.up.railway.app/api/open/v1/listings" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wsk_xxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "X-API-Secret: wss_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "X-Request-Id: partner-crm-0001" \
  -d '{
    "reference": "CRM-20260301-0001",
    "emirate": "dubai",
    "city": "Dubai Marina",
    "location": "Marina Gate 1",
    "location_id": 123,
    "category": "residential",
    "offering_type": "sale",
    "property_type": "apartment",
    "bedrooms": "2",
    "bathrooms": "2",
    "size": 1320,
    "furnishing_type": "furnished",
    "project_status": "completed",
    "price": 1950000,
    "title_en": "2BR Marina Gate High Floor",
    "description_en": "Ready to move apartment with full marina view"
  }'
```

## Success response (`201`)

```json
{
  "success": true,
  "data": {
    "id": 123,
    "reference": "CRM-20260301-0001",
    "status": "draft"
  },
  "meta": {
    "workspace_id": 1
  },
  "request_id": "partner-crm-0001"
}
```

## Error response format

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
- `403 credential_revoked`
- `403 credential_expired`
- `403 insufficient_scope`
- `409 duplicate_reference`
- `422 validation_error`
- `429 rate_limited`
- `500 internal_error`

## Rate limiting

- Default: `60 requests/minute` per credential.
- On `429`, retry after `Retry-After` header seconds.

## Credential management APIs (session-authenticated)

- `GET /api/workspaces/<workspace_id>/open-api/credentials`
- `POST /api/workspaces/<workspace_id>/open-api/credentials`
- `POST /api/workspaces/<workspace_id>/open-api/credentials/<credential_id>/revoke`
- `POST /api/workspaces/<workspace_id>/open-api/credentials/<credential_id>/regenerate-secret`

## Spec endpoints

- Machine-readable spec: `GET /api/open/v1/spec`
- In-app docs page: `GET /open-api/docs`
