# Personal Expense Tracker

A personal expense tracker built with **Python + Streamlit** (UI), **Firebase Firestore** (database) and **Gemini** (AI).

## Features

- Add, edit and delete expenses (stored in Firestore)
- Filter by category, with totals, a monthly total and a per-category chart
- **Smart add:** type `Lunch 450 yesterday` and Gemini pre-fills the form for you to review
- **AI insights:** Gemini summarises your spending and suggests ways to save
- Optional password gate

## Repo layout

```
your-repo/
├── app.py
├── requirements.txt
├── README.md
└── .gitignore            <- see "Security" below
```

## 1. Firebase (Firestore)

1. Open the [Firebase console](https://console.firebase.google.com) and create a project.
2. Go to **Build → Firestore Database → Create database** (Native mode).
3. Go to **Project settings → Service accounts → Generate new private key**. A JSON file downloads.

The app uses the Firebase Admin SDK, which bypasses Firestore security rules. Keep client-side rules locked down:

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} {
      allow read, write: if false;
    }
  }
}
```

## 2. Gemini

Create an API key in [Google AI Studio](https://aistudio.google.com/apikey).

## 3. Secrets

The same TOML works locally and on Streamlit Cloud. Copy each value from the downloaded Firebase JSON file.

```toml
GEMINI_API_KEY = "your-gemini-api-key"
GEMINI_MODEL = "gemini-2.5-flash"   # optional
CURRENCY = "PKR"                    # optional
APP_PASSWORD = "choose-a-password"  # optional, strongly recommended when deployed

[firebase]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\nPASTE_KEY_HERE\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

Keep the `\n` characters inside `private_key` exactly as they appear in the JSON file.

- **Locally:** save this as `.streamlit/secrets.toml`.
- **Streamlit Cloud:** open your app → **Settings → Secrets** and paste it there.

## 4. Run

**Locally**

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

**On Streamlit Community Cloud**

1. Push `app.py`, `requirements.txt` and `README.md` to a GitHub repo.
2. Create the app at [share.streamlit.io](https://share.streamlit.io) and set the main file to `app.py`.
3. In **Advanced settings**, choose **Python 3.12** (newer versions can lack prebuilt packages for `firebase-admin`'s dependencies) and paste your secrets.
4. Deploy.

## Data model

Collection `expenses`, one document per expense:

| Field        | Type      | Notes                                 |
|--------------|-----------|---------------------------------------|
| `title`      | string    | e.g. "Groceries"                      |
| `amount`     | number    | greater than 0, rounded to 2 decimals |
| `category`   | string    | one of the categories in `app.py`     |
| `date`       | string    | ISO format, `YYYY-MM-DD`              |
| `note`       | string    | optional                              |
| `created_at` | timestamp | set by Firestore                      |
| `updated_at` | timestamp | set on edit                           |

## Security

- Add this to `.gitignore` so secrets never reach GitHub:
  ```
  .streamlit/secrets.toml
  serviceAccountKey.json
  .venv/
  __pycache__/
  ```
- Streamlit Cloud apps are public by default. Set `APP_PASSWORD`, and/or restrict viewers in the app's **Share** settings, because this app shows your personal finances.
- Expense data (titles, amounts, categories, dates) is sent to the Gemini API when you use Smart add or AI insights.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not connect to Firebase` | Check the `[firebase]` secrets block and that Firestore is created in your project. |
| `Invalid private key` / PEM error | Make sure `private_key` keeps its `\n` sequences and the BEGIN/END lines. |
| `GEMINI_API_KEY is not set` | Add it to your secrets and reboot the app. |
| Gemini model not found | Set `GEMINI_MODEL` in secrets to a model your key can use. |
| Package install fails on Cloud | Redeploy with Python 3.12 in Advanced settings. |
| Table row selection or `width="stretch"` errors | Upgrade Streamlit (`streamlit>=1.50.0`). |
