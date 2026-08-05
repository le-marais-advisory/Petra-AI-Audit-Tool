"""Generate synthetic fixture PDFs for the SIG-GLOBAL and TOC-conditional rules.

These back the integration cases added for the reviewer-feedback brief items
1.1 (expense signage) and 1.3 (TOC required only for audited statements).

Run from the repo root:  python tests/fixtures/generate_sig_toc_fixtures.py

Produces, under tests/fixtures/documents/:
  - sig_signage_scenarios.pdf   (2 Statement-of-Operations pages: compliant / non-compliant)
  - unaudited_no_toc.pdf        (full section set, no TOC, cover marked UNAUDITED)
  - audited_no_toc.pdf          (full section set, no TOC, cover marked AUDITED)

The content is deliberately minimal but carries the heading/keyword signals the
page classifier and the document-scope rules rely on.
"""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

OUT_DIR = Path(__file__).resolve().parent / "documents"


def _new_pdf() -> FPDF:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    return pdf


def _heading(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 8, text)
    pdf.ln(2)


def _center(pdf: FPDF, text: str, size: int, bold: bool = False) -> None:
    pdf.set_font("Helvetica", "B" if bold else "", size)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 8, text, align="C")


def _line(pdf: FPDF, label: str, amount: str = "") -> None:
    pdf.set_font("Helvetica", "", 11)
    pdf.set_x(pdf.l_margin)
    if amount:
        pdf.cell(120, 6, label)
        pdf.cell(0, 6, amount, align="R")
        pdf.ln(6)
    else:
        pdf.multi_cell(0, 6, label)


def build_sig_signage_scenarios() -> None:
    """Two Statement-of-Operations pages.

    Page 1 (compliant): expenses shown as positive; net investment income positive.
             SIG-GLOBAL should PASS under the revised rule (positive expenses are OK).
    Page 2 (non-compliant): a net investment LOSS shown as a plain positive number
             instead of in parentheses. SIG-GLOBAL should FAIL.
    """
    pdf = _new_pdf()

    # Page 1 — compliant
    pdf.add_page()
    _heading(pdf, "Example Fund I, L.P.\nStatement of Operations\nFor the Year Ended December 31, 2025")
    _line(pdf, "Investment income:")
    _line(pdf, "  Interest income", "100,000")
    _line(pdf, "  Dividend income", "40,000")
    _line(pdf, "Total investment income", "140,000")
    pdf.ln(2)
    _line(pdf, "Expenses:")
    _line(pdf, "  Management fee", "20,000")
    _line(pdf, "  Professional fees", "8,000")
    _line(pdf, "  Administration fees", "5,000")
    _line(pdf, "Total expenses", "33,000")
    pdf.ln(2)
    _line(pdf, "Net investment income", "107,000")
    _line(pdf, "Net realized and unrealized gain on investments", "250,000")
    _line(pdf, "Net increase in net assets resulting from operations", "357,000")

    # Page 2 — non-compliant: net investment loss not parenthesized
    pdf.add_page()
    _heading(pdf, "Example Fund II, L.P.\nStatement of Operations\nFor the Year Ended December 31, 2025")
    _line(pdf, "Investment income:")
    _line(pdf, "  Interest income", "10,000")
    _line(pdf, "Total investment income", "10,000")
    pdf.ln(2)
    _line(pdf, "Expenses:")
    _line(pdf, "  Management fee", "60,000")
    _line(pdf, "  Professional fees", "15,000")
    _line(pdf, "Total expenses", "75,000")
    pdf.ln(2)
    # Loss shown as a plain positive number (should be "(65,000)")
    _line(pdf, "Net investment loss", "65,000")
    _line(pdf, "Net decrease in net assets resulting from operations", "65,000")

    pdf.output(str(OUT_DIR / "sig_signage_scenarios.pdf"))


def _section_pages(pdf: FPDF, *, audited: bool) -> None:
    audit_label = "Audited Financial Statements" if audited else "Unaudited Financial Statements"

    # Cover page (no Table of Contents anywhere in the document)
    pdf.add_page()
    pdf.ln(40)
    _center(pdf, "Example Fund, L.P.", 20, bold=True)
    _center(pdf, audit_label, 14, bold=True)
    _center(pdf, "For the Year Ended December 31, 2025", 12)

    # Balance Sheet
    pdf.add_page()
    _heading(pdf, "Statement of Assets and Liabilities")
    _line(pdf, "Total assets", "5,000,000")
    _line(pdf, "Total liabilities", "500,000")
    _line(pdf, "Members' capital", "4,500,000")

    # Statement of Operations
    pdf.add_page()
    _heading(pdf, "Statement of Operations")
    _line(pdf, "Total investment income", "300,000")
    _line(pdf, "Total expenses", "120,000")
    _line(pdf, "Net investment income", "180,000")

    # Statement of Changes in Members' Capital
    pdf.add_page()
    _heading(pdf, "Statement of Changes in Members' Capital")
    _line(pdf, "Members' capital, beginning of year", "4,000,000")
    _line(pdf, "Members' capital, end of year", "4,500,000")

    # Statement of Cash Flows
    pdf.add_page()
    _heading(pdf, "Statement of Cash Flows")
    _line(pdf, "Cash flows from operating activities")
    _line(pdf, "Net increase in cash", "50,000")

    # Schedule of Investments
    pdf.add_page()
    _heading(pdf, "Schedule of Investments")
    _line(pdf, "Investments in securities (100% of net assets)", "4,500,000")


def build_unaudited_no_toc() -> None:
    pdf = _new_pdf()
    _section_pages(pdf, audited=False)
    pdf.output(str(OUT_DIR / "unaudited_no_toc.pdf"))


def build_audited_no_toc() -> None:
    pdf = _new_pdf()
    _section_pages(pdf, audited=True)
    pdf.output(str(OUT_DIR / "audited_no_toc.pdf"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_sig_signage_scenarios()
    build_unaudited_no_toc()
    build_audited_no_toc()
    print(f"Wrote fixtures to {OUT_DIR}")


if __name__ == "__main__":
    main()
