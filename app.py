import streamlit as st
import pandas as pd
import json
from collections import defaultdict, Counter
from datetime import datetime

st.set_page_config(page_title="Operation Clean Slate", layout="wide")
st.title("🧹 Operation Clean Slate")
st.write("Upload JSON file and detect duplicate client records.")

# -------------------------------
# Helper Functions
# -------------------------------

def parse_date(x):
    try:
        return datetime.fromisoformat(x.replace("Z", ""))
    except:
        return datetime.min

def most_frequent(values):
    values = [v for v in values if pd.notna(v)]
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]

def latest_record(records):
    return max(records, key=lambda r: parse_date(r["created_at"]))

def merge_group(records):
    latest = latest_record(records)

    merged = {
        "record_id": records[0]["record_id"],   # keep oldest/first
        "ssn": records[0]["ssn"],
        "first_name": most_frequent([r["first_name"] for r in records]),
        "last_name": most_frequent([r["last_name"] for r in records]),
        "date_of_birth": most_frequent([r["date_of_birth"] for r in records]),
        "address": latest["address"],
        "phone_number": most_frequent([r["phone_number"] for r in records]),
        "email": latest["email"],
        "created_at": latest["created_at"]
    }

    return merged

def find_duplicates(data):
    groups = defaultdict(list)
    for record in data:
        groups[record["ssn"]].append(record)

    duplicates = {ssn: recs for ssn, recs in groups.items() if len(recs) > 1}
    singles = {ssn: recs for ssn, recs in groups.items() if len(recs) == 1}

    return duplicates, singles

# -------------------------------
# Upload File
# -------------------------------

uploaded_file = st.file_uploader("Upload JSON File", type="json")

if uploaded_file:
    data = json.load(uploaded_file)

    st.success(f"Loaded {len(data)} records")

    duplicates, singles = find_duplicates(data)

    st.subheader("📊 Summary")
    st.write("Duplicate Groups:", len(duplicates))
    st.write("Unique Clients:", len(singles))

    merged_clients = []
    removed_records = []
    audit_logs = []

    # -------------------------------
    # Show Duplicate Groups
    # -------------------------------
    for ssn, records in duplicates.items():
        st.markdown("---")
        st.subheader(f"Duplicate Group - SSN: {ssn}")

        df = pd.DataFrame(records)
        st.dataframe(df)

        merged = merge_group(records)

        st.write("### Recommended Merged Record")
        st.json(merged)

        approve = st.button(f"Approve Merge {ssn}")

        if approve:
            merged_clients.append(merged)
            removed = records[1:]

            removed_records.extend(removed)

            audit_logs.append({
                "ssn": ssn,
                "merged_record_id": merged["record_id"],
                "removed_records": [r["record_id"] for r in removed],
                "timestamp": str(datetime.now())
            })

            st.success(f"Approved merge for {ssn}")

    # Add single records directly
    for ssn, recs in singles.items():
        merged_clients.extend(recs)

    # -------------------------------
    # Downloads
    # -------------------------------
    st.markdown("---")
    st.subheader("⬇ Download Output Files")

    st.download_button(
        "Download merged_clients.json",
        data=json.dumps(merged_clients, indent=2),
        file_name="merged_clients.json",
        mime="application/json"
    )

    st.download_button(
        "Download duplicates_removed.json",
        data=json.dumps(removed_records, indent=2),
        file_name="duplicates_removed.json",
        mime="application/json"
    )

    st.download_button(
        "Download audit_log.json",
        data=json.dumps(audit_logs, indent=2),
        file_name="audit_log.json",
        mime="application/json"
    )