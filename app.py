"""Personal Expense Tracker.

Stack: Python + Streamlit (UI) + Firebase Firestore (database) + Gemini (AI).

Features
- Add / edit / delete expenses (stored in Firestore)
- Filter by category, totals and a per-category chart
- "Smart add": type "Lunch 450 yesterday" and Gemini pre-fills the form
- "AI insights": Gemini summarises your spending and suggests savings
- Optional password gate (set APP_PASSWORD in secrets)
"""

import hmac
import json
import os
from datetime import date

import firebase_admin
import pandas as pd
import streamlit as st
from firebase_admin import credentials, firestore
from google import genai
from google.genai import types

st.set_page_config(page_title="Expense Tracker", page_icon="💸", layout="wide")

# --------------------------------------------------------------------------- #
# Configuration (Streamlit secrets first, environment variables as fallback)
# --------------------------------------------------------------------------- #
CATEGORIES = [
    "Food",
    "Transport",
    "Bills",
    "Shopping",
    "Health",
    "Education",
    "Entertainment",
    "Other",
]
COLLECTION = "expenses"


def _secret(key: str, default=None):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:  # no secrets file available
        pass
    return os.getenv(key, default)


CURRENCY = str(_secret("CURRENCY", "PKR"))
GEMINI_MODEL = str(_secret("GEMINI_MODEL", "gemini-2.5-flash"))


# --------------------------------------------------------------------------- #
# Optional password gate
# --------------------------------------------------------------------------- #
def require_password() -> None:
    password = _secret("APP_PASSWORD")
    if not password or st.session_state.get("authed"):
        return
    st.title("💸 Expense Tracker")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered, str(password)):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


# --------------------------------------------------------------------------- #
# Firebase (Firestore) data layer
# --------------------------------------------------------------------------- #
_SA_KEYS = (
    "type",
    "project_id",
    "private_key_id",
    "private_key",
    "client_email",
    "client_id",
    "auth_uri",
    "token_uri",
    "auth_provider_x509_cert_url",
    "client_x509_cert_url",
    "universe_domain",
)
_SA_REQUIRED = ("project_id", "private_key", "client_email")


def _secrets_status() -> tuple[list[str], str | None]:
    """Return (secret key names, error message if secrets can't be read)."""
    try:
        return list(st.secrets.keys()), None
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"


def _find_service_account() -> dict | str:
    """Locate Firebase credentials. Returns a dict (from secrets) or a file path.

    Supported, in order:
      1. FIREBASE_JSON  = '''<entire service-account JSON>'''   (easiest)
      2. [firebase] table with the JSON fields as TOML keys
      3. the JSON fields pasted at the top level of the secrets
      4. a local key file (FIREBASE_CREDENTIALS, default serviceAccountKey.json)
    """
    for key in ("FIREBASE_JSON", "firebase", "FIREBASE_SERVICE_ACCOUNT"):
        raw = _secret(key)
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"`{key}` is set but is not valid JSON: {exc}") from exc
        return dict(raw)

    if _secret("private_key") and _secret("client_email"):
        return {k: _secret(k) for k in _SA_KEYS if _secret(k) is not None}

    path = str(_secret("FIREBASE_CREDENTIALS", "serviceAccountKey.json"))
    if os.path.exists(path):
        return path

    names, error = _secrets_status()
    detail = (
        f"Streamlit could not read your secrets ({error})."
        if error
        else f"Your secrets currently contain these keys: {names or 'none'}."
    )
    raise RuntimeError(
        "No Firebase credentials found. "
        + detail
        + " Add FIREBASE_JSON (or a [firebase] table) to your secrets, "
        "save, and reboot the app. See README step 3."
    )


@st.cache_resource
def get_db():
    """Initialise Firebase once and return the Firestore client."""
    try:
        firebase_admin.get_app()
    except ValueError:
        source = _find_service_account()
        if isinstance(source, dict):
            missing = [k for k in _SA_REQUIRED if not source.get(k)]
            if missing:
                raise RuntimeError(
                    f"Firebase credentials are missing: {', '.join(missing)}."
                ) from None
            # Fix keys whose newlines were pasted as literal "\n" text.
            source = {
                **source,
                "private_key": str(source["private_key"]).replace("\\n", "\n"),
            }
        firebase_admin.initialize_app(credentials.Certificate(source))
    return firestore.client()


@st.cache_data(ttl=300, show_spinner="Loading expenses...")
def load_expenses() -> list[dict]:
    """Return all expenses, newest first."""
    rows = []
    for doc in get_db().collection(COLLECTION).stream():
        data = doc.to_dict() or {}
        try:
            when = date.fromisoformat(str(data.get("date")))
        except ValueError:
            when = date.today()
        rows.append(
            {
                "id": doc.id,
                "title": str(data.get("title", "")),
                "amount": float(data.get("amount", 0)),
                "category": str(data.get("category", "Other")),
                "date": when,
                "note": str(data.get("note", "")),
            }
        )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def save_expense(expense_id: str | None, payload: dict) -> None:
    col = get_db().collection(COLLECTION)
    if expense_id:
        col.document(expense_id).update(
            {**payload, "updated_at": firestore.SERVER_TIMESTAMP}
        )
    else:
        col.add({**payload, "created_at": firestore.SERVER_TIMESTAMP})
    load_expenses.clear()


def remove_expense(expense_id: str) -> None:
    get_db().collection(COLLECTION).document(expense_id).delete()
    load_expenses.clear()


# --------------------------------------------------------------------------- #
# Gemini AI layer
# --------------------------------------------------------------------------- #
def _gemini_client() -> genai.Client:
    api_key = _secret("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in your secrets.")
    return genai.Client(api_key=str(api_key))


def parse_expense_with_gemini(text: str) -> dict:
    """Turn free text like 'Lunch 450 yesterday' into a structured expense."""
    prompt = f"""Extract exactly one expense from the text below.
Today's date is {date.today().isoformat()}.
Respond with JSON only, in this shape:
{{"title": string, "amount": number, "category": one of {CATEGORIES}, "date": "YYYY-MM-DD", "note": string}}
If the date is not mentioned, use today's date. If unsure of the category, use "Other".

Text: {text}"""
    response = _gemini_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0
        ),
    )
    data = json.loads(response.text)
    if isinstance(data, list):
        data = data[0]

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0.0
    try:
        when = date.fromisoformat(str(data.get("date")))
    except ValueError:
        when = date.today()
    category = data.get("category")
    return {
        "title": str(data.get("title", "")),
        "amount": amount,
        "category": category if category in CATEGORIES else "Other",
        "date": when,
        "note": str(data.get("note", "")),
    }


def generate_spending_insights(expenses: list[dict]) -> str:
    """Ask Gemini for a short analysis of the user's spending."""
    lines = "\n".join(
        f"{e['date']} | {e['title']} | {e['amount']:.2f} {CURRENCY} | {e['category']}"
        for e in expenses[:200]
    )
    prompt = f"""You are a concise personal-finance assistant.
Below are my expenses (date | title | amount | category).

Give me:
1. A two-sentence overview of my spending.
2. The top 3 spending patterns you notice.
3. Three specific, realistic ways I could save money.

Use short markdown bullets and only the data provided. Do not invent numbers.

{lines}"""
    response = _gemini_client().models.generate_content(
        model=GEMINI_MODEL, contents=prompt
    )
    return response.text or "No insights were returned."


# --------------------------------------------------------------------------- #
# Dialogs: add / edit / delete
# --------------------------------------------------------------------------- #
@st.dialog("Expense")
def expense_dialog(expense: dict | None = None) -> None:
    e = expense or {}
    category = e.get("category", CATEGORIES[0])

    title = st.text_input("Title", value=e.get("title", ""), placeholder="Groceries")
    amount = st.number_input(
        f"Amount ({CURRENCY})",
        min_value=0.0,
        step=1.0,
        format="%.2f",
        value=float(e.get("amount", 0.0)),
    )
    category = st.selectbox(
        "Category",
        CATEGORIES,
        index=CATEGORIES.index(category) if category in CATEGORIES else 0,
    )
    when = st.date_input("Date", value=e.get("date", date.today()))
    note = st.text_area("Note (optional)", value=e.get("note", ""))

    if st.button("Save", type="primary"):
        if not title.strip():
            st.error("Please enter a title.")
        elif amount <= 0:
            st.error("Amount must be greater than zero.")
        else:
            try:
                save_expense(
                    e.get("id"),
                    {
                        "title": title.strip(),
                        "amount": round(float(amount), 2),
                        "category": category,
                        "date": when.isoformat(),
                        "note": note.strip(),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save: {exc}")
            else:
                st.toast("Expense saved.", icon="✅")
                st.rerun()


@st.dialog("Delete expense?")
def delete_dialog(expense: dict) -> None:
    st.write(
        f"**{expense['title']}** - {CURRENCY} {expense['amount']:,.2f} "
        f"on {expense['date']}"
    )
    st.caption("This can't be undone.")
    left, right = st.columns(2)
    if left.button("Delete", type="primary"):
        try:
            remove_expense(expense["id"])
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not delete: {exc}")
        else:
            st.toast("Expense deleted.", icon="🗑️")
            st.rerun()
    if right.button("Cancel"):
        st.rerun()


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
require_password()

st.title("💸 Expense Tracker")

try:
    expenses = load_expenses()
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not connect to Firebase: {exc}")
    st.info("Fix your Firebase credentials in the Streamlit secrets (README, step 3).")
    st.stop()

# ---- Smart add (Gemini) ---------------------------------------------------- #
with st.form("smart_add", clear_on_submit=True):
    smart_text = st.text_input(
        "✨ Smart add (Gemini)",
        placeholder="e.g. Lunch at the cafe 450 yesterday",
    )
    smart_submitted = st.form_submit_button("Fill form")

if smart_submitted and smart_text.strip():
    try:
        with st.spinner("Asking Gemini..."):
            parsed = parse_expense_with_gemini(smart_text.strip())
    except Exception as exc:  # noqa: BLE001
        st.error(f"Gemini could not parse that: {exc}")
    else:
        expense_dialog(parsed)  # pre-filled so you can review before saving

# ---- Summary --------------------------------------------------------------- #
top_left, top_right = st.columns([3, 1])
with top_right:
    if st.button("➕ Add expense", type="primary"):
        expense_dialog()

with top_left:
    chosen = st.selectbox("Filter by category", ["All", *CATEGORIES])

visible = expenses if chosen == "All" else [e for e in expenses if e["category"] == chosen]
today = date.today()
month_total = sum(
    e["amount"]
    for e in visible
    if e["date"].year == today.year and e["date"].month == today.month
)

m1, m2, m3 = st.columns(3)
m1.metric(f"Total ({CURRENCY})", f"{sum(e['amount'] for e in visible):,.2f}")
m2.metric("This month", f"{month_total:,.2f}")
m3.metric("Expenses", len(visible))

if expenses:
    by_category = (
        pd.DataFrame(expenses).groupby("category")["amount"].sum().sort_values(ascending=False)
    )
    st.bar_chart(by_category)

# ---- Table with edit / delete ---------------------------------------------- #
st.subheader("Expenses")
if visible:
    amount_label = f"Amount ({CURRENCY})"
    table = pd.DataFrame(
        [
            {
                "Date": e["date"],
                "Title": e["title"],
                "Category": e["category"],
                amount_label: e["amount"],
                "Note": e["note"],
            }
            for e in visible
        ]
    )
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={amount_label: st.column_config.NumberColumn(format="%.2f")},
    )

    selected_rows = event.selection.rows
    if selected_rows:
        selected = visible[selected_rows[0]]
        edit_col, delete_col, _ = st.columns([1, 1, 4])
        if edit_col.button("✏️ Edit selected"):
            expense_dialog(selected)
        if delete_col.button("🗑️ Delete selected"):
            delete_dialog(selected)
    else:
        st.caption("Select a row to edit or delete it.")
else:
    st.info("No expenses yet. Click **Add expense** to get started.")

# ---- AI insights ----------------------------------------------------------- #
st.subheader("AI insights")
if st.button("Analyse my spending"):
    if not expenses:
        st.info("Add a few expenses first.")
    else:
        try:
            with st.spinner("Asking Gemini..."):
                st.session_state["insights"] = generate_spending_insights(expenses)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Gemini error: {exc}")

if st.session_state.get("insights"):
    st.markdown(st.session_state["insights"])
