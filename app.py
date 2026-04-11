import streamlit as st
import pandas as pd
import json
import re
from collections import defaultdict, Counter
from datetime import datetime

st.set_page_config(page_title="Operation Clean Slate", layout="wide", page_icon="🧹")

# ─────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
    .stApp { background: #0f1117; color: #e8eaf0; }
    .field-rule { font-size: 11px; color: #7b8cde; font-style: italic; }
    .badge-approved { background: #1a3a2a; color: #4ade80; padding: 2px 10px; border-radius: 99px; font-size: 12px; }
    .badge-rejected { background: #3a1a1a; color: #f87171; padding: 2px 10px; border-radius: 99px; font-size: 12px; }
    .badge-pending  { background: #2e2a1a; color: #fbbf24; padding: 2px 10px; border-radius: 99px; font-size: 12px; }
    .download-box {
        background: #1a2a1a;
        border: 1px solid #2a4a2a;
        border-radius: 10px;
        padding: 10px 14px;
        margin-top: 10px;
        margin-bottom: 6px;
    }
</style>
""", unsafe_allow_html=True)

st.title("🧹 Operation Clean Slate")
st.caption("Detect, review, and merge duplicate client records anchored by SSN.")

# ─────────────────────────────────────────────
# Session State
# ─────────────────────────────────────────────
for key, default in [
    ("decisions", {}),
    ("data_loaded", False),
    ("records", []),
    ("page", 0),
    ("merge_cache", {}),
    ("filter_status", "All"),
    ("search_ssn", ""),
]:
    if key not in st.session_state:
        st.session_state[key] = default

PAGE_SIZE = 10

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def parse_date(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=None)

def most_frequent(values):
    vals = [v for v in values if v and pd.notna(v)]
    if not vals:
        return "", 0
    c = Counter(vals)
    best, count = c.most_common(1)[0]
    return best, round((count / len(vals)) * 100)

SUFFIX_MAP = {
    r"\bstreet\b": "st", r"\bst\.\b": "st",
    r"\bavenue\b": "ave", r"\bav\b": "ave", r"\bav\.\b": "ave",
    r"\bboulevard\b": "blvd", r"\bblvd\.\b": "blvd",
    r"\bdrive\b": "dr", r"\bdr\.\b": "dr",
    r"\brd\.\b": "rd", r"\broad\b": "rd",
    r"\blane\b": "ln", r"\bln\.\b": "ln",
    r"\bcourt\b": "ct", r"\bct\.\b": "ct",
    r"\bplace\b": "pl", r"\bpl\.\b": "pl",
}

def normalize_address(addr):
    if not addr:
        return ""
    s = addr.lower().strip()
    s = re.sub(r"[,.]", " ", s)
    for pattern, replacement in SUFFIX_MAP.items():
        s = re.sub(pattern, replacement, s)
    return re.sub(r"\s+", " ", s).strip()

def addresses_match(a, b):
    return normalize_address(a) == normalize_address(b)

def best_address(records):
    groups = []
    for i, r in enumerate(records):
        placed = False
        for grp in groups:
            if addresses_match(r.get("address", ""), records[grp[0]].get("address", "")):
                grp.append(i); placed = True; break
        if not placed:
            groups.append([i])
    largest = max(groups, key=len)
    confidence = round((len(largest) / len(records)) * 100)
    latest_idx = max(largest, key=lambda i: parse_date(records[i].get("created_at", "")))
    return records[latest_idx].get("address", ""), confidence

def group_confidence(records):
    return 98 if len(set(r.get("ssn", "") for r in records)) == 1 else 60

RULES = {
    "first_name":    "most_frequent",
    "last_name":     "most_frequent",
    "date_of_birth": "most_frequent",
    "address":       "fuzzy_latest",
    "phone_number":  "most_frequent",
    "email":         "latest",
}

def merge_group(records):
    latest = max(records, key=lambda r: parse_date(r.get("created_at", "")))
    oldest = min(records, key=lambda r: parse_date(r.get("created_at", "")))
    merged, field_meta = {}, {}
    for field, rule in RULES.items():
        if rule == "most_frequent":
            val, conf = most_frequent([r.get(field, "") for r in records])
            merged[field] = val
            field_meta[field] = {"rule": "Most Frequent", "confidence": conf}
        elif rule == "fuzzy_latest":
            val, conf = best_address(records)
            merged[field] = val
            field_meta[field] = {"rule": "Fuzzy-match → Latest", "confidence": conf}
        elif rule == "latest":
            val = latest.get(field, "")
            merged[field] = val
            field_meta[field] = {"rule": "Latest Entry", "confidence": 85}
    merged["record_id"] = oldest.get("record_id", records[0].get("record_id", ""))
    merged["ssn"] = records[0].get("ssn", "")
    merged["created_at"] = latest.get("created_at", "")
    return merged, field_meta

def find_duplicates(data):
    groups = defaultdict(list)
    for record in data:
        groups[record["ssn"]].append(record)
    return (
        {ssn: recs for ssn, recs in groups.items() if len(recs) > 1},
        {ssn: recs for ssn, recs in groups.items() if len(recs) == 1}
    )

def conf_color(c):
    if c >= 85: return "#4ade80"
    if c >= 60: return "#fbbf24"
    return "#f87171"

def to_csv(records):
    if not records:
        return ""
    return pd.DataFrame(records).to_csv(index=False)

def build_outputs(duplicates, singles):
    merged_clients, removed_records, audit_logs = [], [], []
    approved_ssns = {ssn for ssn, d in st.session_state.decisions.items() if d == "approved"}
    for ssn, records in duplicates.items():
        if ssn in approved_ssns:
            if ssn not in st.session_state.merge_cache:
                st.session_state.merge_cache[ssn] = merge_group(records)
            merged, field_meta = st.session_state.merge_cache[ssn]
            merged_clients.append(merged)
            retained_id = merged["record_id"]
            removed = [r for r in records if r.get("record_id") != retained_id]
            removed_records.extend(removed)
            audit_logs.append({
                "ssn": ssn,
                "timestamp": str(datetime.now()),
                "merged_record_id": retained_id,
                "removed_record_ids": [r["record_id"] for r in removed],
                "group_confidence": group_confidence(records),
                "field_decisions": {
                    f: {"value": merged.get(f, ""),
                        "rule": field_meta[f]["rule"],
                        "confidence": field_meta[f]["confidence"]}
                    for f in RULES
                }
            })
        elif st.session_state.decisions.get(ssn) == "rejected":
            merged_clients.extend(records)
    for recs in singles.values():
        merged_clients.extend(recs)
    return merged_clients, removed_records, audit_logs

# ─────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────
uploaded_file = st.file_uploader("📂 Upload JSON File", type="json")

if uploaded_file:
    if not st.session_state.data_loaded:
        st.session_state.records = json.load(uploaded_file)
        st.session_state.data_loaded = True
        st.session_state.decisions = {}
        st.session_state.merge_cache = {}
        st.session_state.page = 0

    data = st.session_state.records
    duplicates, singles = find_duplicates(data)

    tab_review, tab_downloads, tab_quality = st.tabs([
        "🔍 Review Groups", "⬇️ Downloads", "📊 Data Quality Report"
    ])

    # ════════════════════════════════════════════
    # TAB 1 — REVIEW
    # ════════════════════════════════════════════
    with tab_review:
        st.success(f"✅ Loaded **{len(data)}** records")

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Records", len(data))
        c2.metric("Duplicate Groups", len(duplicates))
        c3.metric("Unique Clients", len(singles))

        approved_live = sum(1 for d in st.session_state.decisions.values() if d == "approved")
        rejected_live = sum(1 for d in st.session_state.decisions.values() if d == "rejected")
        pending_live  = len(duplicates) - approved_live - rejected_live

        st.markdown("#### 📈 Review Progress")
        p1, p2, p3 = st.columns(3)
        p1.metric("✅ Approved", approved_live)
        p2.metric("❌ Rejected", rejected_live)
        p3.metric("⏳ Pending",  pending_live)

        if len(duplicates) > 0:
            progress = (approved_live + rejected_live) / len(duplicates)
            st.progress(progress, text=f"{int(progress*100)}% of duplicate groups reviewed")

        st.markdown("---")

        # Bulk approve
        high_conf_pending = [
            ssn for ssn, recs in duplicates.items()
            if st.session_state.decisions.get(ssn, "pending") == "pending"
            and group_confidence(recs) >= 90
        ]
        if high_conf_pending:
            hc1, hc2 = st.columns([3, 1])
            hc1.info(f"⚡ **{len(high_conf_pending)}** high-confidence (≥90%) groups still pending.")
            with hc2:
                if st.button("Bulk Approve All", type="primary"):
                    for ssn in high_conf_pending:
                        st.session_state.decisions[ssn] = "approved"
                    st.rerun()

        # Search & Filter
        st.markdown("#### 🔎 Search & Filter")
        sf1, sf2 = st.columns([2, 1])
        with sf1:
            search = st.text_input("Search by SSN", placeholder="e.g. 123-45-6789",
                                   value=st.session_state.search_ssn,
                                   label_visibility="collapsed")
            st.session_state.search_ssn = search
        with sf2:
            filter_status = st.selectbox(
                "Filter", ["All", "Pending", "Approved", "Rejected"],
                index=["All","Pending","Approved","Rejected"].index(st.session_state.filter_status),
                label_visibility="collapsed")
            st.session_state.filter_status = filter_status

        dup_list = list(duplicates.items())
        if search:
            dup_list = [(ssn, recs) for ssn, recs in dup_list if search in ssn]
        if filter_status != "All":
            status_map = {"Pending": "pending", "Approved": "approved", "Rejected": "rejected"}
            target = status_map[filter_status]
            dup_list = [(ssn, recs) for ssn, recs in dup_list
                        if st.session_state.decisions.get(ssn, "pending") == target]

        if not dup_list:
            st.info("No groups match your search/filter.")
        else:
            total_pages = max(1, (len(dup_list) + PAGE_SIZE - 1) // PAGE_SIZE)
            st.session_state.page = min(st.session_state.page, total_pages - 1)

            nav1, nav2, nav3 = st.columns([1, 3, 1])
            with nav1:
                if st.button("⬅ Prev", disabled=(st.session_state.page == 0)):
                    st.session_state.page -= 1; st.rerun()
            with nav2:
                st.markdown(
                    f"<div style='text-align:center;padding-top:8px'>"
                    f"Page <b>{st.session_state.page+1}</b> of <b>{total_pages}</b>"
                    f" &nbsp;·&nbsp; Groups "
                    f"{st.session_state.page*PAGE_SIZE+1}–"
                    f"{min((st.session_state.page+1)*PAGE_SIZE, len(dup_list))}"
                    f" of {len(dup_list)}</div>", unsafe_allow_html=True)
            with nav3:
                if st.button("Next ➡", disabled=(st.session_state.page >= total_pages-1)):
                    st.session_state.page += 1; st.rerun()

            st.markdown("")
            page_items = dup_list[
                st.session_state.page*PAGE_SIZE:
                (st.session_state.page+1)*PAGE_SIZE
            ]

            for ssn, records in page_items:
                decision = st.session_state.decisions.get(ssn, "pending")

                if ssn not in st.session_state.merge_cache:
                    st.session_state.merge_cache[ssn] = merge_group(records)
                merged, field_meta = st.session_state.merge_cache[ssn]
                g_conf = group_confidence(records)

                badge = (
                    '<span class="badge-approved">✓ Approved</span>' if decision == "approved" else
                    '<span class="badge-rejected">✗ Rejected</span>' if decision == "rejected" else
                    '<span class="badge-pending">⏳ Pending</span>'
                )

                with st.expander(
                    f"SSN: {ssn}  |  {len(records)} records  |  Confidence: {g_conf}%",
                    expanded=(decision == "pending")
                ):
                    st.markdown(
                        f"**Status:** {badge} &nbsp;&nbsp; **Group Confidence:** "
                        f"<span style='color:{conf_color(g_conf)};font-weight:bold'>{g_conf}%</span>",
                        unsafe_allow_html=True)

                    if g_conf < 70:
                        st.warning("⚠️ Low confidence — mandatory human review required.")

                    st.markdown("#### Source Records")
                    df = pd.DataFrame(records)
                    cols = ["record_id","ssn","first_name","last_name","date_of_birth",
                            "address","phone_number","email","created_at"]
                    df = df[[c for c in cols if c in df.columns]]
                    st.dataframe(df, use_container_width=True)

                    st.markdown("#### 🔀 Proposed Merged Record")
                    for field in RULES:
                        meta = field_meta[field]
                        val  = merged.get(field, "")
                        c    = meta["confidence"]
                        st.markdown(
                            f"**{field}:** `{val}`  "
                            f"<span class='field-rule'>({meta['rule']} — "
                            f"<span style='color:{conf_color(c)}'>{c}% confident</span>)</span>",
                            unsafe_allow_html=True)
                    st.markdown(f"**record_id (retained):** `{merged['record_id']}`")
                    st.markdown(f"**created_at:** `{merged['created_at']}`")

                    # Action buttons
                    if decision == "pending":
                        b1, b2, _ = st.columns([1, 1, 5])
                        with b1:
                            if st.button("✅ Approve", key=f"approve_{ssn}"):
                                st.session_state.decisions[ssn] = "approved"
                                st.rerun()
                        with b2:
                            if st.button("❌ Reject", key=f"reject_{ssn}"):
                                st.session_state.decisions[ssn] = "rejected"
                                st.rerun()
                    else:
                        undo_col, _ = st.columns([1, 5])
                        with undo_col:
                            if st.button("↩ Undo", key=f"undo_{ssn}"):
                                st.session_state.decisions[ssn] = "pending"
                                st.rerun()

                        # ── Inline downloads (appear right after approval/rejection) ──
                        if decision == "approved":
                            retained_id = merged["record_id"]
                            removed = [r for r in records if r.get("record_id") != retained_id]
                            audit_entry = {
                                "ssn": ssn,
                                "timestamp": str(datetime.now()),
                                "merged_record_id": retained_id,
                                "removed_record_ids": [r["record_id"] for r in removed],
                                "group_confidence": g_conf,
                                "field_decisions": {
                                    f: {"value": merged.get(f, ""),
                                        "rule": field_meta[f]["rule"],
                                        "confidence": field_meta[f]["confidence"]}
                                    for f in RULES
                                }
                            }
                            st.markdown(
                                "<div class='download-box'>📥 <b>Download this group's data:</b></div>",
                                unsafe_allow_html=True)
                            dl1, dl2, dl3, dl4 = st.columns(4)
                            with dl1:
                                st.download_button(
                                    "Merged Record (JSON)",
                                    data=json.dumps(merged, indent=2),
                                    file_name=f"merged_{ssn.replace('-','_')}.json",
                                    mime="application/json",
                                    key=f"dl_merged_{ssn}")
                            with dl2:
                                st.download_button(
                                    "Removed Records (JSON)",
                                    data=json.dumps(removed, indent=2),
                                    file_name=f"removed_{ssn.replace('-','_')}.json",
                                    mime="application/json",
                                    key=f"dl_removed_{ssn}")
                            with dl3:
                                st.download_button(
                                    "Audit Entry (JSON)",
                                    data=json.dumps(audit_entry, indent=2),
                                    file_name=f"audit_{ssn.replace('-','_')}.json",
                                    mime="application/json",
                                    key=f"dl_audit_{ssn}")
                            with dl4:
                                st.download_button(
                                    "All Records (CSV)",
                                    data=to_csv([merged] + removed),
                                    file_name=f"group_{ssn.replace('-','_')}.csv",
                                    mime="text/csv",
                                    key=f"dl_csv_{ssn}")

            # Bottom nav
            st.markdown("")
            bn1, _, bn3 = st.columns([1, 3, 1])
            with bn1:
                if st.button("⬅ Prev ", disabled=(st.session_state.page == 0)):
                    st.session_state.page -= 1; st.rerun()
            with bn3:
                if st.button(" Next ➡", disabled=(st.session_state.page >= total_pages-1)):
                    st.session_state.page += 1; st.rerun()

    # ════════════════════════════════════════════
    # TAB 2 — DOWNLOADS
    # ════════════════════════════════════════════
    with tab_downloads:
        merged_clients, removed_records, audit_logs = build_outputs(duplicates, singles)
        approved_count = sum(1 for d in st.session_state.decisions.values() if d == "approved")
        rejected_count = sum(1 for d in st.session_state.decisions.values() if d == "rejected")

        st.markdown("### ⬇️ Export All Output Files")
        st.markdown(
            f"**{approved_count}** approved · **{rejected_count}** rejected · "
            f"**{len(duplicates) - approved_count - rejected_count}** pending")

        if approved_count == 0:
            st.warning("Approve at least one group in the Review tab to enable downloads.")
        else:
            st.markdown("#### JSON Format")
            j1, j2, j3 = st.columns(3)
            with j1:
                st.download_button("📄 merged_clients.json",
                    data=json.dumps(merged_clients, indent=2),
                    file_name="merged_clients.json", mime="application/json")
            with j2:
                st.download_button("🗑️ duplicates_removed.json",
                    data=json.dumps(removed_records, indent=2),
                    file_name="duplicates_removed.json", mime="application/json")
            with j3:
                st.download_button("📋 audit_log.json",
                    data=json.dumps(audit_logs, indent=2),
                    file_name="audit_log.json", mime="application/json")

            st.markdown("#### CSV Format")
            cv1, cv2, cv3 = st.columns(3)
            with cv1:
                st.download_button("📄 merged_clients.csv",
                    data=to_csv(merged_clients),
                    file_name="merged_clients.csv", mime="text/csv")
            with cv2:
                st.download_button("🗑️ duplicates_removed.csv",
                    data=to_csv(removed_records),
                    file_name="duplicates_removed.csv", mime="text/csv")
            with cv3:
                # Flatten audit log for CSV
                audit_flat = []
                for entry in audit_logs:
                    row = {k: v for k, v in entry.items() if k != "field_decisions"}
                    row["removed_record_ids"] = ", ".join(entry.get("removed_record_ids", []))
                    for f, meta in entry.get("field_decisions", {}).items():
                        row[f"{f}_value"]      = meta["value"]
                        row[f"{f}_rule"]       = meta["rule"]
                        row[f"{f}_confidence"] = meta["confidence"]
                    audit_flat.append(row)
                st.download_button("📋 audit_log.csv",
                    data=to_csv(audit_flat),
                    file_name="audit_log.csv", mime="text/csv")

            st.markdown("#### 📸 Summary Snapshot")
            summary = {
                "generated_at": str(datetime.now()),
                "total_input_records": len(data),
                "duplicate_groups_found": len(duplicates),
                "groups_approved": approved_count,
                "groups_rejected": rejected_count,
                "groups_pending": len(duplicates) - approved_count - rejected_count,
                "canonical_records_output": len(merged_clients),
                "records_purged": len(removed_records),
            }
            st.json(summary)
            st.download_button("📊 Download Summary (JSON)",
                data=json.dumps(summary, indent=2),
                file_name="summary.json", mime="application/json")

    # ════════════════════════════════════════════
    # TAB 3 — DATA QUALITY REPORT
    # ════════════════════════════════════════════
    with tab_quality:
        st.markdown("### 📊 Data Quality Report")

        approved_live = sum(1 for d in st.session_state.decisions.values() if d == "approved")
        dup_rate = len(duplicates) / len(data) if data else 0
        quality_before = round((1 - dup_rate) * 100, 1)
        remaining_dups = len(duplicates) - approved_live
        quality_after  = round((1 - remaining_dups / len(data)) * 100, 1) if data else 100
        records_to_purge = sum(
            len(duplicates[ssn]) - 1
            for ssn, d in st.session_state.decisions.items()
            if d == "approved" and ssn in duplicates
        )

        q1, q2, q3 = st.columns(3)
        q1.metric("Quality Score (Before)", f"{quality_before}%")
        q2.metric("Quality Score (After Approvals)", f"{quality_after}%",
                  delta=f"+{round(quality_after - quality_before, 1)}%")
        q3.metric("Records to be Purged", records_to_purge)

        st.markdown("---")

        # Group size distribution
        st.markdown("#### Duplicate Group Size Distribution")
        size_counts = Counter(len(recs) for recs in duplicates.values())
        size_df = pd.DataFrame(
            sorted(size_counts.items()),
            columns=["Group Size (# records)", "Number of Groups"]
        )
        st.bar_chart(size_df.set_index("Group Size (# records)"))

        # Field conflict analysis
        st.markdown("#### Field Conflict Analysis")
        st.caption("How many duplicate groups have at least one conflicting value per field?")
        conflict_data = []
        for field in ["first_name", "last_name", "date_of_birth", "address", "phone_number", "email"]:
            conflicts = sum(
                1 for recs in duplicates.values()
                if len(set(r.get(field, "").strip().lower() for r in recs if r.get(field))) > 1
            )
            conflict_data.append({
                "Field": field,
                "Groups with Conflict": conflicts,
                "Conflict Rate": f"{round(conflicts/len(duplicates)*100,1)}%" if duplicates else "0%"
            })
        conflict_df = pd.DataFrame(conflict_data)
        st.dataframe(conflict_df, use_container_width=True, hide_index=True)
        st.bar_chart(conflict_df.set_index("Field")["Groups with Conflict"])

        # Top 10 SSNs with most records
        st.markdown("#### Top 10 SSNs by Record Count")
        top_ssns = sorted(duplicates.items(), key=lambda x: len(x[1]), reverse=True)[:10]
        top_df = pd.DataFrame([
            {"SSN": ssn,
             "Record Count": len(recs),
             "Status": st.session_state.decisions.get(ssn, "pending").title()}
            for ssn, recs in top_ssns
        ])
        st.dataframe(top_df, use_container_width=True, hide_index=True)

        # Decision breakdown
        st.markdown("#### Decision Breakdown")
        decisions_count = Counter(
            st.session_state.decisions.get(ssn, "pending") for ssn in duplicates
        )
        dec_df = pd.DataFrame([
            {"Status": k.title(), "Count": v} for k, v in decisions_count.items()
        ])
        st.dataframe(dec_df, use_container_width=True, hide_index=True)