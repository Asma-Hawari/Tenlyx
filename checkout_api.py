#!/usr/bin/env python3
import json
import os
import asyncio
import httpx
import re
from fastapi import FastAPI, Query, Request
from checkout_sdk.checkout_sdk import CheckoutSdk
from checkout_sdk.environment import Environment
from checkout_sdk.payments.payments import PaymentsQueryFilter
from checkout_sdk.payments.links.payments_links import PaymentLinkRequest
from checkout_sdk.payments.payments_previous import BillingInformation
from checkout_sdk.common.common import Address, Phone
from checkout_sdk.customers.customers import CustomerRequest

# ---- Checkout Client ----
# IMPORTANT: no hard-coded secrets here — set these in environment vars on Railway / locally via .env
CKO_SECRET_KEY = os.getenv("CKO_SECRET_KEY")
CKO_PUBLIC_KEY = os.getenv("CKO_PUBLIC_KEY")

# optional: quick runtime check to help debugging if secrets are missing in deployment
if not CKO_SECRET_KEY or not CKO_PUBLIC_KEY:
    # Do NOT expose secret values — just raise a useful error so you don't forget to set them.
    raise RuntimeError("CKO_SECRET_KEY and CKO_PUBLIC_KEY must be set in environment variables")

# --- Utility Function for Robust Phone Number Cleaning ---
def clean_phone_number(number: str) -> str:
    if not number:
        return ""
    cleaned = re.sub(r'[^\d\+]+', '', number).strip()
    return cleaned

CKO_API_URL = "https://api.sandbox.checkout.com"


async def get_checkout_client() -> CheckoutSdk:
    return (
        CheckoutSdk.builder()
        .secret_key(CKO_SECRET_KEY)
        .public_key(CKO_PUBLIC_KEY)
        .environment(Environment.sandbox())
        .build()
    )


app = FastAPI(title="Checkout MCP API")

@app.get("/health")
async def health():
    return {"status": "ok"}


async def search_payments_by_email(email: str) -> dict:
    try:
        headers = {
            'Authorization': f'Bearer {CKO_SECRET_KEY}',
            'Content-Type': 'application/json'
        }
        payload = {
            "query": f"email:\"{email}\"",
            "limit": 1
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f'{CKO_API_URL}/payments/search', headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            data = response.json()
            payments = data.get('data', [])
            return payments[0] if payments else {}
    except httpx.HTTPStatusError as e:
        print(f"Checkout API Search Error: {e.response.status_code} - {e.response.text}")
        return {}
    except Exception as e:
        print(f"Exception during payment search: {e}")
        return {}

# ---- MCP Tool Functions (unchanged) ----
async def refund_payment(payment_id: str) -> str:
    if not payment_id:
        return "⚠️ Error: Please provide a payment ID to refund."
    try:
        checkout = await get_checkout_client()
        refund_response = checkout.payments.refund_payment(payment_id)
        if refund_response and hasattr(refund_response, 'action_id'):
            return (
                f"--- Refund Request Submitted ---\n"
                f"Payment ID: {payment_id}\n"
                f"Action ID: {getattr(refund_response, 'action_id', 'N/A')}\n"
                f"Reference: {getattr(refund_response, 'reference', 'N/A')}\n"
                f"Status: Pending"
            )
        else:
            return f"❌ Refund Failed for Payment ID {payment_id}."
    except Exception as e:
        return f"⚠️ Exception during refund: {e}"


async def lookup_payment_info(payment_id: str = None, reference_number: str = None) -> str:
    if not payment_id and not reference_number:
        return "⚠️ Error: Provide either payment_id or reference_number."
    try:
        checkout = await get_checkout_client()
        payment_to_detail = None
        source_label = ""
        response_codes = []
        if payment_id:
            payment_to_detail = checkout.payments.get_payment_details(payment_id)
            source_label = f"Payment ID {payment_id}"
        elif reference_number:
            query = PaymentsQueryFilter()
            query.reference = reference_number
            list_response = checkout.payments.get_payments_list(query)
            payments_list = getattr(list_response, 'payments', []) or getattr(list_response, 'data', [])
            if not payments_list:
                return f"🔍 No payments found for reference: {reference_number}"
            payment_to_detail = payments_list[0]
            source_label = f"Reference {reference_number} (first result)"
        if getattr(payment_to_detail, 'status', None) == "Declined":
            actions = checkout.payments.get_payment_actions(payment_to_detail.id)
            for item in getattr(actions, 'items', []):
                if getattr(item, "authorization_type", None) == "Final":
                    response_codes.append(getattr(item, 'response_code', 'N/A'))
        result = (
            f"--- Payment Details ({source_label}) ---\n"
            f"💳 Payment ID: {payment_to_detail.id}\n"
            f"Status: {payment_to_detail.status}\n"
            f"Amount: {payment_to_detail.amount} {payment_to_detail.currency}\n"
            f"Approved: {payment_to_detail.approved}\n"
        )
        if response_codes:
            result += f"⚠️ Declined Response Codes: {', '.join(response_codes)}\n"
        return result
    except Exception as e:
        return f"⚠️ Exception: {e}"


async def create_payment_link(amount: int, currency: str, customer_email: str, phone_country_code: str, phone_number: str, billing_country: str) -> str:
    if not all([amount, currency, customer_email, phone_country_code, phone_number, billing_country]):
        return "⚠️ Error: Missing required parameters."
    try:
        checkout_api = await get_checkout_client()
        phone = Phone()
        phone.country_code = phone_country_code
        phone.number = phone_number
        customer = CustomerRequest()
        customer.email = customer_email
        customer.phone = phone
        address = Address()
        address.country = billing_country
        billing_info = BillingInformation()
        billing_info.address = address
        payment_link = PaymentLinkRequest()
        payment_link.amount = amount
        payment_link.currency = currency
        payment_link.description = "Generated By MCP Server"
        payment_link.capture = True
        payment_link.billing = billing_info
        payment_link.customer = customer
        response = checkout_api.payments_links.create_payment_link(payment_link)
        payment_link_url = getattr(getattr(response, '_links', None), 'redirect', None)
        if payment_link_url:
            return f"--- Payment Link Created ---\n🔗 URL: {payment_link_url.href}\nAmount: {amount} {currency}"
        return "❌ Payment link creation failed."
    except Exception as e:
        return f"⚠️ Exception during payment link creation: {e}"


# ---- API Endpoints (unchanged) ----
@app.get("/create-payment-link")
async def api_create_payment_link(amount: int = Query(...), currency: str = Query(...), email: str = Query(...), phone_country_code: str = Query("+971"), phone_number: str = Query(...), billing_country: str = Query("AE")):
    return {"result": await create_payment_link(amount, currency, email, phone_country_code, phone_number, billing_country)}


@app.get("/lookup-payment")
async def api_lookup_payment(payment_id: str = None, reference_number: str = None):
    return {"result": await lookup_payment_info(payment_id, reference_number)}


@app.get("/refund-payment")
async def api_refund_payment(payment_id: str):
    return {"result": await refund_payment(payment_id)}


@app.post("/get-user-context")
async def get_user_context(request: Request):
    try:
        payload = await request.json()
        telnyx_end_user_target = payload.get("data", {}).get("payload", {}).get("telnyx_end_user_target")
        if not telnyx_end_user_target:
            return {"dynamic_variables": {"lookup_result": "error", "error_message": "Missing telnyx_end_user_target in webhook payload."}}
    except Exception as e:
        return {"dynamic_variables": {"lookup_result": "error", "error_message": f"Failed to parse webhook payload: {e}"}}
    incoming_phone_number_cleaned = clean_phone_number("+971547137304")
    known_customer_phone = clean_phone_number("15551234567")
    if incoming_phone_number_cleaned == clean_phone_number("+971547137304") or incoming_phone_number_cleaned == known_customer_phone:
        customer_email = "asma.hawari@checkout.com"
        customer_name = "Asma Hawari"
    else:
        customer_email = "unknown@example.com"
        customer_name = "Valued Customer"
    latest_payment = await search_payments_by_email(customer_email)
    dynamic_variables_data = {}
    if latest_payment:
        status = latest_payment.get('status', 'N/A')
        payment_Id = latest_payment.get('id', None)
        amount = latest_payment.get('amount', 0)
        currency = latest_payment.get('currency', 'USD')
        customer_data = latest_payment.get("customer", {})
        last_order_number = latest_payment.get("reference")
        dynamic_variables_data = {
            "lookup_result": "success",
            "payment_Id": payment_Id,
            "customer_name": customer_data.get('name', customer_name),
            "customer_email": customer_data.get('email', customer_email),
            "last_order_number": last_order_number or "N/A",
            "last_payment_status": status,
            "last_payment_amount": f"{amount / 100:.2f} {currency}",
            "threshold": 1000
        }
    else:
        dynamic_variables_data = {
            "lookup_result": "not_found",
            "customer_name": customer_name,
            "payment_Id": "N/A",
            "customer_email": customer_email,
            "last_order_number": "N/A",
            "last_payment_status": "No Recent Transaction",
            "last_payment_amount": "N/A",
            "threshold": 1000
        }
    return {
        "dynamic_variables": dynamic_variables_data,
        "memory": {"conversation_query": f"metadata->telnyx_end_user_target=eq.{telnyx_end_user_target}&limit=5&order=last_message_at.desc"},
        "conversation": {"metadata": {"customer_tier": "standard", "preferred_language": "en", "timezone": "UTC"}}
    }


# ---- Run with uvicorn ----
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Starting Checkout FastAPI MCP server on port {port}...")
    uvicorn.run("checkout_api:app", host="0.0.0.0", port=port, reload=True)