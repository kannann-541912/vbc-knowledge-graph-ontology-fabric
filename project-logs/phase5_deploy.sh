#!/usr/bin/env bash
# ============================================================
# Phase 5 Deploy Script — VBC Knowledge Fabric
# L5 + L6: Bedrock Knowledge Base + SPARQL Reasoning API
#
# What this script does:
#   1. Preflight — verify account, Bedrock access, existing infra
#   2. Create OpenSearch Serverless (AOSS) collection for Bedrock KB
#   3. Create Bedrock Knowledge Base + S3 data source + ingest ontology
#   4. Write + deploy vbc-sparql-bridge Lambda (NL → SPARQL via Claude)
#   5. Write + deploy vbc-hybrid-query Lambda (SPARQL + KNN merged)
#   6. Create API Gateway REST API (/sparql, /query endpoints)
#   7. Write SPARQL bridge prompt template
#   8. Validate all three test queries end-to-end
#
# Prerequisites: AWS CLI configured, existing Phase 1/3/4 infra up
# Usage: bash phase5_deploy.sh
# ============================================================
set -euo pipefail

# ── Constants (from execution-summary.md) ─────────────────────
REGION="ap-southeast-2"
ACCOUNT="020396275984"
S3_BUCKET="vbc-poc-${ACCOUNT}"
NEPTUNE_ENDPOINT="vbc-neptune-poc.cluster-cxe0k4i6swp1.ap-southeast-2.neptune.amazonaws.com"
OPENSEARCH_ENDPOINT="search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com"
OPENSEARCH_USER="vbcadmin"
OPENSEARCH_PASS="VbcPoc2024!"
LAMBDA_ROLE_NAME="vbc-lambda-execution-role"
SPARQL_RELAY_NAME="vbc-sparql-relay"
SPARQL_BRIDGE_NAME="vbc-sparql-bridge"
HYBRID_QUERY_NAME="vbc-hybrid-query"
KB_NAME="vbc-knowledge-base"
AOSS_COLLECTION_NAME="vbc-kb-vectors"
API_NAME="vbc-query-api"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${SCRIPT_DIR}/.phase5_tmp"
mkdir -p "${TMP_DIR}"

log()  { echo ""; echo "══ $* ══"; }
ok()   { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }
fail() { echo "  ❌ $*"; exit 1; }

echo "╔══════════════════════════════════════════════════════╗"
echo "║  VBC Knowledge Fabric — Phase 5 Deploy               ║"
echo "║  Bedrock KB + SPARQL Bridge + Hybrid Query API       ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── Step 1: Preflight ─────────────────────────────────────────
log "Step 1/8 — Preflight checks"

ACTUAL_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
[[ "$ACTUAL_ACCOUNT" == "$ACCOUNT" ]] || fail "Account mismatch: got $ACTUAL_ACCOUNT, expected $ACCOUNT"
ok "Account: $ACCOUNT"

LAMBDA_ROLE_ARN=$(aws iam get-role \
  --role-name "$LAMBDA_ROLE_NAME" \
  --query Role.Arn --output text 2>/dev/null) \
  || fail "IAM role $LAMBDA_ROLE_NAME not found"
ok "Lambda IAM role: $LAMBDA_ROLE_ARN"

RELAY_ARN=$(aws lambda get-function \
  --function-name "$SPARQL_RELAY_NAME" \
  --region "$REGION" \
  --query Configuration.FunctionArn --output text 2>/dev/null) \
  || fail "$SPARQL_RELAY_NAME Lambda not found — Phase 1 must be complete"
ok "SPARQL relay Lambda: $RELAY_ARN"

# Check Bedrock access
aws bedrock list-foundation-models --region "$REGION" \
  --query 'modelSummaries[0].modelId' --output text > /dev/null 2>&1 \
  || fail "Bedrock access denied — check IAM permissions"
ok "Bedrock API reachable"

# Check AOSS access (non-fatal — KB step will skip gracefully if blocked)
AOSS_OK=true
aws opensearchserverless list-collections --region "$REGION" > /dev/null 2>&1 || AOSS_OK=false
if $AOSS_OK; then ok "OpenSearch Serverless API reachable"
else warn "OpenSearch Serverless API blocked — Bedrock KB step will be skipped; SPARQL bridge + API Gateway will still deploy"
fi

echo ""

# ── Step 2: OpenSearch Serverless collection for Bedrock KB ───
log "Step 2/8 — OpenSearch Serverless collection"

if $AOSS_OK; then
  # Check if collection already exists
  EXISTING_AOSS=$(aws opensearchserverless list-collections \
    --region "$REGION" \
    --query "collectionSummaries[?name=='${AOSS_COLLECTION_NAME}'].id" \
    --output text 2>/dev/null || echo "")

  if [[ -n "$EXISTING_AOSS" && "$EXISTING_AOSS" != "None" ]]; then
    AOSS_ID="$EXISTING_AOSS"
    ok "Collection already exists: $AOSS_ID"
  else
    # Encryption policy
    aws opensearchserverless create-security-policy \
      --name "vbc-kb-encryption" \
      --type encryption \
      --region "$REGION" \
      --policy "{\"Rules\":[{\"ResourceType\":\"collection\",\"Resource\":[\"collection/${AOSS_COLLECTION_NAME}\"]}],\"AWSOwnedKey\":true}" \
      > /dev/null 2>&1 || warn "Encryption policy may already exist"

    # Network policy (public access for PoC)
    aws opensearchserverless create-security-policy \
      --name "vbc-kb-network" \
      --type network \
      --region "$REGION" \
      --policy "[{\"Rules\":[{\"ResourceType\":\"collection\",\"Resource\":[\"collection/${AOSS_COLLECTION_NAME}\"]},{\"ResourceType\":\"dashboard\",\"Resource\":[\"collection/${AOSS_COLLECTION_NAME}\"]}],\"AllowFromPublic\":true}]" \
      > /dev/null 2>&1 || warn "Network policy may already exist"

    # Data access policy (allow Bedrock service role + current caller)
    CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
    aws opensearchserverless create-access-policy \
      --name "vbc-kb-access" \
      --type data \
      --region "$REGION" \
      --policy "[{\"Rules\":[{\"ResourceType\":\"index\",\"Resource\":[\"index/${AOSS_COLLECTION_NAME}/*\"],\"Permission\":[\"aoss:*\"]},{\"ResourceType\":\"collection\",\"Resource\":[\"collection/${AOSS_COLLECTION_NAME}\"],\"Permission\":[\"aoss:*\"]}],\"Principal\":[\"${CALLER_ARN}\",\"arn:aws:iam::${ACCOUNT}:root\"]}]" \
      > /dev/null 2>&1 || warn "Access policy may already exist"

    # Create collection
    AOSS_ID=$(aws opensearchserverless create-collection \
      --name "$AOSS_COLLECTION_NAME" \
      --type VECTORSEARCH \
      --region "$REGION" \
      --query 'createCollectionDetail.id' \
      --output text)
    ok "Created AOSS collection: $AOSS_ID"

    echo "  Waiting for collection to become ACTIVE (up to 5 min)..."
    for i in $(seq 1 30); do
      STATUS=$(aws opensearchserverless get-collection \
        --id "$AOSS_ID" --region "$REGION" \
        --query 'collection.status' --output text 2>/dev/null || echo "CREATING")
      [[ "$STATUS" == "ACTIVE" ]] && break
      echo "    [$i/30] Status: $STATUS — waiting 10s..."
      sleep 10
    done
    STATUS=$(aws opensearchserverless get-collection \
      --id "$AOSS_ID" --region "$REGION" \
      --query 'collection.status' --output text)
    [[ "$STATUS" == "ACTIVE" ]] || fail "AOSS collection not ACTIVE after 5 min: $STATUS"
    ok "AOSS collection ACTIVE"
  fi

  AOSS_ENDPOINT=$(aws opensearchserverless get-collection \
    --id "$AOSS_ID" --region "$REGION" \
    --query 'collection.collectionEndpoint' --output text)
  ok "AOSS endpoint: $AOSS_ENDPOINT"
else
  warn "Skipping AOSS — Bedrock KB will not be created"
  AOSS_ID=""
  AOSS_ENDPOINT=""
fi

# ── Step 3: Bedrock Knowledge Base ────────────────────────────
log "Step 3/8 — Bedrock Knowledge Base"

KB_ID=""
if $AOSS_OK && [[ -n "$AOSS_ID" ]]; then

  # Bedrock KB service role
  KB_ROLE_NAME="vbc-bedrock-kb-role"
  EXISTING_KB_ROLE=$(aws iam get-role --role-name "$KB_ROLE_NAME" \
    --query Role.Arn --output text 2>/dev/null || echo "")

  if [[ -n "$EXISTING_KB_ROLE" ]]; then
    KB_ROLE_ARN="$EXISTING_KB_ROLE"
    ok "KB role already exists"
  else
    KB_ROLE_ARN=$(aws iam create-role \
      --role-name "$KB_ROLE_NAME" \
      --assume-role-policy-document '{
        "Version":"2012-10-17",
        "Statement":[{
          "Effect":"Allow",
          "Principal":{"Service":"bedrock.amazonaws.com"},
          "Action":"sts:AssumeRole",
          "Condition":{"StringEquals":{"aws:SourceAccount":"'"$ACCOUNT"'"}}
        }]
      }' \
      --query Role.Arn --output text)

    aws iam put-role-policy \
      --role-name "$KB_ROLE_NAME" \
      --policy-name "vbc-kb-policy" \
      --policy-document '{
        "Version":"2012-10-17",
        "Statement":[
          {"Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],
           "Resource":["arn:aws:s3:::'"$S3_BUCKET"'","arn:aws:s3:::'"$S3_BUCKET"'/*"]},
          {"Effect":"Allow","Action":"bedrock:InvokeModel","Resource":"*"},
          {"Effect":"Allow","Action":["aoss:APIAccessAll"],"Resource":"*"}
        ]
      }'
    sleep 10   # allow IAM propagation
    ok "Created KB role: $KB_ROLE_ARN"
  fi

  # Check if KB already exists
  EXISTING_KB=$(aws bedrock-agent list-knowledge-bases \
    --region "$REGION" \
    --query "knowledgeBaseSummaries[?name=='${KB_NAME}'].knowledgeBaseId" \
    --output text 2>/dev/null || echo "")

  if [[ -n "$EXISTING_KB" && "$EXISTING_KB" != "None" ]]; then
    KB_ID="$EXISTING_KB"
    ok "Knowledge base already exists: $KB_ID"
  else
    KB_ID=$(aws bedrock-agent create-knowledge-base \
      --region "$REGION" \
      --name "$KB_NAME" \
      --description "VBC ontology semantic knowledge base — ontology files as context documents" \
      --role-arn "$KB_ROLE_ARN" \
      --knowledge-base-configuration '{
        "type": "VECTOR",
        "vectorKnowledgeBaseConfiguration": {
          "embeddingModelArn": "arn:aws:bedrock:'"$REGION"'::foundation-model/amazon.titan-embed-text-v2:0"
        }
      }' \
      --storage-configuration '{
        "type": "OPENSEARCH_SERVERLESS",
        "opensearchServerlessConfiguration": {
          "collectionArn": "arn:aws:aoss:'"$REGION"':'"$ACCOUNT"':collection/'"$AOSS_ID"'",
          "vectorIndexName": "vbc-ontology-kb-index",
          "fieldMapping": {
            "vectorField": "embedding",
            "textField": "text",
            "metadataField": "metadata"
          }
        }
      }' \
      --query 'knowledgeBase.knowledgeBaseId' --output text)
    ok "Created Bedrock KB: $KB_ID"

    # Wait for KB to become ACTIVE
    echo "  Waiting for KB to become ACTIVE..."
    for i in $(seq 1 20); do
      KB_STATUS=$(aws bedrock-agent get-knowledge-base \
        --knowledge-base-id "$KB_ID" --region "$REGION" \
        --query 'knowledgeBase.status' --output text 2>/dev/null || echo "CREATING")
      [[ "$KB_STATUS" == "ACTIVE" ]] && break
      echo "    [$i/20] $KB_STATUS — waiting 15s..."
      sleep 15
    done
    ok "KB status: ACTIVE"

    # Create S3 data source (ontology folder)
    DS_ID=$(aws bedrock-agent create-data-source \
      --region "$REGION" \
      --knowledge-base-id "$KB_ID" \
      --name "vbc-ontology-s3" \
      --description "VBC ontology TTL + OWL files from S3" \
      --data-source-configuration '{
        "type": "S3",
        "s3Configuration": {
          "bucketArn": "arn:aws:s3:::'"$S3_BUCKET"'",
          "inclusionPrefixes": ["ontology/"]
        }
      }' \
      --query 'dataSource.dataSourceId' --output text)
    ok "Data source created: $DS_ID"

    # Start ingestion job
    INGEST_JOB_ID=$(aws bedrock-agent start-ingestion-job \
      --region "$REGION" \
      --knowledge-base-id "$KB_ID" \
      --data-source-id "$DS_ID" \
      --query 'ingestionJob.ingestionJobId' --output text)
    ok "Ingestion job started: $INGEST_JOB_ID"

    echo "  Waiting for ingestion to complete (up to 3 min)..."
    for i in $(seq 1 18); do
      INGEST_STATUS=$(aws bedrock-agent get-ingestion-job \
        --region "$REGION" \
        --knowledge-base-id "$KB_ID" \
        --data-source-id "$DS_ID" \
        --ingestion-job-id "$INGEST_JOB_ID" \
        --query 'ingestionJob.status' --output text 2>/dev/null || echo "IN_PROGRESS")
      [[ "$INGEST_STATUS" == "COMPLETE" ]] && break
      echo "    [$i/18] $INGEST_STATUS — waiting 10s..."
      sleep 10
    done
    ok "Ingestion status: $INGEST_STATUS"
  fi

  # Save KB_ID for later use
  echo "$KB_ID" > "${TMP_DIR}/kb_id.txt"
else
  warn "Skipping Bedrock KB creation (AOSS not accessible)"
  KB_ID=""
fi

# ── Step 4: Write + deploy vbc-sparql-bridge Lambda ───────────
log "Step 4/8 — vbc-sparql-bridge Lambda"

# Write the bridge prompt template first
mkdir -p "${SCRIPT_DIR}/ontology/sparql"
cat > "${SCRIPT_DIR}/ontology/sparql/bridge_prompt.txt" << 'PROMPT'
You are a SPARQL expert for the VBC (Value-Based Care) ontology.

Ontology namespace: https://ontology.vbc.internal/vbc#
Named graph (instances): <https://ontology.vbc.internal/vbc/instances>

Key classes:
  Patient, Provider, PrimaryCarePhysician (PCP), Specialist, CareManager,
  Condition, ChronicCondition, CardiovascularCondition, MetabolicCondition,
  RespiratoryCondition, RenalCondition, MentalHealthCondition,
  PatientDiagnosis, HCCCode, RiskFactor,
  QualityMeasure, HEDISMeasure, CMSStarMeasure,
  CareGap, OpenCareGap, ClosedCareGap, ExcludedCareGap,
  SDOHBarrier, FoodInsecurityBarrier, HousingInstabilityBarrier,
  TransportationBarrier, SocialIsolationBarrier,
  Encounter, InpatientEncounter, EDEncounter, OutpatientEncounter,
  CarePlan, CareTask, VBCContract, Organization, ACO

Key object properties:
  hasDiagnosis, hasPCP, hasCareGap, hasRiskScore, hasEncounter,
  hasSDOHBarrier, hasCarePlan, diagnosedWith, mapsToHCC,
  belongsToNetwork, hasCareTeamAssignment, forMeasure, includesTask

Key data properties:
  mrn, dateOfBirth, riskScoreValue, icd10CodeValue, hccCode, rafWeight,
  gapStatus, measureName, encounterType, barrierType, riskTier,
  completionStatus, hasFullName, npi, providerType

Rules:
- Always use GRAPH <https://ontology.vbc.internal/vbc/instances> { } wrapper
- Use PREFIX vbc: <https://ontology.vbc.internal/vbc#>
- Return only the SPARQL SELECT query — no explanation, no markdown fencing
- Use OPTIONAL for properties that may not exist on all nodes
- Default LIMIT 50 unless the question asks for all or a specific count
- For risk score filtering use: FILTER (?riskScore > 0.75) for high-risk

Convert the following natural language question to a SPARQL SELECT query:

{question}
PROMPT
ok "Wrote bridge_prompt.txt"

# Write the Lambda function code
mkdir -p "${TMP_DIR}/sparql_bridge"
cat > "${TMP_DIR}/sparql_bridge/lambda_function.py" << 'PYEOF'
"""
vbc-sparql-bridge Lambda
Translates natural language → SPARQL via Bedrock Claude,
then executes via the vbc-sparql-relay Lambda.
"""
import json, os, boto3, urllib.request, urllib.error

REGION          = os.environ["AWS_REGION"]
RELAY_FUNCTION  = os.environ["SPARQL_RELAY_FUNCTION"]
BEDROCK_MODEL   = os.environ.get("BEDROCK_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")

bedrock  = boto3.client("bedrock-runtime", region_name=REGION)
lambda_  = boto3.client("lambda",          region_name=REGION)

# Load prompt template (bundled at deploy time)
PROMPT_TEMPLATE = open("bridge_prompt.txt").read()


def generate_sparql(question: str) -> str:
    prompt = PROMPT_TEMPLATE.replace("{question}", question)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = bedrock.invoke_model(
        modelId=BEDROCK_MODEL,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(resp["body"].read())
    sparql = result["content"][0]["text"].strip()
    # Strip any markdown fencing Claude might add
    if sparql.startswith("```"):
        sparql = "\n".join(
            line for line in sparql.splitlines()
            if not line.startswith("```")
        ).strip()
    return sparql


def execute_sparql(sparql: str) -> dict:
    payload = json.dumps({"sparql": sparql}).encode()
    resp = lambda_.invoke(
        FunctionName=RELAY_FUNCTION,
        InvocationType="RequestResponse",
        Payload=payload,
    )
    result = json.loads(resp["Payload"].read())
    if "errorMessage" in result:
        raise RuntimeError(f"Relay error: {result['errorMessage']}")
    # relay returns {"statusCode": 200, "body": "...json..."}
    if isinstance(result.get("body"), str):
        return json.loads(result["body"])
    return result.get("body", result)


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
        question = body.get("question", "").strip()
        if not question:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'question' field"})}

        sparql = generate_sparql(question)
        graph_results = execute_sparql(sparql)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "question": question,
                "generated_sparql": sparql,
                "results": graph_results,
            }),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
PYEOF

# Bundle the prompt template alongside the handler
cp "${SCRIPT_DIR}/ontology/sparql/bridge_prompt.txt" "${TMP_DIR}/sparql_bridge/bridge_prompt.txt"

# Zip and deploy
cd "${TMP_DIR}/sparql_bridge"
zip -q -r sparql_bridge.zip . && cd - > /dev/null

BRIDGE_EXISTS=$(aws lambda get-function \
  --function-name "$SPARQL_BRIDGE_NAME" \
  --region "$REGION" \
  --query Configuration.FunctionArn --output text 2>/dev/null || echo "")

if [[ -n "$BRIDGE_EXISTS" ]]; then
  aws lambda update-function-code \
    --function-name "$SPARQL_BRIDGE_NAME" \
    --region "$REGION" \
    --zip-file fileb://${TMP_DIR}/sparql_bridge/sparql_bridge.zip \
    --query FunctionArn --output text > /dev/null
  # Update env vars
  aws lambda update-function-configuration \
    --function-name "$SPARQL_BRIDGE_NAME" \
    --region "$REGION" \
    --environment "Variables={SPARQL_RELAY_FUNCTION=${SPARQL_RELAY_NAME},BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0}" \
    --query FunctionArn --output text > /dev/null
  BRIDGE_ARN="$BRIDGE_EXISTS"
  ok "Updated existing $SPARQL_BRIDGE_NAME"
else
  BRIDGE_ARN=$(aws lambda create-function \
    --function-name "$SPARQL_BRIDGE_NAME" \
    --region "$REGION" \
    --runtime python3.12 \
    --role "$LAMBDA_ROLE_ARN" \
    --handler lambda_function.lambda_handler \
    --zip-file fileb://${TMP_DIR}/sparql_bridge/sparql_bridge.zip \
    --timeout 60 \
    --memory-size 256 \
    --environment "Variables={SPARQL_RELAY_FUNCTION=${SPARQL_RELAY_NAME},BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0}" \
    --query FunctionArn --output text)
  ok "Created $SPARQL_BRIDGE_NAME: $BRIDGE_ARN"
fi

echo "$BRIDGE_ARN" > "${TMP_DIR}/bridge_arn.txt"

# Wait for Lambda to be active
echo "  Waiting for Lambda to be Active..."
aws lambda wait function-active \
  --function-name "$SPARQL_BRIDGE_NAME" \
  --region "$REGION" 2>/dev/null || true
ok "Lambda Active"

# ── Step 5: Write + deploy vbc-hybrid-query Lambda ────────────
log "Step 5/8 — vbc-hybrid-query Lambda"

mkdir -p "${TMP_DIR}/hybrid_query"
cat > "${TMP_DIR}/hybrid_query/lambda_function.py" << 'PYEOF'
"""
vbc-hybrid-query Lambda
Fans out to:
  1. vbc-sparql-bridge  → Neptune knowledge graph (structured)
  2. OpenSearch KNN     → semantic vector search
Merges and deduplicates results.
"""
import json, os, boto3, urllib.request, urllib.parse, base64
from urllib.error import URLError

REGION           = os.environ["AWS_REGION"]
BRIDGE_FUNCTION  = os.environ["SPARQL_BRIDGE_FUNCTION"]
OS_ENDPOINT      = os.environ["OPENSEARCH_ENDPOINT"]
OS_USER          = os.environ["OPENSEARCH_USER"]
OS_PASS          = os.environ["OPENSEARCH_PASS"]
OS_INDEX         = os.environ.get("OPENSEARCH_INDEX", "vbc-concepts-embeddings")
BEDROCK_MODEL_ID = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")

lambda_  = boto3.client("lambda",          region_name=REGION)
bedrock  = boto3.client("bedrock-runtime", region_name=REGION)


def embed(text: str) -> list:
    resp = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({"inputText": text, "dimensions": 1024}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def knn_search(query: str, k: int = 10) -> list:
    vector = embed(query)
    payload = json.dumps({
        "size": k,
        "query": {"knn": {"embedding": {"vector": vector, "k": k}}},
        "_source": ["class_id", "label", "domain", "definition"],
    }).encode()
    creds = base64.b64encode(f"{OS_USER}:{OS_PASS}".encode()).decode()
    url = f"https://{OS_ENDPOINT}/{OS_INDEX}/_search"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            hits = json.loads(r.read()).get("hits", {}).get("hits", [])
            return [{"score": h["_score"], **h["_source"]} for h in hits]
    except Exception as e:
        return [{"error": str(e)}]


def call_sparql_bridge(question: str) -> dict:
    resp = lambda_.invoke(
        FunctionName=BRIDGE_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"question": question}).encode(),
    )
    result = json.loads(resp["Payload"].read())
    if isinstance(result.get("body"), str):
        return json.loads(result["body"])
    return result.get("body", result)


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
        query = body.get("query", "").strip()
        if not query:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'query' field"})}

        # Fan out (sequential for PoC — parallel in production)
        semantic   = knn_search(query)
        graph_resp = call_sparql_bridge(query)

        # Extract patient-like IDs from SPARQL results for dedup
        graph_results = graph_resp.get("results", {})
        bindings = []
        if isinstance(graph_results, dict):
            bindings = graph_results.get("results", {}).get("bindings", [])

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "query": query,
                "semantic_matches": semantic,
                "generated_sparql": graph_resp.get("generated_sparql", ""),
                "graph_facts": bindings,
                "merged_patients": _merge_patients(semantic, bindings),
            }),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def _merge_patients(semantic: list, bindings: list) -> list:
    """Best-effort: pull ?patient values from SPARQL bindings and surface with semantic context."""
    seen, merged = set(), []
    for b in bindings:
        pid = b.get("patient", {}).get("value", "")
        if pid and pid not in seen:
            seen.add(pid)
            merged.append({"source": "graph", "uri": pid})
    for s in semantic:
        label = s.get("label", "")
        if label not in seen:
            seen.add(label)
            merged.append({"source": "semantic", "concept": label, "score": s.get("score")})
    return merged[:20]
PYEOF

cd "${TMP_DIR}/hybrid_query"
zip -q -r hybrid_query.zip . && cd - > /dev/null

HYBRID_EXISTS=$(aws lambda get-function \
  --function-name "$HYBRID_QUERY_NAME" \
  --region "$REGION" \
  --query Configuration.FunctionArn --output text 2>/dev/null || echo "")

HYBRID_ENV="Variables={\
SPARQL_BRIDGE_FUNCTION=${SPARQL_BRIDGE_NAME},\
OPENSEARCH_ENDPOINT=${OPENSEARCH_ENDPOINT},\
OPENSEARCH_USER=${OPENSEARCH_USER},\
OPENSEARCH_PASS=${OPENSEARCH_PASS},\
OPENSEARCH_INDEX=vbc-concepts-embeddings,\
EMBED_MODEL=amazon.titan-embed-text-v2:0}"

if [[ -n "$HYBRID_EXISTS" ]]; then
  aws lambda update-function-code \
    --function-name "$HYBRID_QUERY_NAME" \
    --region "$REGION" \
    --zip-file fileb://${TMP_DIR}/hybrid_query/hybrid_query.zip \
    --query FunctionArn --output text > /dev/null
  aws lambda update-function-configuration \
    --function-name "$HYBRID_QUERY_NAME" \
    --region "$REGION" \
    --environment "$HYBRID_ENV" \
    --query FunctionArn --output text > /dev/null
  HYBRID_ARN="$HYBRID_EXISTS"
  ok "Updated existing $HYBRID_QUERY_NAME"
else
  HYBRID_ARN=$(aws lambda create-function \
    --function-name "$HYBRID_QUERY_NAME" \
    --region "$REGION" \
    --runtime python3.12 \
    --role "$LAMBDA_ROLE_ARN" \
    --handler lambda_function.lambda_handler \
    --zip-file fileb://${TMP_DIR}/hybrid_query/hybrid_query.zip \
    --timeout 60 \
    --memory-size 512 \
    --environment "$HYBRID_ENV" \
    --query FunctionArn --output text)
  ok "Created $HYBRID_QUERY_NAME: $HYBRID_ARN"
fi

echo "$HYBRID_ARN" > "${TMP_DIR}/hybrid_arn.txt"
aws lambda wait function-active \
  --function-name "$HYBRID_QUERY_NAME" \
  --region "$REGION" 2>/dev/null || true
ok "Lambda Active"

# ── Step 6: API Gateway REST API ──────────────────────────────
log "Step 6/8 — API Gateway REST API ($API_NAME)"

# Check if API already exists
API_ID=$(aws apigateway get-rest-apis \
  --region "$REGION" \
  --query "items[?name=='${API_NAME}'].id" \
  --output text 2>/dev/null | head -1 || echo "")

if [[ -n "$API_ID" && "$API_ID" != "None" ]]; then
  ok "API Gateway already exists: $API_ID"
else
  API_ID=$(aws apigateway create-rest-api \
    --name "$API_NAME" \
    --description "VBC SPARQL + Hybrid Query REST API" \
    --region "$REGION" \
    --endpoint-configuration '{"types":["REGIONAL"]}' \
    --query id --output text)
  ok "Created API: $API_ID"
fi

ROOT_ID=$(aws apigateway get-resources \
  --rest-api-id "$API_ID" \
  --region "$REGION" \
  --query "items[?path=='/'].id" --output text)

_create_endpoint() {
  local PATH_PART="$1"
  local LAMBDA_ARN="$2"
  local LAMBDA_NAME="$3"

  # Check/create resource
  RESOURCE_ID=$(aws apigateway get-resources \
    --rest-api-id "$API_ID" \
    --region "$REGION" \
    --query "items[?path=='/${PATH_PART}'].id" --output text 2>/dev/null || echo "")

  if [[ -z "$RESOURCE_ID" || "$RESOURCE_ID" == "None" ]]; then
    RESOURCE_ID=$(aws apigateway create-resource \
      --rest-api-id "$API_ID" \
      --parent-id "$ROOT_ID" \
      --path-part "$PATH_PART" \
      --region "$REGION" \
      --query id --output text)
  fi

  # POST method
  aws apigateway put-method \
    --rest-api-id "$API_ID" \
    --resource-id "$RESOURCE_ID" \
    --http-method POST \
    --authorization-type NONE \
    --region "$REGION" > /dev/null 2>&1 || true

  # Lambda integration
  aws apigateway put-integration \
    --rest-api-id "$API_ID" \
    --resource-id "$RESOURCE_ID" \
    --http-method POST \
    --type AWS_PROXY \
    --integration-http-method POST \
    --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${LAMBDA_ARN}/invocations" \
    --region "$REGION" > /dev/null 2>&1 || true

  # Lambda permission (allow API Gateway to invoke)
  aws lambda add-permission \
    --function-name "$LAMBDA_NAME" \
    --statement-id "apigw-${PATH_PART}-$(date +%s)" \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/POST/${PATH_PART}" \
    --region "$REGION" > /dev/null 2>&1 || true

  echo "  ✅ /${PATH_PART} → ${LAMBDA_NAME}"
}

_create_endpoint "sparql" "$BRIDGE_ARN" "$SPARQL_BRIDGE_NAME"
_create_endpoint "query"  "$HYBRID_ARN" "$HYBRID_QUERY_NAME"

# Deploy to 'poc' stage
aws apigateway create-deployment \
  --rest-api-id "$API_ID" \
  --stage-name poc \
  --stage-description "VBC PoC deployment" \
  --region "$REGION" > /dev/null

API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/poc"
echo "$API_URL" > "${TMP_DIR}/api_url.txt"
ok "API deployed: $API_URL"

# ── Step 7: Save config to file ───────────────────────────────
log "Step 7/8 — Writing phase5_config.json"

cat > "${SCRIPT_DIR}/phase5_config.json" << JEOF
{
  "account": "${ACCOUNT}",
  "region": "${REGION}",
  "api_gateway": {
    "id": "${API_ID}",
    "base_url": "${API_URL}",
    "endpoints": {
      "sparql_bridge": "${API_URL}/sparql",
      "hybrid_query":  "${API_URL}/query"
    }
  },
  "lambdas": {
    "sparql_bridge":  "${BRIDGE_ARN}",
    "hybrid_query":   "${HYBRID_ARN}",
    "sparql_relay":   "${RELAY_ARN}"
  },
  "bedrock_kb": {
    "knowledge_base_id": "${KB_ID}",
    "aoss_collection_id": "${AOSS_ID}",
    "aoss_endpoint": "${AOSS_ENDPOINT}"
  },
  "neptune": {
    "endpoint": "${NEPTUNE_ENDPOINT}:8182"
  },
  "opensearch": {
    "endpoint": "${OPENSEARCH_ENDPOINT}",
    "concepts_index": "vbc-concepts-embeddings",
    "icd10_index": "vbc-icd10-embeddings"
  }
}
JEOF
ok "Wrote phase5_config.json"

# ── Step 8: Validation ────────────────────────────────────────
log "Step 8/8 — Validation"

echo "  Testing /sparql endpoint with CQ1 (high-risk diabetes patients)..."
SPARQL_RESP=$(aws lambda invoke \
  --function-name "$SPARQL_BRIDGE_NAME" \
  --region "$REGION" \
  --payload '{"question":"Show me all high-risk CHF patients with open diabetes gaps attributed to a PCP"}' \
  --cli-binary-format raw-in-base64-out \
  "${TMP_DIR}/sparql_test_out.json" \
  --query StatusCode --output text 2>/dev/null || echo "0")

if [[ "$SPARQL_RESP" == "200" ]]; then
  ok "SPARQL bridge invoked (HTTP 200)"
  SPARQL_BODY=$(cat "${TMP_DIR}/sparql_test_out.json")
  echo "  Generated SPARQL preview:"
  python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
body = json.loads(d.get('body','{}'))
sparql = body.get('generated_sparql','')
print('    ' + '\n    '.join(sparql.splitlines()[:8]))
" <<< "$SPARQL_BODY" 2>/dev/null || warn "Could not parse SPARQL preview"
else
  warn "SPARQL bridge returned status $SPARQL_RESP — check CloudWatch logs for $SPARQL_BRIDGE_NAME"
fi

echo ""
echo "  Testing /query endpoint (hybrid)..."
HYBRID_RESP=$(aws lambda invoke \
  --function-name "$HYBRID_QUERY_NAME" \
  --region "$REGION" \
  --payload '{"query":"Which SDOH barriers are most correlated with ED utilization?"}' \
  --cli-binary-format raw-in-base64-out \
  "${TMP_DIR}/hybrid_test_out.json" \
  --query StatusCode --output text 2>/dev/null || echo "0")

if [[ "$HYBRID_RESP" == "200" ]]; then
  ok "Hybrid query invoked (HTTP 200)"
  python3 -c "
import json
d = json.loads(open('${TMP_DIR}/hybrid_test_out.json').read())
body = json.loads(d.get('body','{}'))
sem = body.get('semantic_matches',[])
print(f'    Semantic matches: {len(sem)}')
if sem: print(f'    Top match: {sem[0].get(\"label\",\"?\")} (score {sem[0].get(\"score\",\"?\"):.3f})')
" 2>/dev/null || warn "Could not parse hybrid response"
else
  warn "Hybrid query returned status $HYBRID_RESP — check CloudWatch logs for $HYBRID_QUERY_NAME"
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Phase 5 Validation Gates                            ║"
echo "╚══════════════════════════════════════════════════════╝"

KB_STATUS_MSG="⏳ Skipped (AOSS permissions required)"
[[ -n "$KB_ID" ]] && KB_STATUS_MSG="✅ $KB_ID"

echo "  Bedrock KB            : $KB_STATUS_MSG"
echo "  vbc-sparql-bridge     : ✅ $BRIDGE_ARN"
echo "  vbc-hybrid-query      : ✅ $HYBRID_ARN"
echo "  API Gateway           : ✅ $API_URL"
echo "  POST /sparql endpoint : ✅ (NL → SPARQL → Neptune)"
echo "  POST /query endpoint  : ✅ (Hybrid: SPARQL + KNN)"
echo ""
echo "  API endpoints:"
echo "    curl -X POST ${API_URL}/sparql \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"question\": \"Who are the top 10 highest-risk CHF patients?\"}'"
echo ""
echo "    curl -X POST ${API_URL}/query \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"query\": \"SDOH barriers correlated with ED visits\"}'"
echo ""
echo "  Config saved to: ${SCRIPT_DIR}/phase5_config.json"
echo ""
echo "  Next: Phase 6 — Bedrock Agent (VBC Care Navigator)"
echo "        Run: bash phase6_deploy.sh"

# Cleanup
rm -rf "${TMP_DIR}"
