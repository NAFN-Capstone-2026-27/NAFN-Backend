# NAFN Backend API

The HTTP API defined in `NAFNBackend/BackendInfrastructure/nafn_backend_stack.py`. Handlers live in `NAFNBackend/BackendInfrastructure/lambda/`.

## Base URL

Each environment has its own API. Its URL is the `ApiUrl` output of the `<project_name>-Backend` stack (for example `https://abc123.execute-api.us-east-1.amazonaws.com`). There's no custom domain yet. To look it up:

```
$ aws cloudformation describe-stacks --stack-name NAFN-Backend \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text
```

## Restrictions

These apply to every route.

- **No authentication.** Anyone who has the URL can call any route.
- **Rate limit.** The whole stage is throttled at 10 requests per second, with bursts of up to 20. The limit is shared by all callers. Requests over it get `429 {"message": "Too Many Requests"}`.
- **CORS.** Browsers may call the API only from `https://<domain_name>` (for example `https://dev.nafn.us`) and `https://nafn.us`. The allowed methods are `GET` and `POST`, and the only allowed request header is `Content-Type`. CORS only restricts browsers; other clients such as `curl` can call the API from anywhere.
- **Errors from API Gateway.** A route that doesn't exist returns `404 {"message": "Not Found"}`. An unhandled error in a handler returns `500 {"message": "Internal Server Error"}`.

## Partner object

A partner is stored in the DynamoDB partners table, keyed by `ein`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | Yes | Trimmed. |
| `address` | string | Yes | Trimmed. Free text; it isn't validated. |
| `email` | string | Yes | Trimmed. Must look like `something@domain.tld`. Whether it receives mail isn't checked. |
| `phone` | string | Yes | Any formatting is accepted (`(303) 555-0100`, `303.555.0100`, `+1 ...`). Stored as digits only (`3035550100`). It must contain at least 10 digits. |
| `ein` | string | Yes | Accepted with or without the dash (`12-3456789` or `123456789`). Stored as 9 digits. It must be unique. Send it as a string, because a JSON number drops a leading `0`. |
| `focusAreas` | list of strings | Yes | Any values are accepted. Entries are trimmed, blank ones are dropped, and duplicates are removed. **Order isn't preserved.** |
| `animalsAccepted` | list of strings | No | Cleaned like `focusAreas`. Left out of the stored item when not given. |
| `timeLimits` | string | No | Trimmed. Left out of the stored item when not given. |
| `createdAt` | string | Set by the server | UTC ISO 8601 timestamp of creation, for example `2026-10-23T17:04:12.345678+00:00`. Any value in the request is ignored. |

Any other fields in the request are ignored and aren't stored.

## Routes

### `POST /partners`

Validates a partner and stores it.

Request headers: `Content-Type: application/json`

Request body:

```json
{
  "name": "Example Shelter",
  "address": "1 Main St, Denver CO 80202",
  "email": "contact@example.org",
  "phone": "(303) 555-0100",
  "ein": "12-3456789",
  "focusAreas": ["Domestic Violence", "Climate Emergency"],
  "animalsAccepted": ["Dogs", "Cats"],
  "timeLimits": "Up to 30 days"
}
```

Responses:

| Status | Body | When |
|---|---|---|
| `201` | The stored partner, including `createdAt`, with normalized `phone` and `ein` | The partner was created |
| `400` | `{"error": "Request body must be valid JSON."}` | The body is missing or isn't valid JSON |
| `400` | `{"error": "Request body must be a JSON object."}` | The body is JSON but not an object (for example an array) |
| `400` | `{"errors": ["'email' is required.", "..."]}` | One or more fields are invalid. Every problem is listed, so a form can show them all at once |
| `409` | `{"error": "A partner with this EIN already exists."}` | A partner with the same EIN (after normalizing) already exists. The existing partner isn't changed |

Validation rules behind the `400 {"errors": [...]}` messages:

- A required field that's missing, `null`, blank, or a list containing only blank strings gives `'<field>' is required.`
- A field of the wrong type gives `'<field>' must be a string.` or `'<field>' must be a list of strings.` This also applies to the optional fields: a wrong type is an error, not ignored.
- `email`, `phone` and `ein` also have the format rules listed in the partner table.

There's no way to update or delete a partner through the API.

### `GET /partners`

Returns every partner.

Response `200`:

```json
{
  "partners": [
    {
      "ein": "123456789",
      "name": "Example Shelter",
      "address": "1 Main St, Denver CO 80202",
      "email": "contact@example.org",
      "phone": "3035550100",
      "focusAreas": ["Climate Emergency", "Domestic Violence"],
      "animalsAccepted": ["Cats", "Dogs"],
      "timeLimits": "Up to 30 days",
      "createdAt": "2026-10-23T17:04:12.345678+00:00"
    }
  ]
}
```

- The response isn't paginated, filtered or sorted. The order of partners and of fields isn't guaranteed.
- **This returns every partner's contact details to anyone who calls it.** There's no authentication on this route in any environment, including production.
