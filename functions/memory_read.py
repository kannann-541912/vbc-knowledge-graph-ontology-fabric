"""
vbc-memory-read Lambda  (NOT in VPC)
Called as the `retrieve_memory` action group, BEFORE sparql_query.
Embeds the incoming question, KNN-searches the vbc-query-memory OpenSearch
index for the closest past proven query, and returns it as a reference
template if similarity clears the 0.88 threshold. Otherwise tells the agent
to generate SPARQL fresh.
"""
import json, os, boto3, base64, urllib.request

REGION      = os.environ.get("AWS_REGION", "ap-southeast-2")
OS_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT",
                              "search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com")
OS_USER     = os.environ.get("OPENSEARCH_USER", "vbcadmin")
OS_PASS     = os.environ.get("OPENSEARCH_PASS", "VbcPoc2024!")
OS_INDEX    = os.environ.get("OPENSEARCH_MEMORY_INDEX", "vbc-query-memory")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.88"))

bedrock = boto3.client("bedrock-runtime", region_name=REGION)

def embed(text: str) -> list:
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": 1024}),
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]

def knn_search(vector: list, k: int = 3) -> list:
    creds = base64.b64encode(f"{OS_USER}:{OS_PASS}".encode()).decode()
    payload = json.dumps({
        "size": k,
        "query": {
            "function_score": {
                "query": {"knn": {"embedding": {"vector": vector, "k": k}}},
                "functions": [
                    {"field_value_factor": {"field": "rating", "missing": 3.0, "factor": 0.1}}
                ],
                "boost_mode": "sum",
            }
        },
        "_source": ["query_id", "question", "sparql", "result_count", "rating"],
    }).encode()
    req = urllib.request.Request(
        f"https://{OS_ENDPOINT}/{OS_INDEX}/_search",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["hits"]["hits"]

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
            "actionGroup": event.get("actionGroup", "retrieve_memory"),
            "apiPath":     event.get("apiPath", "/memory/read"),
            "httpMethod":  event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {"body": json.dumps(body)}
            },
        },
    }

def lambda_handler(event, context):
    try:
        question = _extract_param(event, "question")
        if not isinstance(event.get("requestBody"), dict) and not question:
            body = (json.loads(event.get("body", "{}"))
                    if isinstance(event.get("body"), str) else event)
            question = body.get("question", "")

        question = (question or "").strip()
        if not question:
            return _agent_response(event, {"error": "Missing 'question' field"}, 400)

        vector = embed(question)
        hits = knn_search(vector)

        if not hits or hits[0]["_score"] < SIMILARITY_THRESHOLD:
            return _agent_response(event, {
                "found": False,
                "reference_sparql": None,
                "similar_question": None,
            }, 200)

        best = hits[0]["_source"]
        return _agent_response(event, {
            "found":             True,
            "similarity":        round(hits[0]["_score"], 3),
            "similar_question":  best["question"],
            "reference_sparql":  best["sparql"],
            "past_result_count": best.get("result_count", 0),
            "guidance": (f"Adapt this proven SPARQL for the new question. "
                         f"Past question had {best.get('result_count', 0)} results."),
        }, 200)
    except Exception as e:
        # A memory-read failure must never block the agent from answering —
        # degrade to "no memory found" so it falls through to fresh SPARQL generation.
        return _agent_response(event, {"found": False, "reference_sparql": None,
                                        "similar_question": None, "error": str(e)[:200]}, 200)
