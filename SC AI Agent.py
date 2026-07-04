import streamlit as st
import pandas as pd
from cryptography.hazmat.primitives import serialization
import io
from datetime import datetime

# --- 1. SNOWFLAKE CONNECTION SETUP ---
def get_snowflake_conn():
    pem_private_key_str = st.secrets["connections"]["snowflake"]["private_key"]
    private_key_obj = serialization.load_pem_private_key(
        pem_private_key_str.encode('utf-8'),
        password=None,
    )
    private_key_der = private_key_obj.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    return st.connection("snowflake", type="snowflake", private_key=private_key_der)

conn = get_snowflake_conn()

# --- Initialize Change Log Table if not exists ---
def init_change_log():
    try:
        conn.query("""
            CREATE TABLE IF NOT EXISTS ECOLAB_SC_POC.PUBLIC.CHANGE_LOG (
                TIMESTAMP TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                ACTION_TYPE VARCHAR,
                RECORD_ID VARCHAR,
                COLUMN_CHANGED VARCHAR,
                OLD_VALUE VARCHAR,
                NEW_VALUE VARCHAR
            )
        """)
    except Exception as e:
        st.error(f"Failed to initialize change log table: {e}")

init_change_log()

# --- 2. CUSTOM THEMING (Ecolab Blue & Castrol Green) ---
st.set_page_config(page_title="SC Control Tower", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #ffffff; }
    h1 { color: #0072CE; } /* Ecolab Blue */
    h3 { color: #008240; } /* Castrol Green */
    .stDataFrame { border: 1px solid #e6e9ef; border-radius: 5px; }
    footer { visibility: hidden; }
    .custom-footer {
        text-align: center;
        padding: 20px;
        color: #666;
        font-size: 14px;
        border-top: 1px solid #eee;
        margin-top: 50px;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 3. TITLE & HEADER ---
st.title("SC Control Tower by Hitesh")
st.markdown("### AI Agent and Data Visualization")

business_logic = """
You are a Snowflake SQL expert. Use these EXACT table and column names:

1. SALES TABLE: ECOLAB_SC_POC.PUBLIC.SALES_DATA
   - Use 'ORDER_RECEIVE_DATE' for date on which sales order was booked.
   - Use 'ACTUAL_SHIP_DATE' for date on which item or material is actually shipped from warehouse or plant.
   - Use 'SHIP_QUANTITY' for actual volume sold or shipped to customer.
   - Use 'ORDER_QUANTITY' for volume originally ordered by customer.
   - Use 'ACTUAL_DELIVERY_DATE' for date on which material was actually delivered to Customer.
   - Use 'REQUESTED_DELIVERY_DATE' for date on which customer expected the material to be delivered.
   - Use 'ITEM_NUMBER' and 'PLANT_WAREHOUSE_CODE' to join.

2. INVENTORY TABLE: ECOLAB_SC_POC.PUBLIC.ECOLAB_INVENTORY
   - Use 'QTY_UNRESTRICTED' as the current stock or Sellable stock.
   - Use 'ITEM_NUMBER' and 'LOCATION' to join.

3. FORECAST TABLE: ECOLAB_SC_POC.PUBLIC.FORECAST_DATA
   - Use 'FORECAST_QTY' for volume (not FORECAST_QUANTITY).
   - Use 'MONTH_YEAR' (format MON-YYYY) for date logic.

CALCULATIONS:
- DOH: QTY_UNRESTRICTED / (SUM(SHIP_QUANTITY in last 180 days) / 180)
- DOS: QTY_UNRESTRICTED / (SUM(FORECAST_QTY in next 180 days) / 180)
- For DOS 6 Months of forecast from next month, assuming today's month is current month.
- OTIF Calculation: (ACTUAL_DELIVERY_DATE <= REQUESTED_DELIVERY_DATE) AND (SHIP_QUANTITY >= ORDER_QUANTITY)
- DOH (Days on Hand): CURRENT_STOCK / (Total Sales in last 180 days / 180)
- DOS (Days of Supply): CURRENT_STOCK / (Total Forecast in next 180 days / 180)
- The FORECAST_DATA table has a MONTH_YEAR string like Mar-2025. Use TO_DATE(MONTH_YEAR, 'MON-YYYY') for logic.

Follow these rules strictly:
- ONLY return raw SQL. No explanations or introductory text.
- Do not use 'DATE_ADD' or 'INTERVAL 1 DAY'. 
- Instead, use: DATEADD(day, 1, column_name).
- Tables: ECOLAB_SC_POC.PUBLIC.SALES_DATA, ECOLAB_SC_POC.PUBLIC.ECOLAB_INVENTORY, ECOLAB_SC_POC.PUBLIC.FORECAST_DATA.
- Column mapping: Use QTY_UNRESTRICTED for inventory, ORDER_RECEIVE_DATE for sales, and TO_DATE(MONTH_YEAR, 'MON-YYYY') for forecast.
- Never include placeholder text like 'with actual warehouse code' in the SQL.
- Use 'CAST(column AS DATE)' or 'DATE(column)' instead of TRUNC().
- Ensure all comparisons between dates use the DATE type.
- Warehouse codes are case-sensitive; do not guess them unless provided.
"""

with st.container():
    st.info("**Sample Questions:** 'Show me items where DOS > DOH' | 'What is our current OTIF percentage?' | 'List top 5 plants by stock'")
    query = st.chat_input("Ask the SC Agent a question...")

if query:
    with st.spinner("Generating SQL and analyzing..."):
        prompt = f"""
        {business_logic}
        
        Question: {query}
        
        IMPORTANT: Return ONLY the raw SQL code. 
        Do not include any introductory text like 'Here is the code' or 'Sure'.
        Do not use markdown backticks like ```sql.
        Just the code.
        """.replace("'", "''")
        
        try:
            sql_response = conn.query(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('llama3-70b', '{prompt}')").iloc[0,0]
            clean_sql = sql_response.replace("```sql", "").replace("```", "").strip()
            
            if "WITH" in clean_sql.upper():
                clean_sql = clean_sql[clean_sql.upper().find("WITH"):]
            elif "SELECT" in clean_sql.upper():
                clean_sql = clean_sql[clean_sql.upper().find("SELECT"):]

            st.write("### Generated SQL Query:")
            st.code(clean_sql, language="sql")
            
            df = conn.query(clean_sql)
            st.write("### Data Result:")
            st.dataframe(df)
            
            explanation_prompt = f"Explain this data result in plain English for a supply chain manager: {df.head(5).to_string()}"
            explanation = conn.query(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large', '{explanation_prompt}')").iloc[0,0]
            st.info(explanation)
            
        except Exception as e:
            st.error(f"Error: {e}")
            if 'clean_sql' in locals():
                st.code(clean_sql, language="sql")

st.divider()

# --- 5. DATA VISUALIZATION & MANAGEMENT TABLES ---

def log_change(action_type, record_id, column, old_val, new_val):
    """Inserts an audit trail event directly into Snowflake."""
    query = f"""
        INSERT INTO ECOLAB_SC_POC.PUBLIC.CHANGE_LOG (ACTION_TYPE, RECORD_ID, COLUMN_CHANGED, OLD_VALUE, NEW_VALUE)
        VALUES ('{action_type}', '{record_id}', '{column}', '{str(old_val).replace("'", "''")}', '{str(new_val).replace("'", "''")}')
    """
    conn.session.sql(query).collect()

def display_editable_sales_table():
    st.subheader("📊 Sales Data (Editable & Tracked)")
    
    # Always fetch original complete dataframe to track changes accurately
    df = conn.query("SELECT * FROM ECOLAB_SC_POC.PUBLIC.SALES_DATA")
    
    # 1. Filters Configuration
    with st.expander("Filter Sales Data Rows"):
        f_cols = st.columns(4)
        filtered_df = df.copy()
        for i, col_name in enumerate(df.columns):
            options = df[col_name].unique()
            selected = f_cols[i % 4].multiselect(f"{col_name}", options=options, key=f"filt_sales_{col_name}")
            if selected:
                filtered_df = filtered_df[filtered_df[col_name].isin(selected)]
                
    # 2. Bulk Update Tool
    with st.expander("🛠️ Bulk Update Screened Rows"):
        bulk_cols = st.columns(3)
        cols_allowed_to_edit = [c for c in df.columns if c not in ['SALES_ORDER_NUMBER', 'ITEM_NUMBER', 'ITEM_DESCRIPTION']]
        target_col = bulk_cols[0].selectbox("Column to update:", options=cols_allowed_to_edit)
        
        # Adjust input variant mapping data types
        if pd.api.types.is_numeric_dtype(df[target_col]):
            new_value = bulk_cols[1].number_input("New Numerical Value:", value=0)
        elif pd.api.types.is_datetime64_any_dtype(df[target_col]) or "DATE" in target_col.upper():
            new_value = bulk_cols[1].date_input("New Date Value:", datetime.today())
        else:
            new_value = bulk_cols[1].text_input("New Text Value:")
            
        if bulk_cols[2].button("Apply Bulk Update to Filtered Data"):
            for idx, row in filtered_df.iterrows():
                so_num = row['SALES_ORDER_NUMBER']
                old_val = row[target_col]
                
                # Execute direct updates
               # Fixed: Direct execution without 'with' context manager
                conn.session.sql(f"UPDATE ECOLAB_SC_POC.PUBLIC.SALES_DATA SET {target_col} = '{new_value}' WHERE SALES_ORDER_NUMBER = '{so_num}'").collect()
                log_change("BULK_UPDATE", so_num, target_col, old_val, new_value)
            st.success(f"Successfully bulk updated {len(filtered_df)} records!")
            st.rerun()

    # 3. File Upload Overwrite Tool
    with st.expander("📤 Upload Excel template to Upsert/Merge Rows"):
        uploaded_file = st.file_uploader("Choose Excel File", type=["xlsx"])
        if uploaded_file is not None:
            try:
                uploaded_df = pd.read_excel(uploaded_file)
                if 'SALES_ORDER_NUMBER' not in uploaded_df.columns:
                    st.error("Excel must contain 'SALES_ORDER_NUMBER' to uniquely update values.")
                else:
                    if st.button("Commit Excel Upsert onto Snowflake"):
                        for _, row in uploaded_df.iterrows():
                            so_num = str(row['SALES_ORDER_NUMBER'])
                            # Check if exists
                            exists = conn.query(f"SELECT COUNT(*) FROM ECOLAB_SC_POC.PUBLIC.SALES_DATA WHERE SALES_ORDER_NUMBER = '{so_num}'").iloc[0,0]
                            
                            if exists > 0:
                                # Overwrite strategy: Update columns provided
                                update_pairs = [f"{col} = '{str(val).replace("'", "''")}'" for col, val in row.items() if col != 'SALES_ORDER_NUMBER' and pd.notna(val)]
                                if update_pairs:
                                    q = f"UPDATE ECOLAB_SC_POC.PUBLIC.SALES_DATA SET {', '.join(update_pairs)} WHERE SALES_ORDER_NUMBER = '{so_num}'"
                                    conn.session.sql(q).collect()
                                log_change("EXCEL_UPSERT_UPDATE", so_num, "ALL_MODIFIED", "Multiple", "Merged via Excel")
                            else:
                                # Append Strategy
                                cols = ", ".join([str(c) for c in row.index])
                                vals = ", ".join([f"'{str(v).replace("'", "''")}'" for v in row.values])
                                q = f"INSERT INTO ECOLAB_SC_POC.PUBLIC.SALES_DATA ({cols}) VALUES ({vals})"
                                conn.session.sql(q).collect()
                                log_change("EXCEL_UPSERT_APPEND", so_num, "NEW_ROW", "None", "Appended row")
                        st.success("Excel data merged seamlessly into Snowflake!")
                        st.rerun()
            except Exception as ex:
                st.error(f"Error evaluating Excel layout processing: {ex}")

    # 4. Interactive Data Grid Layout (Disallowing Protected Headers)
    st.write("✏️ *Double-click cells below to modify entries directly inline (excluding locked columns):*")
    
    disabled_cols = ['SALES_ORDER_NUMBER', 'ITEM_NUMBER', 'ITEM_DESCRIPTION']
    # Dynamically build configurations to lock non-editable pillars
    col_config = {c: st.column_config.Column(disabled=True) for c in disabled_cols if c in filtered_df.columns}

    edited_df = st.data_editor(
        filtered_df, 
        column_config=col_config, 
        use_container_width=True, 
        hide_index=True,
        key="sales_inline_editor"
    )

    # Detect cell updates between iterations
    if st.button("Save Manual Grid Updates"):
        changes_made = False
        for idx in filtered_df.index:
            so_num = filtered_df.loc[idx, 'SALES_ORDER_NUMBER']
            for col in filtered_df.columns:
                old_val = filtered_df.loc[idx, col]
                new_val = edited_df.loc[idx, col]
                
                if str(old_val) != str(new_val):
                    with conn.session as session:
                        conn.session.sql(f"UPDATE ECOLAB_SC_POC.PUBLIC.SALES_DATA SET {col} = '{str(new_val).replace("'", "''")}' WHERE SALES_ORDER_NUMBER = '{so_num}'").collect()
                    log_change("INLINE_EDIT", so_num, col, old_val, new_val)
                    changes_made = True
                    
        if changes_made:
            st.success("Inline edits logged and updated successfully!")
            st.rerun()

    # Excel Download Export Block
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        filtered_df.to_excel(writer, index=False, sheet_name='SalesData')
    st.download_button(
        label="💾 Export Filtered Sales Data to Excel",
        data=buffer.getvalue(),
        file_name="Sales_Data.xlsx",
        mime="application/vnd.ms-excel",
        key="btn_sales_export"
    )
    st.write("---")

def display_modern_table(table_id, title):
    st.subheader(f"📊 {title}")
    df = conn.query(f"SELECT * FROM {table_id}")
    with st.expander(f"Filter {title} Columns"):
        f_cols = st.columns(4)
        filtered_df = df.copy()
        for i, col_name in enumerate(df.columns):
            options = df[col_name].unique()
            selected = f_cols[i % 4].multiselect(f"{col_name}", options=options, key=f"filt_{table_id}_{col_name}")
            if selected:
                filtered_df = filtered_df[filtered_df[col_name].isin(selected)]

    st.dataframe(filtered_df, use_container_width=True, hide_index=True)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        filtered_df.to_excel(writer, index=False, sheet_name='Data')
    st.download_button(label=f"💾 Export Filtered {title} to Excel", data=buffer.getvalue(), file_name=f"{title.replace(' ', '_')}.xlsx", mime="application/vnd.ms-excel", key=f"btn_{table_id}")
    st.write("---")

# Render Custom Interactive Table followed by rest standard tables
display_editable_sales_table()
display_modern_table("ECOLAB_SC_POC.PUBLIC.ECOLAB_INVENTORY", "Inventory Levels")
display_modern_table("ECOLAB_SC_POC.PUBLIC.FORECAST_DATA", "Demand Forecasts")

# --- 5.5 CHANGE LOG AUDIT VIEWER ---
st.subheader("📜 System Audit Trail & Change Log")
try:
    log_df = conn.query("SELECT * FROM ECOLAB_SC_POC.PUBLIC.CHANGE_LOG ORDER BY TIMESTAMP DESC")
    st.dataframe(log_df, use_container_width=True, hide_index=True)
except Exception as e:
    st.warning("Audit Log entries empty or unavailable.")

# --- 6. FOOTER ---
st.markdown("""
    <div class="custom-footer">
        © 2026 SC Control Tower. All rights reserved. <br>
        <b>Designed by Hitesh Bansal</b>
    </div>
    """, unsafe_allow_html=True)
