import streamlit as st
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

# Fetch and format the key from secrets
private_key_str = st.secrets["connections"]["snowflake"]["private_key"]

if "BEGIN PRIVATE KEY" not in private_key_str:
    private_key_str = f"-----BEGIN PRIVATE KEY-----\n{private_key_str}\n-----END PRIVATE KEY-----"

# Prepare bytes object
raw_key = private_key_str.strip().encode()

p_key = serialization.load_pem_private_key(
    raw_key,
    password=None,
    backend=default_backend()
)

private_key_bytes = p_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

# Connect to Snowflake
conn = st.connection(
    "snowflake",
    type="snowflake",
    private_key=private_key_bytes
)


import pandas as pd

# Connects to Snowflake using Streamlit's built-in connection manager
conn = st.connection("snowflake", type="snowflake")

st.title("Ecolab Supply Chain Intelligence")
st.markdown("### Universal AI Agent (Prompt-Based)")

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

query = st.text_input("Ask a supply chain question:", placeholder="e.g., Which items have DOS > DOH?")

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
