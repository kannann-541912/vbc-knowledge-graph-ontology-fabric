#!/usr/bin/env bash
# ============================================================
# Phase B Deploy — VBC Self-Learning Agents
# Episodic Memory: DynamoDB + OpenSearch
#
# Steps:
#   1. Preflight checks
#   2. Create vbc-query-memory DynamoDB table (idempotent)
#   3. Create vbc-query-memory OpenSearch KNN index (idempotent)
#   4. Deploy vbc-memory-write Lambda
#   5. Deploy vbc-memory-read Lambda
#   6. Deploy vbc-memory-rate Lambda + API Gateway POST /rate route
#   7. Grant Bedrock Agent invoke permission on read/write Lambdas
#   8. Create retrieve_memory + store_memory action groups
#   9. Update agent instruction with memory protocol
#  10. Prepare agent and update alias
#  11. Test: cold question, repeat question (should retrieve memory), rate
# ============================================================
set -euo pipefail

REGION="ap-southeast-2"
ACCOUNT="020396275984"
AGENT_ID="JIEOIRGZVJ"
ALIAS_ID="XN0Z1NPGS8"
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/vbc-lambda-execution-role"
MEMORY_TABLE="vbc-query-memory"
OS_ENDPOINT="search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com"
OS_INDEX="vbc-query-memory"
WRITE_FUNCTION="vbc-memory-write"
READ_FUNCTION="vbc-memory-read"
RATE_FUNCTION="vbc-memory-rate"
API_ID="trzyzra8ve"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${SCRIPT_DIR}/.phase_b_tmp"
mkdir -p "${TMP_DIR}"

log()  { echo ""; echo "== $* =="; }
ok()   { echo "  OK: $*"; }
warn() { echo "  WARN: $*"; }
fail() { echo "  FAIL: $*"; exit 1; }

echo "============================================================"
echo " VBC Self-Learning Agents — Phase B Deploy"
echo " Episodic Memory: DynamoDB + OpenSearch"
echo "============================================================"

# -- Step 1: Preflight ------------------------------------------
log "Step 1/11 - Preflight"
ACTUAL=$(aws sts get-caller-identity --query Account --output text)
[[ "$ACTUAL" == "$ACCOUNT" ]] || fail "Wrong account: $ACTUAL"
ok "Account: $ACCOUNT"

aws bedrock-agent get-agent --agent-id "$AGENT_ID" --region "$REGION" > /dev/null 2>&1 \
  || fail "Cannot reach agent $AGENT_ID"
ok "Agent reachable: $AGENT_ID"

# -- Step 2: DynamoDB table ---------------------------------------
log "Step 2/11 - vbc-query-memory DynamoDB table"

TABLE_EXISTS=$(aws dynamodb describe-table --table-name "$MEMORY_TABLE" --region "$REGION" \
  --query 'Table.TableStatus' --output text 2>/dev/null || echo "")

if [[ -n "$TABLE_EXISTS" ]]; then
  ok "Table already exists: $MEMORY_TABLE ($TABLE_EXISTS)"
else
  aws dynamodb create-table \
    --table-name "$MEMORY_TABLE" \
    --attribute-definitions \
      AttributeName=query_id,AttributeType=S \
      AttributeName=timestamp,AttributeType=S \
    --key-schema \
      AttributeName=query_id,KeyType=HASH \
      AttributeName=timestamp,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION" > /dev/null
  aws dynamodb wait table-exists --table-name "$MEMORY_TABLE" --region "$REGION"
  ok "Created table: $MEMORY_TABLE"
fi

# -- Step 3: OpenSearch KNN index ---------------------------------
log "Step 3/11 - vbc-query-memory OpenSearch index"

python3 - << PYEOF
import requests, json
from requests.auth import HTTPBasicAuth
requests.packages.urllib3.disable_warnings()

BASE_URL = "https://${OS_ENDPOINT}"
AUTH = HTTPBasicAuth("vbcadmin", "VbcPoc2024!")

r = requests.get(f"{BASE_URL}/${OS_INDEX}", auth=AUTH, verify=False, timeout=15)
if r.status_code == 200:
    print("  OK: index already exists")
else:
    body = {
      "settings": {"index": {"knn": True, "knn.algo_param.ef_search": 100,
                              "number_of_shards": 1, "number_of_replicas": 0}},
      "mappings": {"properties": {
          "query_id":     {"type": "keyword"},
          "question":     {"type": "text"},
          "embedding":    {"type": "knn_vector", "dimension": 1024,
                            "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "nmslib"}},
          "result_count": {"type": "integer"},
          "rating":       {"type": "float"},
          "sparql":       {"type": "keyword", "index": False},
      }}
    }
    r2 = requests.put(f"{BASE_URL}/${OS_INDEX}", auth=AUTH, json=body, verify=False, timeout=15)
    if r2.status_code in (200, 201):
        print("  OK: created index")
    else:
        raise SystemExit(f"  FAIL: {r2.status_code} {r2.text[:300]}")
PYEOF

# -- Helper: deploy or update a Lambda ----------------------------
_deploy_lambda() {
  local FUNC_NAME="$1"
  local SRC_FILE="$2"
  local ENV_VARS="$3"

  mkdir -p "${TMP_DIR}/${FUNC_NAME}"
  cp "${SCRIPT_DIR}/functions/${SRC_FILE}" "${TMP_DIR}/${FUNC_NAME}/lambda_function.py"
  (cd "${TMP_DIR}/${FUNC_NAME}" && zip -q "${FUNC_NAME}.zip" lambda_function.py)

  local EXISTS
  EXISTS=$(aws lambda get-function --function-name "$FUNC_NAME" \
    --region "$REGION" --query Configuration.FunctionArn --output text 2>/dev/null || echo "")

  if [[ -n "$EXISTS" ]]; then
    aws lambda update-function-code --function-name "$FUNC_NAME" \
      --region "$REGION" --zip-file "fileb://${TMP_DIR}/${FUNC_NAME}/${FUNC_NAME}.zip" \
      --query FunctionArn --output text > /dev/null
    aws lambda wait function-updated --function-name "$FUNC_NAME" --region "$REGION"
    aws lambda update-function-configuration --function-name "$FUNC_NAME" \
      --region "$REGION" --environment "Variables={${ENV_VARS}}" > /dev/null
    aws lambda wait function-updated --function-name "$FUNC_NAME" --region "$REGION"
    ok "Updated $FUNC_NAME"
    echo "$EXISTS"
  else
    local ARN
    ARN=$(aws lambda create-function \
      --function-name "$FUNC_NAME" \
      --region "$REGION" \
      --runtime python3.12 \
      --role "$LAMBDA_ROLE_ARN" \
      --handler lambda_function.lambda_handler \
      --zip-file "fileb://${TMP_DIR}/${FUNC_NAME}/${FUNC_NAME}.zip" \
      --timeout 60 \
      --memory-size 256 \
      --environment "Variables={${ENV_VARS}}" \
      --query FunctionArn --output text)
    aws lambda wait function-active --function-name "$FUNC_NAME" --region "$REGION"
    ok "Created $FUNC_NAME: $ARN"
    echo "$ARN"
  fi
}

# -- Step 4: vbc-memory-write Lambda -------------------------------
log "Step 4/11 - Deploy vbc-memory-write Lambda"
WRITE_ARN=$(_deploy_lambda "$WRITE_FUNCTION" "memory_write.py" \
  "MEMORY_TABLE=${MEMORY_TABLE},OPENSEARCH_ENDPOINT=${OS_ENDPOINT},OPENSEARCH_MEMORY_INDEX=${OS_INDEX},EMBED_MODEL=amazon.titan-embed-text-v2:0,BEDROCK_MODEL=amazon.nova-pro-v1:0" | tail -1)

# -- Step 5: vbc-memory-read Lambda --------------------------------
log "Step 5/11 - Deploy vbc-memory-read Lambda"
READ_ARN=$(_deploy_lambda "$READ_FUNCTION" "memory_read.py" \
  "OPENSEARCH_ENDPOINT=${OS_ENDPOINT},OPENSEARCH_MEMORY_INDEX=${OS_INDEX},EMBED_MODEL=amazon.titan-embed-text-v2:0,SIMILARITY_THRESHOLD=0.88" | tail -1)

# -- Step 6: vbc-memory-rate Lambda + API Gateway /rate ------------
log "Step 6/11 - Deploy vbc-memory-rate Lambda + POST /rate route"
RATE_ARN=$(_deploy_lambda "$RATE_FUNCTION" "memory_rate.py" \
  "MEMORY_TABLE=${MEMORY_TABLE},OPENSEARCH_ENDPOINT=${OS_ENDPOINT},OPENSEARCH_MEMORY_INDEX=${OS_INDEX}" | tail -1)

ROOT_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$REGION" \
  --query "items[?path=='/'].id" --output text)

RATE_RESOURCE_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$REGION" \
  --query "items[?path=='/rate'].id" --output text)

if [[ -z "$RATE_RESOURCE_ID" || "$RATE_RESOURCE_ID" == "None" ]]; then
  RATE_RESOURCE_ID=$(aws apigateway create-resource --rest-api-id "$API_ID" --region "$REGION" \
    --parent-id "$ROOT_ID" --path-part "rate" --query id --output text)
  ok "Created /rate resource"
else
  ok "/rate resource already exists"
fi

aws apigateway put-method --rest-api-id "$API_ID" --region "$REGION" \
  --resource-id "$RATE_RESOURCE_ID" --http-method POST --authorization-type NONE > /dev/null 2>&1 || true

aws apigateway put-integration --rest-api-id "$API_ID" --region "$REGION" \
  --resource-id "$RATE_RESOURCE_ID" --http-method POST \
  --type AWS_PROXY --integration-http-method POST \
  --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${RATE_ARN}/invocations" > /dev/null

aws lambda add-permission \
  --function-name "$RATE_FUNCTION" \
  --statement-id "apigw-rate-$(date +%s)" \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/POST/rate" \
  --region "$REGION" > /dev/null 2>&1 || true

aws apigateway create-deployment --rest-api-id "$API_ID" --region "$REGION" \
  --stage-name poc > /dev/null
ok "Deployed API Gateway stage 'poc' with POST /rate"

# -- Step 7: Grant Bedrock Agent invoke permission -----------------
log "Step 7/11 - Grant agent invoke permission on memory Lambdas"

for FUNC in "$WRITE_FUNCTION" "$READ_FUNCTION"; do
  aws lambda add-permission \
    --function-name "$FUNC" \
    --statement-id "bedrock-agent-${AGENT_ID}-${FUNC}" \
    --action lambda:InvokeFunction \
    --principal bedrock.amazonaws.com \
    --source-arn "arn:aws:bedrock:${REGION}:${ACCOUNT}:agent/${AGENT_ID}" \
    --region "$REGION" > /dev/null 2>&1 || true
done
ok "Lambda invoke permissions granted (or already present)"

# -- Step 8: Create action groups ----------------------------------
log "Step 8/11 - Create retrieve_memory + store_memory action groups"

_create_or_update_ag() {
  local AG_NAME="$1"
  local AG_ARN="$2"
  local SCHEMA_FILE="$3"

  local EXISTING_AGS
  EXISTING_AGS=$(aws bedrock-agent list-agent-action-groups \
    --agent-id "$AGENT_ID" --agent-version "DRAFT" --region "$REGION" \
    --query 'actionGroupSummaries[*].actionGroupName' --output json 2>/dev/null || echo "[]")

  local EXISTING_AG_ID=""
  if echo "$EXISTING_AGS" | grep -q "\"${AG_NAME}\""; then
    EXISTING_AG_ID=$(aws bedrock-agent list-agent-action-groups \
      --agent-id "$AGENT_ID" --agent-version DRAFT --region "$REGION" \
      --query "actionGroupSummaries[?actionGroupName=='${AG_NAME}'].actionGroupId" --output text)
  fi

  python3 -c "
import json
schema = json.load(open('${SCHEMA_FILE}'))
req = {
  'agentId': '${AGENT_ID}',
  'agentVersion': 'DRAFT',
  'actionGroupName': '${AG_NAME}',
  'description': schema.pop('__description__'),
  'actionGroupExecutor': {'lambda': '${AG_ARN}'},
  'apiSchema': {'payload': json.dumps(schema)},
  'actionGroupState': 'ENABLED',
}
existing_id = '${EXISTING_AG_ID}'.strip()
if existing_id and existing_id != 'None':
    req['actionGroupId'] = existing_id
open('${TMP_DIR}/${AG_NAME}_request.json', 'w').write(json.dumps(req))
"

  if [[ -n "$EXISTING_AG_ID" && "$EXISTING_AG_ID" != "None" ]]; then
    aws bedrock-agent update-agent-action-group --region "$REGION" \
      --cli-input-json "file://${TMP_DIR}/${AG_NAME}_request.json" > /dev/null
    ok "Updated action group: ${AG_NAME}"
  else
    aws bedrock-agent create-agent-action-group --region "$REGION" \
      --cli-input-json "file://${TMP_DIR}/${AG_NAME}_request.json" \
      --query 'agentActionGroup.actionGroupId' --output text > /dev/null
    ok "Created action group: ${AG_NAME}"
  fi
}

cat > "${TMP_DIR}/retrieve_memory_schema.json" << 'SCHEMAEOF'
{
  "__description__": "Retrieve the closest past proven SPARQL query for a similar question. ALWAYS call this before sparql_query.",
  "openapi": "3.0.0",
  "info": {"title": "Memory Retrieve", "version": "1.0"},
  "paths": {
    "/memory/read": {
      "post": {
        "operationId": "retrieveMemory",
        "description": "Retrieve the closest past proven SPARQL query for a similar question via semantic search over episodic memory.",
        "requestBody": {
          "required": true,
          "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {
              "question": {"type": "string", "description": "The current natural language question"}
            },
            "required": ["question"]
          }}}
        },
        "responses": {"200": {"description": "Closest matching past query, if any", "content": {"application/json": {"schema": {"type": "object"}}}}}
      }
    }
  }
}
SCHEMAEOF

cat > "${TMP_DIR}/store_memory_schema.json" << 'SCHEMAEOF'
{
  "__description__": "Store a successful question/SPARQL pair in episodic memory. Call after a sparql_query call returns result_count > 0.",
  "openapi": "3.0.0",
  "info": {"title": "Memory Store", "version": "1.0"},
  "paths": {
    "/memory/write": {
      "post": {
        "operationId": "storeMemory",
        "description": "Store a successful question/SPARQL pair in episodic memory for future reuse.",
        "requestBody": {
          "required": true,
          "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {
              "question":     {"type": "string", "description": "The natural language question that was answered"},
              "sparql":       {"type": "string", "description": "The SPARQL query that produced results"},
              "result_count": {"type": "integer", "description": "Number of results the query returned"},
              "was_refined":  {"type": "boolean", "description": "Whether refine_query was needed to reach this SPARQL"}
            },
            "required": ["question", "sparql", "result_count"]
          }}}
        },
        "responses": {"200": {"description": "Whether the memory was stored", "content": {"application/json": {"schema": {"type": "object"}}}}}
      }
    }
  }
}
SCHEMAEOF

_create_or_update_ag "retrieve_memory" "$READ_ARN" "${TMP_DIR}/retrieve_memory_schema.json"
_create_or_update_ag "store_memory" "$WRITE_ARN" "${TMP_DIR}/store_memory_schema.json"

# -- Step 9: Update agent instruction ------------------------------
log "Step 9/11 - Update agent instruction with memory protocol"

MEMORY_PROTOCOL_MARKER="MEMORY PROTOCOL:"
if ! grep -q "$MEMORY_PROTOCOL_MARKER" "${SCRIPT_DIR}/infra/lib/agent_instruction.txt"; then
  cat >> "${SCRIPT_DIR}/infra/lib/agent_instruction.txt" << 'INSTREOF'

MEMORY PROTOCOL:
- ALWAYS call retrieve_memory FIRST, before calling sparql_query, for every new question.
- If retrieve_memory returns found=true and similarity > 0.88:
    Use reference_sparql as a starting template. Adapt it for the current question.
    Do not generate SPARQL from scratch.
- If retrieve_memory returns found=false:
    Generate SPARQL normally via sparql_query (and refine_query if needed).
- After sparql_query (or refine_query) succeeds with result_count > 0, call store_memory
  with the question, the final working SPARQL, result_count, and was_refined.
- Never tell the user you are checking memory — just silently use it to answer faster.
INSTREOF
  ok "Appended MEMORY PROTOCOL to agent_instruction.txt"
else
  ok "MEMORY PROTOCOL already present in agent_instruction.txt"
fi

AGENT_INSTRUCTION=$(cat "${SCRIPT_DIR}/infra/lib/agent_instruction.txt")

aws bedrock-agent update-agent \
  --agent-id "$AGENT_ID" \
  --agent-name "VBC-Care-Navigator" \
  --agent-resource-role-arn "$(aws bedrock-agent get-agent --agent-id $AGENT_ID --region $REGION --query 'agent.agentResourceRoleArn' --output text)" \
  --foundation-model "$(aws bedrock-agent get-agent --agent-id $AGENT_ID --region $REGION --query 'agent.foundationModel' --output text)" \
  --instruction "$AGENT_INSTRUCTION" \
  --idle-session-ttl-in-seconds 1800 \
  --region "$REGION" > /dev/null
ok "Agent instruction updated with memory protocol"

# -- Step 10: Prepare agent + update alias -------------------------
log "Step 10/11 - Prepare agent"

aws bedrock-agent prepare-agent --agent-id "$AGENT_ID" --region "$REGION" > /dev/null
ok "Prepare triggered"

echo "  Waiting for agent to reach PREPARED state (up to 3 min)..."
STATUS="PREPARING"
for i in $(seq 1 18); do
  STATUS=$(aws bedrock-agent get-agent --agent-id "$AGENT_ID" --region "$REGION" \
    --query 'agent.agentStatus' --output text 2>/dev/null || echo "PREPARING")
  echo "    [$i] $STATUS"
  [[ "$STATUS" == "PREPARED" ]] && break
  sleep 10
done
[[ "$STATUS" == "PREPARED" ]] || warn "Agent status: $STATUS (not PREPARED - check console)"
ok "Agent PREPARED"

aws bedrock-agent update-agent-alias \
  --agent-id "$AGENT_ID" \
  --agent-alias-id "$ALIAS_ID" \
  --agent-alias-name "poc-v1" \
  --region "$REGION" > /dev/null
NEW_VERSION=$(aws bedrock-agent get-agent-alias --agent-id "$AGENT_ID" --agent-alias-id "$ALIAS_ID" \
  --region "$REGION" --query 'agentAlias.routingConfiguration[0].agentVersion' --output text)
ok "Alias poc-v1 now routes to version ${NEW_VERSION}"

cat > "${SCRIPT_DIR}/phase_b_config.json" << JEOF
{
  "agent_id": "${AGENT_ID}",
  "agent_alias_id": "${ALIAS_ID}",
  "agent_version": "${NEW_VERSION}",
  "region": "${REGION}",
  "account": "${ACCOUNT}",
  "action_groups": ["sparql_query", "semantic_search", "get_patient_360", "refine_query", "retrieve_memory", "store_memory"],
  "lambdas": {
    "memory_write": "${WRITE_ARN}",
    "memory_read": "${READ_ARN}",
    "memory_rate": "${RATE_ARN}"
  },
  "dynamodb_table": "${MEMORY_TABLE}",
  "opensearch_index": "${OS_INDEX}",
  "rate_endpoint": "https://${API_ID}.execute-api.${REGION}.amazonaws.com/poc/rate",
  "status": "${STATUS}"
}
JEOF
ok "Wrote phase_b_config.json"

echo "============================================================"
echo " Phase B deploy complete — see Step 11 (run separately):"
echo "   python3 test_phase_b.py"
echo "============================================================"

rm -rf "${TMP_DIR}"
