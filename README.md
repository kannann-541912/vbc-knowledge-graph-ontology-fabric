# VBC Knowledge Graph · Ontology · Knowledge Fabric

> A production-grade semantic knowledge fabric for Value-Based Care on AWS —
> 136-class OWL ontology, 37 K RDF triples in Neptune, hybrid SPARQL + vector search,
> and a conversational Bedrock Agent that reasons over live graph data.

---

## What this is

This repository implements a **7-layer Knowledge Fabric Stack** for Value-Based Care (VBC) analytics. It connects structured claims/clinical data, a formal OWL ontology, a semantic knowledge graph, and a large-language-model agent — all on AWS managed services, deployable to a single region.

The ontology covers the 12 core VBC domains — Patient, Provider, Clinical, Claims, Pharmacy, Quality, Risk, SDOH, Financial, Care Management, Utilization Management, and AI/ML — with 133 OWL classes, 71 object properties, 150 data properties, and 86 named individuals mapped to ICD-10-CM/PCS, CPT, LOINC, NDC, SNOMED CT, RxNorm, HL7 FHIR R4, HEDIS 2024, and CMS Stars 2024.

---

## Architecture — 7 Layers

```
L7  Bedrock Agent          VBC-Care-Navigator (Nova Pro) — conversational reasoning
L6  Access & Reasoning     SPARQL bridge (NL→SPARQL) + hybrid query Lambda + API Gateway
L5  Knowledge Layer        Neptune RDF/SPARQL (37K triples) — ontology + instance graph
L4  Ontology Pipeline      OWL ontology · SKOS thesaurus · RDF taxonomy
L3  Catalog & Governance   AWS DataZone · Glue Data Catalog · Lake Formation
L2  Vector Store           OpenSearch KNN — 1024-dim Titan v2 embeddings (136 concepts + ICD-10)
L1  Physical Storage       S3 + Athena (Parquet) — 8 tables, 7,450 synthetic VBC records
```

### AWS services used

| Layer | Service | Resource |
|---|---|---|
| Storage | S3 + Athena | `vbc-poc-{account}` bucket, `vbc_poc_db` Glue DB |
| Vector | OpenSearch 2.17 | `vbc-vectors-poc` — t3.small, 20 GB gp3 |
| Graph | Neptune | `vbc-neptune-poc` — db.t3.medium, SPARQL endpoint |
| Catalog | AWS Glue | 8 crawlers, Parquet external tables |
| Governance | DataZone | 136-term business glossary (pending) |
| Embeddings | Bedrock Titan v2 | `amazon.titan-embed-text-v2:0` @ 1024d |
| NL→SPARQL | Bedrock Nova Pro | `amazon.nova-pro-v1:0` via Converse API |
| Agent | Bedrock Agent | `VBC-Care-Navigator` — 3 action groups |
| API | API Gateway | `POST /sparql`, `POST /query` |

---

## Repository layout

```
.
├── ontology/
│   ├── controlled_vocabulary.json   # 136 OWL classes — source of truth
│   ├── taxonomy.ttl                 # rdfs:subClassOf hierarchy (729 triples)
│   ├── thesaurus.ttl                # SKOS altLabel / broader / narrower (127 triples)
│   ├── vbc_ontology.owl             # Full OWL/RDF ontology (668 triples)
│   ├── metadata_standards.json      # DCAT / Dublin Core column mappings
│   ├── governance/change_log.md     # Ontology change history
│   └── sparql/                      # Competency questions + bridge prompt
│       ├── high_risk_inference.rq
│       ├── gap_detection.rq
│       ├── readmission_risk.rq
│       └── bridge_prompt.txt        # NL→SPARQL prompt template
├── functions/
│   ├── sparql_relay.py              # VPC Lambda — SigV4-signed Neptune relay
│   ├── sparql_bridge.py             # NL→SPARQL via Nova Pro + template fallback
│   ├── hybrid_query.py              # Neptune + OpenSearch merged results
│   └── get_patient_360.py           # Full patient view via Athena
├── data/
│   ├── loaders/                     # All ETL scripts
│   │   ├── generate_sample_data.py  # Synthetic VBC dataset (faker, seed=42)
│   │   ├── generate_ontology_files.py
│   │   ├── load_neptune_via_lambda.py
│   │   ├── load_opensearch_vectors.py
│   │   └── load_datazone_glossary.py
│   └── sample/                      # Precomputed embeddings (JSONL)
├── infra/                           # CDK stacks (TypeScript)
│   └── lib/
│       ├── l1-storage-stack.ts
│       ├── l2-vector-stack.ts
│       ├── l3-graph-stack.ts
│       ├── l4-catalog-stack.ts
│       ├── l5-ontology-stack.ts
│       ├── l6-reasoning-stack.ts
│       └── l7-agent-stack.ts
├── tests/
│   ├── sparql/run_all_cq.py         # Competency question regression suite
│   └── integration/check_graph_integrity.py
├── notebooks/                       # Phase validation notebooks
└── execution-summary.md             # Full implementation log
```

---

## Ontology

Namespace: `https://ontology.vbc.internal/vbc#`

### Class hierarchy (excerpt)

```
owl:Thing
└── vbc:Person
    ├── vbc:Patient / vbc:Member
    └── vbc:CareTeamMember
        ├── vbc:Physician
        │   ├── vbc:PrimaryCarePhysician
        │   └── vbc:Specialist
        └── vbc:CareManager · vbc:SocialWorker
└── vbc:Condition
    ├── vbc:ChronicCondition
    │   ├── vbc:CardiovascularCondition
    │   ├── vbc:MetabolicCondition
    │   ├── vbc:RespiratoryCondition
    │   ├── vbc:RenalCondition
    │   └── vbc:MentalHealthCondition
    └── vbc:AcuteCondition
└── vbc:QualityMeasure
    ├── vbc:HEDISMeasure
    └── vbc:CMSStarMeasure
└── vbc:CareGap
    ├── vbc:OpenCareGap
    ├── vbc:ClosedCareGap
    └── vbc:ExcludedCareGap
└── vbc:SDOHBarrier
    ├── vbc:FoodInsecurityBarrier
    ├── vbc:HousingInstabilityBarrier
    ├── vbc:TransportationBarrier
    └── vbc:SocialIsolationBarrier
└── vbc:Encounter
    ├── vbc:InpatientEncounter
    ├── vbc:EDEncounter
    └── vbc:OutpatientEncounter
```

### Key object properties

| Property | Domain | Range |
|---|---|---|
| `vbc:hasDiagnosis` | Patient | PatientDiagnosis |
| `vbc:hasPCP` *(functional)* | Patient | PrimaryCarePhysician |
| `vbc:hasCareGap` | Patient | CareGap |
| `vbc:hasRiskScore` | Patient | RiskFactor |
| `vbc:hasSDOHBarrier` | Patient | SDOHBarrier |
| `vbc:mapsToHCC` | PatientDiagnosis | HCCCode |
| `vbc:diagnosedWith` | PatientDiagnosis | Condition |
| `vbc:belongsToNetwork` | Provider | Organization |

### SWRL inference rules

```
Rule 1: hasRiskScore > 0.85 → HighRiskPatient
Rule 2: hasCondition(Diabetes) ∧ ¬hasLabResult(HbA1c) → hasQualityGap(DiabetesHbA1cGap)
Rule 3: readmission within 30 days → hasReadmission(true)
Rule 4: ADI score > 80 → hasSDOHBarrier(HighDeprivationArea)
```

---

## Knowledge graph — Neptune

All data stored as **RDF triples** in named graphs via SPARQL INSERT DATA.
Neptune's SPARQL endpoint is VPC-only; all queries run through `vbc-sparql-relay` Lambda.

| Named graph | Contents |
|---|---|
| `https://ontology.vbc.internal/vbc/schema` | Ontology — taxonomy, thesaurus, OWL axioms |
| `https://ontology.vbc.internal/vbc/instances` | Instance data — patients, providers, diagnoses, gaps |

**Graph statistics**

| Entity | Nodes | Triples |
|---|---|---|
| Patient | 500 | 4,000 |
| Provider (PCP + Specialist) | 50 | 300 |
| PatientDiagnosis | 3,000 | 20,284 |
| CareGap (Open/Closed/Excluded) | 400 | 2,140 |
| RiskFactor | 500 | 3,000 |
| SDOHBarrier | 200 | 1,200 |
| **Total** | | **37,048** |

---

## SPARQL competency questions

Five competency questions validate the ontology end-to-end.
Run the full regression suite with:

```bash
python tests/sparql/run_all_cq.py
```

**CQ1 — High-risk patients with open diabetes gaps**
```sparql
PREFIX vbc: <https://ontology.vbc.internal/vbc#>
SELECT ?patient ?riskScore ?gap
WHERE {
  GRAPH <https://ontology.vbc.internal/vbc/instances> {
    ?patient a vbc:Patient ;
             vbc:hasRiskScore ?rs ;
             vbc:hasCareGap ?gap .
    ?rs vbc:riskScoreValue ?riskScore .
    ?gap a vbc:OpenCareGap .
    FILTER (?riskScore > 0.75)
  }
}
ORDER BY DESC(?riskScore)
```

**CQ2** — Attribution chain: patient → PCP → network  
**CQ3** — SDOH barriers correlated with ED visit count  
**CQ4** — ICD-10 → HCC → RAF weight chain  
**CQ5** — Care plan task closure driving gap closure  

---

## NL → SPARQL → Neptune

The `vbc-sparql-bridge` Lambda translates natural language to SPARQL using Amazon Nova Pro, executes against Neptune, and returns JSON-LD results. A keyword-matched template fallback ensures reliability.

```python
# Example invocation (direct Lambda test)
{
  "question": "Show me the top 10 highest-risk patients with open quality gaps"
}

# Response
{
  "question": "Show me the top 10 ...",
  "sparql_source": "bedrock-nova",
  "generated_sparql": "PREFIX vbc: ...",
  "binding_count": 10,
  "results": { "bindings": [...] }
}
```

**API Gateway endpoints**

```
POST https://{api-id}.execute-api.ap-southeast-2.amazonaws.com/poc/sparql
POST https://{api-id}.execute-api.ap-southeast-2.amazonaws.com/poc/query
```

---

## Bedrock Agent — VBC Care Navigator

Agent ID: `JIEOIRGZVJ` | Model: `amazon.nova-pro-v1:0`

| Action group | Lambda | Purpose |
|---|---|---|
| `sparql_query` | `vbc-sparql-bridge` | NL → SPARQL → Neptune results |
| `semantic_search` | `vbc-hybrid-query` | Vector + graph hybrid search |
| `get_patient_360` | `vbc-get-patient-360` | Full patient view from Athena |

**Validated questions**

1. *"Who are the top 10 highest-risk patients with open quality gaps?"* → ✅ 8 patients named from graph
2. *"Show me the attribution chain for member M-0042"* → ✅ PCP, network, attribution status returned
3. *"Which patients have both housing instability and uncontrolled diabetes?"* → ✅ Accurate (0 in sample)
4. *"What are the most common SDOH barriers?"* → ✅ Food (59), Social isolation (55), Transportation (45), Housing (41)
5. *"Which care managers have the highest gap closure rates?"* → ✅ Accurate response

---

## Vector search — OpenSearch

| Index | Docs | Model | Dimensions |
|---|---|---|---|
| `vbc-concepts-embeddings` | 136 | Titan Embed v2 | 1024 |
| `vbc-icd10-embeddings` | 18 | Titan Embed v2 | 1024 |
| `vbc-clinical-notes` | 0 (ready) | Titan Embed v2 | 1024 |

**Semantic similarity validated:**
- `"congestive heart failure"` → `I50.9` ✅
- `"sugar diabetes"` → `E11.65` (T2DM with hyperglycemia) ✅
- `"care gap quality measure"` → `vbc:CareGap` ✅

---

## Synthetic dataset

Generated by `data/loaders/generate_sample_data.py` (seed=42, fully reproducible).
**No real patient data is used anywhere in this repository.**

| Table | Rows | Notes |
|---|---|---|
| `member_master` | 500 | Realistic demographics, eligibility spans |
| `provider_master` | 50 | 30 PCPs + 20 specialists, 2 networks |
| `claims_medical` | 2,000 | 12% inpatient, 15% readmit flag |
| `diagnosis_history` | 3,000 | Weighted CHF/T2DM/COPD/HTN/CKD/Depression |
| `hedis_gaps` | 400 | 55% open / 35% closed / 10% excluded |
| `risk_scores` | 500 | 60% low / 25% moderate / 15% high |
| `sdoh_barriers` | 200 | Food / housing / transport / social categories |
| `pharmacy_claims` | 800 | PDC adherence metrics |

---

## Getting started

### Prerequisites

```bash
# Python dependencies
pip install boto3 faker pandas pyarrow rdflib requests requests-aws4auth

# AWS CLI configured with appropriate profile
export AWS_PROFILE="your-profile"
export AWS_REGION="ap-southeast-2"
```

### Regenerate synthetic data and reload

```bash
# 1. Generate Parquet files
python data/loaders/generate_sample_data.py

# 2. Register Athena tables
python data/loaders/load_iceberg.py

# 3. Generate ontology files from controlled vocabulary
python data/loaders/generate_ontology_files.py

# 4. Load ontology + instance data into Neptune (via Lambda relay)
python data/loaders/load_neptune_via_lambda.py

# 5. Embed concepts and ICD-10 codes into OpenSearch
python data/loaders/load_opensearch_vectors.py

# 6. Run competency question regression
python tests/sparql/run_all_cq.py
```

### Infrastructure (CDK)

CDK bootstrap was blocked by org SCP in the target account.
All infrastructure was provisioned directly via AWS CLI + boto3.
CDK stacks in `infra/lib/` document the target architecture for
accounts where CloudFormation is permitted.

---

## Implementation status

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Bootstrap — toolchain, S3, Glue, synthetic data | ✅ Complete |
| Phase 1 | L1+L2+L3: Storage, OpenSearch, Neptune | ✅ Complete |
| Phase 3/4 | Ontology pipeline + vector layer | ✅ Complete |
| Phase 5 | L5+L6: Bedrock KB + SPARQL reasoning API | ✅ Complete |
| Phase 6 | L7: Bedrock Agent (VBC Care Navigator) | ✅ Complete |
| Phase 2 | L3 Catalog: DataZone, Lake Formation | ⏳ `datazone.amazonaws.com` PassRole pending |
| Phase 7 | Governance: drift detection, audit trail | ⏳ Not started |

See [`execution-summary.md`](execution-summary.md) for the full implementation log.

---

## Key design decisions

1. **Neptune SPARQL only (no Gremlin)** — All data is stored as RDF triples in named graphs.
   The SPARQL endpoint supports OWL reasoning and SKOS thesaurus queries natively.
   Neptune's Gremlin endpoint is available but unused.

2. **Amazon Nova Pro over Claude** — Org SCP blocks Anthropic Marketplace subscriptions.
   Nova Pro uses the Bedrock Converse API and requires no Marketplace subscription.

3. **Bedrock Titan Embeddings v2 at 1024d** — Balances semantic quality and OpenSearch
   storage cost for PoC sizing.

4. **Template SPARQL fallback** — `sparql_bridge.py` includes keyword-matched SPARQL templates
   that activate if Bedrock is unavailable, ensuring the demo never fails silently.

5. **VPC-only Neptune** — All SPARQL queries route through `vbc-sparql-relay` Lambda using
   SigV4 signing. Neptune has no public endpoint.

6. **Ontology-first, data second** — Taxonomy, thesaurus, and OWL axioms were loaded into
   Neptune before any instance data. The schema graph and instance graph are kept in
   separate named graphs.

---

## Ontology namespace

`https://ontology.vbc.internal/vbc#`

All IRIs, SPARQL queries, named graphs, and Neptune node identifiers use this namespace.

---

## License

This repository contains synthetic data only. No real patient data is present.
The ontology, code, and architecture are provided for research and demonstration purposes.
