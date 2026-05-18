"""
Minimax Izvod Konvertor
=======================
Automatski pretvara PDF izvode u Minimax Excel/XML sa BEX razbijanjem.

FIXES v4:
1. Importovi na vrhu, ne unutar funkcija
2. Proper error handling u PDF ekstrakciji (bez bare except)
3. Retry logika za Claude JSON parsing (3 pokušaja)
4. max_tokens 2048 → 8192 (sprečava isečen JSON za duže izvode)
5. Model ažuriran na claude-sonnet-4-6
6. validate_debit_credit prijavljuje konflikte korisniku umesto tihog rešavanja
7. PrethodnoStanje nije poznat iz stavki - postavlja se na 0.00
8. BEX matching proverava i datum, ne samo iznos
9. TABLE EXTRACTION: pdfplumber čita kolone po poziciji → 100% tačan debit/credit
   Claude ostaje samo za header (broj računa, datum) i fallback za ne-tabelarne PDF
"""

import io
import re
import json
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import streamlit as st
import anthropic
import pdfplumber
import pandas as pd
from openpyxl import Workbook

st.set_page_config(page_title="Minimax Izvod", page_icon="🏦", layout="wide")

API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")

# ========================================================================
# PASSWORD PROTECTION
# ========================================================================
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("# 🔒 Minimax Izvod - Pristup zaštićen")
    st.markdown("Unesi lozinku za pristup aplikaciji:")
    password = st.text_input("Lozinka:", type="password", key="password_input")
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("🔓 Prijavi se", type="primary"):
            correct_password = st.secrets.get("APP_PASSWORD", "minimax2026")
            if password == correct_password:
                st.session_state.authenticated = True
                st.success("✅ Uspešna prijava!")
                st.rerun()
            else:
                st.error("❌ Pogrešna lozinka!")
    st.markdown("---")
    st.info("💡 Kontaktiraj administratora za pristup.")
    st.stop()

# ========================================================================
# CSS
# ========================================================================
st.markdown("""<style>
    .main-title { font-size: 2.5rem; font-weight: 800; margin-bottom: 0.5rem; }
    .subtitle { color: #666; margin-bottom: 2rem; }
    .stButton>button { width: 100%; }
</style>""", unsafe_allow_html=True)

st.markdown('<h1 class="main-title">🏦 Minimax Izvod Konvertor</h1>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">PDF izvodi → Excel/XML sa razbijenim BEX kupcima</p>', unsafe_allow_html=True)

# ========================================================================
# format_account_number - IBAN → domaći format 3-13-2
# ========================================================================
def format_account_number(account_str):
    """
    Konvertuje broj računa u srpski domaći format: XXX-XXXXXXXXXXXXX-XX (3-13-2).

    Minimax XML Partija atribut mora biti 18 cifara BEZ crtica.
    Ova funkcija vraća formatiran string SA crticama - za XML koristiti .replace('-','').
    """
    s = str(account_str).strip()

    # Već tačno 3-13-2 format
    if re.match(r'^\d{3}-\d{13}-\d{2}$', s):
        return s

    # Domaći format 3-X-2 gde X ima MANJE od 13 cifara → dopuni sa leading zeros
    m = re.match(r'^(\d{3})-(\d{1,12})-(\d{2})$', s)
    if m:
        bank, mid, check = m.group(1), m.group(2), m.group(3)
        return f'{bank}-{mid.zfill(13)}-{check}'

    # IBAN format (počinje sa RS)
    if s.upper().startswith('RS'):
        all_digits = re.sub(r'\D', '', s)
        # IBAN = RS + 2 check digits + 18 BBAN cifara → ukupno 20 cifara
        bban_digits = all_digits[2:] if len(all_digits) == 20 else all_digits
        if len(bban_digits) == 18:
            return f'{bban_digits[:3]}-{bban_digits[3:16]}-{bban_digits[16:]}'

    digits = re.sub(r'\D', '', s)

    if len(digits) == 18:
        return f'{digits[:3]}-{digits[3:16]}-{digits[16:]}'

    # 16 cifara = domaći bez leading zeros i bez crtica
    if len(digits) == 16:
        bank = digits[:3]
        mid = digits[3:14]
        check = digits[14:]
        return f'{bank}-{mid.zfill(13)}-{check}'

    if '-' in s:
        return s

    return s


# ========================================================================
# FIX: extract_text_from_pdf - proper error handling, bez bare except
# ========================================================================
def extract_text_from_pdf(pdf_bytes):
    """
    Pokušava ekstrakciju teksta iz PDF-a.
    Raises ValueError ako nijedna metoda ne uspe - ne guta greške tiho.
    """
    # Pokušaj 1: pdfplumber (standardni PDF)
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
        if text.strip():
            return text
        # pdfplumber uspeo ali vratio prazan tekst - skenirani PDF
        raise ValueError("pdfplumber nije izvukao tekst - moguće skenirani PDF")
    except ValueError:
        raise
    except Exception as e:
        # pdfplumber nije mogao da otvori fajl
        pdf_open_error = str(e)

    # Pokušaj 2: ZIP arhiva (neki XML/tekst fajlovi zapakovani kao .pdf)
    if pdf_bytes[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(pdf_bytes)) as z:
                txt_files = sorted([n for n in z.namelist() if n.endswith('.txt')])
                if txt_files:
                    text = ""
                    for tf in txt_files:
                        text += z.read(tf).decode('utf-8', errors='replace') + "\n\n"
                    return text
        except Exception as e:
            raise ValueError(f"ZIP ekstrakcija nije uspela: {e}")

    # Pokušaj 3: direktno UTF-8 (plain text fajl sa .pdf ekstenzijom)
    try:
        decoded = pdf_bytes.decode('utf-8', errors='strict')
        if len(decoded.strip()) > 50:
            return decoded
    except UnicodeDecodeError:
        pass

    raise ValueError(
        f"Ne mogu da izvučem tekst iz fajla. "
        f"pdfplumber greška: {pdf_open_error}. "
        f"Proverite da li je fajl validan PDF."
    )


def parse_xml_izvod(xml_bytes, filename):
    try:
        tree = ET.parse(io.BytesIO(xml_bytes))
        root = tree.getroot()
        zaglavlje = root.find('Zaglavlje')
        if zaglavlje is None:
            raise ValueError("XML nema Zaglavlje element")
        statement = {
            'date': zaglavlje.get('DatumIzvoda', ''),
            'account': zaglavlje.get('Partija', ''),
            'number': zaglavlje.get('BrojIzvoda', ''),
            'owner_name': zaglavlje.get('KomitentNaziv', ''),
            'owner_address': zaglavlje.get('KomitentAdresa', ''),
            'tax_number': zaglavlje.get('MaticniBroj', '')
        }
        transactions = []
        for stavka in root.findall('Stavke'):
            debit = float(stavka.get('Duguje', '0').replace(',', '.') or '0')
            credit = float(stavka.get('Potrazuje', '0').replace(',', '.') or '0')
            transactions.append({
                'date': stavka.get('DatumValute', ''),
                'customer_name': stavka.get('NalogKorisnik', ''),
                'customer_address': stavka.get('Mesto', ''),
                'customer_account': stavka.get('BrojRacunaPrimaocaPosiljaoca', ''),
                'customer_tax_number': '',
                'reference': stavka.get('PozivNaBrojKorisnika', '') or stavka.get('Referenca', ''),
                'currency': 'RSD',
                'debit': debit,
                'credit': credit,
                'description': stavka.get('Opis', '')
            })
        return {'statement': statement, 'transactions': transactions}
    except Exception as e:
        raise ValueError(f"XML parsing greška: {str(e)}")


def parse_bex_specification(file_bytes, filename):
    if filename.lower().endswith('.csv'):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes))
            customers = []
            for _, row in df.iterrows():
                posiljka = str(row.get('IdPosiljke', row.iloc[0] if len(row) > 0 else '')).strip()
                name = str(row.get('UplatilacNaziv', row.iloc[3] if len(row) > 3 else '')).strip()
                address = str(row.get('UplatilacMesto', row.iloc[4] if len(row) > 4 else '')).strip()
                amount_str = str(row.get('UplacenoOtkupa', row.iloc[5] if len(row) > 5 else '0'))
                amount = float(amount_str.replace(',', '').replace('.', ''))
                date_str = str(row.get('DatumNaplateOtkupnine', row.iloc[2] if len(row) > 2 else ''))
                date = date_str.split()[0] if ' ' in date_str else date_str
                if posiljka and name and amount > 0:
                    customers.append({
                        'name': name, 'address': address, 'amount': amount,
                        'posiljka': posiljka, 'reference': f'OT-{posiljka}', 'date': date
                    })
            return customers
        except Exception as e:
            st.error(f"CSV parsing greška: {str(e)}")
            return []
    else:
        try:
            text = extract_text_from_pdf(file_bytes)
            if not API_KEY:
                st.error("API key nije konfigurisan za PDF parsiranje!")
                return []
            client = anthropic.Anthropic(api_key=API_KEY)
            prompt = f"""Analiziraj BEX Express specifikaciju i izvuci podatke o kupcima.

TEKST SPECIFIKACIJE:
{text}

Vrati SAMO JSON (bez markdown):

{{
  "customers": [
    {{
      "posiljka": "262598547",
      "name": "MILEV JOVAN",
      "address": "PIROT, OBILIĆEVA 3",
      "amount": 11400,
      "date": "18.02.2026"
    }}
  ]
}}

KRITIČNO PRAVILA ZA IZNOSE:
1. UKLONI SVE ZAREZE iz iznosa: 11,400 → 11400
2. NIKAD ne dodavaj nule
3. Proveri: suma ispod 1,000,000 RSD

OSTALA PRAVILA:
- posiljka = 9-cifreni broj
- name = TAČNO kao što piše (VELIKA SLOVA)
- date = DD.MM.YYYY
- NIKAD ne izmišljaj podatke"""
            raw = _call_claude_with_retry(client, prompt, max_tokens=4096)
            data = json.loads(raw)
            return [{
                'name': c.get('name', ''), 'address': c.get('address', ''),
                'amount': float(c.get('amount', 0)), 'posiljka': str(c.get('posiljka', '')),
                'reference': f"OT-{c.get('posiljka', '')}", 'date': c.get('date', '')
            } for c in data.get('customers', [])]
        except Exception as e:
            st.error(f"PDF parsing greška: {str(e)}")
            return []


# ========================================================================
# FIX: _call_claude_with_retry - retry logika za JSON parsing greške
# ========================================================================
def _call_claude_with_retry(client, prompt, max_tokens=8192, retries=3):
    """
    Poziva Claude i parsira JSON odgovor.
    Pokušava do `retries` puta ako JSON nije validan.
    Raises ValueError ako svi pokušaji propanu.
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text
            clean = raw.replace('```json', '').replace('```', '').strip()
            # Validacija da je JSON pre nego što vratimo
            json.loads(clean)
            return clean
        except json.JSONDecodeError as e:
            last_error = f"Pokušaj {attempt}/{retries} - nevažeći JSON: {e}"
            if attempt < retries:
                time.sleep(1)
        except anthropic.APIError as e:
            raise ValueError(f"Anthropic API greška: {e}")

    raise ValueError(
        f"Claude nije vratio validan JSON posle {retries} pokušaja. "
        f"Poslednja greška: {last_error}"
    )


# ========================================================================
# parse_amount - konverzija srpskog formata broja u float
# ========================================================================
def parse_amount(s):
    """'31.962,80' → 31962.80 | '32.776,00' → 32776.0 | '' → 0.0"""
    if not s:
        return 0.0
    # Ukloni sve osim cifara i zareza (zarez = decimalni separator u SRB)
    cleaned = re.sub(r'[^\d,]', '', str(s).strip())
    if not cleaned:
        return 0.0
    cleaned = cleaned.replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ========================================================================
# try_extract_table_transactions - čita debit/credit iz kolona tabele
# Ovo je glavno rešenje za tačnost: ne zavisi od AI interpretacije
# ========================================================================
def try_extract_table_transactions(pdf_bytes):
    """
    Pokušava ekstrakciju transakcija iz PDF tabele koristeći pozicije kolona.
    Identifikuje "Na teret"/"Duguje" i "U korist"/"Potražuje" kolone po imenu,
    pa čita iznose direktno — nema AI pogađanja.
    Vraća listu transakcija ili None ako tabela nije pronađena.
    """
    DEBIT_KW  = {'teret', 'duguje', 'debit', 'zaduženje', 'zaduzenje'}
    CREDIT_KW = {'korist', 'potražuje', 'potrazuje', 'credit', 'odobrenje'}
    SKIP_KW   = {'promet', 'total', 'saldo', 'stanje', 'opening', 'closing',
                 'balance', 'prethodno', 'novo stanje'}

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            debit_col  = None
            credit_col = None
            all_data_rows = []

            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    for row_i, row in enumerate(table):
                        cells_lower = [str(c or '').lower() for c in row]
                        row_text = ' '.join(cells_lower)

                        has_debit  = any(kw in row_text for kw in DEBIT_KW)
                        has_credit = any(kw in row_text for kw in CREDIT_KW)

                        if has_debit and has_credit:
                            for col_i, cell in enumerate(cells_lower):
                                if any(kw in cell for kw in DEBIT_KW):
                                    debit_col = col_i
                                if any(kw in cell for kw in CREDIT_KW):
                                    credit_col = col_i
                            all_data_rows.extend(table[row_i + 1:])
                            break
                    else:
                        if debit_col is not None:
                            all_data_rows.extend(table)

            if debit_col is None or credit_col is None or not all_data_rows:
                return None

            transactions = []
            for row in all_data_rows:
                if not row or len(row) <= max(debit_col, credit_col):
                    continue

                row_text = ' '.join(str(c or '').lower() for c in row)
                if any(kw in row_text for kw in SKIP_KW):
                    continue

                debit  = parse_amount(row[debit_col])
                credit = parse_amount(row[credit_col])

                if debit == 0 and credit == 0:
                    continue

                # Izvuci datum (DD.MM.YYYY)
                date = ''
                for cell in row:
                    m = re.search(r'\d{2}\.\d{2}\.\d{4}', str(cell or ''))
                    if m:
                        date = m.group()
                        break

                # Izvuci naziv, adresu, račun, opis
                customer_name    = ''
                customer_address = ''
                customer_account = ''
                description      = ''
                reference        = ''

                for col_i, cell in enumerate(row):
                    if col_i in (debit_col, credit_col):
                        continue
                    cell_str = str(cell or '').strip()
                    if not cell_str:
                        continue

                    # Broj računa (18 cifara bez razmaka)
                    digits_only = re.sub(r'\D', '', cell_str)
                    if len(digits_only) == 18:
                        customer_account = cell_str
                        continue

                    # Redni broj (1-3 cifre)
                    if re.match(r'^\d{1,3}$', cell_str):
                        continue

                    # Datum već uhvaćen
                    if re.match(r'\d{2}\.\d{2}\.\d{4}', cell_str):
                        continue

                    # Višelinijska ćelija: prva linija = naziv, druga = adresa
                    lines = [l.strip() for l in cell_str.split('\n') if l.strip()]
                    if lines and not customer_name:
                        customer_name    = lines[0]
                        customer_address = lines[1] if len(lines) > 1 else ''
                    elif lines and not description:
                        description = ' '.join(lines)

                    # PBZ referenca (npr. "99 OT-1/26")
                    if re.search(r'\d{2}\s+\S+', cell_str) and not reference:
                        reference = cell_str

                transactions.append({
                    'date':              date,
                    'customer_name':     customer_name,
                    'customer_address':  customer_address,
                    'customer_account':  format_account_number(customer_account) if customer_account else '',
                    'customer_tax_number': '',
                    'reference':         reference,
                    'currency':          'RSD',
                    'debit':             debit,
                    'credit':            credit,
                    'description':       description,
                })

            return transactions if transactions else None

    except Exception:
        return None


# ========================================================================
# parse_with_claude - poboljšan prompt + retry + max_tokens 8192
# ========================================================================
def parse_with_claude(text, filename, table_transactions=None):
    """
    Parse izvod pomoću Claude-a.
    Ako su table_transactions dostupne (iz pdfplumber), Claude parsira SAMO header.
    Inače Claude parsira sve (header + transakcije) — fallback za ne-tabelarne PDF.
    """
    if not API_KEY:
        raise ValueError("ANTHROPIC_API_KEY nije konfigurisan!")

    client = anthropic.Anthropic(api_key=API_KEY)

    if table_transactions is not None:
        # Hibridni mode: Claude samo za header
        header_prompt = f"""Analiziraj zaglavlje izvoda banke i izvuci podatke o računu.

TEKST IZVODA:
{text[:3000]}

Vrati SAMO JSON (bez markdown):
{{
  "date": "DD.MM.YYYY",
  "account": "broj računa SA CRTICAMA npr 205-0000000422476-62 ili RS35170003002777200074",
  "number": "broj_izvoda",
  "owner_name": "ime vlasnika",
  "owner_address": "adresa",
  "tax_number": "PIB ili matični broj"
}}

PRAVILA:
- Ako postoji IBAN (RS + cifre), vrati ga TAČNO
- Ako nema IBAN, vrati domaći format TAČNO kao što piše
- NIKAD ne menjaj cifre broja računa"""

        raw = _call_claude_with_retry(client, header_prompt, max_tokens=512)
        statement = json.loads(raw)
        return {'statement': statement, 'transactions': table_transactions}

    # Fallback: Claude parsira sve (za ne-tabelarne PDF)
    prompt = f"""Analiziraj izvod banke i izvuci podatke u JSON formatu.

TEKST IZVODA:
{text}

NAZIV FAJLA: {filename}

Vrati SAMO JSON (bez markdown):

{{
  "statement": {{
    "date": "DD.MM.YYYY",
    "account": "domaći broj računa SA CRTICAMA npr 205-0000000422476-62 ili 170-30027772000-74 (NE IBAN format!)",
    "number": "broj_izvoda",
    "owner_name": "ime vlasnika",
    "owner_address": "adresa",
    "tax_number": "PIB ili matični broj"
  }},
  "transactions": [
    {{
      "date": "DD.MM.YYYY",
      "customer_name": "naziv platioca ili primaoca",
      "customer_address": "adresa",
      "customer_account": "broj računa sa crticama",
      "customer_tax_number": "",
      "reference": "poziv na broj ili referenca",
      "currency": "RSD",
      "debit": 0.00,
      "credit": 0.00,
      "description": "svrha plaćanja"
    }}
  ]
}}

KLJUČNA PRAVILA ZA BROJ RAČUNA (account polje u statement):
- PRIORITET 1: Ako dokument sadrži IBAN (počinje sa "RS" + 2 cifre, npr. "RS35170003007043900080"), vrati GA TAČNO, sve cifre
- PRIORITET 2: Ako nema IBAN, vrati domaći broj TAČNO kao što piše, sa svim ciframa i crticama
- IBAN je uvek precizniji od domaćeg formata - leading zeros su sigurno tačni u IBAN-u
- NIKAD ne menjaj, ne skraćuj, ne zaokružuj cifre broja računa

KLJUČNA PRAVILA ZA DEBIT/CREDIT:
- CREDIT (potražuje) = novac ULAZI na račun = primanja, uplate od kupaca, kreditiranja
- DEBIT (duguje) = novac IZLAZI sa računa = plaćanja, troškovi, transferi prema drugima
- Čitaj kolone "Zaduženje" i "Odobrenje" u izvodu:
  * "Odobrenje" kolona → to je CREDIT (credit > 0, debit = 0)
  * "Zaduženje" kolona → to je DEBIT (debit > 0, credit = 0)
- Ako iznos ima predznak "-" → DEBIT
- NIKAD ne stavljaj isti iznos i u debit i u credit
- NIKAD ne stavljaj 0 i u debit i u credit (tačno jedno mora biti > 0)

OSTALA PRAVILA:
- Račune vrati SA crticama u formatu: XXX-XXXXXXXXXXXXX-XX
- date format: DD.MM.YYYY
- Ignoriši ukupne sume na kraju izvoda (samo pojedinačne stavke)"""

    raw = _call_claude_with_retry(client, prompt, max_tokens=8192)
    return json.loads(raw)


# ========================================================================
# FIX: expand_bex_transactions - matching po iznosu + datumu
# ========================================================================
def expand_bex_transactions(transactions, specifications):
    expanded = []
    for tx in transactions:
        is_bex = 'BEX' in (tx.get('customer_name', '') or '').upper()
        if is_bex:
            tx_amount = tx.get('credit', 0) or tx.get('debit', 0)
            tx_date = tx.get('date', '')
            matched = None

            for spec_name, customers in specifications.items():
                spec_total = sum(c['amount'] for c in customers)
                if abs(spec_total - tx_amount) < 0.01:
                    # Ako imamo datum u specifikaciji, proverimo podudarnost
                    spec_dates = set(c.get('date', '') for c in customers if c.get('date'))
                    date_ok = (
                        not spec_dates  # spec nema datume - ne možemo proveriti
                        or not tx_date  # izvod nema datum transakcije
                        or any(d in tx_date or tx_date in d for d in spec_dates)
                    )
                    if date_ok:
                        matched = customers
                        st.success(f"🔄 Razbijam BEX ({spec_name}): {len(customers)} kupaca, {spec_total:,.2f} RSD")
                        break

            if matched:
                for c in matched:
                    expanded.append({
                        'date': c['date'] or tx.get('date', ''),
                        'customer_name': c['name'],
                        'customer_address': c['address'],
                        'customer_account': '',
                        'customer_tax_number': '',
                        'reference': c['reference'],
                        'currency': 'RSD',
                        'debit': 0,
                        'credit': c['amount'],
                        'description': f"Otkup pošiljke {c['posiljka']}"
                    })
            else:
                expanded.append(tx)
        else:
            expanded.append(tx)
    return expanded


# ========================================================================
# FIX: validate_debit_credit - prijavljuje konflikte korisniku
# ========================================================================
def validate_debit_credit(transactions):
    """
    Proverava da svaka stavka ima ili debit>0 ili credit>0, ne oba.
    Konflikte (oba > 0) prijavljuje korisniku umesto tihog rešavanja.
    """
    fixed = []
    conflicts = []

    for i, tx in enumerate(transactions):
        debit = float(tx.get('debit', 0) or 0)
        credit = float(tx.get('credit', 0) or 0)

        if debit > 0 and credit > 0:
            # Konflikt: Claude vratio oba - zadržavamo credit i prijavljujemo
            tx['debit'] = 0
            tx['credit'] = credit
            conflicts.append(
                f"Stavka {i+1} ({tx.get('customer_name', '?')}): "
                f"debit={debit:.2f} i credit={credit:.2f} su oba > 0. "
                f"Zadržan credit={credit:.2f}."
            )
        elif debit == 0 and credit == 0:
            # Oba nula - prijavimo kao upozorenje
            conflicts.append(
                f"Stavka {i+1} ({tx.get('customer_name', '?')}): "
                f"i debit i credit su 0 - moguća greška u parsiranju."
            )

        fixed.append(tx)

    if conflicts:
        with st.expander(f"⚠️ {len(conflicts)} upozorenja u debit/credit klasifikaciji"):
            for c in conflicts:
                st.warning(c)

    return fixed


def create_minimax_excel(statement, transactions):
    wb = Workbook()
    account = format_account_number(statement.get('account', ''))

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

    ws2 = wb.create_sheet("Transactions")
    headers = ["CustomerName", "CustomerAddress", "CustomerAccount", "CustomerTaxNumber",
               "Date", "Reference", "Currency", "Debit", "Credit", "Description"]
    ws2.append(headers)
    for tx in transactions:
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

    num_cols = {8, 9}
    for row in ws2.iter_rows():
        for cell in row:
            if cell.column in num_cols:
                cell.number_format = "0.00"
            else:
                cell.number_format = "@"

    col_widths = [35, 25, 28, 15, 12, 25, 8, 12, 12, 45]
    for i, width in enumerate(col_widths, 1):
        ws2.column_dimensions[ws2.cell(1, i).column_letter].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ========================================================================
# FIX: create_minimax_xml - PrethodnoStanje nije izračunljivo iz stavki
# ========================================================================
def create_minimax_xml(statement, transactions):
    account = format_account_number(statement.get('account', ''))
    account_no_dashes = account.replace('-', '')

    dugovni = sum(float(tx.get('debit', 0) or 0) for tx in transactions)
    potrazni = sum(float(tx.get('credit', 0) or 0) for tx in transactions)

    root = ET.Element('TransakcioniRacunPrivredaIzvod')
    zaglavlje = ET.SubElement(root, 'Zaglavlje')
    zaglavlje.set('VrstaIzvoda', 'R')
    zaglavlje.set('BrojIzvoda', statement.get('number', ''))
    zaglavlje.set('DatumIzvoda', statement.get('date', ''))
    zaglavlje.set('MaticniBroj', statement.get('tax_number', ''))
    zaglavlje.set('KomitentNaziv', statement.get('owner_name', ''))
    zaglavlje.set('KomitentAdresa', statement.get('owner_address', ''))
    zaglavlje.set('KomitentMesto', '')
    zaglavlje.set('Partija', account_no_dashes)
    zaglavlje.set('TipRacuna', 'Transakcioni depoziti preduzetnika')
    # PrethodnoStanje = otvarajući saldo koji nije dostupan iz stavki - Minimax uvek ažurira stanje
    zaglavlje.set('PrethodnoStanje', '0.00')
    zaglavlje.set('DugovniPromet', f"{dugovni:.2f}")
    zaglavlje.set('PotrazniPromet', f"{potrazni:.2f}")
    zaglavlje.set('NovoStanje', f"{potrazni - dugovni:.2f}")
    zaglavlje.set('StanjeObracunateProvizije', '0')

    for tx in transactions:
        cust_account = format_account_number(tx.get('customer_account', '')) if tx.get('customer_account') else ''
        stavka = ET.SubElement(root, 'Stavke')
        stavka.set('NalogKorisnik', str(tx.get('customer_name', '') or ''))
        stavka.set('Mesto', str(tx.get('customer_address', '') or ''))
        stavka.set('VasBrojNaloga', '')
        stavka.set('BrojRacunaPrimaocaPosiljaoca', cust_account)
        stavka.set('Opis', str(tx.get('description', '') or ''))
        stavka.set('SifraPlacanja', '')
        stavka.set('SifraPlacanjaOpis', '')
        stavka.set('Duguje', f"{float(tx.get('debit', 0) or 0):.2f}")
        stavka.set('Potrazuje', f"{float(tx.get('credit', 0) or 0):.2f}")
        stavka.set('ModelZaduzenjaOdobrenja', '')
        stavka.set('PozivNaBrojZaduzenjaOdobrenja', '')
        stavka.set('ModelKorisnika', '')
        stavka.set('PozivNaBrojKorisnika', str(tx.get('reference', '') or ''))
        stavka.set('BrojZaReklamaciju', '')
        stavka.set('Referenca', str(tx.get('reference', '') or ''))
        stavka.set('Objasnjenje', '')
        stavka.set('DatumValute', str(tx.get('date', '') or ''))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    output = io.BytesIO()
    tree.write(output, encoding='utf-8', xml_declaration=True)
    output.seek(0)
    return output.getvalue()


# ========================================================================
# MAIN UI
# ========================================================================
col1, col2 = st.columns(2)

with col1:
    st.markdown("### 📄 Izvodi banke")
    izvodi_files = st.file_uploader(
        "Upload PDF ili XML izvoda",
        type=['pdf', 'PDF', 'xml', 'XML'],
        accept_multiple_files=True,
        key='izvodi'
    )

with col2:
    st.markdown("### 📋 BEX Specifikacije (opciono)")
    spec_files = st.file_uploader(
        "Upload BEX specifikacija (PDF ili CSV)",
        type=['pdf', 'PDF', 'csv', 'CSV'],
        accept_multiple_files=True,
        key='specs'
    )

if izvodi_files:
    st.markdown("---")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        generate_excel = st.button("📊 Generiši Excel", type="primary", use_container_width=True)
    with col_btn2:
        generate_xml = st.button("📄 Generiši XML", type="secondary", use_container_width=True)

    if generate_excel or generate_xml:
        output_format = "Excel" if generate_excel else "XML"
        st.info(f"Generišem {output_format} format...")

        specifications = {}
        if spec_files:
            with st.spinner("Parsiram BEX specifikacije..."):
                for spec_file in spec_files:
                    try:
                        spec_bytes = spec_file.read()
                        customers = parse_bex_specification(spec_bytes, spec_file.name)
                        if customers:
                            specifications[spec_file.name] = customers
                            total = sum(c['amount'] for c in customers)
                            st.success(f"✅ {spec_file.name}: {len(customers)} kupaca, {total:,.2f} RSD")
                    except Exception as e:
                        st.error(f"❌ {spec_file.name}: {str(e)}")

        progress_bar = st.progress(0)
        results = []

        for i, izvod_file in enumerate(izvodi_files):
            progress_bar.progress((i + 1) / len(izvodi_files))
            try:
                with st.status(f"Obradjujem: {izvod_file.name}"):
                    st.write("Čitam fajl...")
                    pdf_bytes = izvod_file.read()

                    if izvod_file.name.lower().endswith('.xml'):
                        st.write("Parsiram XML izvod...")
                        parsed = parse_xml_izvod(pdf_bytes, izvod_file.name)
                    else:
                        text = extract_text_from_pdf(pdf_bytes)

                        # Pokušaj table extraction za tačan debit/credit
                        st.write("Čitam tabelu iz PDF-a...")
                        table_tx = try_extract_table_transactions(pdf_bytes)

                        if table_tx:
                            st.write(f"✅ Tabela pronađena ({len(table_tx)} stavki) — AI parsira samo header")
                        else:
                            st.write("⚠️ Tabela nije pronađena — AI parsira sve")

                        parsed = parse_with_claude(text, izvod_file.name, table_transactions=table_tx)

                    st.write("Proveravam BEX...")
                    original_count = len(parsed['transactions'])
                    expanded = expand_bex_transactions(parsed['transactions'], specifications)

                    st.write("Validujem debit/credit...")
                    expanded = validate_debit_credit(expanded)

                    st.write(f"Generišem {output_format}...")
                    if generate_excel:
                        file_bytes = create_minimax_excel(parsed['statement'], expanded)
                        output_name = re.sub(r'\.(pdf|PDF|xml|XML)$', '', izvod_file.name) + '_minimax.xlsx'
                        mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    else:
                        file_bytes = create_minimax_xml(parsed['statement'], expanded)
                        output_name = re.sub(r'\.(pdf|PDF|xml|XML)$', '', izvod_file.name) + '_minimax.xml'
                        mime_type = "application/xml"

                    results.append({
                        'success': True,
                        'filename': izvod_file.name,
                        'output_name': output_name,
                        'file_bytes': file_bytes,
                        'mime_type': mime_type,
                        'format': output_format,
                        'statement': parsed['statement'],
                        'tx_count': len(expanded),
                        'bex_expanded': len(expanded) > original_count,
                        'transactions': expanded
                    })
            except Exception as e:
                results.append({'success': False, 'filename': izvod_file.name, 'error': str(e)})

        progress_bar.empty()

        # ================================================================
        # ZIP download za sve fajlove + individualni prikaz
        # ================================================================
        st.markdown("---")
        successful = [r for r in results if r['success']]
        failed = [r for r in results if not r['success']]

        st.markdown(f"## 📥 Rezultati ({output_format})")

        if len(successful) > 1:
            st.markdown("### 📦 Preuzmi sve odjednom")
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for r in successful:
                    zf.writestr(r['output_name'], r['file_bytes'])
            zip_buffer.seek(0)

            ext = "xlsx" if output_format == "Excel" else "xml"
            st.download_button(
                label=f"⬇️ Preuzmi SVE kao ZIP ({len(successful)} fajlova)",
                data=zip_buffer.getvalue(),
                file_name=f"minimax_izvodi_{output_format.lower()}.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True,
                key="download_all_zip"
            )
            st.markdown("---")

        for r in successful:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"### ✅ {r['filename']}")
                raw_account = r['statement']['account']
                formatted_account = format_account_number(raw_account)
                xml_account = formatted_account.replace('-', '')
                st.markdown(f"**Račun:** `{formatted_account}`")
                st.caption(f"🔍 Debug — Claude izvukao: `{raw_account}` → XML Partija: `{xml_account}` ({len(xml_account)} cifara)")
                st.markdown(f"**Transakcija:** {r['tx_count']}" +
                          (f" _(BEX razbijen)_" if r['bex_expanded'] else ""))
            with col2:
                btn_label = "⬇️ Excel" if r['format'] == "Excel" else "⬇️ XML"
                st.download_button(
                    btn_label,
                    data=r['file_bytes'],
                    file_name=r['output_name'],
                    mime=r['mime_type'],
                    key=f"dl_{hash(r['filename'])}_{r['format']}"
                )

            with st.expander(f"📊 Pregledaj transakcije ({r['tx_count']})"):
                tx_data = [{
                    'Br': i,
                    'Datum': tx.get('date', ''),
                    'Kupac': tx.get('customer_name', '')[:40],
                    'Duguje': f"{tx.get('debit', 0):,.2f}",
                    'Potražuje': f"{tx.get('credit', 0):,.2f}",
                    'Opis': tx.get('description', '')[:50]
                } for i, tx in enumerate(r['transactions'], 1)]

                df = pd.DataFrame(tx_data)
                st.dataframe(df, use_container_width=True, hide_index=True)

                total_debit = sum(tx.get('debit', 0) for tx in r['transactions'])
                total_credit = sum(tx.get('credit', 0) for tx in r['transactions'])
                col_s1, col_s2, col_s3 = st.columns(3)
                with col_s1:
                    st.metric("Ukupno Duguje", f"{total_debit:,.2f} RSD")
                with col_s2:
                    st.metric("Ukupno Potražuje", f"{total_credit:,.2f} RSD")
                with col_s3:
                    st.metric("Saldo", f"{total_credit - total_debit:,.2f} RSD")

        for r in failed:
            st.error(f"❌ {r['filename']}: {r['error']}")

        st.markdown("---")
        if st.button("🔄 Novi upload (resetuj)", type="secondary"):
            st.rerun()

else:
    st.info("👆 Započni upload-om PDF izvoda")
