#!/usr/bin/env python3
"""
Generate synthetic VBC sample data — 6 datasets, all IDs consistent across tables.
Outputs Parquet files to data/sample/.
"""
import os, random, uuid
from datetime import date, timedelta
import pandas as pd
from faker import Faker

fake = Faker("en_US")
random.seed(42)
Faker.seed(42)

OUT = os.path.join(os.path.dirname(__file__), "..", "sample")
os.makedirs(OUT, exist_ok=True)

# ── Reference data ─────────────────────────────────────────────────────────────

ICD10_CONDITIONS = [
    ("I50.9",  "Heart failure, unspecified",                         "HCC85",  "Congestive Heart Failure",              0.323),
    ("E11.9",  "Type 2 diabetes mellitus without complications",     "HCC19",  "Diabetes Without Complication",         0.118),
    ("J44.1",  "COPD with acute exacerbation",                       "HCC111", "COPD",                                  0.346),
    ("I10",    "Essential (primary) hypertension",                   "HCC88",  "Hypertension",                          0.095),
    ("N18.4",  "Chronic kidney disease, stage 4",                    "HCC136", "Chronic Kidney Disease Stage 4",        0.289),
    ("F32.9",  "Major depressive disorder, single episode",          "HCC59",  "Major Depressive Disorder",             0.309),
    ("I25.10", "Atherosclerotic heart disease",                      "HCC86",  "Atherosclerotic Heart Disease",         0.368),
    ("E78.5",  "Hyperlipidemia, unspecified",                        "HCC0",   "Hyperlipidemia",                        0.0),
    ("M17.11", "Primary osteoarthritis, right knee",                 "HCC0",   "Osteoarthritis",                        0.0),
    ("J45.40", "Moderate persistent asthma uncomplicated",           "HCC110", "Asthma",                                0.155),
    ("I48.91", "Unspecified atrial fibrillation",                    "HCC96",  "Atrial Fibrillation",                   0.267),
    ("E11.65", "Type 2 diabetes with hyperglycemia",                 "HCC19",  "Diabetes With Complication",            0.302),
    ("N18.3",  "Chronic kidney disease, stage 3 unspecified",        "HCC136", "Chronic Kidney Disease Stage 3",        0.145),
    ("F41.1",  "Generalized anxiety disorder",                       "HCC59",  "Anxiety Disorder",                      0.220),
    ("Z59.0",  "Homelessness",                                       "HCC0",   "Homelessness Z-code",                   0.0),
    ("Z59.4",  "Lack of adequate food",                              "HCC0",   "Food Insecurity Z-code",                0.0),
    ("Z59.8",  "Other problems related to housing",                  "HCC0",   "Housing Instability Z-code",            0.0),
    ("Z60.4",  "Social exclusion and rejection",                     "HCC0",   "Social Isolation Z-code",               0.0),
]

HEDIS_MEASURES = [
    ("CDC-HbA1c9",   "Diabetes HbA1c Poor Control (>9%)",                      "CDC"),
    ("CDC-BPControl","Diabetes Blood Pressure Control",                         "CDC"),
    ("CBP",          "Controlling High Blood Pressure",                         "CBP"),
    ("COL-E",        "Colorectal Cancer Screening",                             "COL"),
    ("BCS-E",        "Breast Cancer Screening",                                 "BCS"),
    ("AMR",          "Asthma Medication Ratio",                                 "AMR"),
    ("PCE",          "Pharmacotherapy Management of COPD Exacerbation",         "PCE"),
    ("ACR",          "Annual Creatinine Testing for Kidney Disease",            "ACR"),
]

SPECIALTIES = ["Family Medicine", "Internal Medicine", "Cardiology",
               "Endocrinology", "Nephrology", "Pulmonology", "Psychiatry"]
NETWORKS    = [("NET001", "Premier Health Network"), ("NET002", "Community Care Alliance")]
SDOH_TYPES  = [
    ("food",             "Z59.4", "Food Security"),
    ("housing",          "Z59.8", "Housing Stability"),
    ("transportation",   "Z60.4", "Transportation Access"),
    ("social_isolation", "Z60.4", "Social Connection"),
]
DRUGS = [
    ("00093-5058-01", "Metformin 500mg",   "Antidiabetic",   "860975",  90),
    ("00071-0155-23", "Lisinopril 10mg",   "ACE Inhibitor",  "29046",   30),
    ("00006-0019-54", "Atorvastatin 40mg", "Statin",         "83367",   90),
    ("00173-0682-00", "Albuterol Inhaler", "Bronchodilator", "435",     30),
    ("00378-0093-10", "Amlodipine 5mg",    "CCB",            "17767",   30),
    ("00093-4131-01", "Carvedilol 6.25mg", "Beta Blocker",   "20352",   90),
    ("51079-839-20",  "Furosemide 40mg",   "Loop Diuretic",  "4603",    30),
    ("00591-0405-01", "Sertraline 50mg",   "SSRI",           "36437",   90),
    ("55513-0019-10", "Insulin Glargine",  "Insulin",        "274783",  30),
]

def rand_date(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta, 0)))

START = date(2024, 7, 1)
END   = date(2025, 6, 30)

# ── 1. Members (500) ──────────────────────────────────────────────────────────
print("Generating members...")
members = []
for i in range(500):
    mid    = f"M-{i+1:04d}"
    dob    = rand_date(date(1940, 1, 1), date(1985, 12, 31))
    gender = random.choice(["M", "F", "M", "F", "M"])
    adi    = random.randint(1, 100)
    risk   = "high" if adi > 75 else ("moderate" if adi > 40 else "low")
    members.append({
        "member_id":          mid,
        "mrn":                f"MRN{i+1:06d}",
        "first_name":         fake.first_name_male() if gender == "M" else fake.first_name_female(),
        "last_name":          fake.last_name(),
        "dob":                dob,
        "gender":             gender,
        "race":               random.choice(["White","Black","Hispanic","Asian","Other"]),
        "ethnicity":          random.choice(["Non-Hispanic","Hispanic","Unknown"]),
        "preferred_language": random.choices(["English","Spanish","Other"], weights=[80,15,5])[0],
        "address_line1":      fake.street_address(),
        "city":               fake.city(),
        "state":              "CA",
        "zip_code":           fake.zipcode(),
        "county":             fake.city(),
        "phone":              fake.phone_number(),
        "email":              fake.email(),
        "eligibility_start":  rand_date(date(2024, 1, 1), date(2024, 7, 1)),
        "eligibility_end":    date(2025, 12, 31),
        "payer_id":           random.choice(["PAY001", "PAY002"]),
        "plan_id":            random.choice(["PLN001", "PLN002", "PLN003"]),
        "pcp_npi":            f"PCP-{random.randint(1,30):03d}",
        "adi_score":          adi,
        "risk_tier":          risk,
        "attribution_status": random.choice(["attributed","prospective","unattributed"]),
        "created_at":         pd.Timestamp("2024-01-01"),
        "updated_at":         pd.Timestamp("2025-01-01"),
    })

df_members = pd.DataFrame(members)
df_members.to_parquet(f"{OUT}/members.parquet", index=False)
print(f"  ✅ {len(df_members)} members")

member_ids = df_members["member_id"].tolist()
member_pcp = dict(zip(df_members["member_id"], df_members["pcp_npi"]))
member_adi = dict(zip(df_members["member_id"], df_members["adi_score"]))

# ── 2. Providers (50) ─────────────────────────────────────────────────────────
print("Generating providers...")
providers = []
for i in range(50):
    is_pcp = i < 30
    spec   = "Family Medicine" if i < 15 else ("Internal Medicine" if i < 30 else random.choice(SPECIALTIES[2:]))
    net    = NETWORKS[i % 2]
    npi    = f"PCP-{i+1:03d}" if is_pcp else f"SPEC-{i-29:03d}"
    providers.append({
        "provider_id":        f"PRV-{i+1:04d}",
        "npi":                npi,
        "first_name":         fake.first_name(),
        "last_name":          fake.last_name(),
        "credential":         random.choice(["MD","DO","NP","PA"]),
        "specialty":          spec,
        "provider_type":      "PCP" if is_pcp else "Specialist",
        "network_id":         net[0],
        "network_name":       net[1],
        "tax_id":             fake.ein(),
        "group_npi":          f"GRP-{net[0]}",
        "practice_name":      f"{net[1]} — {spec}",
        "address_line1":      fake.street_address(),
        "city":               fake.city(),
        "state":              "CA",
        "zip_code":           fake.zipcode(),
        "phone":              fake.phone_number(),
        "accepting_patients": random.random() > 0.15,
        "panel_size":         random.randint(150, 400) if is_pcp else random.randint(50, 150),
        "created_at":         pd.Timestamp("2024-01-01"),
    })

df_providers = pd.DataFrame(providers)
df_providers.to_parquet(f"{OUT}/providers.parquet", index=False)
print(f"  ✅ {len(df_providers)} providers")

pcp_npis  = [p["npi"] for p in providers if p["provider_type"] == "PCP"]
spec_npis = [p["npi"] for p in providers if p["provider_type"] == "Specialist"]

# ── 3. Diagnoses (3000) ───────────────────────────────────────────────────────
print("Generating diagnoses...")
diagnoses = []
weights   = [15,14,12,10,8,8,7,6,5,4,4,3,2,1,0.5,0.5,0.3,0.2]
w_sum     = sum(weights)
weights   = [w/w_sum for w in weights]

for i in range(3000):
    mid  = random.choice(member_ids)
    icd  = random.choices(ICD10_CONDITIONS, weights=weights)[0]
    diagnoses.append({
        "diagnosis_id":      f"DX-{i+1:06d}",
        "member_id":         mid,
        "claim_id":          f"CLM-{random.randint(1,2000):06d}",
        "provider_npi":      member_pcp.get(mid, random.choice(pcp_npis)),
        "icd10_cm_code":     icd[0],
        "icd10_description": icd[1],
        "diagnosis_date":    rand_date(START, END),
        "diagnosis_type":    random.choices(["primary","secondary","admitting"], weights=[60,35,5])[0],
        "hcc_code":          icd[2],
        "hcc_description":   icd[3],
        "raf_weight":        icd[4],
        "chronic_flag":      icd[2] != "HCC0",
        "created_at":        pd.Timestamp("2024-01-01"),
    })

df_diagnoses = pd.DataFrame(diagnoses)
df_diagnoses.to_parquet(f"{OUT}/diagnoses.parquet", index=False)
print(f"  ✅ {len(df_diagnoses)} diagnoses")

# ── 4. Claims (2000) ──────────────────────────────────────────────────────────
print("Generating claims...")
claims = []
for i in range(2000):
    mid       = random.choice(member_ids)
    svc_date  = rand_date(START, END)
    inpatient = random.random() < 0.12
    drg       = random.choice(["291","292","293","470","690","871","872"]) if inpatient else ""
    los       = random.randint(2, 7) if inpatient else 0
    dis_date  = svc_date + timedelta(days=los) if inpatient else svc_date
    cpt       = random.choice(["99213","99214","99215","99232","93000","80053"]) if not inpatient else ""
    billed    = round(random.uniform(150, 25000 if inpatient else 800), 2)
    allowed   = round(billed * random.uniform(0.55, 0.75), 2)
    paid      = round(allowed * random.uniform(0.80, 0.95), 2)
    claims.append({
        "claim_id":          f"CLM-{i+1:06d}",
        "member_id":         mid,
        "provider_npi":      member_pcp.get(mid, random.choice(pcp_npis)),
        "facility_npi":      random.choice(spec_npis) if inpatient else "",
        "service_date":      svc_date,
        "discharge_date":    dis_date,
        "claim_type":        "institutional" if inpatient else "professional",
        "place_of_service":  "21" if inpatient else "11",
        "drg_code":          drg,
        "revenue_code":      random.choice(["0100","0110","0200"]) if inpatient else "",
        "cpt_code":          cpt,
        "hcpcs_code":        "",
        "units":             1,
        "billed_amount":     billed,
        "allowed_amount":    allowed,
        "paid_amount":       paid,
        "member_cost_share": round((allowed - paid) * random.uniform(0.2, 0.5), 2),
        "claim_status":      "paid",
        "primary_icd10":     random.choice(ICD10_CONDITIONS[:8])[0],
        "admit_type":        random.choice(["1","2","3"]) if inpatient else "",
        "los_days":          los,
        "readmission_flag":  inpatient and random.random() < 0.15,
        "created_at":        pd.Timestamp("2024-01-01"),
    })

df_claims = pd.DataFrame(claims)
df_claims.to_parquet(f"{OUT}/claims.parquet", index=False)
print(f"  ✅ {len(df_claims)} claims")

# ── 5. HEDIS gaps (400) ───────────────────────────────────────────────────────
print("Generating HEDIS gaps...")
gaps = []
for i in range(400):
    mid     = random.choice(member_ids)
    measure = random.choice(HEDIS_MEASURES)
    status  = random.choices(["open","closed","excluded"], weights=[55,35,10])[0]
    open_dt = rand_date(date(2024, 1, 1), date(2024, 12, 31))
    gaps.append({
        "gap_id":            f"GAP-{i+1:06d}",
        "member_id":         mid,
        "pcp_npi":           member_pcp.get(mid, random.choice(pcp_npis)),
        "measure_id":        measure[0],
        "measure_name":      measure[1],
        "measure_year":      2024,
        "gap_status":        status,
        "open_date":         open_dt,
        "close_date":        rand_date(open_dt, date(2025, 6, 30)) if status == "closed" else None,
        "exclusion_reason":  "Medical exclusion" if status == "excluded" else None,
        "numerator_flag":    status == "closed",
        "denominator_flag":  True,
        "last_service_date": rand_date(date(2024, 1, 1), date(2025, 3, 31)),
        "created_at":        pd.Timestamp("2024-01-01"),
    })

df_gaps = pd.DataFrame(gaps)
df_gaps.to_parquet(f"{OUT}/hedis_gaps.parquet", index=False)
print(f"  ✅ {len(df_gaps)} HEDIS gaps")

# ── 6. Risk scores (500) ──────────────────────────────────────────────────────
print("Generating risk scores...")
scores = []
for i, mid in enumerate(member_ids):
    tier  = random.choices(["low","moderate","high"], weights=[60,25,15])[0]
    raf   = {"low": random.uniform(0.1, 0.6),
             "moderate": random.uniform(0.6, 1.2),
             "high": random.uniform(1.2, 3.5)}[tier]
    hccs  = random.randint(0, 3) if tier == "low" else random.randint(2, 6)
    cands = [c for c in ICD10_CONDITIONS if c[4] > 0]
    top   = random.choice(cands) if hccs > 0 else ICD10_CONDITIONS[0]
    scores.append({
        "score_id":          f"SCR-{i+1:06d}",
        "member_id":         mid,
        "score_type":        "HCC",
        "score_date":        date(2025, 1, 1),
        "score_period":      "2024-Annual",
        "raf_score":         round(raf, 4),
        "risk_score_value":  round(raf, 4),
        "risk_tier":         tier,
        "hcc_count":         hccs,
        "top_hcc_code":      top[2],
        "top_hcc_weight":    top[4],
        "prospective_score": round(raf * random.uniform(0.95, 1.05), 4),
        "concurrent_score":  round(raf * random.uniform(0.90, 1.10), 4),
        "created_at":        pd.Timestamp("2024-01-01"),
    })

df_scores = pd.DataFrame(scores)
df_scores.to_parquet(f"{OUT}/risk_scores.parquet", index=False)
print(f"  ✅ {len(df_scores)} risk scores")

# ── 7. SDOH barriers (200) ────────────────────────────────────────────────────
print("Generating SDOH barriers...")
barriers      = []
sdoh_members  = random.sample(member_ids, 200)
for i, mid in enumerate(sdoh_members):
    btype = random.choice(SDOH_TYPES)
    ident = rand_date(START, END)
    barriers.append({
        "barrier_id":        f"BAR-{i+1:06d}",
        "member_id":         mid,
        "barrier_type":      btype[0],
        "barrier_category":  btype[2],
        "icd10_z_code":      btype[1],
        "identified_date":   ident,
        "resolved_date":     rand_date(ident, END) if random.random() < 0.35 else None,
        "severity":          random.choice(["mild","moderate","severe"]),
        "source":            random.choice(["screening","claim","referral"]),
        "referral_made":     random.random() > 0.4,
        "referral_org":      fake.company() if random.random() > 0.4 else None,
        "adi_score":         member_adi.get(mid, 50),
        "created_at":        pd.Timestamp("2024-01-01"),
    })

df_barriers = pd.DataFrame(barriers)
df_barriers.to_parquet(f"{OUT}/sdoh_barriers.parquet", index=False)
print(f"  ✅ {len(df_barriers)} SDOH barriers")

# ── 8. Pharmacy claims (800) ──────────────────────────────────────────────────
print("Generating pharmacy claims...")
rx_claims = []
for i in range(800):
    mid  = random.choice(member_ids)
    drug = random.choice(DRUGS)
    fill = rand_date(START, END)
    rx_claims.append({
        "rx_claim_id":    f"RX-{i+1:06d}",
        "member_id":      mid,
        "prescriber_npi": member_pcp.get(mid, random.choice(pcp_npis)),
        "dispensing_npi": f"PHARM-{random.randint(1,10):03d}",
        "fill_date":      fill,
        "ndc_code":       drug[0],
        "drug_name":      drug[1],
        "drug_class":     drug[2],
        "rxnorm_code":    drug[3],
        "days_supply":    drug[4],
        "quantity":       float(drug[4]),
        "refill_number":  random.randint(0, 5),
        "billed_amount":  round(random.uniform(10, 400), 2),
        "paid_amount":    round(random.uniform(5, 300), 2),
        "generic_flag":   random.random() > 0.3,
        "formulary_tier": random.randint(1, 4),
        "pdc_numerator":  random.random() > 0.25,
        "high_alert_flag":drug[1] == "Insulin Glargine",
        "created_at":     pd.Timestamp("2024-01-01"),
    })

df_rx = pd.DataFrame(rx_claims)
df_rx.to_parquet(f"{OUT}/pharmacy_claims.parquet", index=False)
print(f"  ✅ {len(df_rx)} pharmacy claims")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n=== Data generation complete ===")
for name, df in [("members", df_members), ("providers", df_providers),
                  ("claims", df_claims), ("diagnoses", df_diagnoses),
                  ("hedis_gaps", df_gaps), ("risk_scores", df_scores),
                  ("sdoh_barriers", df_barriers), ("pharmacy_claims", df_rx)]:
    size_kb = os.path.getsize(f"{OUT}/{name}.parquet") // 1024
    print(f"  {name:20s}: {len(df):>5d} rows  ({size_kb} KB)")
