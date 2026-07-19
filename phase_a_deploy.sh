#!/usr/bin/env bash
# ============================================================
# Phase A Deploy — VBC Self-Learning Agents
# ReAct: Recursive Query Refinement
#
# Steps:
#   1. Preflight checks
#   2. Deploy vbc-query-refiner Lambda
#   3. Grant Bedrock Agent invoke permission on the Lambda
#   4. Create refine_query action group
#   5. Update agent instruction with refinement protocol
#   6. Prepare agent (DRAFT -> PREPARED) and update alias
#   7. Test with adversarial queries
# ============================================================
set -euo pipefail

REGION="ap-southeast-2"
ACCOUNT="020396275984"
AGENT_ID="JIEOIRGZVJ"
ALIAS_ID="XN0Z1NPGS8"
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/vbc-lambda-execution-role"
REFINER_FUNCTION="vbc-query-refiner"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${SCRIPT_DIR}/.phase_a_tmp"
mkdir -p "${TMP_DIR}"

log()  { echo ""; echo "== $* =="; }
ok()   { echo "  OK: $*"; }
warn() { echo "  WARN: $*"; }
fail() { echo "  FAIL: $*"; exit 1; }

echo "============================================================"
echo " VBC Self-Learning Agents — Phase A Deploy"
echo " ReAct: Recursive Query Refinement"
echo "============================================================"

# -- Step 1: Preflight ------------------------------------------
log "Step 1/7 - Preflight"
ACTUAL=$(aws sts get-caller-identity --query Account --output text)
[[ "$ACTUAL" == "$ACCOUNT" ]] || fail "Wrong account: $ACTUAL"
ok "Account: $ACCOUNT"

aws bedrock-agent get-agent --agent-id "$AGENT_ID" --region "$REGION" > /dev/null 2>&1 \
  || fail "Cannot reach agent $AGENT_ID"
ok "Agent reachable: $AGENT_ID"

# -- Step 2: Deploy vbc-query-refiner Lambda ---------------------
log "Step 2/7 - Deploy vbc-query-refiner Lambda"

mkdir -p "${TMP_DIR}/refiner"
cp "${SCRIPT_DIR}/functions/query_refiner.py" "${TMP_DIR}/refiner/lambda_function.py"
cd "${TMP_DIR}/refiner" && zip -q refiner.zip lambda_function.py && cd - > /dev/null

REFINER_EXISTS=$(aws lambda get-function --function-name "$REFINER_FUNCTION" \
  --region "$REGION" --query Configuration.FunctionArn --output text 2>/dev/null || echo "")

if [[ -n "$REFINER_EXISTS" ]]; then
  aws lambda update-function-code --function-name "$REFINER_FUNCTION" \
    --region "$REGION" --zip-file fileb://${TMP_DIR}/refiner/refiner.zip \
    --query FunctionArn --output text > /dev/null
  aws lambda wait function-updated --function-name "$REFINER_FUNCTION" --region "$REGION"
  REFINER_ARN="$REFINER_EXISTS"
  ok "Updated $REFINER_FUNCTION"
else
  REFINER_ARN=$(aws lambda create-function \
    --function-name "$REFINER_FUNCTION" \
    --region "$REGION" \
    --runtime python3.12 \
    --role "$LAMBDA_ROLE_ARN" \
    --handler lambda_function.lambda_handler \
    --zip-file fileb://${TMP_DIR}/refiner/refiner.zip \
    --timeout 60 \
    --memory-size 256 \
    --environment "Variables={SPARQL_RELAY_FUNCTION=vbc-sparql-relay,BEDROCK_MODEL=amazon.nova-pro-v1:0}" \
    --query FunctionArn --output text)
  aws lambda wait function-active --function-name "$REFINER_FUNCTION" --region "$REGION"
  ok "Created $REFINER_FUNCTION: $REFINER_ARN"
fi

# -- Step 3: Grant Bedrock Agent invoke permission ---------------
log "Step 3/7 - Grant agent invoke permission on Lambda"

aws lambda add-permission \
  --function-name "$REFINER_FUNCTION" \
  --statement-id "bedrock-agent-${AGENT_ID}-${REFINER_FUNCTION}" \
  --action lambda:InvokeFunction \
  --principal bedrock.amazonaws.com \
  --source-arn "arn:aws:bedrock:${REGION}:${ACCOUNT}:agent/${AGENT_ID}" \
  --region "$REGION" > /dev/null 2>&1 || true
ok "Lambda invoke permission granted (or already present)"

# -- Step 4: Create refine_query action group --------------------
log "Step 4/7 - Create refine_query action group"

EXISTING_AGS=$(aws bedrock-agent list-agent-action-groups \
  --agent-id "$AGENT_ID" --agent-version "DRAFT" \
  --region "$REGION" \
  --query 'actionGroupSummaries[*].actionGroupName' \
  --output json 2>/dev/null || echo "[]")

# NOTE: the shorthand `--api-schema payload=file://...` and
# `--action-group-executor lambda={...}` CLI syntax is broken in this
# CLI version (aws-cli/2.34.53) — it fails OpenAPI validation server-side
# even with a byte-identical known-good payload. Using --cli-input-json
# bypasses the shorthand parser entirely and works reliably.
EXISTING_AG_ID=$(echo "$EXISTING_AGS" | grep -q '"refine_query"' && \
  aws bedrock-agent list-agent-action-groups \
    --agent-id "$AGENT_ID" --agent-version DRAFT --region "$REGION" \
    --query "actionGroupSummaries[?actionGroupName=='refine_query'].actionGroupId" --output text || echo "")

python3 -c "
import json

schema = {
  'openapi': '3.0.0',
  'info': {'title': 'Query Refiner', 'version': '1.0'},
  'paths': {
    '/refine': {
      'post': {
        'operationId': 'refineQuery',
        'description': 'Refine a failed SPARQL query and retry. Call when sparql_query returns 0 results or an error. Provide the original question, failed SPARQL, and reason for failure.',
        'requestBody': {
          'required': True,
          'content': {'application/json': {'schema': {
            'type': 'object',
            'properties': {
              'question':        {'type': 'string', 'description': 'The original natural language question'},
              'failed_sparql':   {'type': 'string', 'description': 'The SPARQL query that failed or returned 0 results'},
              'failure_reason':  {'type': 'string', 'description': 'Why the query failed, e.g. 0 results returned'},
              'attempt_number':  {'type': 'integer', 'description': 'Refinement attempt number, starting at 1, max 3'}
            },
            'required': ['question', 'failed_sparql', 'failure_reason', 'attempt_number']
          }}}
        },
        'responses': {'200': {'description': 'Corrected SPARQL and results', 'content': {'application/json': {'schema': {'type': 'object'}}}}}
      }
    }
  }
}

req = {
  'agentId': '${AGENT_ID}',
  'agentVersion': 'DRAFT',
  'actionGroupName': 'refine_query',
  'description': 'Refine a failed SPARQL query and retry. Call when sparql_query returns 0 results or an error.',
  'actionGroupExecutor': {'lambda': '${REFINER_ARN}'},
  'apiSchema': {'payload': json.dumps(schema)},
  'actionGroupState': 'ENABLED',
}
existing_id = '${EXISTING_AG_ID}'.strip()
if existing_id and existing_id != 'None':
    req['actionGroupId'] = existing_id
open('${TMP_DIR}/refine_ag_request.json', 'w').write(json.dumps(req))
"

if [[ -n "$EXISTING_AG_ID" && "$EXISTING_AG_ID" != "None" ]]; then
  aws bedrock-agent update-agent-action-group \
    --region "$REGION" \
    --cli-input-json "file://${TMP_DIR}/refine_ag_request.json" > /dev/null
  ok "Updated action group: refine_query"
else
  aws bedrock-agent create-agent-action-group \
    --region "$REGION" \
    --cli-input-json "file://${TMP_DIR}/refine_ag_request.json" \
    --query 'agentActionGroup.actionGroupId' --output text > /dev/null
  ok "Created action group: refine_query"
fi

# -- Step 5: Update agent instruction ----------------------------
log "Step 5/7 - Update agent instruction"

AGENT_INSTRUCTION=$(cat "${SCRIPT_DIR}/infra/lib/agent_instruction.txt")

aws bedrock-agent update-agent \
  --agent-id "$AGENT_ID" \
  --agent-name "VBC-Care-Navigator" \
  --agent-resource-role-arn "$(aws bedrock-agent get-agent --agent-id $AGENT_ID --region $REGION --query 'agent.agentResourceRoleArn' --output text)" \
  --foundation-model "$(aws bedrock-agent get-agent --agent-id $AGENT_ID --region $REGION --query 'agent.foundationModel' --output text)" \
  --instruction "$AGENT_INSTRUCTION" \
  --idle-session-ttl-in-seconds 1800 \
  --region "$REGION" > /dev/null
ok "Agent instruction updated with refinement protocol"

# -- Step 6: Prepare agent + update alias ------------------------
log "Step 6/7 - Prepare agent"

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
  --agent-alias-name "poc" \
  --region "$REGION" > /dev/null
ok "Alias updated to latest DRAFT version"

# -- Step 7: Test with adversarial queries -----------------------
log "Step 7/7 - Test with adversarial queries"

_ask_agent() {
  local Q="$1"
  local SESSION="phasea-$(date +%s)-$RANDOM"
  echo "  Q: $Q"
  RESP=$(aws bedrock-agent-runtime invoke-agent \
    --agent-id "$AGENT_ID" \
    --agent-alias-id "$ALIAS_ID" \
    --session-id "$SESSION" \
    --input-text "$Q" \
    --region "$REGION" \
    --cli-binary-format raw-in-base64-out \
    --output json 2>/dev/null || echo '{"completion":[]}')
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

_ask_agent "Show patients with score above 75%"
_ask_agent "Find CHF patients"
_ask_agent "Which patients have diabetes care gaps"
_ask_agent "Top 10 highest risk patients with open gaps"

cat > "${SCRIPT_DIR}/phase_a_config.json" << JEOF
{
  "agent_id": "${AGENT_ID}",
  "agent_alias_id": "${ALIAS_ID}",
  "region": "${REGION}",
  "account": "${ACCOUNT}",
  "action_groups": ["sparql_query", "semantic_search", "get_patient_360", "refine_query"],
  "lambdas": {
    "query_refiner": "${REFINER_ARN}"
  },
  "status": "${STATUS}"
}
JEOF
ok "Wrote phase_a_config.json"

echo "============================================================"
echo " Phase A Summary"
echo "============================================================"
echo "  Agent ID     : $AGENT_ID"
echo "  Alias ID     : $ALIAS_ID"
echo "  Status       : $STATUS"
echo "  New Lambda   : $REFINER_FUNCTION"
echo "  New action   : refine_query"
echo ""
echo "  Check CloudWatch for refiner invocations:"
echo "  aws logs tail /aws/lambda/${REFINER_FUNCTION} --region ${REGION} --since 10m"
echo ""
echo "  Next: Phase B — Episodic Memory (DynamoDB + OpenSearch)"

rm -rf "${TMP_DIR}"
