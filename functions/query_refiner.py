"""
vbc-query-refiner Lambda  (NOT in VPC)
Fixes a failed SPARQL query and retries via vbc-sparql-relay.
Called by the Bedrock Agent's refine_query action group when sparql_query
returns 0 results or an error, up to 3 attempts.
"""
import json, os, boto3

REGION         = os.environ.get("AWS_REGION", "ap-southeast-2")
BEDROCK_MODEL  = os.environ.get("BEDROCK_MODEL", "amazon.nova-pro-v1:0")
RELAY_FUNCTION = os.environ.get("SPARQL_RELAY_FUNCTION", "vbc-sparql-relay")

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
lambda_ = boto3.client("lambda",          region_name=REGION)

REFINEMENT_PROMPT = """\
You are a SPARQL expert fixing a failed query for the VBC knowledge graph.

Ontology namespace: https://ontology.vbc.internal/vbc#
Named graph (instances): <https://ontology.vbc.internal/vbc/instances>

ORIGINAL QUESTION: {question}

FAILED SPARQL:
{failed_sparql}

FAILURE REASON: {failure_reason} (attempt {attempt_number} of 3)

Common fixes:
- Always wrap instance data in: GRAPH <https://ontology.vbc.internal/vbc/instances> { }
- Risk scores use: vbc:riskScoreValue (not vbc:score or vbc:riskScore)
- Care gaps join via: ?patient vbc:hasCareGap ?gap . ?gap a vbc:OpenCareGap
- All IRIs need PREFIX vbc: <https://ontology.vbc.internal/vbc#>
- Never use FROM — use GRAPH { } inside WHERE instead

Write a corrected SPARQL SELECT query. Return only the SPARQL, nothing else.
"""

def refine_sparql(question: str, failed_sparql: str, failure_reason: str, attempt_number: int) -> str:
    prompt = (REFINEMENT_PROMPT
              .replace("{question}", question)
              .replace("{failed_sparql}", failed_sparql)
              .replace("{failure_reason}", failure_reason)
              .replace("{attempt_number}", str(attempt_number)))
    resp = bedrock.converse(
        modelId=BEDROCK_MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0},
    )
    sparql = resp["output"]["message"]["content"][0]["text"].strip()
    if sparql.startswith("```"):
        sparql = "\n".join(l for l in sparql.splitlines() if not l.startswith("```")).strip()
    return sparql

def execute_sparql(sparql: str) -> dict:
    resp = lambda_.invoke(
        FunctionName=RELAY_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"sparql": sparql, "type": "query"}).encode(),
    )
    result = json.loads(resp["Payload"].read())
    if "errorMessage" in result:
        raise RuntimeError(f"Relay error: {result['errorMessage']}")
    body = result.get("body", "{}")
    return json.loads(body) if isinstance(body, str) else body

def _extract_param(event, param_name):
    """Extract a named parameter from Bedrock Agent or direct invoke event."""
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
            "actionGroup": event.get("actionGroup", "refine_query"),
            "apiPath":     event.get("apiPath", "/refine"),
            "httpMethod":  event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {"body": json.dumps(body)}
            },
        },
    }

def lambda_handler(event, context):
    try:
        question        = _extract_param(event, "question")
        failed_sparql   = _extract_param(event, "failed_sparql")
        failure_reason  = _extract_param(event, "failure_reason")
        attempt_number  = int(_extract_param(event, "attempt_number") or 1)

        if not isinstance(event.get("requestBody"), dict) and not question:
            body = (json.loads(event.get("body", "{}"))
                    if isinstance(event.get("body"), str) else event)
            question       = body.get("question", "").strip()
            failed_sparql  = body.get("failed_sparql", "")
            failure_reason = body.get("failure_reason", "")
            attempt_number = int(body.get("attempt_number", 1))

        if not question:
            return _agent_response(event, {"error": "Missing 'question' field"}, 400)

        if attempt_number > 3:
            return _agent_response(event, {
                "error": "Max refinement attempts (3) exceeded",
                "results": {}, "corrected_sparql": failed_sparql, "result_count": 0,
            }, 200)

        corrected_sparql = refine_sparql(question, failed_sparql, failure_reason, attempt_number)
        results          = execute_sparql(corrected_sparql)
        bindings         = results.get("results", {}).get("bindings", [])

        return _agent_response(event, {
            "question":         question,
            "attempt_number":   attempt_number,
            "corrected_sparql": corrected_sparql,
            "results":          results,
            "result_count":     len(bindings),
        }, 200)
    except Exception as e:
        return _agent_response(event, {"error": str(e)}, 500)
