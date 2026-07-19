#!/usr/bin/env bash
# ============================================================
# Phase 6 Deploy — VBC Knowledge Fabric
# L7: Bedrock Agent — VBC Care Navigator
#
# Steps:
#   1. Preflight checks
#   2. Deploy get_patient_360 Lambda (Athena full patient view)
#   3. Create Bedrock Agent execution role
#   4. Create Bedrock Agent (VBC-Care-Navigator)
#   5. Create 3 action groups (sparql_query, semantic_search, get_patient_360)
#   6. Prepare + alias the Agent
#   7. Run 5 validation questions
# ============================================================
set -euo pipefail

REGION="ap-southeast-2"
ACCOUNT="020396275984"
S3_BUCKET="vbc-poc-${ACCOUNT}"
AGENT_NAME="VBC-Care-Navigator"
AGENT_ROLE_NAME="vbc-bedrock-agent-role"
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/vbc-lambda-execution-role"
BRIDGE_FUNCTION="vbc-sparql-bridge"
HYBRID_FUNCTION="vbc-hybrid-query"
P360_FUNCTION="vbc-get-patient-360"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${SCRIPT_DIR}/.phase6_tmp"
mkdir -p "${TMP_DIR}"

log()  { echo ""; echo "══ $* ══"; }
ok()   { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }
fail() { echo "  ❌ $*"; exit 1; }

echo "╔══════════════════════════════════════════════════════╗"
echo "║  VBC Knowledge Fabric — Phase 6 Deploy               ║"
echo "║  Bedrock Agent: VBC Care Navigator                   ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── Step 1: Preflight ─────────────────────────────────────────
log "Step 1/7 — Preflight"
ACTUAL=$(aws sts get-caller-identity --query Account --output text)
[[ "$ACTUAL" == "$ACCOUNT" ]] || fail "Wrong account: $ACTUAL"
ok "Account: $ACCOUNT"

aws bedrock-agent list-agents --region "$REGION" > /dev/null 2>&1 \
  || fail "bedrock-agent API not accessible"
ok "Bedrock Agent API reachable"

# ── Step 2: Deploy get_patient_360 Lambda ─────────────────────
log "Step 2/7 — Deploy get_patient_360 Lambda"

mkdir -p "${TMP_DIR}/p360"
cp "${SCRIPT_DIR}/functions/get_patient_360.py" "${TMP_DIR}/p360/lambda_function.py"
cd "${TMP_DIR}/p360" && zip -q p360.zip lambda_function.py && cd - > /dev/null

P360_EXISTS=$(aws lambda get-function --function-name "$P360_FUNCTION" \
  --region "$REGION" --query Configuration.FunctionArn --output text 2>/dev/null || echo "")

if [[ -n "$P360_EXISTS" ]]; then
  aws lambda update-function-code --function-name "$P360_FUNCTION" \
    --region "$REGION" --zip-file fileb://${TMP_DIR}/p360/p360.zip \
    --query FunctionArn --output text > /dev/null
  aws lambda wait function-updated --function-name "$P360_FUNCTION" --region "$REGION"
  P360_ARN="$P360_EXISTS"
  ok "Updated $P360_FUNCTION"
else
  P360_ARN=$(aws lambda create-function \
    --function-name "$P360_FUNCTION" \
    --region "$REGION" \
    --runtime python3.12 \
    --role "$LAMBDA_ROLE_ARN" \
    --handler lambda_function.lambda_handler \
    --zip-file fileb://${TMP_DIR}/p360/p360.zip \
    --timeout 60 \
    --memory-size 256 \
    --environment "Variables={GLUE_DATABASE=vbc_poc_db,ATHENA_OUTPUT=s3://${S3_BUCKET}/raw/athena-results/}" \
    --query FunctionArn --output text)
  aws lambda wait function-active --function-name "$P360_FUNCTION" --region "$REGION"
  ok "Created $P360_FUNCTION: $P360_ARN"
fi

# ── Step 3: Bedrock Agent execution role ──────────────────────
log "Step 3/7 — Bedrock Agent execution role"

AGENT_ROLE_ARN=$(aws iam get-role --role-name "$AGENT_ROLE_NAME" \
  --query Role.Arn --output text 2>/dev/null || echo "")

if [[ -z "$AGENT_ROLE_ARN" ]]; then
  AGENT_ROLE_ARN=$(aws iam create-role \
    --role-name "$AGENT_ROLE_NAME" \
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
    --role-name "$AGENT_ROLE_NAME" \
    --policy-name "vbc-agent-policy" \
    --policy-document '{
      "Version":"2012-10-17",
      "Statement":[
        {"Effect":"Allow",
         "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
         "Resource":"*"},
        {"Effect":"Allow",
         "Action":["lambda:InvokeFunction"],
         "Resource":[
           "arn:aws:lambda:'"$REGION"':'"$ACCOUNT"':function:'"$BRIDGE_FUNCTION"'",
           "arn:aws:lambda:'"$REGION"':'"$ACCOUNT"':function:'"$HYBRID_FUNCTION"'",
           "arn:aws:lambda:'"$REGION"':'"$ACCOUNT"':function:'"$P360_FUNCTION"'"
         ]},
        {"Effect":"Allow",
         "Action":["s3:GetObject","s3:ListBucket"],
         "Resource":["arn:aws:s3:::'"$S3_BUCKET"'","arn:aws:s3:::'"$S3_BUCKET"'/*"]}
      ]
    }'
  sleep 10
  ok "Created agent role: $AGENT_ROLE_ARN"
else
  ok "Agent role exists: $AGENT_ROLE_ARN"
fi

# ── Step 4: Create Bedrock Agent ──────────────────────────────
log "Step 4/7 — Create Bedrock Agent"

AGENT_INSTRUCTION=$(cat "${SCRIPT_DIR}/infra/lib/agent_instruction.txt")

EXISTING_AGENT=$(aws bedrock-agent list-agents --region "$REGION" \
  --query "agentSummaries[?agentName=='${AGENT_NAME}'].agentId" \
  --output text 2>/dev/null | head -1 || echo "")

if [[ -n "$EXISTING_AGENT" && "$EXISTING_AGENT" != "None" ]]; then
  AGENT_ID="$EXISTING_AGENT"
  ok "Agent already exists: $AGENT_ID"
else
  AGENT_ID=$(aws bedrock-agent create-agent \
    --region "$REGION" \
    --agent-name "$AGENT_NAME" \
    --description "VBC Care Navigator — conversational agent over the VBC knowledge graph" \
    --agent-resource-role-arn "$AGENT_ROLE_ARN" \
    --foundation-model "au.anthropic.claude-sonnet-4-6" \
    --instruction "$AGENT_INSTRUCTION" \
    --idle-session-ttl-in-seconds 1800 \
    --query 'agent.agentId' --output text)
  ok "Created agent: $AGENT_ID"

  echo "  Waiting for agent to reach NOT_PREPARED state..."
  for i in $(seq 1 12); do
    STATUS=$(aws bedrock-agent get-agent --agent-id "$AGENT_ID" --region "$REGION" \
      --query 'agent.agentStatus' --output text 2>/dev/null || echo "CREATING")
    [[ "$STATUS" != "CREATING" ]] && break
    echo "    [$i] $STATUS — waiting 5s..."
    sleep 5
  done
  ok "Agent status: $STATUS"
fi

echo "$AGENT_ID" > "${TMP_DIR}/agent_id.txt"

# ── Step 5: Action groups ─────────────────────────────────────
log "Step 5/7 — Action groups"

# Helper: add Lambda invoke permission for Bedrock Agent
_add_lambda_permission() {
  local FN="$1"
  local STMT_ID="bedrock-agent-${AGENT_ID}-${FN}"
  aws lambda add-permission \
    --function-name "$FN" \
    --statement-id "$STMT_ID" \
    --action lambda:InvokeFunction \
    --principal bedrock.amazonaws.com \
    --source-arn "arn:aws:bedrock:${REGION}:${ACCOUNT}:agent/${AGENT_ID}" \
    --region "$REGION" > /dev/null 2>&1 || true
}

_add_lambda_permission "$BRIDGE_FUNCTION"
_add_lambda_permission "$HYBRID_FUNCTION"
_add_lambda_permission "$P360_FUNCTION"

# Check existing action groups
EXISTING_AGS=$(aws bedrock-agent list-agent-action-groups \
  --agent-id "$AGENT_ID" --agent-version "DRAFT" \
  --region "$REGION" \
  --query 'actionGroupSummaries[*].actionGroupName' \
  --output json 2>/dev/null || echo "[]")

_create_action_group() {
  local AG_NAME="$1"
  local DESCRIPTION="$2"
  local LAMBDA_ARN="$3"
  local API_SCHEMA="$4"

  if echo "$EXISTING_AGS" | grep -q "\"${AG_NAME}\""; then
    ok "Action group already exists: $AG_NAME"
    return
  fi

  aws bedrock-agent create-agent-action-group \
    --agent-id "$AGENT_ID" \
    --agent-version "DRAFT" \
    --region "$REGION" \
    --action-group-name "$AG_NAME" \
    --description "$DESCRIPTION" \
    --action-group-executor "lambda={lambdaArn=${LAMBDA_ARN}}" \
    --api-schema "payload=${API_SCHEMA}" \
    --action-group-state ENABLED \
    --query 'agentActionGroup.actionGroupId' --output text > /dev/null
  ok "Created action group: $AG_NAME"
}

# OpenAPI schema for sparql_query
SPARQL_SCHEMA=$(python3 -c "
import json
schema = {
  'openapi': '3.0.0',
  'info': {'title': 'SPARQL Query', 'version': '1.0'},
  'paths': {
    '/sparql': {
      'post': {
        'operationId': 'sparqlQuery',
        'description': 'Convert a natural language question to SPARQL and query the VBC knowledge graph in Neptune. Returns patient IDs, risk scores, care gaps, diagnoses, SDOH barriers, and provider attribution.',
        'requestBody': {
          'required': True,
          'content': {'application/json': {'schema': {
            'type': 'object',
            'properties': {
              'question': {'type': 'string', 'description': 'Natural language question about VBC patients, providers, quality gaps, risk scores, or SDOH barriers'}
            },
            'required': ['question']
          }}}
        },
        'responses': {'200': {'description': 'SPARQL results with graph bindings', 'content': {'application/json': {'schema': {'type': 'object'}}}}}
      }
    }
  }
}
print(json.dumps(schema))
")

# OpenAPI schema for semantic_search
SEMANTIC_SCHEMA=$(python3 -c "
import json
schema = {
  'openapi': '3.0.0',
  'info': {'title': 'Semantic Search', 'version': '1.0'},
  'paths': {
    '/query': {
      'post': {
        'operationId': 'semanticSearch',
        'description': 'Hybrid semantic search combining Neptune graph facts with OpenSearch vector similarity. Returns both structured graph results and semantically similar ontology concepts.',
        'requestBody': {
          'required': True,
          'content': {'application/json': {'schema': {
            'type': 'object',
            'properties': {
              'query': {'type': 'string', 'description': 'Natural language query for hybrid graph + semantic search'}
            },
            'required': ['query']
          }}}
        },
        'responses': {'200': {'description': 'Merged graph facts and semantic matches', 'content': {'application/json': {'schema': {'type': 'object'}}}}}
      }
    }
  }
}
print(json.dumps(schema))
")

# OpenAPI schema for get_patient_360
P360_SCHEMA=$(python3 -c "
import json
schema = {
  'openapi': '3.0.0',
  'info': {'title': 'Patient 360', 'version': '1.0'},
  'paths': {
    '/patient360': {
      'post': {
        'operationId': 'getPatient360',
        'description': 'Retrieve a complete 360-degree view of a patient from Athena: demographics, diagnoses, care gaps, risk scores, and SDOH barriers.',
        'requestBody': {
          'required': True,
          'content': {'application/json': {'schema': {
            'type': 'object',
            'properties': {
              'member_id': {'type': 'string', 'description': 'Patient member ID in format M-XXXX (e.g. M-0042)'}
            },
            'required': ['member_id']
          }}}
        },
        'responses': {'200': {'description': 'Full patient 360 view', 'content': {'application/json': {'schema': {'type': 'object'}}}}}
      }
    }
  }
}
print(json.dumps(schema))
")

_create_action_group "sparql_query"    "Query VBC knowledge graph via NL→SPARQL→Neptune"  \
  "$(aws lambda get-function --function-name $BRIDGE_FUNCTION --region $REGION --query Configuration.FunctionArn --output text)" \
  "$SPARQL_SCHEMA"

_create_action_group "semantic_search" "Hybrid Neptune + OpenSearch semantic vector search" \
  "$(aws lambda get-function --function-name $HYBRID_FUNCTION --region $REGION --query Configuration.FunctionArn --output text)" \
  "$SEMANTIC_SCHEMA"

_create_action_group "get_patient_360" "Full patient view from Athena (demographics, gaps, risk, SDOH)" \
  "$P360_ARN" \
  "$P360_SCHEMA"

# ── Step 6: Prepare and alias the agent ───────────────────────
log "Step 6/7 — Prepare + alias agent"

aws bedrock-agent prepare-agent \
  --agent-id "$AGENT_ID" \
  --region "$REGION" > /dev/null
ok "Prepare triggered"

echo "  Waiting for agent to reach PREPARED state (up to 3 min)..."
for i in $(seq 1 18); do
  STATUS=$(aws bedrock-agent get-agent --agent-id "$AGENT_ID" --region "$REGION" \
    --query 'agent.agentStatus' --output text 2>/dev/null || echo "PREPARING")
  echo "    [$i] $STATUS"
  [[ "$STATUS" == "PREPARED" ]] && break
  sleep 10
done
[[ "$STATUS" == "PREPARED" ]] || { warn "Agent status: $STATUS (not PREPARED — check console)"; }
ok "Agent PREPARED"

# Create alias
ALIAS_ID=$(aws bedrock-agent list-agent-aliases \
  --agent-id "$AGENT_ID" --region "$REGION" \
  --query "agentAliasSummaries[?agentAliasName=='poc'].agentAliasId" \
  --output text 2>/dev/null | head -1 || echo "")

if [[ -z "$ALIAS_ID" || "$ALIAS_ID" == "None" ]]; then
  ALIAS_ID=$(aws bedrock-agent create-agent-alias \
    --agent-id "$AGENT_ID" \
    --agent-alias-name "poc" \
    --description "VBC PoC alias" \
    --region "$REGION" \
    --query 'agentAlias.agentAliasId' --output text)
  ok "Created alias 'poc': $ALIAS_ID"
else
  ok "Alias already exists: $ALIAS_ID"
fi

# Save config
cat > "${SCRIPT_DIR}/phase6_config.json" << JEOF
{
  "agent_id": "${AGENT_ID}",
  "agent_alias_id": "${ALIAS_ID}",
  "agent_name": "${AGENT_NAME}",
  "region": "${REGION}",
  "account": "${ACCOUNT}",
  "lambdas": {
    "sparql_bridge":   "arn:aws:lambda:${REGION}:${ACCOUNT}:function:${BRIDGE_FUNCTION}",
    "hybrid_query":    "arn:aws:lambda:${REGION}:${ACCOUNT}:function:${HYBRID_FUNCTION}",
    "get_patient_360": "${P360_ARN}"
  },
  "invoke_example": "aws bedrock-agent-runtime invoke-agent --agent-id ${AGENT_ID} --agent-alias-id ${ALIAS_ID} --session-id test-001 --input-text 'Who are the top 10 highest-risk patients?' --region ${REGION}"
}
JEOF
ok "Wrote phase6_config.json"

# ── Step 7: Validation ────────────────────────────────────────
log "Step 7/7 — Validation (5 test questions)"

_ask_agent() {
  local Q="$1"
  local SESSION="session-$(date +%s)-$RANDOM"
  echo "  Q: $Q"
  RESP=$(aws bedrock-agent-runtime invoke-agent \
    --agent-id "$AGENT_ID" \
    --agent-alias-id "$ALIAS_ID" \
    --session-id "$SESSION" \
    --input-text "$Q" \
    --region "$REGION" \
    --cli-binary-format raw-in-base64-out \
    --output json 2>/dev/null || echo '{"completion":[]}')
  # Extract text chunks from streamed response
  ANSWER=$(echo "$RESP" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
chunks = d.get('completion', [])
text = ''.join(
    c.get('chunk',{}).get('bytes','') if isinstance(c.get('chunk',{}).get('bytes',''), str)
    else c.get('chunk',{}).get('bytes',b'').decode('utf-8','ignore')
    for c in chunks if 'chunk' in c
)
print(text[:400] if text else '(no text response)')
" 2>/dev/null || echo "(parse error)")
  echo "  A: $ANSWER"
  echo ""
}

_ask_agent "Who are the top 10 highest-risk patients with open quality gaps?"
_ask_agent "Show me the attribution chain for member M-0042"
_ask_agent "Which patients have both housing instability and uncontrolled diabetes?"
_ask_agent "What SDOH barriers are most common in the patient population?"
_ask_agent "Which care managers have the highest gap closure rates?"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  Phase 6 Summary                                      ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  Agent ID    : $AGENT_ID"
echo "  Alias ID    : $ALIAS_ID"
echo "  Status      : $STATUS"
echo "  Action groups: sparql_query, semantic_search, get_patient_360"
echo ""
echo "  CLI invoke:"
echo "  aws bedrock-agent-runtime invoke-agent \\"
echo "    --agent-id $AGENT_ID \\"
echo "    --agent-alias-id $ALIAS_ID \\"
echo "    --session-id my-session \\"
echo "    --input-text 'Who are the top 10 highest-risk patients?' \\"
echo "    --region $REGION"
echo ""
echo "  Next: Phase 7 — Governance (change management, drift detection)"

rm -rf "${TMP_DIR}"
