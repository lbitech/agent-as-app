import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import json

from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    Numeric,
    JSON,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import declarative_base, Session
from google.adk.agents.llm_agent import Agent

# --- DATABASE LOGIC ---
# Reuse the same engine as todo.py — both modules share the same Cloud SQL
# instance and database, so a second connector would conflict in async context.
from .todo import get_engine as _get_engine

Base = declarative_base()


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(PGUUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    account_id = Column(String(8), nullable=False)
    merchant_id = Column(PGUUID(as_uuid=False), nullable=True)
    merchant_name = Column(String(255), nullable=True)

    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")

    type = Column(String(50), nullable=False)
    category = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False, default="pending")

    reference_number = Column(String(100), unique=True, nullable=True)
    description = Column(Text, nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)


# --- TOOL FUNCTIONS ---

def list_transactions(
    account_id: str,
    status: Optional[str] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> list:
    """
    Lists transactions for an account, with optional filters.

    Args:
        account_id (str): The 8-character account ID to query (required).
        status (str, optional): Filter by status — 'pending', 'completed', 'failed', 'reversed'.
        type (str, optional): Filter by type — 'purchase', 'refund', 'transfer', 'fee'.
        category (str, optional): Filter by category, e.g. 'groceries', 'travel'.
        limit (int): Maximum number of results to return (default 20, max 100).

    Returns:
        list: A list of transaction dicts ordered by most recent first.
    """
    print(f"[TXN] list_transactions called: account_id={account_id!r}")
    engine = _get_engine()
    if not engine:
        print("[TXN] list_transactions: no engine available")
        return []

    with Session(engine) as session:
        query = select(Transaction).where(
            Transaction.account_id == account_id
        )
        if status:
            query = query.where(Transaction.status == status.lower())
        if type:
            query = query.where(Transaction.type == type.lower())
        if category:
            query = query.where(Transaction.category == category.lower())
        query = query.order_by(Transaction.created_at.desc()).limit(min(limit, 100))

        results = session.execute(query).scalars().all()
        print(f"[TXN] list_transactions: found {len(results)} rows")
        return [
            {
                "id": t.id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "account_id": t.account_id,
                "merchant_name": t.merchant_name,
                "amount": float(t.amount),
                "currency": t.currency,
                "type": t.type,
                "category": t.category,
                "status": t.status,
                "description": t.description,
                "reference_number": t.reference_number,
            }
            for t in results
        ]


def get_transaction(transaction_id: str) -> dict:
    """
    Retrieves a single transaction by its ID.

    Args:
        transaction_id (str): The UUID of the transaction.

    Returns:
        dict: Full transaction details, or an error message if not found.
    """
    engine = _get_engine()
    if not engine:
        return {"error": "Database not available"}

    with Session(engine) as session:
        t = session.get(Transaction, transaction_id)
        if not t:
            return {"error": f"Transaction {transaction_id} not found"}
        return {
            "id": t.id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "account_id": t.account_id,
            "merchant_id": t.merchant_id,
            "merchant_name": t.merchant_name,
            "amount": float(t.amount),
            "currency": t.currency,
            "type": t.type,
            "category": t.category,
            "status": t.status,
            "reference_number": t.reference_number,
            "description": t.description,
            "metadata": t.extra_metadata,
        }


def create_transaction(
    account_id: str,
    amount: float,
    type: str,
    currency: str = "USD",
    merchant_name: Optional[str] = None,
    merchant_id: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    reference_number: Optional[str] = None,
    status: str = "pending",
) -> dict:
    """
    Creates a new transaction record.

    Args:
        account_id (str): The 8-character account ID (required).
        amount (float): Transaction amount, positive number (required).
        type (str): Transaction type — 'purchase', 'refund', 'transfer', 'fee' (required).
        currency (str): ISO 4217 currency code, e.g. 'USD' (default: 'USD').
        merchant_name (str, optional): Name of the merchant.
        merchant_id (str, optional): UUID of the merchant.
        category (str, optional): Category, e.g. 'groceries', 'travel', 'utilities'.
        description (str, optional): Free-text description.
        reference_number (str, optional): Unique external reference number.
        status (str): Initial status — 'pending', 'completed', 'failed' (default: 'pending').

    Returns:
        dict: The new transaction's ID and confirmation.
    """
    engine = _get_engine()
    if not engine:
        return {"error": "Database not available"}

    with Session(engine) as session:
        txn = Transaction(
            account_id=account_id,
            amount=Decimal(str(amount)),
            type=type.lower(),
            currency=currency.upper(),
            merchant_name=merchant_name,
            merchant_id=merchant_id,
            category=category.lower() if category else None,
            description=description,
            reference_number=reference_number,
            status=status.lower(),
        )
        session.add(txn)
        session.commit()
        return {"id": txn.id, "status": "Transaction created", "amount": float(txn.amount), "type": txn.type}


def update_transaction_status(transaction_id: str, status: str) -> dict:
    """
    Updates the status of an existing transaction.

    Args:
        transaction_id (str): The UUID of the transaction.
        status (str): New status — 'pending', 'completed', 'failed', 'reversed'.

    Returns:
        dict: Confirmation of the update.
    """
    valid_statuses = {"pending", "completed", "failed", "reversed"}
    if status.lower() not in valid_statuses:
        return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"}

    engine = _get_engine()
    if not engine:
        return {"error": "Database not available"}

    with Session(engine) as session:
        result = session.execute(
            update(Transaction)
            .where(Transaction.id == transaction_id)
            .values(status=status.lower(), updated_at=datetime.now(timezone.utc))
        )
        session.commit()
        if result.rowcount == 0:
            return {"error": f"Transaction {transaction_id} not found"}
        return {"id": transaction_id, "status": status.lower(), "updated": True}


def show_transaction_form() -> str:
    """
    Returns a form specification for creating a transaction as a JSON string.
    The frontend will parse this JSON and render it as an interactive HTML form.

    Returns:
        str: JSON string representing the form schema.
    """
    form_spec = {
        "type": "form",
        "form_id": "create_transaction_form",
        "title": "Create Transaction",
        "description": "Enter the transaction details below",
        "fields": [
            {
                "name": "account_id",
                "label": "Account ID",
                "type": "text",
                "required": True,
                "placeholder": "e.g. ACC00001",
            },
            {
                "name": "amount",
                "label": "Amount",
                "type": "text",
                "required": True,
                "placeholder": "e.g. 49.99",
            },
            {
                "name": "type",
                "label": "Transaction Type",
                "type": "select",
                "required": True,
                "default": "purchase",
                "options": [
                    {"value": "purchase", "label": "Purchase"},
                    {"value": "refund", "label": "Refund"},
                    {"value": "transfer", "label": "Transfer"},
                    {"value": "fee", "label": "Fee"},
                ],
            },
            {
                "name": "currency",
                "label": "Currency",
                "type": "select",
                "required": True,
                "default": "USD",
                "options": [
                    {"value": "USD", "label": "USD"},
                    {"value": "EUR", "label": "EUR"},
                    {"value": "GBP", "label": "GBP"},
                ],
            },
            {
                "name": "merchant_name",
                "label": "Merchant Name",
                "type": "text",
                "required": False,
                "placeholder": "e.g. Amazon",
            },
            {
                "name": "category",
                "label": "Category",
                "type": "select",
                "required": False,
                "default": "",
                "options": [
                    {"value": "", "label": "— None —"},
                    {"value": "groceries", "label": "Groceries"},
                    {"value": "travel", "label": "Travel"},
                    {"value": "utilities", "label": "Utilities"},
                    {"value": "entertainment", "label": "Entertainment"},
                    {"value": "healthcare", "label": "Healthcare"},
                    {"value": "other", "label": "Other"},
                ],
            },
            {
                "name": "description",
                "label": "Description",
                "type": "textarea",
                "required": False,
                "placeholder": "Optional notes about this transaction",
            },
            {
                "name": "reference_number",
                "label": "Reference Number",
                "type": "text",
                "required": False,
                "placeholder": "Optional external reference",
            },
        ],
        "submit_button": "Create Transaction",
        "submit_tool": "create_transaction_from_form",
    }
    return json.dumps(form_spec)


def create_transaction_from_form(
    account_id: str,
    amount: str,
    type: str,
    currency: str = "USD",
    merchant_name: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    reference_number: Optional[str] = None,
) -> dict:
    """
    Creates a transaction from form submission data.

    Args:
        account_id (str): Account UUID (required).
        amount (str): Amount as a string (required).
        type (str): Transaction type (required).
        currency (str): Currency code (default: 'USD').
        merchant_name (str, optional): Merchant name.
        category (str, optional): Transaction category.
        description (str, optional): Free-text description.
        reference_number (str, optional): External reference number.

    Returns:
        dict: Result with transaction ID and confirmation.
    """
    try:
        amount_decimal = float(amount)
    except (ValueError, TypeError):
        return {"error": f"Invalid amount: '{amount}'"}

    result = create_transaction(
        account_id=account_id,
        amount=amount_decimal,
        type=type,
        currency=currency,
        merchant_name=merchant_name or None,
        category=category or None,
        description=description or None,
        reference_number=reference_number or None,
    )
    if "error" in result:
        return result
    return {
        "success": True,
        "transaction_id": result["id"],
        "message": f"Transaction of {currency} {amount_decimal:.2f} ({type}) created successfully.",
    }


# --- TRANSACTION SPECIALIST AGENT ---
txn_agent = Agent(
    model="gemini-2.5-flash",
    name="transaction_specialist",
    description="A specialist agent that queries and manages financial transactions.",
    instruction="""
    You are a financial transaction specialist. You help users view, search, and create transactions.

    **For listing/searching transactions:**
    - Always ask for an account_id if not provided — it is required for all queries.
    - Use list_transactions() with appropriate filters (status, type, category, limit).
    - Present results in a clear, readable format with amounts formatted to 2 decimal places.

    **For viewing a specific transaction:**
    - Use get_transaction() with the transaction UUID.

    **For creating a transaction:**
    1. Call show_transaction_form() to get the form JSON.
    2. Return the form JSON as your entire response — no surrounding text.
    3. Once the user submits the form, call create_transaction_from_form() with the submitted data.
    4. Confirm the transaction was created with its ID.

    **For updating a transaction status:**
    - Use update_transaction_status() with the transaction ID and new status.
    - Valid statuses: pending, completed, failed, reversed.

    Always confirm actions clearly and include relevant IDs in your responses.
    """,
    tools=[
        list_transactions,
        get_transaction,
        create_transaction,
        create_transaction_from_form,
        show_transaction_form,
        update_transaction_status,
    ],
)
