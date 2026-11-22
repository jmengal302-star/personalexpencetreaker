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
3. Go to **Project settings → Service accounts → Generate new private key**. A `.json` file downloads. Keep it private.

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

## 3. Secrets (the step most people get wrong)

On Streamlit Cloud there is no key file, so the Firebase credentials must go into **Settings → Secrets** (locally: `.streamlit/secrets.toml`). The secrets box is **TOML**, not JSON.

### Easiest: paste the whole JSON file

Open the downloaded Firebase `.json` file, copy **everything**, and paste it between the triple single quotes (`'''`):

```toml
GEMINI_API_KEY = "your-gemini-api-key"
GEMINI_MODEL = "gemini-2.5-flash"    # optional
CURRENCY = "PKR"                     # optional
APP_PASSWORD = "choose-a-password"   # optional, recommended when deployed

FIREBASE_JSON = '''
{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "...",
  ...
}
'''
```

Use single quotes `'''`, not double quotes, so the `\n` characters inside the key are left alone.

### Alternative: a `[firebase]` table

```toml
GEMINI_API_KEY = "your-gemini-api-key"

[firebase]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

Rules for this format:

- Put plain keys like `GEMINI_API_KEY` **above** `[firebase]`. Anything under `[firebase]` belongs to that table.
- Every value needs quotes, and the key is `key = "value"`, not `"key": "value"`.

After saving the secrets, **reboot the app** (Manage app → ⋮ → Reboot).

## 4. Run

**Locally**

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Locally you can also skip secrets for Firebase by saving the key file as `serviceAccountKey.json` next to `app.py` (set `GEMINI_API_KEY` in `.streamlit/secrets.toml` or as an environment variable).

**On Streamlit Community Cloud**

1. Push `app.py`, `requirements.txt` and `README.md` to a GitHub repo.
2. Create the app at [share.streamlit.io](https://share.streamlit.io) and set the main file to `app.py`.
3. In **Advanced settings**, choose **Python 3.12** (newer versions can lack prebuilt packages for some dependencies) and paste your secrets.
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
  *.json
  .venv/
  __pycache__/
  ```
- Never paste the Firebase key into `app.py` or commit it. If it leaks, delete the key in Firebase (Service accounts → Manage keys) and generate a new one.
- Streamlit Cloud apps are public by default. Set `APP_PASSWORD`, and/or restrict viewers in the app's **Share** settings, because this app shows your personal finances.
- Expense data (titles, amounts, categories, dates) is sent to the Gemini API when you use Smart add or AI insights.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No Firebase credentials found` | The message lists the secret names the app can see. Add `FIREBASE_JSON` (or `[firebase]`) as in step 3, save, reboot. |
| `Streamlit could not read your secrets (...)` | Your secrets box isn't valid TOML (for example raw JSON pasted without `FIREBASE_JSON = '''`). Fix the syntax. |
| `No such file or directory: 'serviceAccountKey.json'` | Old version of the app. Use the latest `app.py`, which reads credentials from secrets. |
| `Firebase credentials are missing: ...` | The JSON is incomplete. Re-copy the whole downloaded file. |
| Invalid private key / PEM error | Re-copy the key. Keep the BEGIN/END lines and the `\n` sequences. |
| Firestore "API not enabled" / permission error | Create the Firestore database in your project and make sure the Cloud Firestore API is enabled. |
| `GEMINI_API_KEY is not set` | Add it to your secrets and reboot the app. |
| Gemini model not found | Set `GEMINI_MODEL` in secrets to a model your key can use. |
| Package install fails on Cloud | Redeploy with Python 3.12 in Advanced settings. |
| Table row selection or `width="stretch"` errors | Use `streamlit>=1.50.0`. |
