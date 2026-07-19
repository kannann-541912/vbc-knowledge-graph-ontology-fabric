# VBC Knowledge Graph · Ontology · Knowledge Fabric

**A research implementation of semantic knowledge infrastructure for Value-Based Care**

> Submitted as part of a PhD programme application in Knowledge Engineering /
> Semantic Web / Health Informatics.  
> Author: Kannan Velmurugiah · kannann.velmurugiah@mastechdigital.com

---

## Research Overview

This project investigates how a formally structured ontology pipeline can transform
fragmented healthcare data into a semantically coherent, machine-queryable knowledge
fabric — with direct applicability to Value-Based Care (VBC) outcomes analytics.

The central research question is:

> **Can a six-stage ontology pipeline — from controlled vocabulary to knowledge graph —
> provide sufficient semantic grounding for an AI agent to perform clinically meaningful
> reasoning over heterogeneous VBC data, without hallucination or loss of domain fidelity?**

The implementation tests this on a synthetic but clinically realistic dataset: 500 patients,
7,450 records across eight care domains, a 136-class OWL ontology, and a conversational
AI agent grounded in a live RDF knowledge graph on AWS.

---

## Theoretical Grounding — The Ontology Pipeline Framework

This work applies and extends the **Ontology Pipeline** framework developed by
**Jessica Talisman** at [ontologypipeline.com](https://www.ontologypipeline.com).

Talisman's framework articulates a six-stage progression from raw terminological
control to full semantic interoperability:

| Stage | Artefact | Role in this research |
|---|---|---|
| 1 · Controlled Vocabulary | `ontology/controlled_vocabulary.json` | 136 VBC terms, definitions, and domain assignments — the semantic bedrock |
| 2 · Metadata Schema | `ontology/metadata_standards.json` | DCAT / Dublin Core mappings across all 8 Glue tables |
| 3 · Taxonomy | `ontology/taxonomy.ttl` | `rdfs:subClassOf` hierarchy — 133 OWL classes across 12 VBC domains |
| 4 · Thesaurus | `ontology/thesaurus.ttl` | SKOS `altLabel`, `broader`, `narrower`, `exactMatch` (Patient ≡ Member, etc.) |
| 5 · Ontology | `ontology/vbc_ontology.owl` | Full OWL/RDF — 71 object properties, 150 data properties, 4 SWRL inference rules |
| 6 · Knowledge Graph | Neptune RDF quad store (37K triples) | All six prior stages unified and queryable via SPARQL |

The pipeline's core principle — *"Your data deserves structure. Your AI needs semantics"* —
is operationalised here by grounding a Bedrock Agent in a Neptune knowledge graph built
strictly bottom-up through each stage. No instance data enters the graph until stages 1–5
are validated. This ordering is a deliberate research constraint, not an implementation convenience.

### Contribution beyond the framework

The original framework is system-agnostic. This research makes three concrete extensions:

1. **Cloud-native instantiation** — each stage is mapped to a specific AWS managed service
   (Glue catalog, Neptune, OpenSearch, Bedrock), producing a deployable reference architecture
   for healthcare organisations.

2. **Hybrid retrieval** — stage 6 (knowledge graph) is augmented with a parallel vector index
   (OpenSearch + Titan Embeddings v2) enabling semantic similarity search alongside structured
   SPARQL reasoning, addressing the limitation of pure graph traversal for natural language queries.

3. **AI agent grounding** — the knowledge fabric is used as the sole factual grounding for a
   conversational agent (Bedrock Agent + Nova Pro), testing the hypothesis that ontology-backed
   graphs eliminate the hallucination problem for domain-constrained healthcare Q&A.

---

## System Architecture — 7-Layer Knowledge Fabric

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
| Governance | DataZone | 136-term business glossary |
| Embeddings | Bedrock Titan v2 | `amazon.titan-embed-text-v2:0` @ 1024d |
| NL→SPARQL | Bedrock Nova Pro | `amazon.nova-pro-v1:0` via Converse API |
| Agent | Bedrock Agent | `VBC-Care-Navigator` — 3 action groups |
| API | API Gateway | `POST /sparql`, `POST /query` |

---

## The Ontology — VBC Domain Model

**Namespace:** `https://ontology.vbc.internal/vbc#`  
**Standards coverage:** ICD-10-CM/PCS · CPT · HCPCS · LOINC · NDC · SNOMED CT · RxNorm · HL7 FHIR R4 · HEDIS 2024 · CMS Stars 2024

### Class hierarchy (12 VBC domains)

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
    │   ├── vbc:CardiovascularCondition    (CHF, CAD, AFib)
    │   ├── vbc:MetabolicCondition         (T2DM, Obesity, Dyslipidemia)
    │   ├── vbc:RespiratoryCondition       (COPD, Asthma)
    │   ├── vbc:RenalCondition             (CKD stages 1–5)
    │   └── vbc:MentalHealthCondition      (Depression, Anxiety)
    └── vbc:AcuteCondition
└── vbc:QualityMeasure
    ├── vbc:HEDISMeasure                   (CDC, CBP, COL, COA, ...)
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
    └── vbc:OutpatientEncounter · vbc:TelehealthEncounter
└── vbc:Organization
    ├── vbc:ACO · vbc:HealthSystem
    └── vbc:MedicalGroup · vbc:Practice
```

### Key object properties

| Property | Domain | Range | Axiom |
|---|---|---|---|
| `vbc:hasDiagnosis` | Patient | PatientDiagnosis | — |
| `vbc:hasPCP` | Patient | PrimaryCarePhysician | Functional (exactly 1) |
| `vbc:hasCareGap` | Patient | CareGap | — |
| `vbc:hasRiskScore` | Patient | RiskFactor | — |
| `vbc:hasSDOHBarrier` | Patient | SDOHBarrier | — |
| `vbc:mapsToHCC` | PatientDiagnosis | HCCCode | — |
| `vbc:diagnosedWith` | PatientDiagnosis | Condition | — |
| `vbc:belongsToNetwork` | Provider | Organization | — |

### SWRL inference rules

```
Rule 1: hasRiskScore > 0.85 → HighRiskPatient
Rule 2: hasCondition(Diabetes) ∧ ¬hasLabResult(HbA1c) → hasQualityGap(DiabetesHbA1cGap)
Rule 3: readmission within 30 days → hasReadmission(true)
Rule 4: ADI score > 80 → hasSDOHBarrier(HighDeprivationArea)
```

---

## Knowledge Graph — Neptune RDF Quad Store

All data is stored as RDF triples in named graphs. Neptune's SPARQL endpoint
is VPC-only; all queries route through the `vbc-sparql-relay` Lambda.

| Named graph | Contents |
|---|---|
| `https://ontology.vbc.internal/vbc/schema` | Ontology — taxonomy, thesaurus, OWL axioms (1,524 triples) |
| `https://ontology.vbc.internal/vbc/instances` | Instance data — all patient, provider, and clinical nodes (35,524 triples) |

**Graph statistics**

| Entity type | Nodes | Triples |
|---|---|---|
| Patient | 500 | 4,000 |
| Provider (PCP + Specialist) | 50 | 300 |
| PatientDiagnosis | 3,000 | 20,284 |
| CareGap (Open / Closed / Excluded) | 400 | 2,140 |
| RiskFactor | 500 | 3,000 |
| SDOHBarrier | 200 | 1,200 |
| **Total (instances)** | | **30,924** |
| Schema (ontology triples) | | 1,524 |
| **Grand total** | | **37,048** |

---

## SPARQL Competency Questions

Five competency questions were defined *before* any ontology class was declared —
following the principle that a class with no answerable query has no justified existence.
All five return non-empty results against live Neptune data.

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

**CQ2** — Attribution chain: Patient → PCP → network  
**CQ3** — SDOH barrier type correlated with ED encounter count  
**CQ4** — ICD-10 code → HCC category → RAF weight traversal  
**CQ5** — Care plan task completion driving quality gap closure  

Run the full regression suite:

```bash
python tests/sparql/run_all_cq.py
```

---

## NL → SPARQL → Neptune Pipeline

The `vbc-sparql-bridge` Lambda translates natural language questions to SPARQL
using Amazon Nova Pro (Bedrock Converse API), executes against Neptune, and returns
structured results. A keyword-matched template fallback ensures reliability when
the model is unavailable.

```
User question
    ↓
Nova Pro (Bedrock Converse API) + bridge_prompt.txt
    ↓ generated SPARQL
vbc-sparql-relay Lambda (VPC, SigV4-signed)
    ↓
Neptune SPARQL endpoint
    ↓
JSON-LD bindings → caller
```

**API Gateway endpoints**

```
POST /sparql   →  vbc-sparql-bridge   (NL → SPARQL → Neptune)
POST /query    →  vbc-hybrid-query    (Neptune + OpenSearch KNN merged)
```

---

## Bedrock Agent — VBC Care Navigator

A conversational agent grounded entirely in the knowledge graph.
The agent cannot answer from parametric memory alone — every factual claim
is retrieved from Neptune (via SPARQL) or OpenSearch (via KNN vector search).

**Action groups**

| Action group | Lambda | Purpose |
|---|---|---|
| `sparql_query` | `vbc-sparql-bridge` | NL → SPARQL → Neptune results |
| `semantic_search` | `vbc-hybrid-query` | Vector + graph hybrid search |
| `get_patient_360` | `vbc-get-patient-360` | Full patient view from Athena |

**Validation results** (against live graph data)

| Question | Result |
|---|---|
| *"Who are the top 10 highest-risk patients with open quality gaps?"* | ✅ 8 patients named from graph |
| *"Show me the attribution chain for member M-0042"* | ✅ PCP, network, attribution status |
| *"Which patients have both housing instability and uncontrolled diabetes?"* | ✅ Accurate (0 in this cohort) |
| *"What are the most common SDOH barriers in this population?"* | ✅ Food (59), Social isolation (55), Transportation (45), Housing (41) |
| *"Which care managers have the highest gap closure rates?"* | ✅ Accurate — care managers not in synthetic cohort |

---

## Vector Search — OpenSearch

Parallel to the graph, all ontology concepts and ICD-10 codes are embedded using
Amazon Titan Embeddings v2 (1024 dimensions, HNSW cosine similarity) to enable
semantic search over natural language queries that do not resolve to exact graph patterns.

| Index | Documents | Notes |
|---|---|---|
| `vbc-concepts-embeddings` | 136 | All OWL class labels + definitions |
| `vbc-icd10-embeddings` | 18 | Condition codes used in the synthetic cohort |
| `vbc-clinical-notes` | 0 (mapped, ready) | Unstructured text chunks |

**Semantic similarity validated:**

| Query | Top result | Correct? |
|---|---|---|
| `"congestive heart failure"` | `I50.9` | ✅ |
| `"sugar diabetes"` | `E11.65` (T2DM with hyperglycemia) | ✅ |
| `"care gap quality measure"` | `vbc:CareGap` | ✅ |

---

## Synthetic Dataset

Generated by `data/loaders/generate_sample_data.py` (seed=42, fully reproducible).
**No real patient data is used anywhere in this repository.**

| Table | Rows | Notes |
|---|---|---|
| `member_master` | 500 | Realistic demographics, eligibility spans |
| `provider_master` | 50 | 30 PCPs + 20 specialists, 2 provider networks |
| `claims_medical` | 2,000 | 12% inpatient, 15% readmission flag |
| `diagnosis_history` | 3,000 | Weighted: CHF · T2DM · COPD · HTN · CKD · Depression |
| `hedis_gaps` | 400 | 55% open / 35% closed / 10% excluded |
| `risk_scores` | 500 | 60% low / 25% moderate / 15% high |
| `sdoh_barriers` | 200 | Food / housing / transportation / social isolation |
| `pharmacy_claims` | 800 | PDC adherence metrics |

---

## Repository Structure

```
.
├── ontology/
│   ├── controlled_vocabulary.json   # Stage 1 — 136 VBC terms
│   ├── metadata_standards.json      # Stage 2 — DCAT/Dublin Core mappings
│   ├── taxonomy.ttl                 # Stage 3 — rdfs:subClassOf hierarchy
│   ├── thesaurus.ttl                # Stage 4 — SKOS synonyms and relations
│   ├── vbc_ontology.owl             # Stage 5 — full OWL/RDF ontology
│   ├── governance/change_log.md     # Ontology change history
│   └── sparql/                      # Competency questions + NL→SPARQL prompt
├── functions/
│   ├── sparql_relay.py              # VPC Lambda — SigV4-signed Neptune relay
│   ├── sparql_bridge.py             # NL→SPARQL via Nova Pro + template fallback
│   ├── hybrid_query.py              # Neptune + OpenSearch merged results
│   └── get_patient_360.py           # Full patient view via Athena
├── data/loaders/                    # All ETL and generation scripts
├── infra/lib/                       # CDK stacks (TypeScript) — L1 through L7
├── tests/
│   ├── sparql/run_all_cq.py         # Competency question regression suite
│   └── integration/check_graph_integrity.py
├── notebooks/                       # Phase validation notebooks
└── project-logs/                    # Phase configs, deploy scripts, execution summaries
```

---

## Getting Started

```bash
# Dependencies
pip install boto3 faker pandas pyarrow rdflib requests requests-aws4auth

# AWS credentials
export AWS_PROFILE="your-profile"
export AWS_REGION="ap-southeast-2"

# Regenerate and reload from scratch
python data/loaders/generate_sample_data.py
python data/loaders/load_iceberg.py
python data/loaders/generate_ontology_files.py
python data/loaders/load_neptune_via_lambda.py
python data/loaders/load_opensearch_vectors.py

# Validate
python tests/sparql/run_all_cq.py
```

---

## Implementation Status

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Bootstrap — toolchain, S3, Glue, synthetic data | ✅ Complete |
| Phase 1 | L1+L2+L3: Storage, OpenSearch, Neptune | ✅ Complete |
| Phase 3/4 | Ontology pipeline + vector layer | ✅ Complete |
| Phase 5 | L5+L6: Bedrock KB + SPARQL reasoning API | ✅ Complete |
| Phase 6 | L7: Bedrock Agent (VBC Care Navigator) | ✅ Complete |
| Phase 2 | L3 Catalog: DataZone, Lake Formation | ⏳ IAM PassRole pending |
| Phase 7 | Governance: drift detection, audit trail | ⏳ Not started |

See [`project-logs/execution-summary.md`](project-logs/execution-summary.md) for the full implementation log.

---

## Key Design Decisions

1. **Ontology-first, data second** — No instance data enters Neptune until all five pipeline
   stages (vocabulary through OWL) are loaded and validated. This enforces semantic integrity
   as a hard constraint rather than a retrospective quality check.

2. **Neptune SPARQL only (no Gremlin)** — All data is stored as RDF triples in named graphs.
   The SPARQL endpoint supports OWL reasoning and SKOS thesaurus queries natively.
   Neptune's Gremlin endpoint is available but intentionally unused — property graphs do not
   carry ontology semantics.

3. **Competency-question-driven class design** — Before any OWL class was declared, a SPARQL
   query it must answer was written. Classes that produced no answerable query were not added.

4. **Agent grounding over parametric memory** — The Bedrock Agent's instruction explicitly
   forbids fabricating patient data. Every answer must be sourced from the Neptune graph or
   OpenSearch index via the action group tools.

5. **Hybrid retrieval** — Pure SPARQL graph traversal cannot resolve natural language synonyms
   or paraphrases. The parallel vector index (OpenSearch + Titan Embeddings) handles the
   semantic similarity layer, with results merged at the `vbc-hybrid-query` Lambda.

---

## Inspiration and Reference

This research is directly inspired by the **Ontology Pipeline** methodology articulated by
**Jessica Talisman** at [ontologypipeline.com](https://www.ontologypipeline.com).

Talisman's framework establishes that meaningful AI integration requires a deliberate
progression through six knowledge engineering stages — from standardised vocabulary to
formal ontology to knowledge graph — rather than the common practice of applying machine
learning directly to unstructured or loosely structured data. The guiding principle,
*"Distilling data into information to form knowledge is how humans and machines can truly
connect,"* is the intellectual foundation for every architectural decision in this project.

This implementation serves as a domain-specific validation of that framework in the
healthcare and Value-Based Care context, with the AWS Knowledge Fabric Stack as the
deployment substrate.

---

## License

This repository contains synthetic data only. No real patient data is present.
The ontology, code, and architecture are provided for research purposes.
