#!/usr/bin/env python3
"""
Generate taxonomy.ttl, thesaurus.ttl, and vbc_ontology.owl from
controlled_vocabulary.json + the canonical VBC class hierarchy.

Run: python3 generate_ontology_files.py
"""
import json, os

BASE    = os.path.join(os.path.dirname(__file__), "..", "..")
CV_PATH = os.path.join(BASE, "ontology", "controlled_vocabulary.json")
ONT_DIR = os.path.join(BASE, "ontology")

VBC = "https://ontology.vbc.internal/vbc#"

cv = json.load(open(CV_PATH))
classes = cv.get("classes", [])
obj_props = cv.get("objectProperties", [])

# ── Canonical subClassOf hierarchy from CLAUDE.md + ontology docx ─────────────
HIERARCHY = [
    # Persons
    ("Patient",                   "Person"),
    ("Member",                    "Person"),
    ("CareTeamMember",            "Person"),
    ("Physician",                 "CareTeamMember"),
    ("PrimaryCarePhysician",      "Physician"),
    ("Specialist",                "Physician"),
    ("CareManager",               "CareTeamMember"),
    ("SocialWorker",              "CareTeamMember"),
    # Conditions
    ("ChronicCondition",          "Condition"),
    ("AcuteCondition",            "Condition"),
    ("CardiovascularCondition",   "ChronicCondition"),
    ("MetabolicCondition",        "ChronicCondition"),
    ("RespiratoryCondition",      "ChronicCondition"),
    ("RenalCondition",            "ChronicCondition"),
    ("MentalHealthCondition",     "ChronicCondition"),
    # Quality
    ("HEDISMeasure",              "QualityMeasure"),
    ("CMSStarMeasure",            "QualityMeasure"),
    ("CustomMeasure",             "QualityMeasure"),
    # Care Gaps
    ("OpenCareGap",               "CareGap"),
    ("ClosedCareGap",             "CareGap"),
    ("ExcludedCareGap",           "CareGap"),
    # Encounters
    ("InpatientEncounter",        "Encounter"),
    ("EDEncounter",               "Encounter"),
    ("OutpatientEncounter",       "Encounter"),
    ("TelehealthEncounter",       "Encounter"),
    # Medications
    ("HighAlertMedication",       "Medication"),
    # SDOH
    ("FoodInsecurityBarrier",     "SDOHBarrier"),
    ("HousingInstabilityBarrier", "SDOHBarrier"),
    ("TransportationBarrier",     "SDOHBarrier"),
    ("SocialIsolationBarrier",    "SDOHBarrier"),
    # Organizations
    ("HealthSystem",              "Organization"),
    ("ACO",                       "Organization"),
    ("MedicalGroup",              "Organization"),
    ("Practice",                  "Organization"),
    # Clinical
    ("PatientDiagnosis",          "ClinicalObservation"),
    ("LabResult",                 "ClinicalObservation"),
    ("VitalSign",                 "ClinicalObservation"),
    # Risk
    ("RiskFactor",                "RiskAssessment"),
    ("HCCCode",                   "RiskAssessment"),
    # Financial
    ("VBCContract",               "FinancialArrangement"),
    ("CapitationPayment",         "FinancialArrangement"),
    # Care Management
    ("CarePlan",                  "CareManagementEntity"),
    ("CareTask",                  "CareManagementEntity"),
    ("CareTeamAssignment",        "CareManagementEntity"),
    # Claims
    ("MedicalClaim",              "Claim"),
    ("PharmacyClaim",             "Claim"),
    # Utilization
    ("PriorAuthorization",        "UtilizationEvent"),
    ("CareTransition",            "UtilizationEvent"),
]

# All classes from CV + hierarchy parents (collect unique set)
all_class_labels = set(c.get("label", "") for c in classes)
# Add any parents from hierarchy not already in CV
for child, parent in HIERARCHY:
    all_class_labels.add(child)
    all_class_labels.add(parent)

# Build label→id map from CV
label_to_id = {}
for c in classes:
    lbl = c.get("label", "")
    cid = c.get("id", f"vbc:{lbl}")
    short = cid.replace("vbc:", "")
    label_to_id[lbl] = short

def cls(label):
    """Return short local name for a class label."""
    return label_to_id.get(label, label)


# ══════════════════════════════════════════════════════════════════════════════
# 1. taxonomy.ttl
# ══════════════════════════════════════════════════════════════════════════════
print("Generating taxonomy.ttl...")

lines = [
    "@prefix vbc:  <https://ontology.vbc.internal/vbc#> .",
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
    "@prefix owl:  <http://www.w3.org/2002/07/owl#> .",
    "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
    "",
    "# VBC Ontology Taxonomy — is-a hierarchy",
    "# Generated from controlled_vocabulary.json",
    "",
    "<https://ontology.vbc.internal/vbc> a owl:Ontology .",
    "",
    "# ── Root classes (no parent — subClassOf owl:Thing) ──────────────────────",
]

# Declare all CV classes with their labels/definitions
for c in classes:
    lbl = c.get("label", "")
    short = cls(lbl)
    defn = c.get("definition", "").replace('"', "'")
    domain = c.get("domain", "")
    lines.append(f"vbc:{short} a owl:Class ;")
    lines.append(f'    rdfs:label "{lbl}" ;')
    if defn:
        lines.append(f'    rdfs:comment "{defn}" ;')
    if domain:
        lines.append(f'    vbc:domain "{domain}" ;')
    lines.append("    rdfs:subClassOf owl:Thing .")
    lines.append("")

lines.append("# ── Subclass relationships ───────────────────────────────────────────────")
for child, parent in HIERARCHY:
    lines.append(f"vbc:{cls(child)} rdfs:subClassOf vbc:{cls(parent)} .")

taxonomy_path = os.path.join(ONT_DIR, "taxonomy.ttl")
with open(taxonomy_path, "w") as f:
    f.write("\n".join(lines))
print(f"  ✅ taxonomy.ttl written ({len(lines)} lines, {len(HIERARCHY)} subClassOf triples)")


# ══════════════════════════════════════════════════════════════════════════════
# 2. thesaurus.ttl
# ══════════════════════════════════════════════════════════════════════════════
print("Generating thesaurus.ttl...")

THESAURUS = [
    ("Patient",                ["Beneficiary", "Enrollee", "Covered Life", "Plan Member"],
     [("skos:exactMatch", "Member"), ("skos:related", "CareTeamMember")]),
    ("Member",                 ["Beneficiary", "Enrollee", "Covered Life"],
     [("skos:exactMatch", "Patient")]),
    ("PrimaryCarePhysician",   ["PCP", "Primary Care Doctor", "Attending Physician", "Primary Care Provider"],
     [("skos:broader", "Physician")]),
    ("Specialist",             ["Consulting Physician", "Subspecialist"],
     [("skos:broader", "Physician")]),
    ("HCCCode",                ["HCC", "Hierarchical Condition Category", "Risk Adjustment Category"],
     [("skos:related", "RAFScore"), ("skos:related", "RiskFactor")]),
    ("QualityMeasure",         ["Quality Metric", "Performance Measure", "HEDIS Measure"],
     [("skos:narrower", "HEDISMeasure"), ("skos:narrower", "CMSStarMeasure")]),
    ("CareGap",                ["Quality Gap", "Open Gap", "Measure Gap", "Care Opportunity"],
     [("skos:related", "QualityMeasure")]),
    ("OpenCareGap",            ["Open Quality Gap", "Unresolved Care Gap"],
     [("skos:broader", "CareGap")]),
    ("ClosedCareGap",          ["Resolved Gap", "Closed Quality Gap"],
     [("skos:broader", "CareGap")]),
    ("PMPM",                   ["Per Member Per Month", "Capitation Rate", "Monthly Premium"],
     [("skos:related", "VBCContract"), ("skos:related", "TCOC")]),
    ("TCOC",                   ["Total Cost of Care", "Total Medical Expense", "TME"],
     [("skos:related", "PMPM")]),
    ("SDOHBarrier",            ["Social Barrier", "Social Need", "Non-Clinical Barrier", "Z-Code Factor"],
     [("skos:narrower", "FoodInsecurityBarrier"),
      ("skos:narrower", "HousingInstabilityBarrier"),
      ("skos:narrower", "TransportationBarrier"),
      ("skos:narrower", "SocialIsolationBarrier")]),
    ("FoodInsecurityBarrier",  ["Food Insecurity", "Hunger", "Food Desert"],
     [("skos:broader", "SDOHBarrier")]),
    ("HousingInstabilityBarrier", ["Housing Insecurity", "Homelessness Risk", "Unstable Housing"],
     [("skos:broader", "SDOHBarrier")]),
    ("TransportationBarrier",  ["Transportation Need", "Lack of Transportation"],
     [("skos:broader", "SDOHBarrier")]),
    ("SocialIsolationBarrier", ["Social Isolation", "Loneliness", "Social Exclusion"],
     [("skos:broader", "SDOHBarrier")]),
    ("Attribution",            ["Panel Assignment", "Patient Attribution", "Attributed Patient"],
     [("skos:related", "PrimaryCarePhysician")]),
    ("RAFScore",               ["RAF", "Risk Adjustment Factor", "CMS RAF"],
     [("skos:related", "HCCCode"), ("skos:related", "RiskFactor")]),
    ("RiskFactor",             ["Risk Score", "Clinical Risk", "Predictive Risk Score"],
     [("skos:related", "HCCCode"), ("skos:related", "RAFScore")]),
    ("PatientDiagnosis",       ["Diagnosis", "Dx", "Problem", "Clinical Finding"],
     [("skos:related", "HCCCode")]),
    ("CarePlan",               ["Care Program", "Treatment Plan", "Care Pathway"],
     [("skos:related", "CareTask")]),
    ("HEDISMeasure",           ["HEDIS", "NCQA Measure", "HEDIS Quality Measure"],
     [("skos:broader", "QualityMeasure")]),
    ("CMSStarMeasure",         ["CMS Stars", "Star Rating Measure", "Part C/D Star Measure"],
     [("skos:broader", "QualityMeasure")]),
    ("ACO",                    ["Accountable Care Organization", "Care Organization"],
     [("skos:broader", "Organization")]),
    ("InpatientEncounter",     ["Hospital Admission", "Inpatient Stay", "IP Encounter"],
     [("skos:broader", "Encounter")]),
    ("EDEncounter",            ["Emergency Visit", "ER Visit", "Emergency Department Visit"],
     [("skos:broader", "Encounter")]),
    ("VBCContract",            ["Value-Based Contract", "Risk Contract", "Shared Savings Contract"],
     [("skos:related", "PMPM"), ("skos:related", "TCOC")]),
    ("PDCMeasure",             ["Proportion of Days Covered", "Medication Adherence", "PDC"],
     [("skos:related", "QualityMeasure")]),
    ("PriorAuthorization",     ["PA", "Prior Auth", "Pre-authorization", "Precertification"],
     [("skos:broader", "UtilizationEvent")]),
]

tlines = [
    "@prefix vbc:  <https://ontology.vbc.internal/vbc#> .",
    "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
    "",
    "# VBC Ontology Thesaurus — synonyms, alt labels, broader/narrower",
    "",
]

alt_label_count = 0
for label, alt_labels, relations in THESAURUS:
    short = cls(label)
    tlines.append(f"vbc:{short}")
    for i, alt in enumerate(alt_labels):
        sep = " ;" if (i < len(alt_labels) - 1 or relations) else " ."
        tlines.append(f'    skos:altLabel "{alt}"@en{sep}')
        alt_label_count += 1
    for i, (pred, target) in enumerate(relations):
        sep = " ;" if i < len(relations) - 1 else " ."
        tlines.append(f"    {pred} vbc:{cls(target)}{sep}")
    tlines.append("")

thesaurus_path = os.path.join(ONT_DIR, "thesaurus.ttl")
with open(thesaurus_path, "w") as f:
    f.write("\n".join(tlines))
print(f"  ✅ thesaurus.ttl written ({alt_label_count} altLabel triples, {len(THESAURUS)} concepts)")


# ══════════════════════════════════════════════════════════════════════════════
# 3. vbc_ontology.owl  (OWL/XML)
# ══════════════════════════════════════════════════════════════════════════════
print("Generating vbc_ontology.owl...")

OBJ_PROPS = [
    ("hasDiagnosis",          "Patient",           "PatientDiagnosis",     False),
    ("hasCareTeamAssignment", "Patient",           "CareTeamAssignment",   False),
    ("hasPCP",                "Patient",           "PrimaryCarePhysician", True),   # functional
    ("hasCareGap",            "Patient",           "CareGap",              False),
    ("hasRiskScore",          "Patient",           "RiskFactor",           False),
    ("hasSDOHBarrier",        "Patient",           "SDOHBarrier",          False),
    ("hasEncounter",          "Patient",           "Encounter",            False),
    ("hasCarePlan",           "Patient",           "CarePlan",             False),
    ("diagnosedWith",         "PatientDiagnosis",  "Condition",            False),
    ("mapsToHCC",             "PatientDiagnosis",  "HCCCode",              False),
    ("forMember",             "PatientDiagnosis",  "Patient",              False),
    ("belongsToNetwork",      "Physician",         "Organization",         False),
    ("includesTask",          "CarePlan",          "CareTask",             False),
    ("forMeasure",            "CareGap",           "QualityMeasure",       False),
    ("hasLabResult",          "Patient",           "LabResult",            False),
    ("treatedBy",             "Patient",           "Physician",            False),
    ("memberOf",              "Physician",         "MedicalGroup",         False),
    ("hasAttribution",        "Patient",           "Attribution",          False),
    ("coveredUnder",          "Patient",           "VBCContract",          False),
    ("prescribedTo",          "Medication",        "Patient",              False),
    ("performedAt",           "Encounter",         "Organization",         False),
    ("closedBy",              "ClosedCareGap",     "CareTask",             False),
]

DATA_PROPS = [
    ("mrn",                   "Patient",           "xsd:string"),
    ("dateOfBirth",           "Patient",           "xsd:date"),
    ("sex",                   "Patient",           "xsd:string"),
    ("preferredLanguage",     "Patient",           "xsd:string"),
    ("adiScore",              "Patient",           "xsd:integer"),
    ("riskTier",              "Patient",           "xsd:string"),
    ("npi",                   "Physician",         "xsd:string"),
    ("hasFullName",           "Person",            "xsd:string"),
    ("specialty",             "Physician",         "xsd:string"),
    ("networkId",             "Physician",         "xsd:string"),
    ("icd10CodeValue",        "PatientDiagnosis",  "xsd:string"),
    ("icd10Description",      "PatientDiagnosis",  "xsd:string"),
    ("diagnosisDate",         "PatientDiagnosis",  "xsd:date"),
    ("diagnosisType",         "PatientDiagnosis",  "xsd:string"),
    ("hccCode",               "HCCCode",           "xsd:string"),
    ("rafWeight",             "HCCCode",           "xsd:decimal"),
    ("riskScoreValue",        "RiskFactor",        "xsd:decimal"),
    ("rafScore",              "RiskFactor",        "xsd:decimal"),
    ("hccCount",              "RiskFactor",        "xsd:integer"),
    ("topHccCode",            "RiskFactor",        "xsd:string"),
    ("gapStatus",             "CareGap",           "xsd:string"),
    ("measureId",             "CareGap",           "xsd:string"),
    ("measureName",           "QualityMeasure",    "xsd:string"),
    ("gapOpenDate",           "CareGap",           "xsd:date"),
    ("gapCloseDate",          "ClosedCareGap",     "xsd:date"),
    ("barrierType",           "SDOHBarrier",       "xsd:string"),
    ("barrierCategory",       "SDOHBarrier",       "xsd:string"),
    ("severity",              "SDOHBarrier",       "xsd:string"),
    ("icd10ZCode",            "SDOHBarrier",       "xsd:string"),
    ("encounterType",         "Encounter",         "xsd:string"),
    ("serviceDate",           "Encounter",         "xsd:date"),
    ("losdays",               "InpatientEncounter","xsd:integer"),
    ("completionStatus",      "CareTask",          "xsd:string"),
    ("pmpmAmount",            "VBCContract",       "xsd:decimal"),
    ("tcocAmount",            "VBCContract",       "xsd:decimal"),
    ("assignedPCPNPI",        "Patient",           "xsd:string"),
]

owl_lines = ['<?xml version="1.0"?>',
'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
'         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
'         xmlns:owl="http://www.w3.org/2002/07/owl#"',
'         xmlns:xsd="http://www.w3.org/2001/XMLSchema#"',
'         xmlns:skos="http://www.w3.org/2004/02/skos/core#"',
'         xmlns:vbc="https://ontology.vbc.internal/vbc#"',
'         xml:base="https://ontology.vbc.internal/vbc">',
'',
'  <owl:Ontology rdf:about="https://ontology.vbc.internal/vbc">',
'    <rdfs:label>VBC Knowledge Ontology v1.1</rdfs:label>',
'    <rdfs:comment>Value-Based Care ontology for the AWS Knowledge Fabric Stack</rdfs:comment>',
'  </owl:Ontology>',
'',
'  <!-- ═══ Classes ═══ -->',
]

for c in classes:
    lbl  = c.get("label", "")
    short = cls(lbl)
    defn  = c.get("definition", "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    parent = c.get("subClassOf", "")
    owl_lines.append(f'  <owl:Class rdf:about="https://ontology.vbc.internal/vbc#{short}">')
    owl_lines.append(f'    <rdfs:label>{lbl}</rdfs:label>')
    if defn:
        owl_lines.append(f'    <rdfs:comment>{defn}</rdfs:comment>')
    if parent:
        p_short = parent.replace("vbc:", "")
        owl_lines.append(f'    <rdfs:subClassOf rdf:resource="https://ontology.vbc.internal/vbc#{p_short}"/>')
    owl_lines.append('  </owl:Class>')
    owl_lines.append('')

# subClassOf from hierarchy
owl_lines.append('  <!-- ═══ Subclass axioms ═══ -->')
for child, parent in HIERARCHY:
    owl_lines.append(f'  <owl:Class rdf:about="https://ontology.vbc.internal/vbc#{cls(child)}">')
    owl_lines.append(f'    <rdfs:subClassOf rdf:resource="https://ontology.vbc.internal/vbc#{cls(parent)}"/>')
    owl_lines.append('  </owl:Class>')

owl_lines.append('')
owl_lines.append('  <!-- ═══ Object Properties ═══ -->')
for prop, domain, range_, functional in OBJ_PROPS:
    ptype = "owl:FunctionalProperty" if functional else "owl:ObjectProperty"
    owl_lines.append(f'  <{ptype} rdf:about="https://ontology.vbc.internal/vbc#{prop}">')
    owl_lines.append(f'    <rdfs:domain rdf:resource="https://ontology.vbc.internal/vbc#{cls(domain)}"/>')
    owl_lines.append(f'    <rdfs:range  rdf:resource="https://ontology.vbc.internal/vbc#{cls(range_)}"/>')
    owl_lines.append(f'  </{ptype}>')

owl_lines.append('')
owl_lines.append('  <!-- ═══ Data Properties ═══ -->')
for prop, domain, xsd_type in DATA_PROPS:
    owl_lines.append(f'  <owl:DatatypeProperty rdf:about="https://ontology.vbc.internal/vbc#{prop}">')
    owl_lines.append(f'    <rdfs:domain rdf:resource="https://ontology.vbc.internal/vbc#{cls(domain)}"/>')
    owl_lines.append(f'    <rdfs:range  rdf:resource="http://www.w3.org/2001/XMLSchema#{xsd_type.replace("xsd:", "")}"/>')
    owl_lines.append(f'  </owl:DatatypeProperty>')

owl_lines.append('')
owl_lines.append('</rdf:RDF>')

owl_path = os.path.join(ONT_DIR, "vbc_ontology.owl")
with open(owl_path, "w") as f:
    f.write("\n".join(owl_lines))
print(f"  ✅ vbc_ontology.owl written ({len(OBJ_PROPS)} obj props, {len(DATA_PROPS)} data props)")

# ── Upload refreshed files to S3 ─────────────────────────────────────────────
import boto3
s3 = boto3.client("s3", region_name="ap-southeast-2")
BUCKET = "vbc-poc-020396275984"
for fname in ["taxonomy.ttl", "thesaurus.ttl", "vbc_ontology.owl"]:
    s3.upload_file(os.path.join(ONT_DIR, fname), BUCKET, f"ontology/{fname}")
    print(f"  ⬆️  Uploaded s3://{BUCKET}/ontology/{fname}")

print("\n✅ All ontology files generated and uploaded to S3.")
