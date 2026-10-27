# NAFN Backend API

The backend exposes two independent entities: partner organizations and resource
locations/services. UUID string IDs and UTC ISO 8601 `createdAt` timestamps are
created by the backend. There is no approval workflow or automatic geocoding.

## Base URL and behavior

| Environment | Base URL |
| --- | --- |
| Production | `https://w55apwodk0.execute-api.us-east-1.amazonaws.com` |
| Development | `https://8ud9th48ee.execute-api.us-east-1.amazonaws.com` |

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

- No authentication or API key is required. Listed records include contact details
  and supplied partner EINs.
- Development allows browser requests from any origin, including localhost and PR
  previews. Production currently allows `https://nafn.us` and `https://dev.nafn.us`
  (plus a legacy `https://PLACEHOLDER` entry). Localhost and PR preview origins
  cannot call production from a browser without a CORS configuration change.
- Use GET/POST and `Content-Type: application/json`. Do not send cookies with
  `credentials: "include"`; credentialed CORS is not enabled.
- Send JSON objects. Unknown fields,
  client-supplied primary IDs, and client-supplied timestamps are ignored.
- Invalid JSON/non-object input returns 400 with `{"error":"..."}`. Invalid
  fields return 400 with `{"errors":["..."]}`. Server failures may return 5xx;
  show a failure message rather than treating the request as successful. A failed network response does not prove a POST was not
  saved, so avoid automatically retrying submissions: there is no idempotency key.
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
A supplied EIN that already exists returns 409. Avoid parallel duplicate
submissions: simultaneous requests can still create duplicate EINs.

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

- 400: invalid/missing required field or malformed supplied EIN/focus area.
- 409: `{"error":"A partner with this EIN already exists."}` when a duplicate
  is found. An extremely unlikely generated-ID collision also returns 409 with
  `{"error":"Partner ID collision; please retry."}`.

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
- 409: `{"error":"Resource ID collision; please retry."}` for a generated-ID collision.

### GET /resources

Response 200: `{"resources":[...]}` containing stored resource records, including
optional partner links and numeric coordinates. Empty table: `{"resources":[]}`.

## Frontend request example

```javascript
const API_BASE_URL = "https://w55apwodk0.execute-api.us-east-1.amazonaws.com";

async function apiRequest(path, payload) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: payload === undefined ? "GET" : "POST",
    ...(payload === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
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

For a partner submission, pass the partner form values as the second argument to
`apiRequest("/partners", values)` and save the returned `partnerId`. For a linked
resource, include that ID in `apiRequest("/resources", values)`. For an independent
resource, omit `partnerId`. Populate a partner selector from GET `/partners` when
linking resources to an existing organization.

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
