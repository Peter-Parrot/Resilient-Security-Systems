# Client <--> Server Communication Protocol

- **Transport :** TCP (`socket.SOCK_STREAM`). 
  - Using TCP allows the client to immediately detect connection drops (`ConnectionRefusedError` / `ConnectionResetError`) when the daemon crashes.
- **Framing:** Newline (`\n`) deliminated JSON objects, encoded in `utf-8`.

---
### Request Schema

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `request_id` | `string` | Yes | Unique id used to correlate responses and trace requests. |
| `action` | `string` | Yes | One of:<br> `"GET_CERT"` - retrieve a certificate or public key,<br> `"ENCRYPT_KEY"` - Encrypt a session key,<br> `"LIST_KEYS"` - List available key ID's. |
| `key_id` | `string` | Conditional | Target key/certificate ID (required for `GET_CERT` and `ENCRYPT_KEY`). |
| `plaintext_hash` | `string` | Optional | Dummy session key to encrypt when `action == "ENCRYPT_KEY"`. |
| `client_id` | `string` | No | Identifier of the requesting client. |
| `timestamp` | `float` | Yes | Unix epoch timestamp (`time.time()`). |

---
### Additional Response Fields

Server responses echo `request_id`, `timestamp` (updated to server response time), and—on `"OK"` responses—`action` and `key_id`, along with the following response-specific fields:

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `status` | `string` | Yes | Server response status. One of: `"OK"` or `"ERROR"`. |
| `data` | `object` | Conditional | Result container returned when `status == "OK"`. |
| `data.algorithm` | `string` | Conditional | Cryptographic algorithm of the key/certificate (e.g., `"RSA-2048"`). |
| `data.fingerprint_sha256` | `string` | Conditional | SHA-256 hex fingerprint of the key/certificate. |
| `data.payload` | `string` | Conditional | PEM-encoded certificate/key material, encrypted key output, or key list. |
| `error` | `string` | Conditional | Machine-readable error code when `status == "ERROR"` (e.g., `"KEY_NOT_FOUND"`). |
| `message` | `string` | Conditional | Human-readable error details when `status == "ERROR"`. |

---
### Example Client Request

```json
{
  "request_id": "req-1042",
  "action": "GET_CERT",
  "key_id": "tls-gateway-prod-01",
  "client_id": "client_01",
  "timestamp": 1761073200.125
}
```

### Example Server Responses
---

**Success Response:**
```json
{
  "request_id": "req-2",
  "status": "OK",
  "action": "GET_CERT",
  "key_id": "key2",
  "data": {
    "algorithm": "RSA-2048",
    "fingerprint_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "payload": "-----BEGIN CERTIFICATE-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...\n-----END CERTIFICATE-----"
  },
  "timestamp": 1761073200.130
}
```

**Error Response (e.g., unknown `key_id` or malformed request):**
```json
{
  "request_id": "req-2",
  "status": "ERROR",
  "error": "KEY_NOT_FOUND",
  "message": "Key ID 'unknown-key' does not exist in vault.",
  "timestamp": 1761073200.130
}
```

### Pre-Populated Vault Keys
---

The daemon initializes with dummy key IDs so the client has known IDs to query:
- `"key1"`
- `"key2"`
- `"Encryption Key"` (Key Encryption Key for `WRAP_KEY` requests)