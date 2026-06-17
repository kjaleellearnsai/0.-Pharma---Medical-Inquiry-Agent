import streamlit as st
import pandas as pd
import pg8000.native
import os

# Configure global professional layout for the medical desk
st.set_page_config(page_title="Pharma Medical Affairs Dashboard", layout="wide")

# 🟢 ENVIRONMENT-AWARE ROUTING: Detects if running on GCP or local desktop machine
def get_db_connection():
    db_password = os.getenv("DB_PASSWORD", "").strip()
    if not db_password:
        raise RuntimeError("DB_PASSWORD environment variable is required.")

    # Cloud Run automatically injects K_SERVICE or Cloud SQL variables into active container threads
    if os.getenv("K_SERVICE") or os.path.exists("/cloudsql"):
        # Live Cloud connection socket path matching your main.py setup
        return pg8000.native.Connection(
            user="postgres",
            password=db_password,
            database="postgres",
            unix_sock="/cloudsql/medical-inquiry-agent:us-central1:pharma-dashboard-db/.s.PGSQL.5432"
        )
    else:
        # Fallback local testing parameters using your running desktop proxy tunnel
        return pg8000.native.Connection(
            user="postgres",
            password=db_password,
            host="127.0.0.1",
            port=5432,
            database="postgres"
        )

st.title("🔬 Medical Affairs - MSL Copilot Dashboard")
st.markdown("---")

# Toggle between open action items and full historical audits
view_mode = st.sidebar.radio("Select Dashboard View Mode:", ("Active Action Queue", "Full Historical Audit Log"))

# 🟢 ROBUST SCOPE INITIALIZATION: Initialize variable as None to eliminate NameErrors
db = None
df = pd.DataFrame()

try:
    db = get_db_connection()
    
    if view_mode == "Active Action Queue":
        rows = db.run(
            "SELECT inquiry_id, timestamp, hcp_raw_query, extracted_keywords, status "
            "FROM inbound_data_gaps "
            "WHERE status NOT IN ('RESOLVED_BY_AI', 'RESOLVED_BY_HITL') "
            "ORDER BY CASE WHEN status = 'CRITICAL_SAFETY_ALERT' THEN 1 ELSE 2 END, timestamp DESC"
        )
    else:
        rows = db.run(
            "SELECT inquiry_id, timestamp, hcp_raw_query, extracted_keywords, status "
            "FROM inbound_data_gaps "
            "ORDER BY timestamp DESC LIMIT 50"
        )
        
    column_names = [col['name'] for col in db.columns] if db.columns else ["inquiry_id", "timestamp", "hcp_raw_query", "extracted_keywords", "status"]
    df = pd.DataFrame(rows, columns=column_names) if rows else pd.DataFrame(columns=column_names)
except Exception as conn_err:
    st.error(f"❌ Database Connectivity Error: Could not reach the Cloud SQL instance backend. Details: {str(conn_err)}")
finally:
    # 🟢 SAFE CLEANUP LAYER: Only call close() if the connection variable was successfully initialized
    if db is not None:
        db.close()


# DYNAMIC LAYOUT ENGINE SWITCHBOARD
if view_mode == "Full Historical Audit Log":
    st.subheader("📋 Comprehensive Regulatory Audit Lake")
    st.caption("Complete, immutable history of all automated AI transactions and human decisions:")
    st.info("ℹ️ **Audit Workspace Active**: The manual processing workbench is disabled while viewing historical audit logs. Switch back to 'Active Action Queue' to resolve outstanding items.")
    
    if df.empty:
        st.success("🎉 No audit history found in Cloud SQL.")
    else:
        st.dataframe(
            df, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "inquiry_id": st.column_config.TextColumn(
                    "Inquiry ID Reference",
                    width="small",
                    help="Unique regulatory tracking UUID for this transaction."
                )
            }
        )

else:
    col_queue, col_workbench = st.columns(2)
    
    with col_queue:
        st.subheader("🚨 Transactional Operation Desk Queue")
        st.caption("Outstanding issues requiring human intervention or documentation updates:")
        
        if df.empty:
            st.success("🎉 Queue clear. All records processed successfully.")
        else:
            for index, row in df.iterrows():
                if row['status'] == 'CRITICAL_SAFETY_ALERT':
                    st.error(f"⚠️ **CRITICAL SAFETY ALERT** | ID: `{row['inquiry_id']}`\n\n**HCP Question:** {row['hcp_raw_query']}")
                else:
                    st.warning(f"📋 **Standard Data Gap** | ID: `{row['inquiry_id']}`\n\n**HCP Question:** {row['hcp_raw_query']}")

    with col_workbench:
        st.subheader("🛠️ Active Manual Override Workbench")
        st.markdown("Select an outstanding case to view parameters and process responses.")
        
        if not df.empty:
            unique_ids = list(df["inquiry_id"].unique())
            dropdown_options = ["---Select Transaction---"] + unique_ids
            
            selected_id = st.selectbox("Select Transaction to Process", options=dropdown_options)
            
            if selected_id == "---Select Transaction---":
                st.info("💡 **Desk Standby**: Please choose a specific transaction ID from the selection menu above to open the manual processing form fields.")
            else:
                active_record = df[df["inquiry_id"] == selected_id].iloc[0]
                
                if active_record['status'] == 'CRITICAL_SAFETY_ALERT':
                    st.error("🔒 **Safety Case Locked**: This inquiry contains an adverse event and has been fully automated and routed straight to Global Pharmacovigilance. No human modification permitted here.")
                    if st.button("Archive Safety Alert Record from UI Desk"):
                        db_action = None
                        try:
                            db_action = get_db_connection()
                            db_action.run("UPDATE inbound_data_gaps SET status = 'RESOLVED_BY_HITL' WHERE inquiry_id = :id", id=selected_id)
                            st.success("Safety alert archived from the active desk.")
                            st.rerun()
                        finally:
                            if db_action is not None:
                                db_action.close()
                else:
                    st.info(f"**Standard Data Gap Question:**\n\n {active_record['hcp_raw_query']}")
                    msl_override = st.text_area("Paste Verified Reference Literature:")
                    final_draft = st.text_area("Refined Compliant Response Draft to HCP:")
                    
                    if st.button("Approve, Log Override, and Dispatch to CRM"):
                        if not msl_override or not final_draft:
                            st.warning("Please complete both form inputs to document the manual override process correctly.")
                        else:
                            db_action = None
                            try:
                                db_action = get_db_connection()
                                db_action.run(
                                    "INSERT INTO msl_resolutions (inquiry_id, msl_pasted_reference, final_approved_response) VALUES (:id, :ref, :draft)",
                                    id=selected_id, ref=msl_override, draft=final_draft
                                )
                                db_action.run("UPDATE inbound_data_gaps SET status = 'RESOLVED_BY_HITL' WHERE inquiry_id = :id", id=selected_id)
                                st.success("Transaction updated to RESOLVED_BY_HITL state.")
                                st.balloons()
                                st.rerun()
                            finally:
                                if db_action is not None:
                                    db_action.close()
        else:
            st.info("The operational review workbench is currently idle because the alert queue is clear.")
            