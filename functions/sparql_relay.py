"""
Lambda function — SPARQL Relay for Neptune with AWS SigV4 signing.
Runs inside VPC, signs requests using Lambda execution role credentials.
Neptune is on port 8182 (non-standard) — Host header must include port.

Event: { "type": "update"|"query", "sparql": "<SPARQL string>" }
"""
import hashlib, hmac, datetime, json, os, ssl, urllib.parse, urllib.request

NEPTUNE_HOST = os.environ.get(
    "NEPTUNE_HOST",
    "vbc-neptune-poc.cluster-cxe0k4i6swp1.ap-southeast-2.neptune.amazonaws.com"
)
NEPTUNE_PORT = int(os.environ.get("NEPTUNE_PORT", "8182"))
NEPTUNE_PATH = "/sparql"
REGION       = os.environ.get("AWS_REGION", "ap-southeast-2")
SERVICE      = "neptune-db"

SPARQL_URL   = f"https://{NEPTUNE_HOST}:{NEPTUNE_PORT}{NEPTUNE_PATH}"
# Host header must include port for non-443 endpoints
HOST_HEADER  = f"{NEPTUNE_HOST}:{NEPTUNE_PORT}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def get_signing_key(secret_key: str, date_stamp: str) -> bytes:
    k = hmac_sha256(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k = hmac_sha256(k, REGION)
    k = hmac_sha256(k, SERVICE)
    k = hmac_sha256(k, "aws4_request")
    return k


def sigv4_sign(method, payload_bytes, content_type, accept,
               access_key, secret_key, session_token):
    now        = datetime.datetime.utcnow()
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    payload_hash = sha256_hex(payload_bytes)

    # Canonical headers — must be sorted, lowercase names, trimmed values
    headers_dict = {
        "content-type": content_type,
        "host":         HOST_HEADER,
        "x-amz-date":  amz_date,
    }
    if session_token:
        headers_dict["x-amz-security-token"] = session_token

    sorted_header_names = sorted(headers_dict.keys())
    canonical_headers   = "".join(f"{k}:{headers_dict[k]}\n" for k in sorted_header_names)
    signed_headers      = ";".join(sorted_header_names)

    canonical_request = "\n".join([
        method,
        NEPTUNE_PATH,
        "",                  # no query string
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    string_to_sign   = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        sha256_hex(canonical_request.encode("utf-8")),
    ])

    signing_key = get_signing_key(secret_key, date_stamp)
    signature   = hmac.new(signing_key, string_to_sign.encode("utf-8"),
                           hashlib.sha256).hexdigest()

    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    # Build final headers for the actual HTTP request
    final_headers = {k: headers_dict[k] for k in headers_dict}
    final_headers["Authorization"] = auth
    final_headers["Accept"]        = accept
    return final_headers


def handler(event, context):
    sparql_type = event.get("type", "update")
    sparql      = event.get("sparql", "")
    if not sparql:
        return {"statusCode": 400, "body": "Missing sparql field"}

    access_key    = os.environ["AWS_ACCESS_KEY_ID"]
    secret_key    = os.environ["AWS_SECRET_ACCESS_KEY"]
    session_token = os.environ.get("AWS_SESSION_TOKEN", "")

    if sparql_type == "update":
        body_str     = urllib.parse.urlencode({"update": sparql})
        content_type = "application/x-www-form-urlencoded"
        accept       = "application/json"
    else:
        body_str     = urllib.parse.urlencode({"query": sparql})
        content_type = "application/x-www-form-urlencoded"
        accept       = "application/sparql-results+json"

    payload = body_str.encode("utf-8")
    headers = sigv4_sign("POST", payload, content_type, accept,
                         access_key, secret_key, session_token)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    req = urllib.request.Request(SPARQL_URL, data=payload,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return {"statusCode": 200, "body": resp.read().decode()}
    except urllib.error.HTTPError as e:
        return {"statusCode": e.code, "body": e.read().decode()}
    except Exception as e:
        return {"statusCode": 500, "body": str(e)}
