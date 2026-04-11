import streamlit as st
import pandas as pd
import json
import re
from collections import defaultdict, Counter
from datetime import datetime

st.set_page_config(page_title="Operation Clean Slate", layout="wide")

# ─────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
    .stApp { background: #0f1117; color: #e8eaf0; }
    .group-card {
        background: #1a1d27;
        border: 1px solid #2e3147;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 24px;
    }
    .confidence-bar {
        background: #2e3147;
        border-radius: 4px;
        height: 6px;
        margin-top: 4px;
    }
    .field-rule { font-size: 11px; color: #7b8cde; font-style: italic; }
    .badge-approved { background: #1a3a2a; color: #4ade80; padding: 2px 10px; border-radius: 99px; font-size: 12px; }
    .badge-rejected { background: #3a1a1a; color: #f87171; padding: 2px 10px; border-radius: 99px; font-size: 12px; }
    .badge-pending  { background: #2e2a1a; color: #fbbf24; padding: 2px 10px; border-radius: 99px; font-size: 12px; }
</style>
""", unsafe_allow_html=True)

st.title("🧹 Operation Clean Slate")
st.caption("Detect, review, and merge duplicate client records anchored by SSN.")

# ─────────────────────────────────────────────
# Session State Init
# ─────────────────────────────────────────────
if "decisions" not in st.session_state:
    st.session_state.decisions = {}   # ssn -> "approved" | "rejected"
if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False
if "records" not in st.session_state:
    st.session_state.records = []
if "page" not in st.session_state:
    st.session_state.page = 0
if "merge_cache" not in st.session_state:
    st.session_state.merge_cache = {}  # ssn -> (merged, field_meta)

PAGE_SIZE = 10

# ─────────────────────────────────────────────
# Helper: Date Parsing
# ─────────────────────────────────────────────
def parse_date(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=None)

# ─────────────────────────────────────────────
# Helper: Most Frequent
# ─────────────────────────────────────────────
def most_frequent(values):
    vals = [v for v in values if v and pd.notna(v)]
    if not vals:
        return "", 0
    c = Counter(vals)
    best, count = c.most_common(1)[0]
    confidence = round((count / len(vals)) * 100)
    return best, confidence

# ─────────────────────────────────────────────
# Helper: Normalize Address for Fuzzy Matching
# ─────────────────────────────────────────────
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
    """Fuzzy-group addresses, pick group with most members, then latest record in that group."""
    addrs = [r.get("address", "") for r in records]
    groups = []
    for i, r in enumerate(records):
        placed = False
        for grp in groups:
            if addresses_match(r.get("address", ""), records[grp[0]].get("address", "")):
                grp.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])

    # largest group wins
    largest = max(groups, key=len)
    group_size = len(largest)
    confidence = round((group_size / len(records)) * 100)

    # latest in that group
    latest_idx = max(largest, key=lambda i: parse_date(records[i].get("created_at", "")))
    return records[latest_idx].get("address", ""), confidence

# ─────────────────────────────────────────────
# Group-level confidence (exact SSN = high)
# ─────────────────────────────────────────────
def group_confidence(records):
    ssns = [r.get("ssn", "") for r in records]
    if len(set(ssns)) == 1:
        return 98  # exact SSN match
    return 60  # fuzzy / near-match group

# ─────────────────────────────────────────────
# Merge Recommendation
# ─────────────────────────────────────────────
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

    merged = {}
    field_meta = {}  # field -> {rule, confidence, value}

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

# ─────────────────────────────────────────────
# Find Duplicates
# ─────────────────────────────────────────────
def find_duplicates(data):
    groups = defaultdict(list)
    for record in data:
        groups[record["ssn"]].append(record)
    duplicates = {ssn: recs for ssn, recs in groups.items() if len(recs) > 1}
    singles    = {ssn: recs for ssn, recs in groups.items() if len(recs) == 1}
    return duplicates, singles

# ─────────────────────────────────────────────
# Confidence Color
# ─────────────────────────────────────────────
def conf_color(c):
    if c >= 85: return "#4ade80"
    if c >= 60: return "#fbbf24"
    return "#f87171"

# ─────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────
uploaded_file = st.file_uploader("Upload JSON File", type="json")

if uploaded_file:
    if not st.session_state.data_loaded:
        st.session_state.records = json.load(uploaded_file)
        st.session_state.data_loaded = True
        st.session_state.decisions = {}

    data = st.session_state.records
    st.success(f"✅ Loaded **{len(data)}** records")

    duplicates, singles = find_duplicates(data)

    # ── Summary Metrics ──
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Records", len(data))
    col2.metric("Duplicate Groups", len(duplicates))
    col3.metric("Unique Clients (no duplicates)", len(singles))

    # ── Live Progress Metrics ──
    approved_count_live = sum(1 for d in st.session_state.decisions.values() if d == "approved")
    rejected_count_live = sum(1 for d in st.session_state.decisions.values() if d == "rejected")
    pending_count_live  = len(duplicates) - approved_count_live - rejected_count_live

    st.markdown("#### 📈 Review Progress")
    p1, p2, p3 = st.columns(3)
    p1.metric("✅ Approved", approved_count_live)
    p2.metric("❌ Rejected", rejected_count_live)
    p3.metric("⏳ Pending",  pending_count_live)

    # Progress bar
    if len(duplicates) > 0:
        progress = (approved_count_live + rejected_count_live) / len(duplicates)
        st.progress(progress, text=f"{int(progress*100)}% of duplicate groups reviewed")

    st.markdown("---")
    st.subheader("🔍 Duplicate Groups — Review & Approve")

    # ── Pagination Controls ──
    dup_list = list(duplicates.items())  # [(ssn, records), ...]
    total_pages = max(1, (len(dup_list) + PAGE_SIZE - 1) // PAGE_SIZE)
    st.session_state.page = min(st.session_state.page, total_pages - 1)

    nav1, nav2, nav3 = st.columns([1, 3, 1])
    with nav1:
        if st.button("⬅ Prev", disabled=(st.session_state.page == 0)):
            st.session_state.page -= 1
            st.rerun()
    with nav2:
        st.markdown(
            f"<div style='text-align:center;padding-top:8px'>Page "
            f"<b>{st.session_state.page + 1}</b> of <b>{total_pages}</b> "
            f"&nbsp;·&nbsp; showing groups "
            f"{st.session_state.page * PAGE_SIZE + 1}–"
            f"{min((st.session_state.page + 1) * PAGE_SIZE, len(dup_list))} "
            f"of {len(dup_list)}</div>",
            unsafe_allow_html=True
        )
    with nav3:
        if st.button("Next ➡", disabled=(st.session_state.page >= total_pages - 1)):
            st.session_state.page += 1
            st.rerun()

    st.markdown("")

    # ── Render only current page ──
    page_items = dup_list[
        st.session_state.page * PAGE_SIZE :
        (st.session_state.page + 1) * PAGE_SIZE
    ]

    for ssn, records in page_items:
        decision = st.session_state.decisions.get(ssn, "pending")

        # Cache merge computation so it's not repeated every rerun
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
            f"SSN: {ssn}  |  {len(records)} records  |  Group Confidence: {g_conf}%",
            expanded=(decision == "pending")
        ):
            st.markdown(f"**Status:** {badge}  &nbsp;&nbsp; **Group Confidence:** "
                        f"<span style='color:{conf_color(g_conf)};font-weight:bold'>{g_conf}%</span>",
                        unsafe_allow_html=True)

            if g_conf < 70:
                st.warning("⚠️ Low confidence group — **mandatory human review required**.")

            # Source records side-by-side
            st.markdown("#### Source Records")
            df = pd.DataFrame(records)
            cols = ["record_id","ssn","first_name","last_name","date_of_birth","address","phone_number","email","created_at"]
            df = df[[c for c in cols if c in df.columns]]
            st.dataframe(df, use_container_width=True)

            # Proposed merged record
            st.markdown("#### 🔀 Proposed Merged Record")
            for field, rule in RULES.items():
                meta = field_meta[field]
                val  = merged.get(field, "")
                c    = meta["confidence"]
                st.markdown(
                    f"**{field}:** `{val}`  "
                    f"<span class='field-rule'>({meta['rule']} — "
                    f"<span style='color:{conf_color(c)}'>{c}% confident</span>)</span>",
                    unsafe_allow_html=True
                )

            st.markdown(f"**record_id (retained):** `{merged['record_id']}`")
            st.markdown(f"**created_at:** `{merged['created_at']}`")

            # Approve / Reject buttons
            if decision == "pending":
                c1, c2, _ = st.columns([1, 1, 6])
                with c1:
                    if st.button("✅ Approve", key=f"approve_{ssn}"):
                        st.session_state.decisions[ssn] = "approved"
                        st.rerun()
                with c2:
                    if st.button("❌ Reject", key=f"reject_{ssn}"):
                        st.session_state.decisions[ssn] = "rejected"
                        st.rerun()
            else:
                if st.button("↩ Undo Decision", key=f"undo_{ssn}"):
                    st.session_state.decisions[ssn] = "pending"
                    st.rerun()

    # Bottom pagination (repeat for convenience)
    st.markdown("")
    b1, b2, b3 = st.columns([1, 3, 1])
    with b1:
        if st.button("⬅ Prev ", disabled=(st.session_state.page == 0)):
            st.session_state.page -= 1
            st.rerun()
    with b3:
        if st.button(" Next ➡", disabled=(st.session_state.page >= total_pages - 1)):
            st.session_state.page += 1
            st.rerun()

    # ── Bulk Approve ──
    st.markdown("---")
    high_conf_pending = [
        ssn for ssn, recs in duplicates.items()
        if st.session_state.decisions.get(ssn, "pending") == "pending"
        and group_confidence(recs) >= 90
    ]
    if high_conf_pending:
        st.info(f"**{len(high_conf_pending)}** high-confidence group(s) (≥90%) are still pending.")
        if st.button("⚡ Bulk Approve All High-Confidence Groups"):
            for ssn in high_conf_pending:
                st.session_state.decisions[ssn] = "approved"
            st.rerun()

    # ── Build Output Files ──
    merged_clients   = []
    removed_records  = []
    audit_logs       = []

    approved_ssns = {ssn for ssn, d in st.session_state.decisions.items() if d == "approved"}

    for ssn, records in duplicates.items():
        if ssn in approved_ssns:
            merged, field_meta = merge_group(records)
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
                    field: {
                        "value": merged.get(field, ""),
                        "rule": field_meta[field]["rule"],
                        "confidence": field_meta[field]["confidence"]
                    }
                    for field in RULES
                }
            })
        # rejected groups: keep all records as-is (no merge)
        elif st.session_state.decisions.get(ssn) == "rejected":
            merged_clients.extend(records)

    # Add single (non-duplicate) records
    for ssn, recs in singles.items():
        merged_clients.extend(recs)

    # ── Downloads ──
    st.subheader("⬇️ Download Output Files")

    approved_count = len(approved_ssns)
    st.markdown(f"**{approved_count}** group(s) approved and ready for export.")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "📄 merged_clients.json",
            data=json.dumps(merged_clients, indent=2),
            file_name="merged_clients.json",
            mime="application/json",
            disabled=(approved_count == 0)
        )
    with col2:
        st.download_button(
            "🗑️ duplicates_removed.json",
            data=json.dumps(removed_records, indent=2),
            file_name="duplicates_removed.json",
            mime="application/json",
            disabled=(approved_count == 0)
        )
    with col3:
        st.download_button(
            "📋 audit_log.json",
            data=json.dumps(audit_logs, indent=2),
            file_name="audit_log.json",
            mime="application/json",
            disabled=(approved_count == 0)
        )

    if approved_count == 0:
        st.caption("Approve at least one group above to enable downloads.")
