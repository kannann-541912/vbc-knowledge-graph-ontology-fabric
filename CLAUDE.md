# VBC Ontology — AWS Knowledge Fabric Stack
## Implementation Plan for Claude Code

> **How to use this file**: Place it in your project root as `CLAUDE.md`.
> Claude Code reads this automatically on every session. Run phases in order.
> Each phase ends with a validation gate — do not proceed until it passes.

---

## Project context

The source ontology is `VBC_Ontology_v1_1.docx` in this project. It defines:
- 12 core VBC domains (Patient, Provider, Clinical, Claims, Pharmacy, Quality,
  Risk, SDOH, Financial, Care Management, Utilization Management, AI/ML)
- 133 OWL classes, 71 object properties, 150 data properties, 86 named individuals
- IRI namespace: `https://ontology.vbc.internal/vbc#`
- Industry standards: ICD-10-CM/PCS, CPT, HCPCS, LOINC, NDC, SNOMED CT,
  RxNorm, HL7 FHIR R4, HEDIS 2024, CMS Stars 2024

The target architecture is the 7-layer Knowledge Fabric Stack on AWS:
- L1 Source → L2 Storage → L3 Catalog → L4 Ontology Pipeline
  → L5 Knowledge Layer → L6 Access & Reasoning → L7 Agent

---

## MCP servers required

Install these before starting. Run each command, then verify with `claude mcp list`.

```bash
# 0. Environment — set once, add to ~/.zshrc or ~/.bashrc
export AWS_PROFILE="your-profile"
export AWS_REGION="us-east-1"
export MCP_LOG_LEVEL="ERROR"
export VBC_ENV="poc"

# 1. AWS core — MUST be first
claude mcp add awslabs.core-mcp-server \
  -e FASTMCP_LOG_LEVEL=$MCP_LOG_LEVEL \
  -- uvx awslabs.core-mcp-server@latest

# 2. AWS main API (Neptune, S3, Glue, OpenSearch, Bedrock, DataZone)
claude mcp add awslabs.aws-mcp-server \
  -e AWS_PROFILE=$AWS_PROFILE \
  -e AWS_REGION=$AWS_REGION \
  -- uvx awslabs.aws-mcp-server@latest

# 3. IaC — CDK and CloudFormation authoring and validation
claude mcp add awslabs.cdk-mcp-server \
  -e FASTMCP_LOG_LEVEL=$MCP_LOG_LEVEL \
  -e AWS_PROFILE=$AWS_PROFILE \
  -e AWS_REGION=$AWS_REGION \
  -- uvx awslabs.cdk-mcp-server@latest

# 4. Cost visibility — keep PoC spend in check
claude mcp add awslabs.cost-analysis-mcp-server \
  -e AWS_PROFILE=$AWS_PROFILE \
  -- uvx awslabs.cost-analysis-mcp-server@latest

# 5. Documentation search — always get current AWS docs
claude mcp add awslabs.aws-documentation-mcp-server \
  -e FASTMCP_LOG_LEVEL=$MCP_LOG_LEVEL \
  -- uvx awslabs.aws-documentation-mcp-server@latest
```

---

## Repository layout

Claude Code should scaffold this structure at the start of Phase 0:

```
vbc-knowledge-fabric/
├── CLAUDE.md                        ← this file
├── VBC_Ontology_v1_1.docx           ← source ontology (read-only reference)
├── infra/                           ← CDK stacks (one per layer)
│   ├── cdk.json
│   ├── bin/app.ts
│   ├── lib/
│   │   ├── l1-storage-stack.ts      ← S3, Iceberg, DocumentDB
│   │   ├── l2-vector-stack.ts       ← OpenSearch vector engine
│   │   ├── l3-graph-stack.ts        ← Neptune cluster
│   │   ├── l4-catalog-stack.ts      ← Glue, DataZone
│   │   ├── l5-ontology-stack.ts     ← Neptune RDF + vocabulary load
│   │   ├── l6-reasoning-stack.ts    ← Bedrock KB, Lambda SPARQL bridge
│   │   └── l7-agent-stack.ts        ← Bedrock Agent
│   └── package.json
├── ontology/
│   ├── controlled_vocabulary.json   ← generated from docx in Phase 1
│   ├── metadata_standards.json      ← DCAT / Dublin Core mappings
│   ├── taxonomy.ttl                 ← is-a hierarchy in Turtle RDF
│   ├── thesaurus.ttl                ← SKOS broader/narrower/exactMatch
│   ├── vbc_ontology.owl             ← full OWL/RDF ontology
│   └── sparql/                      ← reasoning queries
│       ├── high_risk_inference.rq
│       ├── gap_detection.rq
│       └── readmission_risk.rq
├── data/
│   ├── sample/                      ← synthetic PoC data (generated)
│   │   ├── members.parquet
│   │   ├── providers.parquet
│   │   ├── claims.parquet
│   │   ├── diagnoses.parquet
│   │   ├── hedis_gaps.parquet
│   │   └── risk_scores.parquet
│   └── loaders/                     ← Python scripts to load each layer
│       ├── load_iceberg.py
│       ├── load_neptune_nodes.py
│       ├── load_neptune_edges.py
│       ├── load_opensearch_vectors.py
│       └── load_datazone_glossary.py
├── notebooks/                       ← validation notebooks
│   ├── phase1_validate.ipynb
│   ├── phase2_validate.ipynb
│   ├── phase3_validate.ipynb
│   └── phase4_validate.ipynb
└── tests/
    ├── sparql/                      ← competency question tests
    └── integration/
```

---

## Phase 0 — Bootstrap (Day 1, ~2 hours)

**Goal**: AWS account ready, CDK bootstrapped, repo scaffolded.

### Steps for Claude Code

1. Verify AWS credentials and region:
   ```bash
   aws sts get-caller-identity
   aws configure get region
   ```

2. Bootstrap CDK in target account/region:
   ```bash
   cdk bootstrap aws://ACCOUNT_ID/us-east-1
   ```

3. Scaffold the repo layout shown above. Create all directories and empty
   placeholder files. Do not create CDK stacks yet.

4. Parse `VBC_Ontology_v1_1.docx` and extract the following into
   `ontology/controlled_vocabulary.json`:
   - All 133 OWL class names with their domain, label, and definition
   - All 71 object property names with domain and range classes
   - All 86 named individuals with their type and key attributes
   - The 12 domain names with their entity lists

   Format:
   ```json
   {
     "classes": [
       {
         "id": "vbc:Patient",
         "label": "Patient",
         "domain": "Patient/Member",
         "definition": "Individual receiving healthcare services",
         "subClassOf": "vbc:Person"
       }
     ],
     "objectProperties": [...],
     "individuals": [...],
     "domains": [...]
   }
   ```

5. Check estimated monthly cost for PoC sizing using the cost MCP server.
   Target: under $300/month. Use these instance sizes:
   - Neptune: `db.t3.medium` (single instance, no replica)
   - OpenSearch: `t3.small.search` (1 node, 20GB EBS)
   - Glue: serverless crawlers only
   - Bedrock: on-demand (no provisioned throughput)

### Validation gate 0
- [ ] `aws sts get-caller-identity` returns correct account
- [ ] `cdk bootstrap` completed without error
- [ ] `ontology/controlled_vocabulary.json` exists and contains ≥130 classes
- [ ] Estimated monthly cost confirmed under $300

---

## Phase 1 — L1 + L2: Physical Storage (Days 2–4)

**Goal**: S3 Iceberg tables for structured data, OpenSearch for vectors,
Neptune cluster up. All three stores empty but ready to receive data.

### 1a. CDK stack: `l1-storage-stack.ts`

Provision:
- S3 bucket: `vbc-poc-{account-id}` with versioning enabled
- S3 prefixes: `/raw/`, `/iceberg/`, `/ontology/`, `/embeddings/`
- Glue database: `vbc_poc_db`
- Glue catalog: Iceberg table format (use `ICEBERG` table type in Glue)
- Tables to create as Iceberg:
  - `member_master` — from Domain 1 schema in ontology docx
  - `provider_master` — from Domain 2
  - `claims_medical` — from Domain 4
  - `diagnosis_history` — from Domain 3
  - `hedis_gaps` — from Domain 6
  - `risk_scores` — from Domain 7
  - `sdoh_barriers` — from Domain 8
  - `pharmacy_claims` — from Domain 5

  Column definitions must match the attribute lists in
  `VBC_Ontology_v1_1.docx` sections 3.1–3.8 exactly.

- DocumentDB cluster (t3.medium): `vbc-docdb-poc` for semi-structured
  clinical notes and FHIR bundles

### 1b. CDK stack: `l2-vector-stack.ts`

Provision:
- OpenSearch domain: `vbc-vectors-poc`
  - Engine: OpenSearch 2.x
  - Instance: `t3.small.search`, 1 node, 20GB gp3
  - Enable vector engine plugin (`knn` enabled)
- Indices to create after cluster is up:
  - `vbc-icd10-embeddings` — ICD-10 code + description vectors
  - `vbc-concepts-embeddings` — ontology class label + definition vectors
  - `vbc-clinical-notes` — unstructured text chunk vectors

### 1c. CDK stack: `l3-graph-stack.ts`

Provision:
- Neptune cluster: `vbc-neptune-poc`
  - Instance: `db.t3.medium`
  - Enable both Gremlin and SPARQL endpoints
  - Enable Neptune notebook (SageMaker-linked) for interactive queries
- Neptune IAM role for Bedrock access (for later Phase 4)
- VPC: dedicated `/24` VPC with private subnets for Neptune + OpenSearch,
  public subnet for bastion/jump host (t3.micro)

### 1d. Generate synthetic sample data

Write `data/loaders/generate_sample_data.py` that creates:
- 500 synthetic members (realistic demographics, eligibility spans)
- 50 providers (mix of PCPs and specialists, 2 networks)
- 2,000 medical claims (6 months of data, varied DRGs and CPTs)
- 3,000 diagnoses (weighted toward CHF, T2DM, COPD, HTN, CKD)
- 400 HEDIS gaps (CDC, CBP, COL measures — mix of open/closed)
- 500 risk scores (distribution: 60% low, 25% moderate, 15% high)
- 200 SDOH barriers (housing, food, transportation categories)

Use Python `faker` library. All IDs must be consistent across tables
(member_id in claims must exist in member_master, etc.).

Load into Iceberg tables using `data/loaders/load_iceberg.py` with
AWS Glue job or local `awswrangler` + Athena.

### Validation gate 1
- [ ] All 3 CDK stacks deploy without error
- [ ] `aws glue get-tables --database-name vbc_poc_db` returns 8 tables
- [ ] Athena query `SELECT COUNT(*) FROM vbc_poc_db.member_master` returns 500
- [ ] OpenSearch cluster status is GREEN
- [ ] Neptune cluster status is AVAILABLE, both Gremlin + SPARQL endpoints reachable
- [ ] Bastion host reachable via SSM Session Manager

---

## Phase 2 — L3: Catalog, Governance, Lineage (Days 5–7)

**Goal**: DataZone domain with VBC business glossary (controlled vocabulary),
Glue crawlers keeping schema registry current, Lake Formation access policies.

### 2a. CDK stack: `l4-catalog-stack.ts`

Provision:
- AWS DataZone domain: `VBC-Knowledge-Fabric-PoC`
- DataZone project: `VBC-Ontology-PoC`
- Lake Formation: register S3 bucket as data lake location,
  grant SELECT to DataZone execution role on all Glue tables
- Glue crawlers: one per Iceberg table, schedule hourly

### 2b. Load controlled vocabulary into DataZone glossary

Write `data/loaders/load_datazone_glossary.py` that reads
`ontology/controlled_vocabulary.json` and for each class creates a
DataZone glossary term with:
- `name`: class label (e.g. "Patient", "HCC Code", "Quality Gap")
- `shortDescription`: definition from ontology
- `longDescription`: domain context + related terms
- `status`: ENABLED

Priority terms to load first (these are the core VBC vocabulary):
Patient, Member, Provider, PCP, Specialist, Diagnosis, HCC_Code,
RAF_Score, Quality_Measure, HEDIS_Measure, CMS_Star, Care_Gap,
SDOH_Barrier, Risk_Score, Care_Plan, Attribution, PMPM, TCOC,
PDC_Measure, Prior_Authorization

After loading terms, link each term to its corresponding Glue table asset
in DataZone using the `create_asset` and `create_glossary_term_relationship`
APIs.

### 2c. Generate metadata standards mapping

Write `ontology/metadata_standards.json` mapping each Glue table column
to Dublin Core and DCAT metadata elements where applicable. Example:
```json
{
  "member_master.member_id": {
    "dc:identifier": true,
    "dcat:Dataset": "member_master",
    "owl_property": "vbc:mrn"
  },
  "member_master.dob": {
    "dc:date": true,
    "owl_property": "vbc:dateOfBirth",
    "xsd_type": "xsd:date"
  }
}
```

### Validation gate 2
- [ ] DataZone domain is ACTIVE
- [ ] Glossary contains ≥ 130 terms matching OWL class names
- [ ] At least 20 terms are linked to Glue table assets
- [ ] All Glue crawlers run successfully, schema registry shows correct columns
- [ ] Lake Formation `DESCRIBE` permissions working for test IAM user
- [ ] `ontology/metadata_standards.json` covers all 8 core tables

---

## Phase 3 — L4: Ontology Pipeline (Days 8–14)

**Goal**: Build all 5 ontology pipeline stages progressively in Neptune,
then verify with SPARQL competency questions.

This is the core semantic build. Take it one stage at a time.

### 3a. Stage 1 + 2 already done in Phase 2
Controlled vocabulary → DataZone glossary ✓
Metadata standards → `metadata_standards.json` ✓

### 3b. Stage 3: Taxonomy — `ontology/taxonomy.ttl`

Generate a Turtle RDF file encoding the full is-a hierarchy from the
ontology docx. Key hierarchies to encode:

```turtle
@prefix vbc: <https://ontology.vbc.internal/vbc#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

vbc:Patient rdfs:subClassOf vbc:Person .
vbc:Member rdfs:subClassOf vbc:Person .
vbc:CareTeamMember rdfs:subClassOf vbc:Person .
vbc:Physician rdfs:subClassOf vbc:CareTeamMember .
vbc:PrimaryCarePhysician rdfs:subClassOf vbc:Physician .
vbc:Specialist rdfs:subClassOf vbc:Physician .
vbc:CareManager rdfs:subClassOf vbc:CareTeamMember .
vbc:SocialWorker rdfs:subClassOf vbc:CareTeamMember .
vbc:ChronicCondition rdfs:subClassOf vbc:Condition .
vbc:AcuteCondition rdfs:subClassOf vbc:Condition .
vbc:CardiovascularCondition rdfs:subClassOf vbc:ChronicCondition .
vbc:MetabolicCondition rdfs:subClassOf vbc:ChronicCondition .
vbc:RespiratoryCondition rdfs:subClassOf vbc:ChronicCondition .
vbc:RenalCondition rdfs:subClassOf vbc:ChronicCondition .
vbc:MentalHealthCondition rdfs:subClassOf vbc:ChronicCondition .
vbc:HEDISMeasure rdfs:subClassOf vbc:QualityMeasure .
vbc:CMSStarMeasure rdfs:subClassOf vbc:QualityMeasure .
vbc:CustomMeasure rdfs:subClassOf vbc:QualityMeasure .
vbc:OpenCareGap rdfs:subClassOf vbc:CareGap .
vbc:ClosedCareGap rdfs:subClassOf vbc:CareGap .
vbc:ExcludedCareGap rdfs:subClassOf vbc:CareGap .
vbc:InpatientEncounter rdfs:subClassOf vbc:Encounter .
vbc:EDEncounter rdfs:subClassOf vbc:Encounter .
vbc:OutpatientEncounter rdfs:subClassOf vbc:Encounter .
vbc:TelehealthEncounter rdfs:subClassOf vbc:Encounter .
vbc:HighAlertMedication rdfs:subClassOf vbc:Medication .
vbc:FoodInsecurityBarrier rdfs:subClassOf vbc:SDOHBarrier .
vbc:HousingInstabilityBarrier rdfs:subClassOf vbc:SDOHBarrier .
vbc:TransportationBarrier rdfs:subClassOf vbc:SDOHBarrier .
vbc:SocialIsolationBarrier rdfs:subClassOf vbc:SDOHBarrier .
vbc:HealthSystem rdfs:subClassOf vbc:Organization .
vbc:ACO rdfs:subClassOf vbc:Organization .
vbc:MedicalGroup rdfs:subClassOf vbc:Organization .
vbc:Practice rdfs:subClassOf vbc:Organization .
```

All 133 classes must appear. Load into Neptune via SPARQL LOAD:
```bash
aws neptune-db execute-fast-reset ...  # only if reloading
# Upload taxonomy.ttl to S3, then:
aws neptune-db start-loader-job \
  --source s3://vbc-poc-{account}/ontology/taxonomy.ttl \
  --format turtle \
  --iam-role-arn arn:aws:iam::ACCOUNT:role/NeptuneLoadRole
```

### 3c. Stage 4: Thesaurus — `ontology/thesaurus.ttl`

Generate SKOS thesaurus encoding synonyms and related terms. Cover:

```turtle
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix vbc: <https://ontology.vbc.internal/vbc#> .

vbc:Patient skos:exactMatch vbc:Member ;
  skos:altLabel "Beneficiary", "Enrollee", "Covered Life" ;
  skos:related vbc:CareTeamMember .

vbc:PrimaryCarePhysician skos:altLabel "PCP", "Primary Care Doctor",
  "Attending Physician" ;
  skos:narrower vbc:FamilyMedicine ;
  skos:broader vbc:Physician .

vbc:HCCCode skos:altLabel "HCC", "Hierarchical Condition Category",
  "Risk Adjustment Category" ;
  skos:related vbc:RAFScore, vbc:RiskScore .

vbc:QualityMeasure skos:altLabel "Quality Metric", "Performance Measure" ;
  skos:narrower vbc:HEDISMeasure, vbc:CMSStarMeasure .

vbc:CareGap skos:altLabel "Quality Gap", "Open Gap", "Measure Gap" ;
  skos:related vbc:QualityMeasure .

vbc:PMPM skos:altLabel "Per Member Per Month",
  "Capitation Rate", "Monthly Premium" ;
  skos:related vbc:VBCContract, vbc:TCOC .

vbc:SDOHBarrier skos:altLabel "Social Barrier", "Social Need",
  "Non-Clinical Barrier", "Z-Code Factor" ;
  skos:narrower vbc:FoodInsecurityBarrier,
    vbc:HousingInstabilityBarrier,
    vbc:TransportationBarrier .

vbc:Attribution skos:altLabel "Panel Assignment",
  "Patient Attribution", "Attributed Patient" ;
  skos:related vbc:PrimaryCarePhysician .
```

Load `thesaurus.ttl` into Neptune via the same S3 loader pattern.

### 3d. Stage 5: Full OWL ontology — `ontology/vbc_ontology.owl`

Generate a complete OWL/XML file combining taxonomy + thesaurus +
all 71 object properties + 150 data properties from the docx.

Key object properties to encode:
```xml
<!-- Patient → Condition -->
<ObjectProperty IRI="vbc:hasDiagnosis">
  <Domain IRI="vbc:Patient"/>
  <Range IRI="vbc:PatientDiagnosis"/>
</ObjectProperty>

<!-- Patient → CareTeamAssignment -->
<ObjectProperty IRI="vbc:hasCareTeamAssignment">
  <Domain IRI="vbc:Patient"/>
  <Range IRI="vbc:CareTeamAssignment"/>
</ObjectProperty>

<!-- Patient → PCP (functional — exactly 1) -->
<FunctionalObjectProperty IRI="vbc:hasPCP"/>
<ObjectProperty IRI="vbc:hasPCP">
  <Domain IRI="vbc:Patient"/>
  <Range IRI="vbc:PrimaryCarePhysician"/>
</ObjectProperty>

<!-- Patient → QualityGap -->
<ObjectProperty IRI="vbc:hasCareGap">
  <Domain IRI="vbc:Patient"/>
  <Range IRI="vbc:CareGap"/>
</ObjectProperty>

<!-- Patient → RiskScore -->
<ObjectProperty IRI="vbc:hasRiskScore">
  <Domain IRI="vbc:Patient"/>
  <Range IRI="vbc:RiskFactor"/>
</ObjectProperty>

<!-- PatientDiagnosis → Condition -->
<ObjectProperty IRI="vbc:diagnosedWith">
  <Domain IRI="vbc:PatientDiagnosis"/>
  <Range IRI="vbc:Condition"/>
</ObjectProperty>

<!-- PatientDiagnosis → HCCCode -->
<ObjectProperty IRI="vbc:mapsToHCC">
  <Domain IRI="vbc:PatientDiagnosis"/>
  <Range IRI="vbc:HCCCode"/>
</ObjectProperty>
```

Include all SWRL inference rules from the ontology docx Section 8.2:
- Rule 1: `hasRiskScore > 0.85 → HighRiskPatient`
- Rule 2: `hasCondition(Diabetes) AND NOT hasLabResult(HbA1c) → hasQualityGap(DiabetesHbA1cGap)`
- Rule 3: Readmission within 30 days → `hasReadmission(true)`
- Rule 4: ADI score > 80 → `hasSDOHBarrier(HighDeprivationArea)`

Load `vbc_ontology.owl` into Neptune SPARQL endpoint via S3 loader.

### 3e. Stage 6: Populate the knowledge graph with sample data

Write `data/loaders/load_neptune_nodes.py` and
`data/loaders/load_neptune_edges.py`.

**Node loading strategy** — read from Iceberg/Athena, write to Neptune:

For each member in `member_master`:
```sparql
INSERT DATA {
  GRAPH <https://ontology.vbc.internal/vbc/instances> {
    vbc:Patient_{MEMBER_ID} a vbc:Patient ;
      vbc:mrn "{MEMBER_ID}" ;
      vbc:dateOfBirth "{DOB}"^^xsd:date ;
      vbc:sex "{GENDER}" ;
      vbc:preferredLanguage "{PREFERRED_LANGUAGE}" .
  }
}
```

For each diagnosis in `diagnosis_history`:
```sparql
INSERT DATA {
  GRAPH <https://ontology.vbc.internal/vbc/instances> {
    vbc:PatientDiagnosis_{DIAGNOSIS_ID} a vbc:PatientDiagnosis ;
      vbc:icd10CodeValue "{ICD10_CM_CODE}" ;
      vbc:diagnosisDate "{DIAGNOSIS_DATE}"^^xsd:date .
  }
}
```

**Edge loading strategy** — link nodes with typed relationships:
```sparql
INSERT DATA {
  GRAPH <https://ontology.vbc.internal/vbc/instances> {
    vbc:Patient_{MEMBER_ID} vbc:hasDiagnosis
      vbc:PatientDiagnosis_{DIAGNOSIS_ID} .
  }
}
```

Load all 12 entity types. Minimum graph at end of phase:
- ≥ 500 Patient nodes
- ≥ 50 Provider nodes
- ≥ 3,000 PatientDiagnosis nodes
- ≥ 400 CareGap nodes
- ≥ 500 RiskFactor nodes
- ≥ 200 SDOHBarrier nodes
- ≥ 5,000 total edges

### 3f. SPARQL competency questions — `ontology/sparql/`

Write and test these 5 queries. All must return results.

**CQ1 — High-risk patients with open diabetes gaps:**
```sparql
PREFIX vbc: <https://ontology.vbc.internal/vbc#>

SELECT ?patient ?riskScore ?gap
WHERE {
  ?patient a vbc:Patient ;
    vbc:hasRiskScore ?rs ;
    vbc:hasCareGap ?gap .
  ?rs vbc:riskScoreValue ?riskScore .
  ?gap a vbc:OpenCareGap ;
    vbc:forMeasure ?measure .
  ?measure vbc:measureName "CDC-HbA1c9" .
  FILTER (?riskScore > 0.75)
}
ORDER BY DESC(?riskScore)
```

**CQ2 — Attribution chain (patient → PCP → network → contract):**
```sparql
SELECT ?patient ?pcp ?pcpName ?network
WHERE {
  ?patient a vbc:Patient ;
    vbc:hasPCP ?pcp .
  ?pcp vbc:hasFullName ?pcpName ;
    vbc:belongsToNetwork ?network .
}
```

**CQ3 — SDOH barriers correlated with ED visits:**
```sparql
SELECT ?barrierType (COUNT(?enc) AS ?edCount)
WHERE {
  ?patient vbc:hasSDOHBarrier ?barrier ;
    vbc:hasEncounter ?enc .
  ?barrier vbc:barrierType ?barrierType .
  ?enc a vbc:EDEncounter .
}
GROUP BY ?barrierType
ORDER BY DESC(?edCount)
```

**CQ4 — HCC to risk score chain:**
```sparql
SELECT ?patient ?icd10 ?hcc ?rafWeight
WHERE {
  ?patient vbc:hasDiagnosis ?dx .
  ?dx vbc:icd10CodeValue ?icd10 ;
    vbc:mapsToHCC ?hccNode .
  ?hccNode vbc:hccCode ?hcc ;
    vbc:rafWeight ?rafWeight .
}
ORDER BY DESC(?rafWeight)
LIMIT 20
```

**CQ5 — Care plan task closure driving gap closure:**
```sparql
SELECT ?patient ?task ?gap ?closureDate
WHERE {
  ?patient vbc:hasCareGap ?gap ;
    vbc:hasCarePlan ?plan .
  ?gap a vbc:ClosedCareGap ;
    vbc:gapCloseDate ?closureDate .
  ?plan vbc:includesTask ?task .
  ?task vbc:completionStatus "Completed" .
}
```

### Validation gate 3
- [ ] `ontology/taxonomy.ttl` loaded — Neptune SPARQL returns ≥ 130 triples
      for `SELECT * WHERE { ?s rdfs:subClassOf ?o }`
- [ ] `ontology/thesaurus.ttl` loaded — ≥ 40 `skos:altLabel` triples present
- [ ] `ontology/vbc_ontology.owl` loaded — all 71 object properties queryable
- [ ] Neptune node count ≥ 4,250 (across all entity types)
- [ ] Neptune edge count ≥ 5,000
- [ ] All 5 competency questions return results (non-empty)
- [ ] CQ1 returns at least 5 high-risk patients

---

## Phase 4 — L2 Vector Layer: Embeddings (Days 12–14, parallel with Phase 3)

**Goal**: Embed all ICD-10 concepts, ontology class definitions, and clinical
note chunks into OpenSearch. Enable hybrid search (vector + keyword).

### 4a. Embed ontology concepts

Write `data/loaders/load_opensearch_vectors.py`:

For each class in `controlled_vocabulary.json`, call Bedrock Titan
Embeddings v2 and store in `vbc-concepts-embeddings` index:
```python
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

def embed(text: str) -> list[float]:
    response = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=json.dumps({"inputText": text, "dimensions": 1024})
    )
    return json.loads(response["body"].read())["embedding"]

# For each class:
doc = {
    "class_id": "vbc:Patient",
    "label": "Patient",
    "domain": "Patient/Member",
    "definition": "Individual receiving healthcare services",
    "embedding": embed("Patient: Individual receiving healthcare services "
                       "in the value-based care context. Also known as "
                       "Member, Beneficiary, Enrollee.")
}
```

### 4b. Embed ICD-10 codes used in sample data

For each distinct ICD-10 code in `diagnosis_history`, embed
`"{code} {description}"` and store in `vbc-icd10-embeddings`.

Include all named individuals from Section 7.6 of the ontology docx:
ICD_I509 (CHF), ICD_E119 (T2DM), ICD_J441 (COPD), ICD_I10 (HTN),
ICD_N184 (CKD4), ICD_F329 (Depression).

### 4c. Create hybrid search Lambda function

Write a Lambda function `functions/hybrid_search.py` that accepts a
natural language query and returns results from both:
1. OpenSearch KNN vector search (semantic similarity)
2. Neptune SPARQL (structured graph facts)

Combined response format:
```json
{
  "query": "high risk CHF patients with open diabetes gaps",
  "semantic_matches": [...],
  "graph_facts": [...],
  "merged_patients": [...]
}
```

### Validation gate 4
- [ ] `vbc-concepts-embeddings` index has ≥ 130 documents
- [ ] `vbc-icd10-embeddings` index has ≥ 6 named condition documents
- [ ] Vector similarity search for "congestive heart failure" returns
      ICD_I509 as top result
- [ ] Vector similarity search for "sugar diabetes" returns ICD_E119
      as top result (synonym matching working)
- [ ] Hybrid search Lambda returns combined results for CQ1 query

---

## Phase 5 — L5 + L6: Bedrock Knowledge Base + Reasoning (Days 15–18)

**Goal**: Wire Bedrock Knowledge Base over OpenSearch, build the Lambda
SPARQL bridge, and expose a unified semantic query endpoint.

### 5a. CDK stack: `l6-reasoning-stack.ts`

Provision:
- Bedrock Knowledge Base: `vbc-knowledge-base`
  - Data source: S3 `s3://vbc-poc-{account}/ontology/`
    (loads all `.ttl` and `.owl` files as context documents)
  - Vector store: existing `vbc-concepts-embeddings` OpenSearch index
  - Embedding model: `amazon.titan-embed-text-v2:0`
- Lambda function: `vbc-sparql-bridge`
  - Runtime: Python 3.12
  - Connects to Neptune SPARQL endpoint from VPC
  - Translates natural language → SPARQL via Bedrock Claude Sonnet
  - Returns JSON-LD results
- Lambda function: `vbc-hybrid-query`
  - Calls both `vbc-sparql-bridge` and OpenSearch KNN
  - Merges and deduplicates results
- API Gateway REST API: `vbc-query-api`
  - POST `/query` → `vbc-hybrid-query` Lambda
  - POST `/sparql` → `vbc-sparql-bridge` Lambda

### 5b. SPARQL bridge prompt template

Store in `ontology/sparql/bridge_prompt.txt`:

```
You are a SPARQL expert for the VBC (Value-Based Care) ontology.
Ontology namespace: https://ontology.vbc.internal/vbc#

Key classes: Patient, Provider, PrimaryCarePhysician, Condition,
ChronicCondition, CardiovascularCondition, MetabolicCondition,
PatientDiagnosis, HCCCode, QualityMeasure, HEDISMeasure, CareGap,
OpenCareGap, ClosedCareGap, RiskFactor, SDOHBarrier, Encounter,
EDEncounter, InpatientEncounter, CarePlan, CareTask, Medication.

Key object properties: hasDiagnosis, hasPCP, hasCareGap, hasRiskScore,
hasEncounter, hasSDOHBarrier, hasCarePlan, diagnosedWith, mapsToHCC,
belongsToNetwork, hasCareTeamAssignment.

Key data properties: mrn, dateOfBirth, riskScoreValue, icd10CodeValue,
hccCode, rafWeight, gapStatus, measureName, encounterType, barrierType.

Named graph: <https://ontology.vbc.internal/vbc/instances>

Convert this natural language question to a SPARQL SELECT query.
Return only the SPARQL query, no explanation.

Question: {question}
```

### 5c. Test end-to-end semantic query

Test these 3 queries through the API Gateway endpoint:

1. `"Show me all high-risk CHF patients with open diabetes gaps attributed
   to a PCP"`
2. `"Which SDOH barriers are most correlated with ED utilization in the
   last 6 months?"`
3. `"Trace the care pathway for patients with both housing instability and
   uncontrolled hypertension"`

### Validation gate 5
- [ ] Bedrock KB ingests all ontology files (status: READY)
- [ ] SPARQL bridge Lambda returns valid SPARQL for all 3 test queries above
- [ ] API Gateway `/query` endpoint returns HTTP 200 with results
- [ ] Neptune query latency < 2s for CQ1-CQ5
- [ ] OpenSearch KNN search latency < 500ms

---

## Phase 6 — L7: Bedrock Agent (Days 19–21)

**Goal**: A conversational Bedrock Agent that reasons over the VBC knowledge
graph using the ontology, able to answer complex VBC questions in plain English.

### 6a. CDK stack: `l7-agent-stack.ts`

Provision:
- Bedrock Agent: `VBC-Care-Navigator`
  - Foundation model: Claude Sonnet (latest)
  - Instruction: (see below)
  - Knowledge base: attach `vbc-knowledge-base` from Phase 5
  - Action groups: (see below)

Agent instruction (store in `infra/lib/agent_instruction.txt`):
```
You are VBC Care Navigator, an AI assistant specializing in
Value-Based Care analytics and care coordination.

You have access to a knowledge graph containing data on patients,
providers, diagnoses, quality measures, risk scores, SDOH barriers,
care plans, and financial performance.

Your ontology namespace is https://ontology.vbc.internal/vbc#.
You understand concepts like HCC coding, HEDIS measures, CMS Stars,
RAF scores, PMPM, TCOC, PDC adherence, and care gap closure.

When answering questions:
1. First check the knowledge base for ontology context
2. Then query the graph via the sparql_query action
3. If needed, perform semantic search via semantic_search action
4. Always cite which patients or data points support your answer
5. Flag any data quality issues or gaps in coverage

You must NOT fabricate patient data. Only report what the graph contains.
```

Action groups:
- `sparql_query`: invokes `vbc-sparql-bridge` Lambda
- `semantic_search`: invokes `vbc-hybrid-query` Lambda
- `get_patient_360`: Athena query returning full patient view

### 6b. Test the agent with VBC questions

These must all return accurate, graph-grounded answers:

1. "Who are the top 10 highest-risk patients with open quality gaps?"
2. "Show me the attribution chain for member M-0042"
3. "Which patients have both housing instability and uncontrolled diabetes?"
4. "What is the readmission rate for CHF patients in the last 90 days?"
5. "Which care managers have the highest gap closure rates?"

### Validation gate 6
- [ ] Bedrock Agent status is PREPARED
- [ ] Agent answers question 1 with ≥ 5 named patients from the graph
- [ ] Agent answers question 2 with correct provider NPI from sample data
- [ ] Agent does not hallucinate patients not in the dataset
- [ ] All 5 test questions answered within 30 seconds

---

## Phase 7 — Governance layer (Days 22–24)

**Goal**: Implement the governance engineering discipline from the
Talisman refresh — change management, drift detection, audit trails.

### 7a. Ontology change management

Create `ontology/governance/change_log.md` — every change to any
`.ttl` or `.owl` file must be logged with:
- Date, author, change type (add/modify/deprecate)
- Affected classes or properties
- Competency question re-run results

Write `tests/sparql/run_all_cq.py` — reruns all 5 competency questions
and asserts expected row counts. Must pass before any ontology change
is deployed to Neptune.

### 7b. DataZone governance policies

- Subscription workflow: require approval before external projects
  can subscribe to VBC data assets
- Tag-based access: tag `member_master` and `claims_medical` as
  `sensitivity=PHI` — Lake Formation blocks access without explicit grant
- Enable DataZone data lineage: link Glue ETL jobs as lineage sources
  for each Iceberg table

### 7c. Neptune graph drift detection

Write `tests/integration/check_graph_integrity.py`:
- Verify every Patient node has a `vbc:hasPCP` edge (functional property)
- Verify no CareGap node exists without a linked Patient
- Verify all HCCCode individuals from the ontology are present as nodes
- Verify taxonomy depth: every class has a path to owl:Thing via subClassOf

Schedule this as a daily Lambda function with CloudWatch alerts on failure.

### 7d. CloudTrail audit for ontology access

Create CloudWatch dashboard: `VBC-Ontology-Governance`
Metrics to track:
- Neptune SPARQL query count by IAM principal
- DataZone asset access events
- Bedrock Agent invocations
- Lambda SPARQL bridge calls

### Validation gate 7
- [ ] `run_all_cq.py` passes all 5 competency questions
- [ ] Graph integrity check passes (0 orphaned nodes, 0 missing PCP edges)
- [ ] PHI tagging verified — ungranted user cannot query `member_master`
- [ ] CloudTrail shows audit trail for last 24 hours of agent queries
- [ ] `change_log.md` has an entry for each ontology file loaded

---

## Ongoing: AI partnership tasks (Claude Code can execute any time)

These are the AI-accelerated tasks from the Talisman refresh.
Run them on demand as the ontology matures.

```
# Entity extraction from new clinical documents
"Parse this FHIR bundle and extract candidate ontology terms to add
to controlled_vocabulary.json. Flag any terms not already in the
vocabulary."

# Gap analysis
"Run a coverage analysis: which ICD-10 codes in diagnosis_history
do not map to any HCC node in the Neptune graph? Return as a CSV."

# Vocabulary drift detection
"Compare the current DataZone glossary terms against
controlled_vocabulary.json. List any terms present in DataZone
but missing from the OWL ontology, or vice versa."

# Candidate synonym discovery
"Query the vbc-concepts-embeddings index for all class labels.
Find pairs with cosine similarity > 0.85 that do not already have
a skos:exactMatch or skos:closeMatch edge in the thesaurus. Return
as candidate synonym pairs for human review."

# SPARQL query optimisation
"Profile these 5 competency questions against Neptune. Identify
which ones do full graph scans. Suggest index additions or query
rewrites to bring each under 500ms."
```

---

## AWS resource summary (PoC sizing)

| Service | Resource | Est. cost/month |
|---|---|---|
| S3 | vbc-poc bucket (~5GB) | ~$0.12 |
| Glue | 8 crawlers + catalog | ~$5 |
| Neptune | db.t3.medium, 1 instance | ~$70 |
| OpenSearch | t3.small.search, 20GB | ~$35 |
| Bedrock | Claude Sonnet on-demand | ~$20–50 |
| Bedrock | Titan Embeddings | ~$5 |
| DataZone | 1 domain | ~$0 (free tier) |
| Lambda | SPARQL bridge + hybrid | ~$1 |
| API Gateway | REST API | ~$1 |
| DocumentDB | t3.medium | ~$60 |
| Total estimate | | **~$200–230/month** |

> Tear down Neptune and DocumentDB when not actively working to save ~$130/day.
> Use `cdk destroy l3-graph-stack` and `cdk deploy` to bring back up in ~15 min.

---

## Key decisions and constraints

1. **Neptune over raw RDF store**: Neptune supports both Gremlin (property
   graph) and SPARQL (RDF) — use SPARQL for ontology reasoning and Gremlin
   for traversal queries. Do not use both in the same query.

2. **Bedrock Titan Embeddings v2 at 1024 dimensions**: balances quality
   and OpenSearch storage cost. Do not use 1536d for PoC.

3. **Iceberg on S3 via Glue**: do not use Athena CTAS for Iceberg creation
   — use Glue ETL or `awswrangler` with `wr.s3.to_parquet(..., dataset=True)`.

4. **PHI handling**: all member PII (SSN, DOB, address) in `member_master`
   must be synthetic for PoC. The sample data generator must never use real
   patient data. Tag all PHI columns in Glue with `pii=true`.

5. **OWL namespace IRI**: use `https://ontology.vbc.internal/vbc#` consistently
   across all `.ttl`, `.owl`, SPARQL queries, and Neptune node IRIs.
   Never mix with the Snowflake namespace from the source docx
   (`https://ontology.mastechdigital.com/vbc#`).

6. **Ontology-first, data second**: do not load sample data into Neptune
   until taxonomy + thesaurus + OWL are loaded and validated. Data without
   a schema is just triples.

7. **Competency questions before class declarations**: before adding any
   new class to the ontology, write the SPARQL competency question it
   must answer. If you cannot write the question, do not add the class.
