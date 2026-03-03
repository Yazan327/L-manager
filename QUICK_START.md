# Quick Start Guide

## 🚀 Running the Flask App

### Development (Local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env

# 3. Edit .env with your settings (optional for basic testing)
# - Add PropertyFinder API credentials: PF_CLIENT_ID, PF_CLIENT_SECRET
# - Customize admin email: ADMIN_EMAIL (default: admin@example.com)
# - Customize admin password: ADMIN_PASSWORD (default: changeme)

# 4. Run the app
python app.py

# 5. Open in browser
# http://localhost:5000
# Login with credentials from .env
```

### Dashboard CSS (Tailwind)

The dashboard now uses a compiled CSS file (`src/dashboard/static/css/app.css`) instead of the Tailwind CDN script in production.

```bash
# Install frontend build tools (one-time)
npm install

# Build CSS for commit/deploy
npm run build:css

# Optional while editing UI
npm run watch:css
```

Production note:
- Commit `src/dashboard/static/css/app.css` so Railway can serve it without Node.

### Localization (EN/AR)

```bash
# Validate Arabic/English dictionary shape
python scripts/check_i18n.py

# Validate template key usage against dictionaries
python scripts/i18n/check_missing_keys.py

# Prevent new hardcoded JS/UI literals
python scripts/i18n/check_hardcoded_strings.py

# Optional: generate translation inventory CSV
python scripts/i18n/extract_ui_strings.py

# Rebuild runtime string dictionary for pages that still contain legacy literals
python scripts/i18n/build_runtime_strings.py
```

Localization notes:
- Use `{{ t('namespace.key', default='...') }}` for template strings.
- Use `i18nT('namespace.key', 'fallback', vars)` for JS strings.
- Keep Arabic UI with Western digits (`ar-AE-u-nu-latn`).
- For runtime alerts/confirms built from old literals, add entries to `legacy_messages` or `runtime_prefixes` in `src/dashboard/i18n/*.json`.
- Use `runtime_strings` in `src/dashboard/i18n/*.json` for exact UI phrase fallback on legacy templates.

### Production (Railway)

```bash
# 1. Push to GitHub main branch
git push origin main

# 2. Create Railway project
# - Connect GitHub repository
# - Railway will auto-detect Python and use Procfile

# 3. Set environment variables in Railway dashboard
SECRET_KEY=<random 32+ char string>
PF_CLIENT_ID=your_client_id
PF_CLIENT_SECRET=your_client_secret
ADMIN_EMAIL=admin@yourcompany.com
ADMIN_PASSWORD=strong_password
DEFAULT_WORKSPACE_NAME=Your Company
DEFAULT_WORKSPACE_SLUG=your-company

# 4. (Optional) Add PostgreSQL
# Click "+ New" → Database → PostgreSQL
# Railway sets DATABASE_URL automatically

# 5. Deploy
# Railway auto-deploys when you push to main
```

## 📋 Essential Files

| File | Purpose |
|------|---------|
| `app.py` | Main Flask application (entry point) |
| `.env.example` | Configuration template |
| `.env` | Your actual configuration (create from .env.example) |
| `README.md` | Full documentation |
| `MIGRATION_SUMMARY.md` | What changed and why |
| `_archive/README.md` | How to restore Django app |

## ⚙️ Key Environment Variables

**Minimal Setup** (for testing):
```env
SECRET_KEY=dev-key-change-this-in-production
```

**Production Setup**:
```env
SECRET_KEY=<generate-random-32-chars>
PF_CLIENT_ID=your_client_id
PF_CLIENT_SECRET=your_client_secret
ADMIN_EMAIL=admin@yourcompany.com
ADMIN_PASSWORD=strong_password
DEFAULT_WORKSPACE_NAME=Your Company Name
DEFAULT_WORKSPACE_SLUG=your-company-slug
```

## 🔐 Default Login

When first running the app:
- **Email**: Check your `.env` `ADMIN_EMAIL` (default: admin@example.com)
- **Password**: Check your `.env` `ADMIN_PASSWORD` (default: changeme)

⚠️ **Change the password immediately after first login in production!**

## 📁 What's Where

```
L-manager/
├── app.py                          ← Start here
├── README.md                        ← Full documentation
├── MIGRATION_SUMMARY.md             ← What changed
├── .env.example                     ← Configuration template
├── _archive/README.md               ← Restore Django if needed
├── _archive/django_app/             ← Old Django app (archived)
└── _archive/flask_old/              ← Old Flask app (archived)
```

## 🐛 Troubleshooting

**"Address already in use" on port 5000**:
```bash
PORT=5001 python app.py
# Or set PORT in .env
```

**"Missing PropertyFinder credentials"**:
```bash
# Add to .env:
PF_CLIENT_ID=your_client_id
PF_CLIENT_SECRET=your_client_secret
```

**"Cannot access http://localhost:5000"**:
```bash
# Check if app is running in terminal
# Look for: "Running on http://127.0.0.1:5000"
# Try: curl http://localhost:5000/health
```

**Database errors**:
- App auto-creates SQLite at `data/listings.db`
- For PostgreSQL: set `DATABASE_URL` in `.env`

## 📊 What Changed

1. ✅ **Removed hardcoded workspace name** - Now configurable via `DEFAULT_WORKSPACE_*`
2. ✅ **Removed hardcoded admin credentials** - Now configurable via `ADMIN_*`
3. ✅ **Generalized host/port** - Now configurable via `HOST` and `PORT`
4. ✅ **Updated .env.example** - Complete with all options
5. ✅ **Updated README.md** - Comprehensive documentation
6. ✅ **Archived Django app** - Safe in `_archive/django_app/`

## 🔄 Need to Switch Back to Django?

See `_archive/README.md` for detailed restoration instructions.

Quick summary:
```bash
# Restore Django app
mv _archive/django_app ./

# Switch to staging branch
git checkout staging

# Install Django dependencies
pip install -r requirements.txt

# Run Django
python django_app/manage.py runserver
```

---

**App Status**: ✅ Running on `http://localhost:5000`  
**Configuration**: ⚙️ Via `.env` (copy from `.env.example`)  
**Production Ready**: 🚀 Deploy to Railway  
**Documentation**: 📚 See `README.md`
