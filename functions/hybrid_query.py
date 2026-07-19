"""
vbc-hybrid-query Lambda  (NOT in VPC)
Fans out to:
  1. vbc-sparql-bridge Lambda → Neptune graph (NL→SPARQL→results)
  2. OpenSearch KNN          → semantic vector search
Merges results.
"""
import json, os, base64, urllib.request, urllib.parse, boto3

REGION          = os.environ.get("AWS_REGION", "ap-southeast-2")
BRIDGE_FUNCTION = os.environ.get("SPARQL_BRIDGE_FUNCTION", "vbc-sparql-bridge")
OS_ENDPOINT     = os.environ.get("OPENSEARCH_ENDPOINT")
OS_USER         = os.environ.get("OPENSEARCH_USER", "vbcadmin")
OS_PASS         = os.environ.get("OPENSEARCH_PASS", "VbcPoc2024!")
OS_INDEX        = os.environ.get("OPENSEARCH_INDEX", "vbc-concepts-embeddings")
EMBED_MODEL     = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")

lambda_ = boto3.client("lambda",          region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

def embed(text: str) -> list:
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": 1024}),
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]

def knn_search(query: str, k: int = 10) -> list:
    try:
        vector = embed(query)
        payload = json.dumps({
            "size": k,
            "query": {"knn": {"embedding": {"vector": vector, "k": k}}},
            "_source": ["class_id", "label", "domain", "definition"],
        }).encode()
        creds = base64.b64encode(f"{OS_USER}:{OS_PASS}".encode()).decode()
        req = urllib.request.Request(
            f"https://{OS_ENDPOINT}/{OS_INDEX}/_search",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Basic {creds}"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            hits = json.loads(r.read())["hits"]["hits"]
            return [{"score": h["_score"], **h["_source"]} for h in hits]
    except Exception as e:
        return [{"error": str(e)[:200]}]

def call_sparql_bridge(question: str) -> dict:
    resp = lambda_.invoke(
        FunctionName=BRIDGE_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"question": question}).encode(),
    )
    result = json.loads(resp["Payload"].read())
    body = result.get("body", "{}")
    return json.loads(body) if isinstance(body, str) else body

def merge(semantic: list, bindings: list) -> list:
    seen, merged = set(), []
    for b in bindings:
        pid = b.get("patient", {}).get("value", "")
        if pid and pid not in seen:
            seen.add(pid)
            merged.append({"source": "graph", "id": pid.split("#")[-1],
                           "riskScore": b.get("riskScore", {}).get("value", "")})
    for s in semantic:
        label = s.get("label", "")
        if label and label not in seen:
            seen.add(label)
            merged.append({"source": "semantic", "concept": label,
                           "score": round(s.get("score", 0), 3)})
    return merged[:25]

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
            "actionGroup": event.get("actionGroup", "semantic_search"),
            "apiPath":     event.get("apiPath", "/query"),
            "httpMethod":  event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {"body": json.dumps(body)}
            },
        },
    }

def lambda_handler(event, context):
    try:
        query = _extract_param(event, "query")
        if not query:
            body = (json.loads(event.get("body", "{}"))
                    if isinstance(event.get("body"), str) else event)
            query = body.get("query", "").strip()

        if not query:
            return _agent_response(event, {"error": "Missing 'query' field"}, 400)

        semantic   = knn_search(query)
        graph_resp = call_sparql_bridge(query)
        bindings   = graph_resp.get("results", {}).get("results", {}).get("bindings", [])

        return _agent_response(event, {
            "query":            query,
            "generated_sparql": graph_resp.get("generated_sparql", ""),
            "sparql_source":    graph_resp.get("sparql_source", ""),
            "semantic_matches": semantic,
            "graph_facts":      bindings,
            "merged_patients":  merge(semantic, bindings),
        }, 200)
    except Exception as e:
        return _agent_response(event, {"error": str(e)}, 500)
