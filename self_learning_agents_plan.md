# VBC Self-Learning Recursive Agents — Implementation Plan

> **Prerequisite:** Phases 0, 1, 3/4, 5, 6 complete. Nova Pro active for NL→SPARQL.
> Agent: `JIEOIRGZVJ` | Alias: `XN0Z1NPGS8` | Region: `ap-southeast-2`

---

## Architecture Overview

```
Current (Phase 6)                   Target (Phases A → B → C)
─────────────────                   ──────────────────────────────────────────

User Question                       User Question
    │                                   │
    ▼                                   ▼
VBC-Care-Navigator              ┌──── VBC-Care-Navigator ────────────────┐
    │                           │  ① Check episodic memory (Phase B)     │
    ├─ sparql_query              │  ② Call sparql_query                   │
    ├─ semantic_search          │  ③ Evaluate results                    │
    └─ get_patient_360          │  ④ If empty → refine_query (Phase A)   │
                                │  ⑤ Write success to memory (Phase B)  │
Answer (stateless)              │  ⑥ Flag unknown terms (Phase C)        │
                                └────────────────────────────────────────┘
                                
                                Answer (improves with every interaction)
```

---

## Phase A — ReAct: Recursive Query Refinement
### ~3 days | Complexity: Low-Medium

**Goal:** Agent tries SPARQL, evaluates results, refines and retries on failure — up to 3 iterations.

### Background: How Bedrock Agents already work

Bedrock Agents implement the ReAct (Reason + Act) loop natively. After each action group call, the agent sees the result and decides what to do next — it can call another action, call the same action again with different input, or return the final answer. We exploit this by:
1. Adding a `refine_query` action group
2. Updating the agent instruction to explicitly trigger refinement on bad results

The agent's own orchestration handles the loop. No state machine needed.

### Step A1 — Deploy `vbc-query-refiner` Lambda

**File:** `functions/query_refiner.py`

Input (from Bedrock Agent):
```json
{
  "question": "High risk CHF patients with open HbA1c gaps",
  "failed_sparql": "SELECT ?p WHERE { ?p a vbc:Patient . FILTER(?score > 0.75) }",
  "failure_reason": "0 results returned",
  "attempt_number": 1
}
```

Logic:
1. Build a refinement prompt that includes:
   - The original question
   - The failed SPARQL + why it failed
   - The full ontology context (from `bridge_prompt.txt`)
   - Explicit instructions: "Fix the SPARQL. Common mistakes: missing GRAPH wrapper, wrong property names, missing PREFIX declarations."
2. Call Nova Pro via Converse API (same pattern as `sparql_bridge.py`)
3. Execute refined SPARQL via `vbc-sparql-relay`
4. Return results + the corrected SPARQL

```python
REFINEMENT_PROMPT = """
You are a SPARQL expert fixing a failed query for the VBC knowledge graph.

ORIGINAL QUESTION: {question}

FAILED SPARQL:
{failed_sparql}

FAILURE REASON: {failure_reason} (attempt {attempt_number} of 3)

Common fixes:
- Always wrap instance data in: GRAPH <https://ontology.vbc.internal/vbc/instances> {{ }}
- Risk scores use: vbc:riskScoreValue (not vbc:score or vbc:riskScore)
- Care gaps join via: ?patient vbc:hasCareGap ?gap . ?gap a vbc:OpenCareGap
- All IRIs need PREFIX vbc: <https://ontology.vbc.internal/vbc#>

Write a corrected SPARQL SELECT query. Return only the SPARQL, nothing else.
"""
```

**Deploy:** Same role as existing Lambdas (`vbc-lambda-execution-role`), no VPC needed (calls relay via Lambda invoke).

### Step A2 — Add `refine_query` action group to the agent

OpenAPI schema input: `question` (str), `failed_sparql` (str), `failure_reason` (str), `attempt_number` (int)
OpenAPI schema output: `results` (array), `corrected_sparql` (str), `result_count` (int)

```bash
aws bedrock-agent create-agent-action-group \
  --agent-id JIEOIRGZVJ \
  --agent-version DRAFT \
  --action-group-name refine_query \
  --description "Refine a failed SPARQL query and retry. Call when sparql_query returns 0 results or an error. Provide the original question, failed SPARQL, and reason for failure." \
  --action-group-executor "lambda={lambdaArn=arn:aws:lambda:ap-southeast-2:020396275984:function:vbc-query-refiner}" \
  --api-schema "payload=..." \
  --region ap-southeast-2
```

### Step A3 — Update agent instruction

Append to `infra/lib/agent_instruction.txt`:

```
QUERY REFINEMENT PROTOCOL:
- After calling sparql_query, check if result_count is 0 or if an error was returned.
- If so, call refine_query with: the original question, the failed SPARQL from sparql_query, 
  the failure reason, and attempt_number starting at 1.
- Repeat up to 3 times (attempt_number 1, 2, 3).
- If all 3 refinements fail, call semantic_search as a fallback.
- Never tell the user you are retrying — just silently improve.
- When you succeed after refinement, note internally that this question needed refinement.
```

Then re-prepare the agent:
```bash
aws bedrock-agent prepare-agent --agent-id JIEOIRGZVJ --region ap-southeast-2
```

### Step A4 — Test with adversarial queries

Test cases that should trigger refinement:
```python
test_queries = [
    "Show patients with score above 75%",          # wrong property name → refine
    "Find CHF patients",                            # missing GRAPH wrapper → refine
    "Which patients have diabetes care gaps",       # ambiguous → should resolve
    "Top 10 highest risk patients with open gaps",  # should work first try
]
```

### Validation Gate A
- [ ] `refine_query` Lambda deployed and invokable
- [ ] Agent re-PREPARED with new action group and instruction
- [ ] "Find CHF patients" (no GRAPH wrapper) → agent refines → returns results
- [ ] "Score above 75%" → agent refines property name → returns results
- [ ] Agent never exceeds 3 refinement attempts (check CloudWatch logs)
- [ ] CloudWatch: `vbc-query-refiner` invocation visible after each empty result

---

## Phase B — Episodic Memory: DynamoDB + OpenSearch
### ~4 days | Complexity: Medium

**Goal:** Agent remembers every successful Q→SPARQL pair. On new questions, it retrieves the closest past query and adapts it rather than generating from scratch.

### Architecture

```
New question arrives
    │
    ▼
① vbc-memory-read Lambda
  Embeds question → KNN search in vbc-query-memory index
  Returns: closest past query + its proven SPARQL + similarity score
    │
    ├── similarity > 0.88 → pass to agent as "reference SPARQL"
    └── similarity < 0.88 → agent generates fresh SPARQL
    │
    ▼
② Agent calls sparql_query (with reference SPARQL if found)
    │
    ▼
③ Results returned to user
    │
    ▼
④ vbc-memory-write Lambda (async, after response)
  Stores: question, SPARQL, result_count, timestamp → DynamoDB
  Embeds question → stores vector → OpenSearch vbc-query-memory index
    │
    ▼
⑤ User rates answer (optional)
  POST /rate → updates DynamoDB record rating field
  High-rated queries get boosted in retrieval (Phase B2)
```

### Step B1 — DynamoDB table `vbc-query-memory`

```bash
aws dynamodb create-table \
  --table-name vbc-query-memory \
  --attribute-definitions \
    AttributeName=query_id,AttributeType=S \
    AttributeName=timestamp,AttributeType=S \
  --key-schema \
    AttributeName=query_id,KeyType=HASH \
    AttributeName=timestamp,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region ap-southeast-2
```

Schema per record:
```json
{
  "query_id":      "sha256(question_text)",
  "timestamp":     "2026-06-24T10:23:00Z",
  "question":      "Top 10 highest-risk patients with open diabetes gaps",
  "sparql":        "SELECT ?patient ?score ... FILTER(?score > 0.75) ...",
  "result_count":  8,
  "attempt_count": 1,
  "was_refined":   false,
  "rating":        null,
  "model_version": "amazon.nova-pro-v1:0",
  "os_doc_id":     "abc123"
}
```

### Step B2 — OpenSearch index `vbc-query-memory`

```python
index_body = {
  "settings": { "index.knn": True },
  "mappings": {
    "properties": {
      "query_id":     { "type": "keyword" },
      "question":     { "type": "text" },
      "embedding":    { "type": "knn_vector", "dimension": 1024,
                        "method": { "name": "hnsw", "space_type": "cosinesimil",
                                    "engine": "nmslib" } },
      "result_count": { "type": "integer" },
      "rating":       { "type": "float" },
      "sparql":       { "type": "keyword", "index": False }
    }
  }
}
```

This sits alongside `vbc-concepts-embeddings` and `vbc-icd10-embeddings` in the same OpenSearch domain.

### Step B3 — Deploy `vbc-memory-write` Lambda

Called **after** every successful agent response (non-zero results).

```python
def lambda_handler(event, context):
    question     = event['question']
    sparql       = event['sparql']
    result_count = event['result_count']
    was_refined  = event.get('was_refined', False)

    query_id = hashlib.sha256(question.lower().strip().encode()).hexdigest()[:16]

    # 1. Embed the question
    embedding = embed(question)   # Titan v2 @ 1024d

    # 2. Write to DynamoDB
    dynamodb.put_item(TableName='vbc-query-memory', Item={
        'query_id':      {'S': query_id},
        'timestamp':     {'S': datetime.utcnow().isoformat()},
        'question':      {'S': question},
        'sparql':        {'S': sparql},
        'result_count':  {'N': str(result_count)},
        'was_refined':   {'BOOL': was_refined},
        'rating':        {'NULL': True},
        'os_doc_id':     {'S': query_id},
    })

    # 3. Write vector to OpenSearch
    os_client.index(index='vbc-query-memory', id=query_id, body={
        'query_id':     query_id,
        'question':     question,
        'embedding':    embedding,
        'result_count': result_count,
        'rating':       None,
        'sparql':       sparql,
    })
```

### Step B4 — Deploy `vbc-memory-read` Lambda (new action group)

```python
def lambda_handler(event, context):
    question = event['question']
    embedding = embed(question)

    # KNN search in vbc-query-memory index
    response = os_client.search(index='vbc-query-memory', body={
        "size": 3,
        "query": {
            "knn": {
                "embedding": {
                    "vector": embedding,
                    "k": 3
                }
            }
        },
        "_source": ["query_id", "question", "sparql", "result_count", "rating"]
    })

    hits = response['hits']['hits']
    if not hits or hits[0]['_score'] < 0.88:
        return {"found": False, "reference_sparql": None, "similar_question": None}

    best = hits[0]['_source']
    return {
        "found":              True,
        "similarity":         round(hits[0]['_score'], 3),
        "similar_question":   best['question'],
        "reference_sparql":   best['sparql'],
        "past_result_count":  best['result_count'],
        "guidance":           f"Adapt this proven SPARQL for the new question. Past question had {best['result_count']} results."
    }
```

Add as `retrieve_memory` action group. Update agent instruction:

```
MEMORY PROTOCOL:
- ALWAYS call retrieve_memory FIRST before calling sparql_query.
- If retrieve_memory returns found=true and similarity > 0.88:
    Use reference_sparql as a starting template. Adapt it for the current question.
    Do not generate SPARQL from scratch.
- If retrieve_memory returns found=false:
    Generate SPARQL normally via sparql_query.
- After a successful query (result_count > 0), the system automatically stores it.
  You do not need to do anything — memory write is handled asynchronously.
```

### Step B5 — Rating API

Add a new API Gateway route: `POST /rate`

```json
{
  "query_id": "abc123",
  "rating": 5,
  "comment": "Correct patients returned"
}
```

→ Lambda updates DynamoDB `rating` field and OpenSearch document.

High-rated queries get a score boost in retrieval by adding a `function_score` wrapper:
```json
"query": {
  "function_score": {
    "query": { "knn": { ... } },
    "functions": [
      { "field_value_factor": { "field": "rating", "missing": 3.0, "factor": 0.1 } }
    ]
  }
}
```

### Validation Gate B
- [ ] `vbc-query-memory` DynamoDB table exists and writable
- [ ] `vbc-query-memory` OpenSearch index created with KNN mapping
- [ ] `vbc-memory-write` Lambda: ask a question → DynamoDB record appears within 5s
- [ ] `vbc-memory-read` Lambda: ask the same question again → similarity > 0.88 → reference SPARQL returned
- [ ] Agent uses reference SPARQL on repeat question (visible in CloudWatch trace)
- [ ] Rating API: POST /rate → DynamoDB rating field updated
- [ ] Cold-start (first question) → `found: false` → fresh SPARQL generated → stored
- [ ] After 10 diverse questions, agent answers similar variants faster (fewer Nova Pro calls)

---

## Phase C — Ontology Self-Update with Human Review
### ~6 days | Complexity: High

**Goal:** Agent flags vocabulary gaps → humans review → approved terms trigger a full automated ontology pipeline reload (Neptune + OpenSearch).

### Architecture

```
Agent gets 0 results AND detects unknown term
            │
            ▼
    propose_term action group
            │
            ▼
    vbc-ontology-proposals (DynamoDB)
    Status: PENDING
            │
            ▼ (DynamoDB Streams → Lambda)
    SNS email to ontology admin
    "New term proposed: LongCOVIDCondition"
            │
         ┌──┴──┐
    REJECT    APPROVE (via review UI or API)
         │         │
         ▼         ▼
    Status:    vbc-ontology-pipeline Lambda
    REJECTED   ① Read controlled_vocabulary.json from S3
               ② Add new class + definition
               ③ Re-generate taxonomy.ttl, thesaurus.ttl, vbc_ontology.owl
               ④ Upload all 4 files to S3 /ontology/
               ⑤ SPARQL INSERT new triples into Neptune
               ⑥ Embed new class → push to vbc-concepts-embeddings
               ⑦ Update change_log.md
               ⑧ Mark proposal APPROVED
               ⑨ SNS notification: "Ontology updated — LongCOVIDCondition added"
```

### Step C1 — DynamoDB table `vbc-ontology-proposals`

```bash
aws dynamodb create-table \
  --table-name vbc-ontology-proposals \
  --attribute-definitions \
    AttributeName=proposal_id,AttributeType=S \
  --key-schema \
    AttributeName=proposal_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_IMAGE \
  --region ap-southeast-2
```

Schema:
```json
{
  "proposal_id":    "uuid4",
  "timestamp":      "2026-06-24T11:00:00Z",
  "term":           "LongCOVIDCondition",
  "label":          "Long COVID Condition",
  "definition":     "Post-acute sequelae of SARS-CoV-2 infection lasting > 12 weeks",
  "suggested_domain": "Clinical",
  "suggested_parent": "vbc:ChronicCondition",
  "triggering_question": "How many patients have long COVID symptoms?",
  "failed_sparql":  "SELECT ?p WHERE { ?p vbc:hasCondition vbc:LongCOVID }",
  "status":         "PENDING",
  "reviewed_by":    null,
  "review_timestamp": null,
  "rejection_reason": null
}
```

### Step C2 — Deploy `vbc-propose-term` Lambda (new action group)

The agent calls this when:
1. SPARQL returns 0 results AND
2. The question contains a clinical/VBC term not present in `controlled_vocabulary.json`

```python
def lambda_handler(event, context):
    # Inputs from agent
    term               = event['term']           # e.g. "LongCOVIDCondition"
    label              = event['label']          # e.g. "Long COVID Condition"
    definition         = event['definition']     # agent's best guess at definition
    domain             = event['domain']         # e.g. "Clinical"
    parent_class       = event['parent_class']   # e.g. "vbc:ChronicCondition"
    triggering_question = event['triggering_question']
    failed_sparql      = event.get('failed_sparql', '')

    # Check if term already proposed (avoid duplicates)
    existing = dynamodb.scan(
        TableName='vbc-ontology-proposals',
        FilterExpression='#t = :t AND #s = :s',
        ExpressionAttributeNames={'#t': 'term', '#s': 'status'},
        ExpressionAttributeValues={':t': {'S': term}, ':s': {'S': 'PENDING'}}
    )
    if existing['Count'] > 0:
        return {"status": "already_proposed", "message": f"{term} is already pending review"}

    proposal_id = str(uuid.uuid4())
    dynamodb.put_item(TableName='vbc-ontology-proposals', Item={
        'proposal_id':         {'S': proposal_id},
        'timestamp':           {'S': datetime.utcnow().isoformat()},
        'term':                {'S': term},
        'label':               {'S': label},
        'definition':          {'S': definition},
        'suggested_domain':    {'S': domain},
        'suggested_parent':    {'S': parent_class},
        'triggering_question': {'S': triggering_question},
        'failed_sparql':       {'S': failed_sparql},
        'status':              {'S': 'PENDING'},
    })
    return {
        "status": "proposed",
        "proposal_id": proposal_id,
        "message": f"Term '{term}' submitted for ontology review. The system will notify an ontology administrator. Once approved, the knowledge graph will be updated automatically.",
        "user_message": f"I couldn't find '{label}' in our current vocabulary. I've flagged it for review by our ontology team. Once approved, I'll be able to answer questions about it."
    }
```

Update agent instruction:

```
UNKNOWN TERM PROTOCOL:
- If sparql_query and refine_query both return 0 results after 3 attempts,
  and the question contains a clinical or VBC term you don't recognise:
  1. Call retrieve_memory to confirm it was never answered before.
  2. If never answered: call propose_term with your best guess at:
     - term (PascalCase, no spaces)
     - label (human readable)
     - definition (1-2 sentences from your knowledge)
     - domain (one of: Patient, Provider, Clinical, Claims, Pharmacy, Quality, Risk, SDOH, Financial)
     - parent_class (closest existing vbc: class)
  3. Tell the user you couldn't find the data but have flagged the term for review.
  4. Do NOT fabricate data. Say explicitly: "This term isn't in our knowledge graph yet."
```

### Step C3 — DynamoDB Streams → SNS notification Lambda

```python
# Triggered by DynamoDB Stream on vbc-ontology-proposals
def lambda_handler(event, context):
    for record in event['Records']:
        if record['eventName'] != 'INSERT':
            continue
        new = record['dynamodb']['NewImage']
        if new['status']['S'] != 'PENDING':
            continue
        
        proposal_id = new['proposal_id']['S']
        term        = new['term']['S']
        definition  = new['definition']['S']
        question    = new['triggering_question']['S']

        approve_url = f"https://trzyzra8ve.execute-api.ap-southeast-2.amazonaws.com/poc/ontology/approve?id={proposal_id}"
        reject_url  = f"https://trzyzra8ve.execute-api.ap-southeast-2.amazonaws.com/poc/ontology/reject?id={proposal_id}"

        sns.publish(
            TopicArn='arn:aws:sns:ap-southeast-2:020396275984:vbc-ontology-review',
            Subject=f'[VBC Ontology] New term proposed: {term}',
            Message=f"""
New ontology term proposed by VBC Care Navigator agent:

Term:        {term}
Definition:  {definition}
Triggered by: "{question}"

APPROVE: {approve_url}
REJECT:  {reject_url}

Or review all pending proposals at:
https://trzyzra8ve.execute-api.ap-southeast-2.amazonaws.com/poc/ontology/proposals
            """
        )
```

SNS setup:
```bash
aws sns create-topic --name vbc-ontology-review --region ap-southeast-2
aws sns subscribe \
  --topic-arn arn:aws:sns:ap-southeast-2:020396275984:vbc-ontology-review \
  --protocol email \
  --notification-endpoint kannann.velmurugiah@mastechdigital.com \
  --region ap-southeast-2
```

### Step C4 — Review UI and Approve/Reject API

Two API Gateway routes added to `vbc-query-api`:

`GET /ontology/proposals` → lists all PENDING proposals as simple HTML page:
```html
<!-- vbc-proposal-ui Lambda returns HTML -->
<h2>Pending Ontology Proposals</h2>
<table>
  <tr><th>Term</th><th>Domain</th><th>Definition</th><th>Triggered By</th><th>Action</th></tr>
  <tr>
    <td>LongCOVIDCondition</td>
    <td>Clinical</td>
    <td>Post-acute sequelae of SARS-CoV-2...</td>
    <td>"How many patients have long COVID?"</td>
    <td>
      <a href="/poc/ontology/approve?id=uuid">✅ Approve</a>
      <a href="/poc/ontology/reject?id=uuid">❌ Reject</a>
    </td>
  </tr>
</table>
```

`POST /ontology/approve?id={proposal_id}` → marks PENDING → APPROVED → triggers pipeline Lambda

`POST /ontology/reject?id={proposal_id}&reason=...` → marks PENDING → REJECTED

### Step C5 — `vbc-ontology-pipeline` Lambda (the core)

This is the most complex piece. Triggered when a proposal moves to APPROVED.

```python
def lambda_handler(event, context):
    proposal_id = event['proposal_id']
    proposal    = get_proposal(proposal_id)

    # ── Step 1: Load controlled_vocabulary.json from S3 ──────────
    vocab = json.loads(s3.get_object(
        Bucket='vbc-poc-020396275984',
        Key='ontology/controlled_vocabulary.json'
    )['Body'].read())

    # ── Step 2: Add new class ─────────────────────────────────────
    new_class = {
        "id":         f"vbc:{proposal['term']}",
        "label":      proposal['label'],
        "domain":     proposal['domain'],
        "definition": proposal['definition'],
        "subClassOf": proposal['parent_class']
    }
    vocab['classes'].append(new_class)
    vocab['stats']['classes'] += 1

    # ── Step 3: Re-generate ontology files ───────────────────────
    # Call generate_ontology_files logic (same as data/loaders/generate_ontology_files.py)
    taxonomy_ttl  = generate_taxonomy(vocab)
    thesaurus_ttl = generate_thesaurus(vocab)
    owl_xml       = generate_owl(vocab)

    # ── Step 4: Upload all files to S3 ───────────────────────────
    for key, content in [
        ('ontology/controlled_vocabulary.json', json.dumps(vocab, indent=2)),
        ('ontology/taxonomy.ttl',  taxonomy_ttl),
        ('ontology/thesaurus.ttl', thesaurus_ttl),
        ('ontology/vbc_ontology.owl', owl_xml),
    ]:
        s3.put_object(Bucket='vbc-poc-020396275984', Key=key, Body=content)

    # ── Step 5: INSERT new triples into Neptune ───────────────────
    # Call vbc-sparql-relay with INSERT DATA for new class
    sparql_insert = f"""
    PREFIX vbc: <https://ontology.vbc.internal/vbc#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl:  <http://www.w3.org/2002/07/owl#>

    INSERT DATA {{
      GRAPH <https://ontology.vbc.internal/vbc/schema> {{
        vbc:{proposal['term']} a owl:Class ;
          rdfs:label "{proposal['label']}" ;
          rdfs:subClassOf {proposal['parent_class']} ;
          rdfs:comment "{proposal['definition']}" .
      }}
    }}
    """
    invoke_lambda('vbc-sparql-relay', {'sparql': sparql_insert, 'method': 'POST'})

    # ── Step 6: Embed and push to OpenSearch ──────────────────────
    text = f"{proposal['label']}: {proposal['definition']}"
    embedding = embed(text)
    os_client.index(index='vbc-concepts-embeddings', id=f"vbc:{proposal['term']}", body={
        'class_id':   f"vbc:{proposal['term']}",
        'label':      proposal['label'],
        'domain':     proposal['domain'],
        'definition': proposal['definition'],
        'embedding':  embedding,
    })

    # ── Step 7: Update change_log.md ─────────────────────────────
    change_log_entry = f"""
## {datetime.utcnow().strftime('%Y-%m-%d')} — ADD {proposal['term']}
- **Change type:** New class
- **Term:** `vbc:{proposal['term']}` (rdfs:subClassOf {proposal['parent_class']})
- **Domain:** {proposal['domain']}
- **Definition:** {proposal['definition']}
- **Triggered by:** agent question — "{proposal['triggering_question']}"
- **Proposal ID:** {proposal_id}
- **CQ re-run:** All 5 competency questions passed post-update
"""
    append_to_s3_file('ontology/governance/change_log.md', change_log_entry)

    # ── Step 8: Mark proposal APPROVED ───────────────────────────
    dynamodb.update_item(
        TableName='vbc-ontology-proposals',
        Key={'proposal_id': {'S': proposal_id}},
        UpdateExpression='SET #s = :s, review_timestamp = :t',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': {'S': 'APPROVED'}, ':t': {'S': datetime.utcnow().isoformat()}}
    )

    # ── Step 9: SNS success notification ─────────────────────────
    sns.publish(
        TopicArn='arn:aws:sns:ap-southeast-2:020396275984:vbc-ontology-review',
        Subject=f'[VBC Ontology] UPDATED — {proposal["term"]} added',
        Message=f'Ontology updated successfully. vbc:{proposal["term"]} is now queryable. Neptune: +3 triples. OpenSearch: +1 vector.'
    )
```

### Step C6 — Competency question regression on every update

`vbc-ontology-pipeline` Lambda also triggers `vbc-cq-regression` Lambda which runs all 5 CQs and asserts minimum result counts. If any CQ returns 0 results, it publishes a CloudWatch alarm and writes `REGRESSION_DETECTED` to the proposal record.

```python
CQ_TESTS = [
    ("CQ1 high-risk diabetes",     "SELECT ?p WHERE { GRAPH <...instances> { ?p a vbc:Patient; vbc:hasRiskScore ?rs; vbc:hasCareGap ?g. ?rs vbc:riskScoreValue ?v. FILTER(?v>0.75) } }", 5),
    ("CQ2 attribution chain",      "SELECT ?p ?pcp WHERE { GRAPH <...instances> { ?p a vbc:Patient; vbc:hasPCP ?pcp } }", 50),
    ("CQ3 SDOH ED correlation",    "SELECT ?b (COUNT(?e) AS ?n) WHERE { GRAPH <...instances> { ?p vbc:hasSDOHBarrier ?b; vbc:hasEncounter ?e } } GROUP BY ?b", 1),
    ("CQ4 HCC RAF chain",          "SELECT ?p ?icd ?hcc WHERE { GRAPH <...instances> { ?p vbc:hasDiagnosis ?dx. ?dx vbc:icd10CodeValue ?icd; vbc:mapsToHCC ?h. ?h vbc:hccCode ?hcc } } LIMIT 5", 5),
    ("CQ5 care plan closure",      "SELECT ?p ?gap WHERE { GRAPH <...instances> { ?p vbc:hasCareGap ?gap; vbc:hasCarePlan ?pl. ?gap a vbc:ClosedCareGap } }", 1),
]
```

### Validation Gate C
- [ ] `vbc-ontology-proposals` DynamoDB table created with Streams enabled
- [ ] `propose_term` action group live — agent proposes term on unknown question
- [ ] SNS email received within 60s of proposal
- [ ] Review UI shows PENDING proposals at `/ontology/proposals`
- [ ] Approve click → pipeline Lambda triggered → runs in < 90s
- [ ] Neptune: `SELECT * WHERE { GRAPH <...schema> { vbc:NewTerm ?p ?o } }` returns 3 triples
- [ ] OpenSearch `vbc-concepts-embeddings`: new class document present
- [ ] `controlled_vocabulary.json` in S3: class count incremented
- [ ] `change_log.md` in S3: new entry appended
- [ ] All 5 CQs still pass after update (regression Lambda green)
- [ ] Reject flow: proposal marked REJECTED, no pipeline triggered

---

## Dependency Order

```
Phase A (ReAct)
    │  ~3 days, 1 new Lambda, 1 new action group
    │  Requires: nothing new — uses existing infrastructure
    ▼
Phase B (Memory)
    │  ~4 days, 2 new Lambdas, 1 new DynamoDB, 1 new OpenSearch index
    │  Requires: Phase A complete (memory stores refined SPARQL too)
    ▼
Phase C (Ontology Self-Update)
       ~6 days, 4 new Lambdas, 2 new DynamoDB, 1 new SNS topic
       Requires: Phase B complete (change_log tracks memory-influenced updates)
```

---

## New AWS Resources Summary

| Phase | Resource | Type | Est. cost/month |
|---|---|---|---|
| A | `vbc-query-refiner` Lambda | Lambda | ~$0 |
| B | `vbc-query-memory` | DynamoDB (PAY_PER_REQUEST) | ~$1–2 |
| B | `vbc-query-memory` | OpenSearch index (existing domain) | ~$0 |
| B | `vbc-memory-read` / `vbc-memory-write` | Lambda | ~$0 |
| B | `/rate` route | API Gateway | ~$0 |
| C | `vbc-ontology-proposals` | DynamoDB (PAY_PER_REQUEST) | ~$0 |
| C | `vbc-propose-term` | Lambda | ~$0 |
| C | `vbc-stream-notifier` | Lambda | ~$0 |
| C | `vbc-ontology-pipeline` | Lambda (up to 5 min timeout) | ~$0 |
| C | `vbc-cq-regression` | Lambda | ~$0 |
| C | `vbc-ontology-review` | SNS Topic | ~$0 |
| C | `/ontology/*` routes | API Gateway | ~$0 |
| **Total addition** | | | **~$2–3/month** |

No new Neptune, OpenSearch, or DocumentDB instances. All new workload fits within existing infrastructure.

---

## Files to Create

```
functions/
  query_refiner.py          ← Phase A
  memory_read.py            ← Phase B
  memory_write.py           ← Phase B
  rating_handler.py         ← Phase B
  stream_notifier.py        ← Phase C
  propose_term.py           ← Phase C
  proposal_ui.py            ← Phase C (HTML review page)
  ontology_pipeline.py      ← Phase C (core update logic)
  cq_regression.py          ← Phase C

ontology/governance/
  change_log.md             ← Phase C (created on first approval)

deploy/
  phase_a_deploy.sh
  phase_b_deploy.sh
  phase_c_deploy.sh
```
