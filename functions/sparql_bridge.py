"""
vbc-sparql-bridge Lambda  (NOT in VPC)
Translates natural language → SPARQL via Bedrock Claude,
then executes via vbc-sparql-relay Lambda (which is in VPC with Neptune access).

Lambda-to-Lambda invoke requires lambda:InvokeFunction on the execution role.
Falls back to template SPARQL if Bedrock is unavailable.
"""
import json, os, boto3

REGION         = os.environ.get("AWS_REGION", "ap-southeast-2")
BEDROCK_MODEL  = os.environ.get("BEDROCK_MODEL", "amazon.nova-pro-v1:0")
RELAY_FUNCTION = os.environ.get("SPARQL_RELAY_FUNCTION", "vbc-sparql-relay")

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
lambda_ = boto3.client("lambda",          region_name=REGION)

PROMPT_TEMPLATE = open("bridge_prompt.txt").read()

GRAPH = "<https://ontology.vbc.internal/vbc/instances>"
NS    = "PREFIX vbc: <https://ontology.vbc.internal/vbc#>"

TEMPLATES = {
    "high_risk_gaps": f"""\
{NS}
SELECT DISTINCT ?patient ?riskScore ?gap
WHERE {{
  GRAPH {GRAPH} {{
    ?patient a vbc:Patient ;
             vbc:hasRiskScore ?rs ;
             vbc:hasCareGap   ?gap ;
             vbc:hasPCP       ?pcp .
    ?rs  vbc:riskScoreValue ?riskScore .
    ?gap a vbc:OpenCareGap .
    FILTER (?riskScore > 0.75)
  }}
}}
ORDER BY DESC(?riskScore)
LIMIT 20""",

    "high_risk": f"""\
{NS}
SELECT ?patient ?riskTier ?riskScore
WHERE {{
  GRAPH {GRAPH} {{
    ?patient a vbc:Patient ; vbc:hasRiskScore ?rs .
    ?rs vbc:riskScoreValue ?riskScore ; vbc:riskTier ?riskTier .
    FILTER (?riskScore > 0.75)
  }}
}}
ORDER BY DESC(?riskScore)
LIMIT 10""",

    "sdoh": f"""\
{NS}
SELECT ?barrierType (COUNT(?patient) AS ?patientCount)
WHERE {{
  GRAPH {GRAPH} {{
    ?patient a vbc:Patient ; vbc:hasSDOHBarrier ?barrier .
    ?barrier vbc:barrierType ?barrierType .
  }}
}}
GROUP BY ?barrierType
ORDER BY DESC(?patientCount)""",

    "attribution": f"""\
{NS}
SELECT ?patient ?pcp
WHERE {{
  GRAPH {GRAPH} {{
    ?patient a vbc:Patient ; vbc:hasPCP ?pcp .
  }}
}}
LIMIT 50""",

    "housing": f"""\
{NS}
SELECT DISTINCT ?patient ?barrierType ?riskScore
WHERE {{
  GRAPH {GRAPH} {{
    ?patient a vbc:Patient ;
             vbc:hasSDOHBarrier ?barrier ;
             vbc:hasRiskScore ?rs .
    ?barrier vbc:barrierType ?barrierType .
    ?rs vbc:riskScoreValue ?riskScore .
  }}
}}
ORDER BY DESC(?riskScore)
LIMIT 20""",
}

KEYWORD_MAP = [
    (["chf", "congestive", "heart failure", "diabetes", "open gap", "attributed"], "high_risk_gaps"),
    (["sdoh", "barrier", "ed ", "emergency", "utilization", "correlated"], "sdoh"),
    (["housing", "hypertension", "instability", "care pathway"], "housing"),
    (["attribution", "pcp", "network", "contract"], "attribution"),
    (["high risk", "top 10", "highest", "risk score"], "high_risk"),
]

def template_sparql(question: str) -> str:
    q = question.lower()
    for keywords, key in KEYWORD_MAP:
        if any(kw in q for kw in keywords):
            return TEMPLATES[key]
    return TEMPLATES["high_risk"]

def generate_sparql(question: str) -> tuple:
    try:
        resp = bedrock.converse(
            modelId=BEDROCK_MODEL,
            messages=[{"role": "user", "content": [{"text": PROMPT_TEMPLATE.replace("{question}", question)}]}],
            inferenceConfig={"maxTokens": 1024, "temperature": 0},
        )
        sparql = resp["output"]["message"]["content"][0]["text"].strip()
        if sparql.startswith("```"):
            sparql = "\n".join(l for l in sparql.splitlines() if not l.startswith("```")).strip()
        return sparql, "bedrock-nova"
    except Exception as e:
        return template_sparql(question), f"template (bedrock: {str(e)[:200]})"

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
            "actionGroup": event.get("actionGroup", "sparql_query"),
            "apiPath":     event.get("apiPath", "/sparql"),
            "httpMethod":  event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {"body": json.dumps(body)}
            },
        },
    }

def lambda_handler(event, context):
    try:
        # Support Bedrock Agent event format and direct invoke
        question = _extract_param(event, "question")
        if not question:
            # Fallback: API Gateway body
            body = (json.loads(event.get("body", "{}"))
                    if isinstance(event.get("body"), str) else event)
            question = body.get("question", "").strip()

        if not question:
            return _agent_response(event, {"error": "Missing 'question' field"}, 400)

        sparql, source = generate_sparql(question)
        results        = execute_sparql(sparql)
        bindings       = results.get("results", {}).get("bindings", [])

        return _agent_response(event, {
            "question":         question,
            "sparql_source":    source,
            "generated_sparql": sparql,
            "results":          results,
            "binding_count":    len(bindings),
        }, 200)
    except Exception as e:
        return _agent_response(event, {"error": str(e)}, 500)
