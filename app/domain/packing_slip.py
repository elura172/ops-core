import base64
from datetime import UTC, datetime
from typing import Any

from fpdf import FPDF


def generate_packing_slip(payload: dict[str, Any], tracking_number: str, provider: str) -> str:
    """
    Generates a PDF packing slip and returns it as a base64 string.
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()

    # Header
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(0, 10, "PACKING SLIP", border=0, align="C")
    pdf.ln(15)

    # Order details
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Order Information", new_x="LM", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(40, 6, "Order ID:")
    pdf.cell(0, 6, str(payload.get("order_id", "Unknown")), new_x="LM", new_y="NEXT")
    pdf.cell(40, 6, "Sales Channel:")
    pdf.cell(0, 6, str(payload.get("source_channel", "Unknown")).replace("_", " ").title(), new_x="LM", new_y="NEXT")
    pdf.cell(40, 6, "Date Generated:")
    pdf.cell(0, 6, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"), new_x="LM", new_y="NEXT")
    pdf.cell(40, 6, "Shipping Provider:")
    pdf.cell(0, 6, provider, new_x="LM", new_y="NEXT")
    pdf.cell(40, 6, "Tracking Number:")
    pdf.cell(0, 6, tracking_number, new_x="LM", new_y="NEXT")
    pdf.ln(10)

    # Customer details
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Shipping Address", new_x="LM", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)

    address = payload.get("shipping_address", {})
    pdf.cell(0, 6, str(address.get("name", "Unknown Customer")), new_x="LM", new_y="NEXT")
    if "line1" in address:
        pdf.cell(0, 6, str(address["line1"]), new_x="LM", new_y="NEXT")
    if "line2" in address:
        pdf.cell(0, 6, str(address["line2"]), new_x="LM", new_y="NEXT")
    city = address.get("city", "")
    state = address.get("state", "")
    zip_code = address.get("postal_code", "")
    city_str = f"{city}, {state} {zip_code}".strip()
    if city_str.strip(","):
        pdf.cell(0, 6, city_str, new_x="LM", new_y="NEXT")
    if "country" in address:
        pdf.cell(0, 6, str(address["country"]), new_x="LM", new_y="NEXT")
    pdf.ln(10)

    # Line items
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Line Items", new_x="LM", new_y="NEXT")
    pdf.set_font("helvetica", "B", 10)

    # Table header
    pdf.cell(120, 8, "SKU", border=1)
    pdf.cell(30, 8, "Qty", border=1, align="C")
    pdf.ln(8)

    # Table rows
    pdf.set_font("helvetica", "", 10)
    items = payload.get("line_items", [])
    if not items:
        pdf.cell(150, 8, "No items listed", border=1, align="C")
        pdf.ln(8)
    else:
        for item in items:
            pdf.cell(120, 8, str(item.get("sku", "Unknown SKU")), border=1)
            pdf.cell(30, 8, str(item.get("quantity", 1)), border=1, align="C")
            pdf.ln(8)

    pdf.ln(20)
    pdf.set_font("helvetica", "I", 9)
    pdf.cell(0, 6, "Thank you for your business!", align="C", new_x="LM", new_y="NEXT")

    # Output to a bytes buffer instead of writing to disk
    pdf_bytes = pdf.output()
    return base64.b64encode(pdf_bytes).decode("utf-8")
