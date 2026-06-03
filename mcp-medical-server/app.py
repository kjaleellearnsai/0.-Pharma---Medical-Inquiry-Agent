import streamlit as st
import pandas as pd
import pg8000.native

# 1. Configure global professional layout for the medical desk
st.set_page_config(page_title="Pharma Medical Affairs Dashboard", layout="wide")

# 2. Connection function targeting our running local proxy tunnel
def get_db_connection():
    return pg8000.native.Connection(
        user="postgres",
        password="SecurePharmaPass2026!",
        host="127.0.0.1",
        port=5432,
        database="postgres"
    )

st.title("🔬 Medical Affairs - MSL Copilot Dashboard")
st.markdown("---")


# 3. GLOBAL DATA FETCH: Read outstanding gaps and safety instances
try:
    db = get_db_connection()
    
    # Execute query (returns a standard Python list of rows)
    rows = db.run(
        "SELECT inquiry_id, timestamp, hcp_raw_query, extracted_keywords, status "
        "FROM inbound_data_gaps "
        "WHERE status != 'RESOLVED' "
        "ORDER BY CASE WHEN status = 'CRITICAL_SAFETY_ALERT' THEN 1 ELSE 2 END, timestamp DESC"
    )
    
    # Extract the column names directly from the db connection metadata
    column_names = [col['name'] for col in db.columns] if db.columns else ["inquiry_id", "timestamp", "hcp_raw_query", "extracted_keywords", "status"]
    
    # Construct DataFrame by matching the list of rows to the extracted column headers
    df = pd.DataFrame(rows, columns=column_names) if rows else pd.DataFrame(columns=column_names)
    
finally:
    db.close()


# 4. BUILD THE USER INTERFACE COLUMNS
col_queue, col_workbench = st.columns(2)

with col_queue:
    st.subheader("🚨 Transactional Operation Desk Queue")
    st.caption("Active data gaps and critical safety alerts fetched directly from Cloud SQL:")
    
    if df.empty:
        st.success("🎉 Operational Review Queue is Clear. Excellent job!")
    else:
        # Loop through rows and display color-coded status banners
        for index, row in df.iterrows():
            if row['status'] == 'CRITICAL_SAFETY_ALERT':
                st.error(
                    f"⚠️ **CRITICAL SAFETY ALERT** | ID: `{row['inquiry_id']}`\n\n"
                    f"**HCP Question:** {row['hcp_raw_query']}"
                )
            else:
                st.warning(
                    f"📋 **Standard Data Gap** | ID: `{row['inquiry_id']}`\n\n"
                    f"**HCP Question:** {row['hcp_raw_query']}"
                )

with col_workbench:
    st.subheader("🛠️ Active Manual Override Workbench")
    st.markdown("Select an outstanding case to view parameters and process responses.")
    
    if not df.empty:
        selected_id = st.selectbox("Select Transaction to Process", df["inquiry_id"].unique())
        
        # Safely isolate the chosen record row
        active_record = df[df["inquiry_id"] == selected_id].iloc[0]
        
        # UI Safety Constraint: If an item is an adverse event, lock the input fields
        if active_record['status'] == 'CRITICAL_SAFETY_ALERT':
            st.error(
                "🔒 **Safety Case Locked**: This inquiry contains an adverse event and has been fully automated "
                "and dispatched straight to Global Pharmacovigilance via Pub/Sub. No text modification is permitted here."
            )
            # Render a disabled template button for strict compliance tracking
            st.button("Archive Safety Alert Record from UI Desk", disabled=True)
        else:
            st.info(f"**Standard Data Gap Question:**\n\n {active_record['hcp_raw_query']}")
            
            # Interactive Review Form
            msl_override = st.text_area("Paste Verified Reference Literature:")
            final_draft = st.text_area("Refined Compliant Response Draft to HCP:")
            
            if st.button("Approve and Dispatch to CRM"):
                if not msl_override or not final_draft:
                    st.warning("Please complete both form inputs before submission.")
                else:
                    # Write resolution back to Cloud SQL and mark case as RESOLVED
                    db = get_db_connection()
                    try:
                        db.run(
                            "INSERT INTO msl_resolutions (inquiry_id, msl_pasted_reference, final_approved_response) "
                            "VALUES (:id, :ref, :draft)", 
                            id=selected_id, ref=msl_override, draft=final_draft
                        )
                        db.run(
                            "UPDATE inbound_data_gaps SET status = 'RESOLVED' WHERE inquiry_id = :id", 
                            id=selected_id
                        )
                    finally:
                        db.close()
                        
                    st.success("Transaction successfully updated to RESOLVED state.")
                    st.balloons()
                    st.rerun()
    else:
        st.info("The operational review workbench is currently idle because the alert queue is clear.")
