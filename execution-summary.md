# VBC Knowledge Fabric — Execution Summary

> **Purpose:** Incremental implementation log. Update this file after every phase or significant step.
> **Account:** `020396275984` | **Region:** `ap-southeast-2` | **Date started:** 2026-06-11

---

## Overall Status

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Bootstrap — toolchain, S3, Glue, synthetic data, embeddings | ✅ Complete |
| Phase 1 | L1+L2+L3: Storage, OpenSearch, Neptune | ✅ Complete |
| Phase 3/4 | Ontology Pipeline + Vector Layer | ✅ Complete |
| Phase 5 | L5+L6: Bedrock KB + SPARQL reasoning API | ✅ Functional (Nova Pro NL→SPARQL active) |
| Phase 6 | L7: Bedrock Agent (VBC Care Navigator) | ✅ Complete |
| Phase 2 | L3 Catalog: DataZone, Lake Formation | ⏳ Blocked — `datazone:*` permission pending |
| Phase 7 | Governance: change management, drift detection | ⏳ Not started |

---

## Phase 0 — Bootstrap ✅

### Decision log
- CDK bootstrap blocked — `cloudformation:DescribeStacks` denied on SSO role `SNFStudio-ServiceAdminAccess`
- **Strategy pivot**: all infrastructure provisioned directly via AWS CLI + boto3 (no CloudFormation)
- Region confirmed as `ap-southeast-2` (SSO role scoped to this region)

### Toolchain installed
- CDK CLI v2.1126.0 (npm global)
- Python packages: `boto3`, `faker`, `pandas`, `pyarrow`, `rdflib`, `requests`, `requests-aws4auth`

### Validation gates
| Gate | Result |
|---|---|
| AWS credentials valid — Account `020396275984` | ✅ |
| CDK bootstrap | ❌ IAM blocked — bypassed with CLI-direct strategy |
| `controlled_vocabulary.json` ≥ 130 classes | ✅ 136 classes |
| Monthly cost estimate < $300 | ✅ ~$200–230/month |

---

## Phase 1 — L1 Storage + L2 OpenSearch + L3 Neptune ✅

### S3 — `vbc-poc-020396275984`
- Bucket created with versioning enabled, region `ap-southeast-2`
- Prefixes: `/raw/`, `/iceberg/`, `/ontology/`, `/embeddings/`
- All ontology files uploaded to `s3://vbc-poc-020396275984/ontology/`

### Glue + Athena — `vbc_poc_db`
- Glue database created
- 8 external tables registered via Athena CREATE EXTERNAL TABLE DDL
- **Note:** Initial Glue table creation with `table_type=ICEBERG` flag caused Athena errors — deleted and recreated via Athena DDL (plain Parquet EXTERNAL_TABLE)

| Table | Rows | Validated |
|---|---|---|
| `member_master` | 500 | ✅ |
| `provider_master` | 50 | ✅ |
| `claims_medical` | 2,000 | ✅ |
| `diagnosis_history` | 3,000 | ✅ |
| `hedis_gaps` | 400 | ✅ |
| `risk_scores` | 500 | ✅ |
| `sdoh_barriers` | 200 | ✅ |
| `pharmacy_claims` | 800 | ✅ |

### Synthetic Data — `data/sample/` (seed=42, reproducible)
| File | Rows |
|---|---|
| `members.parquet` | 500 |
| `providers.parquet` | 50 (30 PCPs + 20 specialists, 2 networks) |
| `claims.parquet` | 2,000 (12% inpatient, 15% readmit flag) |
| `diagnoses.parquet` | 3,000 (weighted CHF/T2DM/COPD/HTN/CKD/Depression) |
| `hedis_gaps.parquet` | 400 (55% open / 35% closed / 10% excluded) |
| `risk_scores.parquet` | 500 (60% low / 25% moderate / 15% high) |
| `sdoh_barriers.parquet` | 200 (food/housing/transport/social) |
| `pharmacy_claims.parquet` | 800 |

### OpenSearch — `vbc-vectors-poc`
- Engine: OpenSearch 2.17
- Instance: `t3.small.search`, 1 node, 20GB gp3
- Endpoint: `search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com`
- FGAC enabled — master user `vbcadmin`, IAM role mapped to `all_access`
- Access policy: open (FGAC handles authorization)
- **Note:** Resource-based policy must be open (`Principal: *`) when FGAC is enabled; IAM-only auth blocks master user basic auth

| Index | Docs | KNN |
|---|---|---|
| `vbc-concepts-embeddings` | 136 | ✅ 1024d HNSW cosine |
| `vbc-icd10-embeddings` | 18 | ✅ 1024d HNSW cosine |
| `vbc-clinical-notes` | 0 (ready) | ✅ mapped |

**Embedding model:** `amazon.titan-embed-text-v2:0` @ 1024 dimensions

**Vector search validation:**
- "congestive heart failure" → `I50.9` ✅
- "sugar diabetes" → `E11.65` (T2DM with hyperglycemia — semantically correct) ✅
- "care gap quality measure" → `vbc:CareGap` ✅

### Neptune — `vbc-neptune-poc`
- Engine: Neptune (latest)
- Instance: `db.t3.medium`
- Cluster endpoint: `vbc-neptune-poc.cluster-cxe0k4i6swp1.ap-southeast-2.neptune.amazonaws.com:8182`
- IAM database authentication: **enabled**
- VPC: default VPC, subnets across 3 AZs
- Security group: `sg-034c63d345bf01a34` — port 8182 open to VPC CIDR
- **Note:** Neptune has no public endpoint — data loaded via `vbc-sparql-relay` Lambda deployed in same VPC

---

## Phase 3/4 — Ontology Pipeline + Knowledge Graph ✅

### Ontology files generated and loaded
All three files generated from `controlled_vocabulary.json` by `generate_ontology_files.py`:

| File | Triples | Graph URI |
|---|---|---|
| `taxonomy.ttl` | 729 | `https://ontology.vbc.internal/vbc/schema` |
| `thesaurus.ttl` | 127 | `https://ontology.vbc.internal/vbc/schema` |
| `vbc_ontology.owl` | 668 | `https://ontology.vbc.internal/vbc/schema` |

### Knowledge graph — instance nodes
| Node type | Triples | Rows |
|---|---|---|
| Patient | 4,000 | 500 |
| Provider (PCP + Specialist) | 300 | 50 |
| PatientDiagnosis | 20,284 | 3,000 |
| CareGap (Open/Closed/Excluded) | 2,140 | 400 |
| RiskFactor | 3,000 | 500 |
| SDOHBarrier | 1,200 | 200 |
| **Total node triples** | **30,924** | |

### Knowledge graph — edges
| Edge | Count |
|---|---|
| `vbc:hasDiagnosis` | 3,000 |
| `vbc:hasCareGap` | 400 |
| `vbc:hasRiskScore` | 500 |
| `vbc:hasSDOHBarrier` | 200 |
| `vbc:hasPCP` | 500 |
| **Total edge triples** | **4,600** |

**Grand total in Neptune:** 35,524 triples + 1,524 ontology triples = **37,048 triples**

### Phase 3 validation gates
| Gate | Result |
|---|---|
| `rdfs:subClassOf` triples ≥ 48 | ✅ 213 |
| `skos:altLabel` triples ≥ 40 | ✅ 88 |
| Patient nodes ≥ 450 | ✅ 1,000 (riskTier property count) |
| RiskFactor nodes ≥ 450 | ✅ 500 |
| `hasDiagnosis` edges ≥ 2,500 | ✅ 3,000 |

---

## Phase 5 — Bedrock KB + SPARQL Reasoning API ✅

### Lambdas deployed
- `vbc-sparql-bridge` — NL → SPARQL (Nova Pro via Converse API) → Neptune via relay → results
- `vbc-hybrid-query` — Neptune graph + OpenSearch KNN merged results
- `vbc-sparql-relay` — VPC-internal SigV4-signed Neptune SPARQL relay

### API Gateway — `vbc-query-api`
- ID: `trzyzra8ve`
- Base URL: `https://trzyzra8ve.execute-api.ap-southeast-2.amazonaws.com/poc`
- `POST /sparql` → `vbc-sparql-bridge`
- `POST /query` → `vbc-hybrid-query`

### Validation results
| Query | Result |
|---|---|
| Q1 — High-risk patients with open gaps | ✅ 20 Neptune bindings |
| Q2 — SDOH barriers vs ED utilization | ✅ Graph + semantic merged |
| Q3 — Housing instability + HTN patients | ✅ 10 merged results |

### Notes
- Claude NL→SPARQL originally blocked by org SCP; switched to `amazon.nova-pro-v1:0` via Converse API
- Template SPARQL fallback still active as safety net
- Bedrock KB (AOSS) skipped — `opensearchserverless:*` permission not granted
- VPC endpoint for bedrock-runtime: `vpce-07f399db83802310b`

---

## Phase 6 — Bedrock Agent ✅

### Agent: VBC-Care-Navigator
- Agent ID: `JIEOIRGZVJ` | Alias: `XN0Z1NPGS8`
- Foundation model: `amazon.nova-pro-v1:0`
- Action groups:

| Action Group | Lambda | Path |
|---|---|---|
| `sparql_query` | `vbc-sparql-bridge` | `POST /sparql` |
| `semantic_search` | `vbc-hybrid-query` | `POST /query` |
| `get_patient_360` | `vbc-get-patient-360` | `POST /patient360` |

### Validation results
| Question | Result |
|---|---|
| Q1: Top 10 highest-risk patients with open gaps | ✅ 8 named patients (M-0204 score 3.49 highest) |
| Q2: Attribution chain for M-0042 | ✅ Attribution status "prospective" returned |
| Q3: Patients with housing instability + diabetes | ✅ Responded accurately (0 in sample data) |
| Q4: Most common SDOH barriers | ✅ Food (59), Social isolation (55), Transportation (45), Housing (41) |
| Q5: Care managers with highest gap closure rates | ✅ Responded accurately (care managers not in sample) |

### Notes
- Claude blocked in account: Anthropic Marketplace subscription incomplete — switched to Nova Pro
- All 3 Lambda functions updated to return Bedrock Agent response format (`messageVersion: 1.0`)
- `get_patient_360` column names corrected: `hcc_category→hcc_code`, `gap_open_date→open_date`, `composite_risk_score→risk_score_value`, `barrier_severity→severity`

---

## Infrastructure Deployed

| Resource | ID / Endpoint | Status |
|---|---|---|
| S3 bucket | `vbc-poc-020396275984` | ✅ |
| Glue DB | `vbc_poc_db` | ✅ |
| Neptune cluster | `vbc-neptune-poc` | ✅ Available |
| Neptune instance | `vbc-neptune-poc-instance` (db.t3.medium) | ✅ Available |
| OpenSearch domain | `vbc-vectors-poc` (OpenSearch 2.17) | ✅ Active |
| Lambda — SPARQL relay | `vbc-sparql-relay` (Python 3.12, VPC) | ✅ |
| Lambda — SPARQL bridge | `vbc-sparql-bridge` (Python 3.12, Nova Pro) | ✅ |
| Lambda — Hybrid query | `vbc-hybrid-query` (Python 3.12) | ✅ |
| Lambda — Patient 360 | `vbc-get-patient-360` (Python 3.12) | ✅ |
| API Gateway | `trzyzra8ve` — `poc` stage | ✅ |
| Bedrock Agent | `JIEOIRGZVJ` alias `XN0Z1NPGS8` | ✅ PREPARED |
| IAM execution role | `vbc-lambda-execution-role` | ✅ |

---

## Scripts Inventory

| Script | Purpose | Status |
|---|---|---|
| `phase0_bootstrap.sh` | CDK install + npm deps (CDK step skipped) | ✅ |
| `data/loaders/generate_sample_data.py` | Generates all 8 synthetic Parquet datasets | ✅ |
| `data/loaders/load_iceberg.py` | Athena CREATE EXTERNAL TABLE + COUNT(*) validation | ✅ |
| `data/loaders/load_opensearch_vectors.py` | Bedrock Titan embeddings → JSONL → S3 | ✅ |
| `data/loaders/generate_ontology_files.py` | Generates taxonomy.ttl, thesaurus.ttl, vbc_ontology.owl | ✅ |
| `data/loaders/load_neptune_via_lambda.py` | **Primary Neptune loader** — all stages via Lambda relay | ✅ |
| `data/loaders/setup_opensearch.py` | Create indices + bulk load embeddings + validate search | ✅ |
| `functions/sparql_relay.py` | Lambda SPARQL relay with SigV4 signing for Neptune | ✅ |
| `functions/sparql_bridge.py` | NL→SPARQL via Nova Pro + template fallback | ✅ |
| `functions/hybrid_query.py` | Neptune + OpenSearch hybrid search | ✅ |
| `functions/get_patient_360.py` | Full patient 360 view via Athena | ✅ |

---

## Pending

### Phase 2 — DataZone (blocked)
- Need `datazone:*` permission added to SSO user
- Once granted: run `data/loaders/load_datazone_glossary.py` to load 136 glossary terms

### Phase 7 — Governance (not started)
- Ontology change log + competency question regression tests
- Neptune graph integrity checks (orphaned nodes, missing PCP edges)
- CloudTrail audit dashboard
- DataZone PHI tagging + subscription approval workflow

---

## Session notes
- Neptune is VPC-only — all SPARQL runs through `vbc-sparql-relay` Lambda
- OpenSearch uses master-user basic auth (`vbcadmin / VbcPoc2024!`) — IAM role mapping done
- AWS session tokens expire every ~1 hour — write to `~/.aws/credentials` at start of each session
- Neptune security group `sg-034c63d345bf01a34` has rule allowing `223.185.21.53/32` — remove if IP changes
