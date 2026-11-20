# NAFN Backend API

The backend exposes two independent entities: partner organizations and resource
locations/services. UUID string IDs and UTC ISO 8601 `createdAt` timestamps are
created by the backend. There is no approval workflow or automatic geocoding.

## Base URL and behavior

| Environment | Base URL |
| --- | --- |
| Production | `https://api.nafn.us` |
| Development | `https://api.dev.nafn.us` |

The backend stack configures custom domains at `api.<domain_name>`. These URLs
are available after deploying the custom domain configuration and remain stable
when the underlying API is replaced. After deployment, use the backend stack's
`ApiUrl` CloudFormation output as the source of truth for frontend configuration.

Use the production API URL for all frontend integration for now. Requests use
the production database, so submitted records are real shared data. The development
URL is available if separate testing is needed. Append an endpoint below to the
base URL; there is no `/api` prefix.

| Method | Path | Success response |
| --- | --- | --- |
| POST | `/partners` | 201, created partner object |
| GET | `/partners` | 200, `{ "partners": [...] }` |
| POST | `/resources` | 201, created resource object |
| GET | `/resources` | 200, `{ "resources": [...] }` |

- The API is not secure for now: it has no authentication or authorization.
- Use GET/POST and `Content-Type: application/json`. Do not send cookies with
  `credentials: "include"`; credentialed CORS is not enabled.
- Send JSON objects. Unknown fields,
  client-supplied primary IDs, and client-supplied timestamps are ignored.
- Invalid JSON/non-object input returns 400 with `{"error":"..."}`. Invalid
  fields return 400 with `{"errors":["..."]}`. Server failures may return 5xx;
  a failed response can occur after the write succeeds. Only partner submissions
  with an EIN support safe retries. For other submissions, check the saved
  records before submitting again.
- GET endpoints return all records for the demo dataset, with no pagination,
  filters, or guaranteed order. Sort/filter the returned arrays in the frontend.
- Requests are limited to about 10/second with burst capacity of 20 across callers.
  Handle HTTP 429 (too many requests). Unknown routes return 404.
- There are no update/delete endpoints or individual-record GET endpoints.
  Partner links do not grant ownership or editing permissions.

## Partners

Record identifier: `partnerId` (UUID string).

| Field | Required on POST | Behavior |
| --- | --- | --- |
| `name` | Yes | Nonempty trimmed organization name |
| `address` | Yes | Nonempty trimmed organization/mailing address |
| `email` | Yes | Basic email format; no delivery verification |
| `phone` | Yes | String, stored as digits only, at least 10 digits |
| `ein` | No | String: nine ASCII digits or `NN-NNNNNNN`; normalized to nine digits, preserving leading zeros |
| `focusAreas` | Yes | Nonempty list of the allowed values below; blanks removed and duplicates removed in input order |
| `partnerId` | Generated | Backend UUID; not supplied by the form |
| `createdAt` | Generated | UTC ISO 8601 timestamp |

Focus areas: Domestic Violence, Rehabilitation, Assisted Living, Medical Emergency,
Climate Emergency.

Missing, null, empty or whitespace-only EIN is omitted from the stored record.
When supplying an EIN, send a new UUIDv4 in the `Idempotency-Key` header for
each new submission. If a response is lost or returns 5xx, retry with the same
key and the same partner data. The key is scoped to the normalized EIN. A retry
after creation returns 201 with the original partner ID and timestamp.

The EIN is reserved before the partner is written. If creation is interrupted,
the reservation survives, and only the original submission can complete it.

`animalsAccepted` and `timeLimits` belong to resources. If sent to POST /partners,
these fields are ignored and not stored.

### POST /partners

```json
{
  "name": "Example Charity",
  "address": "1 Main St, Denver CO 80202",
  "email": "contact@example.org",
  "phone": "(303) 555-0100",
  "ein": "01-2345678",
  "focusAreas": ["Domestic Violence", "Climate Emergency"]
}
```

Response 201: the cleaned partner above, with `partnerId` and `createdAt` added,
`phone` normalized to `3035550100`, and `ein` normalized to `012345678`.

- 400 for invalid partner data or a missing or invalid `Idempotency-Key` on a
  submission with an EIN.
- 409 with `{"error":"A partner with this EIN already exists."}` when the EIN
  belongs to a partner or is reserved by a different submission. Reusing the
  same key for the EIN with different normalized partner data also returns 409.

### GET /partners

Response 200: `{"partners":[...]}` containing stored partner records. Empty table:
`{"partners":[]}`. Partners without EINs are included and have no `ein` attribute.

## Resources

Record identifier: `resourceId` (UUID string).

| Field | Required on POST | Behavior |
| --- | --- | --- |
| `name` | Yes | Nonempty trimmed location/service name |
| `category` | Yes | Exactly one allowed category below |
| `address` | Yes | Nonempty trimmed location address |
| `lat` | Yes | Finite JSON number from -90 through 90 |
| `lng` | Yes | Finite JSON number from -180 through 180 |
| `partnerId` | No | UUID string referencing an existing partner; omitted for independent resources |
| `phone` | No | String, digits only when supplied, at least 10 digits |
| `email` | No | Basic email format when supplied |
| `website` | No | HTTP/HTTPS URL with a host; embedded username/password not accepted |
| `description` | No | Trimmed plain text |
| `animalsAccepted` | No | List of strings; trimmed, blanks removed, duplicates removed in input order |
| `timeLimits` | No | Trimmed text such as “Up to 30 days” or “Case by case” |
| `resourceId` | Generated | Backend UUID; not supplied by the form |
| `createdAt` | Generated | UTC ISO 8601 timestamp |

Categories: Temporary Foster Services, Emergency Vets, Food Assistance, Pet Supplies,
Wildlife, Animal Control, Other.

Blank/null optional text and empty animal lists are omitted. Wrong types are
rejected. Coordinates must be JSON numbers, not strings or booleans; zero is valid.
They are returned as JSON numbers. An address does not produce coordinates
automatically. EIN/focusAreas are not resource attributes.

### POST /resources

```json
{
  "name": "Example Charity - Denver",
  "partnerId": "c215a75a-31e9-4a44-8f9f-936ec54ca928",
  "category": "Temporary Foster Services",
  "address": "1 Main St, Denver CO 80202",
  "lat": 39.7392,
  "lng": -104.9903,
  "phone": "(303) 555-0100",
  "animalsAccepted": ["Dogs", "Cats"],
  "timeLimits": "Up to 30 days"
}
```

Create the referenced partner first, or omit `partnerId` for an independent
resource. Multiple resources can reference the same partner.

Response 201: cleaned resource with generated `resourceId` and `createdAt`.

- 400: invalid required/optional fields or nonexistent referenced partner.

### GET /resources

Response 200: `{"resources":[...]}` containing stored resource records, including
optional partner links and numeric coordinates. Empty table: `{"resources":[]}`.

## Frontend request example

```javascript
const API_BASE_URL = "https://api.nafn.us";

async function apiRequest(path, payload, idempotencyKey) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: payload === undefined ? "GET" : "POST",
    ...(payload === undefined ? {} : {
      headers: {
        "Content-Type": "application/json",
        ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
      },
      body: JSON.stringify(payload),
    }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      data?.errors?.join(" ") || data?.error || data?.message ||
      `Request failed (${response.status})`
    );
  }
  return data;
}

const { partners } = await apiRequest("/partners");
const { resources } = await apiRequest("/resources");
```

For a partner submission with an EIN, generate the key once with
`crypto.randomUUID()` and keep it with the submitted form values for retries.
Call `apiRequest("/partners", values, idempotencyKey)` and save the returned
`partnerId`. Omit the key for submissions without an EIN. For a linked resource,
include the saved partner ID in `apiRequest("/resources", values)`. For an
independent resource, omit `partnerId`. Populate a partner selector from GET
`/partners` when linking resources to an existing organization.

Convert coordinate inputs to numbers before submitting (for example,
`Number(latInput)`), but validate nonempty inputs first: `Number("")` produces
zero. The frontend must supply coordinates from a map, geocoder, or manual input;
the API does not derive them from the address.

Handle both validation error shapes:

```json
{"error": "Request body must be valid JSON."}
```

```json
{"errors": ["'name' is required.", "'focusAreas' is required."]}
```

Disable repeated submission while a request is pending. On HTTP 201, use the
returned object as the saved record; do not generate IDs/timestamps in the form.
On HTTP 400/409, show the API's message and preserve the user's input. Network or
CORS failures reject `fetch` without an HTTP response; show a connection error.
