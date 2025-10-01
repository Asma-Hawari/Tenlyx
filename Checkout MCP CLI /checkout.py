#!/usr/bin/env python3
from typing import Any
import os
import sys
import asyncio
import json
from checkout_sdk.checkout_sdk import CheckoutSdk
from checkout_sdk.environment import Environment
from checkout_sdk.payments.payments import PaymentsQueryFilter  # <-- Added this necessary import
from mcp.server.fastmcp import FastMCP
from checkout_sdk.payments.links.payments_links import PaymentLinkRequest
from checkout_sdk.payments.payments_previous import BillingInformation
from checkout_sdk.common.common import Address, Phone
from checkout_sdk.customers.customers import CustomerRequest

mcp = FastMCP("checkout")
CHECKOUT_API_BASE = "https://api.sandbox.checkout.com"

# Use environment variable if available
CKO_SECRET_KEY = os.getenv("CKO_SECRET_KEY")
CKO_PUBLIC_KEY = os.getenv("CKO_PUBLIC_KEY")


async def get_checkout_client() -> CheckoutSdk:
    """
    Build and return a Checkout.com SDK client for the sandbox environment.
    """
    return (
        CheckoutSdk.builder()
        .secret_key(CKO_SECRET_KEY)
        .public_key(CKO_PUBLIC_KEY)
        .environment(Environment.sandbox())
        # .environment_subdomain("subdomain")  # optional
        .build()
    )



@mcp.tool()
async def refund_payment(payment_id: str) -> str:
    """
    Initiates a full refund for a payment using its unique payment ID.
    Note: For a partial refund, the SDK typically requires a RefundRequest body with the amount.
    This simplified tool assumes a full refund when only the payment_id is provided.
    """
    if not payment_id:
        return "⚠️ Error: Please provide a payment ID to refund."

    try:
        checkout = await get_checkout_client()
        payments_client = checkout.payments

        # FIX: Using the variable 'payment_id' instead of the hardcoded string 'payment_id'.
        # Assuming the refund_payment method accepts only the ID for a full refund (simple case).
        # In a real app, you would likely need to pass an amount/currency request body.
        refund_response = payments_client.refund_payment(payment_id)

        # Check the response for success indicators based on the provided JSON structure (action_id presence)
        if refund_response and hasattr(refund_response, 'action_id'):
            # Use getattr to safely access response attributes
            action_id = getattr(refund_response, 'action_id', 'N/A')
            reference = getattr(refund_response, 'reference', 'N/A')

            return (
                f"--- Refund Request Submitted Successfully ---\n"
                f"Original Payment ID: {payment_id}\n"
                f"Action ID: {action_id}\n"
                f"Reference: {reference}\n"
                f"Status: Pending (Check payment details for final status)"
            # Refund is usually pending async processing
            )
        else:
            # Handle non-successful submission (e.g., API returned an error response wrapper)
            status = getattr(refund_response, 'status', 'Failed')
            error_details = getattr(refund_response, 'error_message', 'Details unavailable in response.')
            return f"❌ Refund Submission Failed for Payment ID {payment_id}. Status: {status}. Error: {error_details}"

    except Exception as e:
        # Catch network errors, bad IDs, etc.
        return f"⚠️ Exception occurred during refund processing: {e}"


@mcp.tool()
async def lookup_payment_info(payment_id: str = None, reference_number: str = None) -> str:
    """
    Looks up payment details using either a specific payment_id/transaction_id  or an order/reference number.
    Prioritizes payment_id for a direct lookup if both are provided.
    """
    if not payment_id and not reference_number:
        return "⚠️ Error: Please provide either a payment ID or a reference number."

    try:
        checkout = await get_checkout_client()
        payment_to_detail = None
        source_label = ""

        if payment_id:
            # Case 1: Direct lookup by payment ID (faster and more specific)
            payment_to_detail = checkout.payments.get_payment_details(payment_id)
            source_label = f"Payment ID {payment_id}"

        elif reference_number:
            # Case 2: Lookup by reference number (queries the list endpoint with a filter)
            query = PaymentsQueryFilter()
            query.reference = reference_number

            # get_payments_list returns a ResponseWrapper object
            list_response = checkout.payments.get_payments_list(query)

            # Check the common attributes where the list of payments might be located
            payments_list = getattr(list_response, 'payments', [])

            # Fallback: check if the SDK uses the 'data' attribute for the list
            if not payments_list and hasattr(list_response, 'data') and isinstance(list_response.data, list):
                payments_list = list_response.data

            if not payments_list:
                return f"🔍 No payments found for reference number: {reference_number}"

            # Take the first payment found for the given reference
            payment_to_detail = payments_list[0]
            source_label = f"Reference Number {reference_number} (first result)"

        # Format and return the details of the found payment
        if payment_to_detail:
            # Assuming the single payment object has these attributes directly
            return (
                f"--- Payment Details ({source_label}) ---\n"
                f"💳 Payment ID: {payment_to_detail.id}\n"
                f"Status: {payment_to_detail.status}\n"
                f"Amount: {payment_to_detail.amount} {payment_to_detail.currency}\n"
                f"Approved: {payment_to_detail.approved}\n"
            )
        else:
            return "🔍 Payment not found."

    except Exception as e:
        # Catch network errors, bad IDs, etc.
        return f"⚠️ Exception occurred: {e}"


@mcp.tool()
async def create_payment_link(
        amount: int,
        currency: str,
        customer_email: str,
        phone_country_code: str,
        phone_number: str,
        billing_country: str
) -> str:
    """
    Creates a hosted payment page link for the customer to complete a payment,
    using all specified customer and billing details passed as arguments.

    Requires amount (in minor units), currency (e.g., 'USD'), a merchant reference, return URL,
    and comprehensive customer/billing information.
    """
    if not all([amount, currency, customer_email,
                phone_country_code, phone_number,
                 billing_country]):
        return "⚠️ Error: Please provide all required parameters: amount, currency, reference, customer_email, phone_country_code, phone_number, billing_address_line1, billing_city, and billing_country."

    try:
        checkout_api = await get_checkout_client()

        # 1. Setup Phone object dynamically
        phone = Phone()
        phone.country_code = phone_country_code
        phone.number = phone_number

        # 2. Setup Customer object dynamically
        customer = CustomerRequest()
        customer.email = customer_email
        customer.phone = phone

        # 3. Setup Billing Address and Info objects dynamically
        address = Address()

        address.country = billing_country

        billing_info = BillingInformation()
        billing_info.address = address

        # 4. Setup PaymentLinkRequest object dynamically
        payment_link = PaymentLinkRequest()
        payment_link.amount = amount
        payment_link.currency = currency
        payment_link.description = "Generated By MCP Server"
        payment_link.capture = True
        payment_link.billing = billing_info
        payment_link.customer = customer

        # Make the API call using the structured SDK object
        response = checkout_api.payments_links.create_payment_link(payment_link)

        # Check if the response contains the links attribute and extract the redirect URL
        if response :
            # The 'redirect' link contains the actual URL to the hosted payment page
            payment_link_url = response._links.redirect.href

            if payment_link_url:
                return (
                    f"--- Payment Link Created Successfully ---\n"
                    f"🔗 Payment Link URL: {payment_link_url}\n"
                    f"Amount: {amount} {currency}"
                )

            return f"❌ Payment Link Creation Failed: Could not extract redirect URL from response."

        else:
            # Try to get error details from the response object
            error_details = getattr(response, 'error_message', 'Details unavailable in response.')
            return f"❌ Payment Link Creation Failed. Error: {error_details}"

    except Exception as e:
        return f"⚠️ Exception occurred during payment link creation: {e}"


# Quick CLI test mode
async def _main_test(lookup_value: str):
    """CLI Test: Tries to look up payment info using the provided value as a reference number."""
    print(f"Attempting to lookup payment for reference: {lookup_value}")
    # In the CLI test, we assume the input is the reference number for simplicity
    result = await lookup_payment_info(reference_number=lookup_value)
    print(result)


async def _test_create_payment_link():
    result = await create_payment_link(
        amount=1000,  # amount in minor units (e.g., 10 AED = 1000 fils)
        currency="AED",
        customer_email="asma@gmail.com",
        phone_country_code="+971",
        phone_number="547137304",
        billing_country="AE"
    )
    print(result)

# If you want to run it directly without MCP server:
if __name__ == "__main__" and not sys.argv[1:]:
    if len(sys.argv) > 1:
        asyncio.run(_main_test(sys.argv[1]))
    else:
        # Run as MCP server for Claude
        #mcp.run(transport="stdio")
        mcp.run(transport="http", host="0.0.0.0", port=8000)