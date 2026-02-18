"""
Minimax Izvod Konvertor
=======================
Automatski pretvara PDF izvode u Minimax Excel sa BEX razbijanjem.
"""

import streamlit as st
import io
import re
import json
from pathlib import Path
import anthropic
from openpyxl import Workbook

# Page config
st.set_page_config(page_title="Minimax Izvod", page_icon="🏦", layout="wide")

# Custom CSS
st.markdown("""<style>
    .main-title { font-size: 2.5rem; font-weight: 800; margin-bottom: 0.5rem; }
    .subtitle { color: #666; margin-bottom: 2rem; }
    .stButton>button { width: 100%; }
</style>""", unsafe_allow_html=True)

# Title
st.markdown('<h1 class="main-title">🏦 Minimax Izvod Konvertor</h1>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">PDF izvodi → Excel sa razbijenim BEX kupcima</p>', unsafe_allow_html=True)

# API Key
API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")

# Helper functions
def extract_text_from_pdf(pdf_bytes):
    """Extract text from PDF (supports both regular PDF and ZIP format)."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + "\n\n"
        return text
    except:
        # Try as ZIP
        import zipfile
        if pdf_bytes[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(pdf_bytes)) as z:
                txt_files = sorted([n for n in z.namelist() if n.endswith('.txt')])
                text = ""
                for tf in txt_files:
                    text += z.read(tf).decode('utf-8', errors='replace') + "\n\n"
                return text
        return pdf_bytes.decode('utf-8', errors='replace')

def format_account_number(account_str):
    """Format account to XXX-XXXXXXXXXXXXX-XX (3-13-2) if needed."""
    if not account_str:
        return ""
    
    # Remove all non-digits
    digits = re.sub(r'\D', '', str(account_str))
    
    # If exactly 18 digits, format as 3-13-2
    if len(digits) == 18:
        formatted = f"{digits[:3]}-{digits[3:16]}-{digits[16:]}"
        return formatted
    
    # If already has dashes in correct format, keep as is
    if re.match(r'^\d{3}-\d{13}-\d{2}$', str(account_str)):
        return str(account_str)
    
    # Otherwise return as-is
    return str(account_str)

def parse_bex_specification(text):
    """Parse BEX specification PDF and extract customers."""
    customers = []
    lines = text.split('\n')
    
    for line in lines:
        if not line.strip() or 'Br. pošiljke' in line or 'Specifikacija' in line:
            continue
        
        # Look for amount pattern before "MG"
        amount_match = re.search(r'\s(\d{1,2}),(\d{3})\s+MG', line)
        if not amount_match:
            continue
        
        amount = float(amount_match.group(1) + amount_match.group(2))
        parts = line.strip().split()
        
        mg_idx = None
        for i, p in enumerate(parts):
            if p == 'MG':
                mg_idx = i
                break
        
        if not mg_idx or mg_idx < 6:
            continue
        
        rb = parts[0]
        posiljka = parts[1]
        
        name_parts = []
        address_parts = []
        in_address = False
        
        for i in range(4, mg_idx - 1):
            if ',' in parts[i] or in_address:
                in_address = True
                address_parts.append(parts[i])
            else:
                name_parts.append(parts[i])
        
        customers.append({
            'name': ' '.join(name_parts),
            'address': ' '.join(address_parts).rstrip(','),
            'amount': amount,
            'reference': f'WS-MM-2026{rb.zfill(6)}',
            'posiljka': posiljka,
            'date': '09.02.2026'
        })
    
    return customers

def parse_with_claude(text, filename):
    """Parse izvod using Claude API."""
    client = anthropic.Anthropic(api_key=API_KEY)
    
    prompt = f"""Analiziraj izvod banke i izvuci podatke u JSON formatu.

TEKST IZVODA:
{text}

NAZIV FAJLA: {filename}

Vrati SAMO JSON (bez markdown):

{{
  "statement": {{
    "date": "DD.MM.YYYY",
    "account": "broj-racuna-SA-SVIM-NULAMA-BEZ-crtica-samo-18-cifara",
    "number": "broj_izvoda",
    "owner_name": "ime vlasnika",
    "owner_address": "adresa",
    "tax_number": "PIB"
  }},
  "transactions": [
    {{
      "date": "DD.MM.YYYY",
      "customer_name": "naziv",
      "customer_address": "adresa",
      "customer_account": "racun-BEZ-crtica-samo-cifre",
      "customer_tax_number": "",
      "reference": "referenca",
      "currency": "RSD",
      "debit": 0.00,
      "credit": 0.00,
      "description": "opis"
    }}
  ]
}}

KRITIČNA PRAVILA:
- debit = IZLAZI (pozitivan, credit=0)
- credit = ULAZI (pozitivan, debit=0)
- Račune vrati BEZ crtica, samo cifre (18 cifara)
- NIKAD ne skraćuj nule u brojevima
- date format: DD.MM.YYYY
- Ignoriši ukupne sume
- Za račune u izvodu - vrati SVE cifre bez crtica"""
    
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw = msg.content[0].text
    clean = raw.replace('```json', '').replace('```', '').strip()
    return json.loads(clean)

def expand_bex_transactions(transactions, specifications):
    """Expand BEX transactions using specifications."""
    expanded = []
    
    # DEBUG: Show what we have
    if specifications:
        st.info(f"🔍 Učitano specifikacija: {len(specifications)}")
        for spec_name, customers in specifications.items():
            spec_total = sum(c['amount'] for c in customers)
            st.write(f"   → {spec_name}: {len(customers)} kupaca = {spec_total:,.2f} RSD")
    else:
        st.warning("⚠️ Nema učitanih BEX specifikacija!")
    
    for tx in transactions:
        customer_name = tx.get('customer_name', '') or ''
        is_bex = 'BEX' in customer_name.upper()
        
        if is_bex:
            tx_amount = tx.get('credit', 0) or tx.get('debit', 0)
            st.info(f"🔍 Pronađena BEX transakcija: '{customer_name}' = {tx_amount:,.2f} RSD")
            
            # Find matching spec
            matched = None
            if specifications:
                for spec_name, customers in specifications.items():
                    spec_total = sum(c['amount'] for c in customers)
                    diff = abs(spec_total - tx_amount)
                    st.write(f"   Poređenje: spec={spec_total:,.2f} vs tx={tx_amount:,.2f}, razlika={diff:.4f}")
                    
                    if diff < 0.01:
                        matched = customers
                        st.success(f"✅ MATCH! Razbijam na {len(customers)} kupaca")
                        break
            
            if matched:
                for c in matched:
                    expanded.append({
                        'date': c['date'],
                        'customer_name': c['name'],
                        'customer_address': c['address'],
                        'customer_account': '',
                        'customer_tax_number': '',
                        'reference': c['reference'],
                        'currency': 'RSD',
                        'debit': c['amount'] if tx.get('debit', 0) > 0 else 0,
                        'credit': c['amount'] if tx.get('credit', 0) > 0 else 0,
                        'description': f"Otkup pošiljke {c['posiljka']}"
                    })
            else:
                st.error(f"❌ BEX transakcija NEMA matching specifikaciju! (Iznos: {tx_amount:,.2f})")
                expanded.append(tx)
        else:
            expanded.append(tx)
    
    st.info(f"📊 Rezultat: {len(transactions)} → {len(expanded)} transakcija")
    return expanded

def create_minimax_excel(statement, transactions):
    """Generate Minimax Excel with correct formatting."""
    wb = Workbook()
    
    # Format account number
    account = format_account_number(statement.get('account', ''))
    st.success(f"✅ Račun formatiran: {account}")
    
    # Sheet 1: Statement
    ws1 = wb.active
    ws1.title = "Statement"
    ws1.append(["Date", "Account", "Number"])
    ws1.append([statement.get('date', ''), account, statement.get('number', '')])
    
    for row in ws1.iter_rows():
        for cell in row:
            cell.number_format = "@"
    
    ws1.column_dimensions["A"].width = 15
    ws1.column_dimensions["B"].width = 32
    ws1.column_dimensions["C"].width = 10
    
    # Sheet 2: Transactions
    ws2 = wb.create_sheet("Transactions")
    headers = ["CustomerName","CustomerAddress","CustomerAccount","CustomerTaxNumber",
               "Date","Reference","Currency","Debit","Credit","Description"]
    ws2.append(headers)
    
    for tx in transactions:
        # Format customer account if present
        cust_account = format_account_number(tx.get('customer_account', '')) if tx.get('customer_account') else ''
        
        ws2.append([
            str(tx.get("customer_name", "") or ""),
            str(tx.get("customer_address", "") or ""),
            cust_account,
            str(tx.get("customer_tax_number", "") or ""),
            str(tx.get("date", "") or ""),
            str(tx.get("reference", "") or ""),
            "RSD",
            float(tx.get("debit", 0) or 0),
            float(tx.get("credit", 0) or 0),
            str(tx.get("description", "") or ""),
        ])
    
    # Format numbers
    num_cols = {8, 9}
    for row in ws2.iter_rows():
        for cell in row:
            if cell.column in num_cols:
                cell.number_format = "0.00"
            else:
                cell.number_format = "@"
    
    # Column widths
    col_widths = [35, 25, 28, 15, 12, 25, 8, 12, 12, 45]
    for i, width in enumerate(col_widths, 1):
        ws2.column_dimensions[ws2.cell(1, i).column_letter].width = width
    
    # Save to bytes
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()

# Main UI
col1, col2 = st.columns(2)

with col1:
    st.markdown("### 📄 Izvodi banke")
    izvodi_files = st.file_uploader(
        "Upload PDF izvoda",
        type=['pdf', 'PDF'],
        accept_multiple_files=True,
        key='izvodi'
    )

with col2:
    st.markdown("### 📋 BEX Specifikacije (opciono)")
    spec_files = st.file_uploader(
        "Upload BEX specifikacija",
        type=['pdf', 'PDF'],
        accept_multiple_files=True,
        key='specs'
    )

if izvodi_files:
    st.markdown("---")
    
    if st.button("⚡ Generiši Minimax Excel", type="primary"):
        # Parse BEX specs first
        specifications = {}
        
        if spec_files:
            st.markdown("### 📋 Parsiranje BEX specifikacija")
            for spec_file in spec_files:
                try:
                    spec_bytes = spec_file.read()
                    spec_text = extract_text_from_pdf(spec_bytes)
                    customers = parse_bex_specification(spec_text)
                    
                    if customers:
                        specifications[spec_file.name] = customers
                        total = sum(c['amount'] for c in customers)
                        st.success(f"✅ {spec_file.name}: {len(customers)} kupaca, {total:,.2f} RSD")
                        
                        # Show details
                        with st.expander(f"Detalji: {spec_file.name}"):
                            for c in customers:
                                st.write(f"  • {c['name']}: {c['amount']:,.2f} RSD")
                    else:
                        st.warning(f"⚠️ {spec_file.name}: Nisu pronađeni kupci")
                except Exception as e:
                    st.error(f"❌ {spec_file.name}: {e}
