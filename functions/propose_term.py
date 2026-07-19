"""
vbc-propose-term Lambda — Bedrock Agent action group.
Called by the agent when a question uses a clinical/VBC term that isn't in
the ontology and both sparql_query + refine_query returned 0 results.

Always returns HTTP 200 (never 5xx) — a 5xx here aborts the entire agent
turn (same failure mode documented in Phase B for memory_write/memory_read).
"""
import json, os, uuid, hashlib
from datetime import datetime, timezone
import boto3

REGION      = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE       = os.environ.get("PROPOSALS_TABLE", "vbc-ontology-proposals")

dynamodb = boto3.client("dynamodb", region_name=REGION)


def _extract_params(event):
    """Bedrock action-group events carry params in event['parameters'] (list of
    {name, value}) or, for RequestBody-based schemas, in
    event['requestBody']['content']['application/json']['properties']."""
    params = {}
    for p in event.get("parameters", []) or []:
        params[p["name"]] = p.get("value")

    body = (event.get("requestBody", {})
                 .get("content", {})
                 .get("application/json", {})
                 .get("properties", []))
    for p in body:
        params[p["name"]] = p.get("value")

    # Plain JSON invocation (e.g. direct testing)
    for k in ("term", "label", "definition", "domain", "parent_class",
              "triggering_question", "failed_sparql"):
        if k in event and k not in params:
            params[k] = event[k]
    return params


def _respond(event, status_body, http_code=200):
    action_group = event.get("actionGroup", "propose_term")
    api_path     = event.get("apiPath", "/propose_term")
    http_method  = event.get("httpMethod", "POST")
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "apiPath": api_path,
            "httpMethod": http_method,
            "httpStatusCode": http_code,
            "responseBody": {
                "application/json": {"body": json.dumps(status_body)}
            },
        },
    }


def lambda_handler(event, context):
    try:
        params = _extract_params(event)
        term       = (params.get("term") or "").strip()
        label      = (params.get("label") or term).strip()
        definition = (params.get("definition") or "").strip()
        domain     = (params.get("domain") or "Clinical").strip()
        parent_class = (params.get("parent_class") or "vbc:Thing").strip()
        triggering_question = (params.get("triggering_question") or "").strip()
        failed_sparql = (params.get("failed_sparql") or "").strip()

        if not term:
            return _respond(event, {"status": "error", "message": "term is required"})

        # Dedup: skip if an identical term is already PENDING
        try:
            scan = dynamodb.scan(
                TableName=TABLE,
                FilterExpression="#t = :t AND #s = :s",
                ExpressionAttributeNames={"#t": "term", "#s": "status"},
                ExpressionAttributeValues={":t": {"S": term}, ":s": {"S": "PENDING"}},
            )
            if scan.get("Count", 0) > 0:
                return _respond(event, {
                    "status": "already_proposed",
                    "message": f"'{label}' is already pending ontology review.",
                })
        except Exception:
            pass  # dedup is best-effort; fall through to propose anyway

        proposal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        try:
            dynamodb.put_item(TableName=TABLE, Item={
                "proposal_id":         {"S": proposal_id},
                "timestamp":           {"S": now},
                "term":                {"S": term},
                "label":               {"S": label},
                "definition":          {"S": definition or f"Auto-flagged term: {label}"},
                "suggested_domain":    {"S": domain},
                "suggested_parent":    {"S": parent_class},
                "triggering_question": {"S": triggering_question},
                "failed_sparql":       {"S": failed_sparql},
                "status":              {"S": "PENDING"},
            })
        except Exception as e:
            return _respond(event, {
                "status": "not_stored",
                "message": "Could not record the proposal right now, but I've noted it isn't in our vocabulary.",
                "error": str(e)[:200],
            })

        return _respond(event, {
            "status": "proposed",
            "proposal_id": proposal_id,
            "message": (f"Term '{label}' submitted for ontology review. "
                        "An ontology administrator will be notified. Once approved, "
                        "the knowledge graph will be updated automatically."),
            "user_message": (f"I couldn't find '{label}' in our current vocabulary. "
                              "I've flagged it for review by our ontology team. "
                              "Once approved, I'll be able to answer questions about it."),
        })
    except Exception as e:
        return _respond(event, {"status": "error", "error": str(e)[:200]})
