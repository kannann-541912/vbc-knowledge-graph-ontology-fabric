"""
vbc-cq-regression Lambda — reruns the 5 ontology competency questions
against Neptune (via vbc-sparql-relay) and asserts minimum result counts.
Called synchronously by vbc-ontology-pipeline after every ontology update.
"""
import json, os
import boto3

REGION       = os.environ.get("AWS_REGION", "ap-southeast-2")
RELAY_FN     = os.environ.get("SPARQL_RELAY_FUNCTION", "vbc-sparql-relay")
GRAPH        = "https://ontology.vbc.internal/vbc/instances"

lambda_ = boto3.client("lambda", region_name=REGION)

CQ_TESTS = [
    ("CQ1_high_risk_diabetes", f"""
        PREFIX vbc: <https://ontology.vbc.internal/vbc#>
        SELECT ?p WHERE {{ GRAPH <{GRAPH}> {{
          ?p a vbc:Patient ; vbc:hasRiskScore ?rs ; vbc:hasCareGap ?g .
          ?rs vbc:riskScoreValue ?v . FILTER(?v > 0.75)
        }} }}""", 0),
    ("CQ2_attribution_chain", f"""
        PREFIX vbc: <https://ontology.vbc.internal/vbc#>
        SELECT ?p ?pcp WHERE {{ GRAPH <{GRAPH}> {{
          ?p a vbc:Patient ; vbc:hasPCP ?pcp
        }} }}""", 0),
    ("CQ3_sdoh_ed_correlation", f"""
        PREFIX vbc: <https://ontology.vbc.internal/vbc#>
        SELECT ?b (COUNT(?e) AS ?n) WHERE {{ GRAPH <{GRAPH}> {{
          ?p vbc:hasSDOHBarrier ?b ; vbc:hasEncounter ?e
        }} }} GROUP BY ?b""", 0),
    ("CQ4_hcc_raf_chain", f"""
        PREFIX vbc: <https://ontology.vbc.internal/vbc#>
        SELECT ?p ?icd ?hcc WHERE {{ GRAPH <{GRAPH}> {{
          ?p vbc:hasDiagnosis ?dx . ?dx vbc:icd10CodeValue ?icd ; vbc:mapsToHCC ?h .
          ?h vbc:hccCode ?hcc
        }} }} LIMIT 5""", 0),
    ("CQ5_careplan_closure", f"""
        PREFIX vbc: <https://ontology.vbc.internal/vbc#>
        SELECT ?p ?gap WHERE {{ GRAPH <{GRAPH}> {{
          ?p vbc:hasCareGap ?gap ; vbc:hasCarePlan ?pl .
          ?gap a vbc:ClosedCareGap
        }} }}""", 0),
]


def run_query(sparql):
    resp = lambda_.invoke(
        FunctionName=RELAY_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps({"type": "query", "sparql": sparql}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    body = payload.get("body", "{}")
    if isinstance(body, str):
        body = json.loads(body)
    bindings = body.get("results", {}).get("bindings", [])
    return len(bindings)


def lambda_handler(event, context):
    results = []
    all_pass = True
    for name, sparql, min_count in CQ_TESTS:
        try:
            n = run_query(sparql)
            passed = n >= min_count
        except Exception as e:
            n, passed = -1, False
            print(f"[cq_regression] {name} errored: {e}")
        if not passed:
            all_pass = False
        results.append({"cq": name, "result_count": n, "min_expected": min_count, "passed": passed})

    return {
        "regression_detected": not all_pass,
        "results": results,
    }
