"""Personal Expense Tracker.

Stack: Python + Reflex (UI) + Firebase Firestore (database) + Gemini (AI).

Features
- Add / edit / delete expenses (stored in Firestore)
- Filter by category, live totals per category
- "Smart add": type "Lunch 450 yesterday" and Gemini fills the form for you
- "AI insights": Gemini summarises your spending and suggests savings
"""

import json
import os
from datetime import date
from functools import lru_cache

import firebase_admin
import reflex as rx
from dotenv import load_dotenv
from firebase_admin import credentials, firestore
from google import genai
from google.genai import types

load_dotenv()

# --------------------------------------------------------------------------- #
# Configuration
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
CURRENCY = os.getenv("CURRENCY", "PKR")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# --------------------------------------------------------------------------- #
# Firebase (Firestore) data layer
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_db():
    """Initialise Firebase once and return the Firestore client."""
    key_path = os.getenv("FIREBASE_CREDENTIALS", "serviceAccountKey.json")
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(credentials.Certificate(key_path))
    return firestore.client()


def fetch_expenses() -> list[dict[str, str]]:
    """Return all expenses, newest first. Every value is a string for the UI."""
    rows: list[dict[str, str]] = []
    for doc in get_db().collection(COLLECTION).stream():
        data = doc.to_dict() or {}
        rows.append(
            {
                "id": doc.id,
                "title": str(data.get("title", "")),
                "amount": f"{float(data.get('amount', 0)):.2f}",
                "category": str(data.get("category", "Other")),
                "date": str(data.get("date", "")),
                "note": str(data.get("note", "")),
            }
        )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def create_expense(payload: dict) -> None:
    get_db().collection(COLLECTION).add(
        {**payload, "created_at": firestore.SERVER_TIMESTAMP}
    )


def update_expense(expense_id: str, payload: dict) -> None:
    get_db().collection(COLLECTION).document(expense_id).update(
        {**payload, "updated_at": firestore.SERVER_TIMESTAMP}
    )


def remove_expense(expense_id: str) -> None:
    get_db().collection(COLLECTION).document(expense_id).delete()


# --------------------------------------------------------------------------- #
# Gemini AI layer
# --------------------------------------------------------------------------- #
def _gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in your .env file.")
    return genai.Client(api_key=api_key)


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
    if data.get("category") not in CATEGORIES:
        data["category"] = "Other"
    return data


def generate_spending_insights(expenses: list[dict[str, str]]) -> str:
    """Ask Gemini for a short analysis of the user's spending."""
    lines = "\n".join(
        f"{e['date']} | {e['title']} | {e['amount']} {CURRENCY} | {e['category']}"
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
# State
# --------------------------------------------------------------------------- #
class State(rx.State):
    # Data
    expenses: list[dict[str, str]] = []
    loading: bool = False
    filter_category: str = "All"

    # Add / edit form
    show_form: bool = False
    editing_id: str = ""
    form_title: str = ""
    form_amount: str = ""
    form_category: str = CATEGORIES[0]
    form_date: str = ""
    form_note: str = ""

    # AI
    smart_text: str = ""
    ai_loading: bool = False
    insights: str = ""

    # ---- generic setter (avoids one setter per field) ---------------------- #
    def set_field(self, field: str, value: str):
        setattr(self, field, value)

    def toggle_form(self, is_open: bool):
        self.show_form = is_open

    # ---- computed values --------------------------------------------------- #
    @rx.var
    def visible_expenses(self) -> list[dict[str, str]]:
        if self.filter_category == "All":
            return self.expenses
        return [e for e in self.expenses if e["category"] == self.filter_category]

    @rx.var
    def total_spent(self) -> str:
        total = sum(float(e["amount"]) for e in self.visible_expenses)
        return f"{total:,.2f}"

    @rx.var
    def category_totals(self) -> list[dict[str, str]]:
        totals: dict[str, float] = {}
        for e in self.expenses:
            totals[e["category"]] = totals.get(e["category"], 0) + float(e["amount"])
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        return [{"category": c, "amount": f"{a:,.2f}"} for c, a in ranked]

    # ---- read -------------------------------------------------------------- #
    def load_expenses(self):
        self.loading = True
        yield
        try:
            self.expenses = fetch_expenses()
        except Exception as exc:  # noqa: BLE001
            yield rx.toast.error(f"Could not load expenses: {exc}")
        finally:
            self.loading = False

    # ---- create / update --------------------------------------------------- #
    def _reset_form(self):
        self.editing_id = ""
        self.form_title = ""
        self.form_amount = ""
        self.form_category = CATEGORIES[0]
        self.form_date = date.today().isoformat()
        self.form_note = ""

    def open_add(self):
        self._reset_form()
        self.show_form = True

    def open_edit(self, expense_id: str):
        expense = next((e for e in self.expenses if e["id"] == expense_id), None)
        if expense is None:
            return rx.toast.error("Expense not found.")
        self.editing_id = expense_id
        self.form_title = expense["title"]
        self.form_amount = expense["amount"]
        self.form_category = expense["category"]
        self.form_date = expense["date"]
        self.form_note = expense["note"]
        self.show_form = True

    def save_expense(self):
        title = self.form_title.strip()
        if not title:
            return rx.toast.error("Please enter a title.")
        try:
            amount = float(self.form_amount)
        except ValueError:
            return rx.toast.error("Amount must be a number.")
        if amount <= 0:
            return rx.toast.error("Amount must be greater than zero.")
        if not self.form_date:
            return rx.toast.error("Please pick a date.")

        payload = {
            "title": title,
            "amount": round(amount, 2),
            "category": self.form_category,
            "date": self.form_date,
            "note": self.form_note.strip(),
        }
        try:
            if self.editing_id:
                update_expense(self.editing_id, payload)
            else:
                create_expense(payload)
            self.expenses = fetch_expenses()
        except Exception as exc:  # noqa: BLE001
            return rx.toast.error(f"Could not save: {exc}")

        self.show_form = False
        return rx.toast.success("Expense saved.")

    # ---- delete ------------------------------------------------------------ #
    def delete_expense(self, expense_id: str):
        try:
            remove_expense(expense_id)
            self.expenses = [e for e in self.expenses if e["id"] != expense_id]
        except Exception as exc:  # noqa: BLE001
            return rx.toast.error(f"Could not delete: {exc}")
        return rx.toast.success("Expense deleted.")

    # ---- AI: smart add ----------------------------------------------------- #
    def smart_add(self):
        text = self.smart_text.strip()
        if not text:
            yield rx.toast.warning("Type something like 'Lunch 450 yesterday'.")
            return
        self.ai_loading = True
        yield
        try:
            parsed = parse_expense_with_gemini(text)
        except Exception as exc:  # noqa: BLE001
            self.ai_loading = False
            yield rx.toast.error(f"Gemini could not parse that: {exc}")
            return

        # Pre-fill the form so you can review before saving.
        self.editing_id = ""
        self.form_title = str(parsed.get("title", ""))
        self.form_amount = str(parsed.get("amount", ""))
        self.form_category = parsed.get("category", "Other")
        self.form_date = str(parsed.get("date") or date.today().isoformat())
        self.form_note = str(parsed.get("note", ""))
        self.smart_text = ""
        self.ai_loading = False
        self.show_form = True

    # ---- AI: insights ------------------------------------------------------ #
    def get_insights(self):
        if not self.expenses:
            yield rx.toast.info("Add a few expenses first.")
            return
        self.ai_loading = True
        yield
        try:
            self.insights = generate_spending_insights(self.expenses)
        except Exception as exc:  # noqa: BLE001
            yield rx.toast.error(f"Gemini error: {exc}")
        finally:
            self.ai_loading = False


# --------------------------------------------------------------------------- #
# UI components
# --------------------------------------------------------------------------- #
def expense_row(e: dict) -> rx.Component:
    return rx.table.row(
        rx.table.cell(e["date"]),
        rx.table.cell(
            rx.vstack(
                rx.text(e["title"], weight="medium"),
                rx.cond(
                    e["note"] != "",
                    rx.text(e["note"], size="1", color_scheme="gray"),
                ),
                spacing="0",
            )
        ),
        rx.table.cell(rx.badge(e["category"])),
        rx.table.cell(rx.text(CURRENCY, " ", e["amount"], weight="bold")),
        rx.table.cell(
            rx.hstack(
                rx.icon_button(
                    "pencil",
                    variant="soft",
                    on_click=State.open_edit(e["id"]),
                ),
                rx.alert_dialog.root(
                    rx.alert_dialog.trigger(
                        rx.icon_button("trash-2", variant="soft", color_scheme="red")
                    ),
                    rx.alert_dialog.content(
                        rx.alert_dialog.title("Delete expense?"),
                        rx.alert_dialog.description("This can't be undone."),
                        rx.flex(
                            rx.alert_dialog.cancel(
                                rx.button("Cancel", variant="soft", color_scheme="gray")
                            ),
                            rx.alert_dialog.action(
                                rx.button(
                                    "Delete",
                                    color_scheme="red",
                                    on_click=State.delete_expense(e["id"]),
                                )
                            ),
                            spacing="3",
                            justify="end",
                            margin_top="16px",
                        ),
                    ),
                ),
                spacing="2",
            )
        ),
    )


def expense_form() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(
                rx.cond(State.editing_id != "", "Edit expense", "Add expense")
            ),
            rx.vstack(
                rx.input(
                    placeholder="Title (e.g. Groceries)",
                    value=State.form_title,
                    on_change=lambda v: State.set_field("form_title", v),
                    width="100%",
                ),
                rx.input(
                    placeholder="Amount",
                    type="number",
                    value=State.form_amount,
                    on_change=lambda v: State.set_field("form_amount", v),
                    width="100%",
                ),
                rx.select(
                    CATEGORIES,
                    value=State.form_category,
                    on_change=lambda v: State.set_field("form_category", v),
                    width="100%",
                ),
                rx.input(
                    type="date",
                    value=State.form_date,
                    on_change=lambda v: State.set_field("form_date", v),
                    width="100%",
                ),
                rx.text_area(
                    placeholder="Note (optional)",
                    value=State.form_note,
                    on_change=lambda v: State.set_field("form_note", v),
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            rx.flex(
                rx.dialog.close(
                    rx.button("Cancel", variant="soft", color_scheme="gray")
                ),
                rx.button("Save", on_click=State.save_expense),
                spacing="3",
                justify="end",
                margin_top="16px",
            ),
        ),
        open=State.show_form,
        on_open_change=State.toggle_form,
    )


def smart_add_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("sparkles", size=18),
                rx.text("Smart add (Gemini)", weight="bold"),
                align="center",
            ),
            rx.hstack(
                rx.input(
                    placeholder="e.g. Lunch at the cafe 450 yesterday",
                    value=State.smart_text,
                    on_change=lambda v: State.set_field("smart_text", v),
                    width="100%",
                ),
                rx.button(
                    "Fill form",
                    on_click=State.smart_add,
                    loading=State.ai_loading,
                ),
                width="100%",
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def summary_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.vstack(
                    rx.text("Total (current view)", size="2", color_scheme="gray"),
                    rx.heading(CURRENCY, " ", State.total_spent, size="7"),
                    spacing="1",
                ),
                rx.spacer(),
                rx.select(
                    ["All", *CATEGORIES],
                    value=State.filter_category,
                    on_change=lambda v: State.set_field("filter_category", v),
                ),
                width="100%",
                align="center",
            ),
            rx.flex(
                rx.foreach(
                    State.category_totals,
                    lambda c: rx.badge(
                        c["category"], ": ", c["amount"], variant="soft", size="2"
                    ),
                ),
                wrap="wrap",
                spacing="2",
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def expense_table() -> rx.Component:
    return rx.cond(
        State.visible_expenses.length() > 0,
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Date"),
                    rx.table.column_header_cell("Title"),
                    rx.table.column_header_cell("Category"),
                    rx.table.column_header_cell("Amount"),
                    rx.table.column_header_cell("Actions"),
                )
            ),
            rx.table.body(rx.foreach(State.visible_expenses, expense_row)),
            width="100%",
        ),
        rx.center(
            rx.text("No expenses yet. Click “Add expense” to get started."),
            padding="32px",
            width="100%",
        ),
    )


def insights_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("brain", size=18),
                rx.text("AI insights", weight="bold"),
                rx.spacer(),
                rx.button(
                    "Analyse my spending",
                    variant="soft",
                    on_click=State.get_insights,
                    loading=State.ai_loading,
                ),
                width="100%",
                align="center",
            ),
            rx.cond(State.insights != "", rx.markdown(State.insights)),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def index() -> rx.Component:
    return rx.container(
        rx.vstack(
            rx.hstack(
                rx.heading("Expense Tracker", size="8"),
                rx.spacer(),
                rx.color_mode.button(),
                rx.button(rx.icon("plus"), "Add expense", on_click=State.open_add),
                width="100%",
                align="center",
            ),
            smart_add_card(),
            summary_card(),
            rx.cond(State.loading, rx.center(rx.spinner(), width="100%")),
            expense_table(),
            insights_card(),
            expense_form(),
            spacing="5",
            width="100%",
            padding_y="24px",
        ),
        size="3",
    )


app = rx.App(theme=rx.theme(accent_color="indigo", radius="large"))
app.add_page(index, title="Expense Tracker", on_load=State.load_expenses)
