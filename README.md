# 🚀 DEPLOYMENT UPUTSTVO - Minimax Izvod

## 📹 VIDEO UPUTSTVA (OBAVEZNO POGLEDAJ)

### Glavni tutorial (12 min - SVE objašnjava):
https://www.youtube.com/watch?v=HKoOBiAaHGg
**Prati ovaj korak po korak!**

### Kraći (samo deploy, 5 min):
https://www.youtube.com/watch?v=kXvmqg8hc70

### GitHub osnove (ako ti treba):
https://www.youtube.com/watch?v=iv8rSLsi1xo

---

## ⚡ BRZI KORACI (5 minuta)

### 1️⃣ GitHub Account
- Idi na: https://github.com/signup
- Napravi besplatan nalog
- Verifikuj email

### 2️⃣ Napravi Repository
- Klikni zeleno dugme **"New"** (ili idi na https://github.com/new)
- Repository name: `minimax-izvod`
- Izaberi **Public**
- ✅ Čekiraj "Add a README file"
- Klikni **"Create repository"**

### 3️⃣ Upload Fajlova
U svom novom repo:

**A) Kreiraj folder `.streamlit`:**
- Klikni **"Add file"** → **"Create new file"**
- Ime fajla upiši: `.streamlit/secrets.toml`
- Kopiraj sadržaj iz `secrets_template.toml` koji sam ti dao
- **VAŽNO:** Zameni `sk-ant-tvoj-api-kljuc-ovde` sa pravim API ključem
- Klikni **"Commit new file"**

**B) Upload `app.py`:**
- Klikni **"Add file"** → **"Upload files"**
- Prevuci `app.py` fajl
- Klikni **"Commit changes"**

**C) Upload `requirements.txt`:**
- Ponovi isto za `requirements.txt`

Tvoj repo sada izgleda:
```
minimax-izvod/
├── .streamlit/
│   └── secrets.toml
├── app.py
├── requirements.txt
└── README.md
```

### 4️⃣ Deploy na Streamlit Cloud

**A) Registracija:**
- Idi na: https://share.streamlit.io
- Klikni **"Sign in"**
- Izaberi **"Continue with GitHub"**
- Autorizuj pristup

**B) Deploy:**
- Klikni **"New app"**
- Repository: `tvoj-username/minimax-izvod`
- Branch: `main`
- Main file path: `app.py`
- Klikni **"Deploy!"**

**C) Dodaj API Key (VAŽNO!):**
- Dok se app deploy-uje, klikni **⋮** (tri tačkice) → **"Settings"**
- Scroll do **"Secrets"**
- Kopiraj sadržaj:
  ```toml
  ANTHROPIC_API_KEY = "sk-ant-tvoj-pravi-kljuc"
  ```
- Klikni **"Save"**

### 5️⃣ Gotovo! 🎉

App će biti dostupan na:
```
https://minimax-izvod.streamlit.app
```
(ili sličan URL koji ti Streamlit dodeli)

**Sačekaj 2-3 minuta** da se app pokrene prvi put.

---

## 👥 Deljenje sa Timom

Pošalji im link:
```
https://tvoj-app-url.streamlit.app
```

Oni samo:
1. Otvore link
2. Upload-uju PDF izvode
3. Upload-uju BEX specifikacije (opciono)
4. Kliknu "Generiši"
5. Download-uju Excel fajlove

**Niko ne treba API ključ** - ti si ga postavio u Secrets!

---

## 🔄 Kako Ažurirati App

Ako želiš da promeniš kod:

1. Idi u svoj GitHub repo
2. Klikni na `app.py`
3. Klikni ikonu **pencil** (Edit)
4. Napravi izmene
5. Klikni **"Commit changes"**

**Streamlit će automatski re-deploy-ovati app!** (1-2 min)

---

## 🆘 Česta Pitanja

### Q: App ne radi, šta da radim?
A: Klikni **⋮** → **"Reboot app"**

### Q: "Module not found" greška?
A: Proveri da li je `requirements.txt` upload-ovan

### Q: "API key not found" greška?
A: Proveri Settings → Secrets, mora biti:
```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

### Q: Koliko košta?
A: **BESPLATNO!** Streamlit Cloud je free. Plaćaš samo Anthropic API (~$0.01 po izvodu).

### Q: Mogu li да sakijem API key?
A: **MOŽE!** Koristeći GitHub private repo:
1. Napravi repo kao **Private** umesto Public
2. Ostalo isto

---

## 📊 Napredne Opcije

### Custom Domain
Umesto `minimax-izvod.streamlit.app`, možeš postaviti svoj domen (npr. `izvodi.vasafirma.rs`):
- Settings → General → Custom subdomain

### Analytics
Vidi koliko ljudi koristi app:
- Settings → Analytics

### Multiple Environments
Napravi `dev` i `prod` verzije:
- Napravi branch `dev` u GitHub-u
- Deploy dva puta (jedan za `main`, jedan za `dev`)

---

## 🎓 Dodatni Resursi

- **Streamlit Docs:** https://docs.streamlit.io/
- **Deploy Tutorial:** https://docs.streamlit.io/streamlit-community-cloud/get-started
- **Troubleshooting:** https://docs.streamlit.io/knowledge-base

---

**Sretno! 🚀**
Za pomoć, pošalji screenshot greške ako se nešto zabloči.
