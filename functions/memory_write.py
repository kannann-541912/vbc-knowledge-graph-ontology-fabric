"""
vbc-memory-write Lambda  (NOT in VPC)
Persists a successful Q -> SPARQL pair to DynamoDB (vbc-query-memory) and
OpenSearch (vbc-query-memory index, KNN) so future similar questions can
reuse the proven query instead of generating from scratch.

Called directly by the agent as an action (fire-and-forget from the agent's
perspective — it does not gate the user-facing answer).
"""
import json, os, hashlib, boto3
from datetime import datetime, timezone
import urllib.request, base64

REGION      = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE_NAME  = os.environ.get("MEMORY_TABLE", "vbc-query-memory")
OS_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT",
                              "search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com")
OS_USER     = os.environ.get("OPENSEARCH_USER", "vbcadmin")
OS_PASS     = os.environ.get("OPENSEARCH_PASS", "VbcPoc2024!")
OS_INDEX    = os.environ.get("OPENSEARCH_MEMORY_INDEX", "vbc-query-memory")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")
MODEL_VERSION = os.environ.get("BEDROCK_MODEL", "amazon.nova-pro-v1:0")

bedrock  = boto3.client("bedrock-runtime", region_name=REGION)
dynamodb = boto3.client("dynamodb",        region_name=REGION)

def embed(text: str) -> list:
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": 1024}),
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]

def os_index(doc_id: str, body: dict):
    creds = base64.b64encode(f"{OS_USER}:{OS_PASS}".encode()).decode()
    req = urllib.request.Request(
        f"https://{OS_ENDPOINT}/{OS_INDEX}/_doc/{doc_id}",
        data=json.dumps(body).encode(),
        method="PUT",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def _extract_param(event, param_name):
    props = (event.get("requestBody", {})
                  .get("content", {})
                  .get("application/json", {})
                  .get("properties", []))
    for p in props:
        if p.get("name") == param_name:
            return p.get("value", "")
    return event.get(param_name, "")

def _agent_response(event, body: dict, status: int) -> dict:
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", "store_memory"),
            "apiPath":     event.get("apiPath", "/memory/write"),
            "httpMethod":  event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {"body": json.dumps(body)}
            },
        },
    }

def lambda_handler(event, context):
    try:
        question     = _extract_param(event, "question")
        sparql       = _extract_param(event, "sparql")
        result_count = _extract_param(event, "result_count")
        was_refined  = _extract_param(event, "was_refined")

        if not isinstance(event.get("requestBody"), dict) and not question:
            body = (json.loads(event.get("body", "{}"))
                    if isinstance(event.get("body"), str) else event)
            question     = body.get("question", "")
            sparql       = body.get("sparql", "")
            result_count = body.get("result_count", 0)
            was_refined  = body.get("was_refined", False)

        question = (question or "").strip()
        if not question or not sparql:
            return _agent_response(event, {"error": "Missing 'question' or 'sparql' field"}, 400)

        result_count = int(result_count or 0)
        was_refined  = str(was_refined).lower() in ("true", "1", "yes") if not isinstance(was_refined, bool) else was_refined

        if result_count <= 0:
            return _agent_response(event, {"stored": False, "reason": "result_count is 0, not worth memorizing"}, 200)

        query_id  = hashlib.sha256(question.lower().strip().encode()).hexdigest()[:16]
        timestamp = datetime.now(timezone.utc).isoformat()
        embedding = embed(question)

        dynamodb.put_item(TableName=TABLE_NAME, Item={
            "query_id":      {"S": query_id},
            "timestamp":     {"S": timestamp},
            "question":      {"S": question},
            "sparql":        {"S": sparql},
            "result_count":  {"N": str(result_count)},
            "was_refined":   {"BOOL": was_refined},
            "rating":        {"NULL": True},
            "model_version": {"S": MODEL_VERSION},
            "os_doc_id":     {"S": query_id},
        })

        os_index(query_id, {
            "query_id":     query_id,
            "question":     question,
            "embedding":    embedding,
            "result_count": result_count,
            "rating":       None,
            "sparql":       sparql,
        })

        return _agent_response(event, {
            "stored":   True,
            "query_id": query_id,
        }, 200)
    except Exception as e:
        # A memory-write failure must never fail the agent's user-facing answer.
        # Return 200 with stored=false so Bedrock treats this as a completed
        # (if unsuccessful) action rather than a dependency failure that
        # aborts the whole invocation.
        return _agent_response(event, {"stored": False, "error": str(e)}, 200)
