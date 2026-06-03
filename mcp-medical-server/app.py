import streamlit as st
import pandas as pd
from google.cloud import bigquery

# 1. Configure global professional layout for the medical desk
st.set_page_config(page_title="Pharma Medical Affairs Dashboard", layout="wide")

# 2. Initialize BigQuery client
bq_client = bigquery.Client(project="medical-inquiry-agent")

st.title("🔬 Medical Affairs - MSL Copilot Dashboard")
st.markdown("---")

# 3. GLOBAL DATA FETCH: Execute the query before building columns so 'df' is universally accessible
sql_query = """
    SELECT inquiry_id, timestamp, hcp_raw_query, extracted_keywords, documents_returned_count 
    FROM `medical-inquiry-agent.telemetry_data.agent_logs`
    WHERE documents_returned_count = 0
    ORDER BY timestamp DESC
    LIMIT 10
"""

# Fetch the data into a universally accessible global dataframe
try:
    df = bq_client.query(sql_query).to_dataframe()
except Exception as e:
    st.error(f"Failed to query telemetry logs: {str(e)}")
    df = pd.DataFrame() # Fallback to empty container to prevent application crash

# 4. BUILD THE USER INTERFACE COLUMNS
col_queue, col_workbench = st.columns(2)

with col_queue:
    st.subheader("🚨 System Audit Queue (Data Gaps Detected)")
    st.caption("The following incoming HCP queries returned zero matching files from our clinical storage bucket:")
    
    if df.empty:
        st.success("🎉 Clean Audit Desk: No unresolved data gaps found in BigQuery.")
    else:
        # Display rows as an interactive data spreadsheet component
        st.dataframe(df, use_container_width=True, hide_index=True)

with col_workbench:
    st.subheader("🛠️ Actionable Review Desk")
    st.markdown("Select a specific transaction to manually review and provide data override updates.")
    
    if not df.empty:
        # Create a dropdown menu listing all logged inquiry IDs requiring attention
        selected_id = st.selectbox("Select Actionable Inquiry ID to Resolve", df["inquiry_id"].unique())
        
        # Filter the selected row data safely using loc
        active_record = df[df["inquiry_id"] == selected_id].iloc[0]
        
        st.info(f"**Raw HCP Question Asked:**\n\n {active_record['hcp_raw_query']}")
        
        # Human Input Form Fields
        st.markdown("#### MSL Human Intervention Actions")
        msl_text_override = st.text_area(
            "Paste Verified Medical Reference / Local PDF Content here:",
            placeholder="Type or paste approved clinical journal snippets or standard response template paragraphs..."
        )
        
        final_draft = st.text_area(
            "Refined Compliant Response Draft to HCP:",
            placeholder="Dear Doctor, in response to your inquiry regarding Xenotrin..."
        )
        
        # Action Dispatch Button
        if st.button("Approve, Log Override, and Dispatch to CRM"):
            if not msl_text_override or not final_draft:
                st.warning("Please complete both form inputs to document the manual override process correctly.")
            else:
                st.success(f"Transaction {selected_id} resolved! Logged by MSL, overriding response data sent to CRM tracking queue.")
                st.balloons()
    else:
        st.info("The operational review workbench is currently idle because the alert queue is clear.")
