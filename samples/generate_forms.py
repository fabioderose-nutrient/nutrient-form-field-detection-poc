"""Generate four flat PDF forms of different styles, and the ground-truth list of their fields.

The forms look like forms typed in a word processor and printed to PDF: labels, lines, boxes and
check boxes are plain page content, there is no AcroForm. Every organisation, person and address is
invented.

For each form the generator writes samples/forms/<name>.pdf and samples/forms/<name>.fields.json,
with every field the form designer meant: page, name, type, label and rectangle in PDF points
(origin at the top left of the page, like the Web SDK). The fifth form ("held_out") was written after
the detection and naming rules and is not used to tune them.

Field types: text, multiline, date, email, phone, number, checkbox, signature.

Run from the repository root:  python samples/generate_forms.py
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent / "forms"
W, H = A4
LINE_FIELD_HEIGHT = 18  # a field that sits on a line covers the 18 pt above it


class Form:
    def __init__(self, name: str, title: str, org: str, held_out: bool = False):
        self.name = name
        self.held_out = held_out
        self.path = OUT / f"{name}.pdf"
        self.c = canvas.Canvas(str(self.path), pagesize=A4)
        self.c.setTitle(title)
        self.c.setAuthor(org)
        self.page = 1
        self.fields: list[dict] = []
        self.title, self.org = title, org

    # --- drawing helpers (y = distance from the top of the page, in points)

    def text(self, x: float, y: float, s: str, size: float = 10, font: str = "Helvetica") -> float:
        self.c.setFont(font, size)
        self.c.drawString(x, H - y, s)
        return x + stringWidth(s, font, size)

    def header(self, subtitle: str = "") -> None:
        self.text(50, 62, self.title, 17, "Helvetica-Bold")
        self.text(50, 80, self.org + (f"  |  {subtitle}" if subtitle else ""), 9.5, "Helvetica")
        self.c.setLineWidth(1.2)
        self.c.line(50, H - 90, W - 50, H - 90)

    def section(self, y: float, s: str) -> None:
        self.text(50, y, s, 11.5, "Helvetica-Bold")

    def add(self, name: str, ftype: str, label: str, rect: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = rect
        self.fields.append({"page": self.page, "name": name, "type": ftype, "label": label,
                            "rect": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]})

    # --- field styles

    def line_field(self, x0: float, x1: float, y: float, name: str, ftype: str, label: str) -> None:
        """A field on a drawn line; y is the baseline of the label text."""
        self.c.setLineWidth(0.7)
        self.c.line(x0, H - (y + 2), x1, H - (y + 2))
        self.add(name, ftype, label, (x0, y + 2 - LINE_FIELD_HEIGHT, x1, y + 2))

    def labelled_line(self, x: float, x1: float, y: float, label: str, name: str, ftype: str = "text") -> None:
        end = self.text(x, y, label + ":")
        self.line_field(end + 6, x1, y, name, ftype, label)

    def typed_run(self, x: float, y: float, char: str, count: int, name: str, ftype: str, label: str, size: float = 10) -> float:
        """Underscores or dots typed as text, the way many word-processor forms are made."""
        s = char * count
        end = self.text(x, y, s, size)
        self.add(name, ftype, label, (x, y + 2 - LINE_FIELD_HEIGHT, end, y + 2))
        return end

    def box(self, x: float, top: float, w: float, h: float, name: str, ftype: str, label: str) -> None:
        self.c.setLineWidth(0.8)
        self.c.rect(x, H - top - h, w, h)
        self.add(name, ftype, label, (x, top, x + w, top + h))

    def checkbox(self, x: float, y: float, name: str, label: str, option: str | None = None, size: float = 10,
                 option_left: bool = False) -> float:
        """A square on the text baseline, with its option text on the right (or on the left)."""
        if option and option_left:
            x = self.text(x, y, option) + 5
        self.c.setLineWidth(0.8)
        self.c.rect(x, H - y - 1, size, size)
        self.add(name, "checkbox", label, (x, y + 1 - size, x + size, y + 1))
        end = x + size
        if option and not option_left:
            end = self.text(x + size + 5, y, option)
        return end

    def footer(self) -> None:
        self.c.setFont("Helvetica", 8)
        self.c.drawString(50, 30, f"{self.org} - {self.title}")
        self.c.drawRightString(W - 50, 30, f"Page {self.page}")

    def new_page(self) -> None:
        self.footer()
        self.c.showPage()
        self.page += 1

    def save(self) -> None:
        self.footer()
        self.c.save()
        truth = {"form": self.name, "title": self.title, "held_out": self.held_out, "pages": self.page,
                 "page_size": [W, H], "fields": self.fields}
        (OUT / f"{self.name}.fields.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
        print(f"{self.path.name:34s} {self.page} page(s) {len(self.fields):3d} fields")


# --------------------------------------------------------------------------- form 1: labels and lines

def membership_application() -> None:
    f = Form("membership-application", "Membership Application", "Kestrel Bay Rowing Club")
    f.header("Please write in block capitals")
    f.section(120, "1. Personal details")
    f.labelled_line(50, 545, 148, "Full name", "full_name")
    f.labelled_line(50, 290, 176, "Date of birth", "date_of_birth", "date")
    f.labelled_line(305, 545, 176, "Phone number", "phone_number", "phone")
    f.labelled_line(50, 545, 204, "Email address", "email_address", "email")
    f.labelled_line(50, 545, 232, "Home address", "home_address", "text")
    f.line_field(121, 545, 260, "home_address_line_2", "text", "Home address (second line)")
    f.labelled_line(50, 220, 288, "Postcode", "postcode")

    f.section(330, "2. Membership")
    x = f.text(50, 358, "Membership type:") + 14
    for option in ["Adult", "Junior", "Family", "Social"]:
        x = f.checkbox(x, 358, f"membership_{option.lower()}", f"Membership type: {option}", option) + 22
    x = f.text(50, 386, "Have you rowed before?") + 14
    x = f.checkbox(x, 386, "rowed_before_yes", "Have you rowed before? Yes", "Yes") + 22
    f.checkbox(x, 386, "rowed_before_no", "Have you rowed before? No", "No")
    f.labelled_line(50, 300, 414, "Years of rowing experience", "years_of_experience", "number")

    f.section(456, "3. Health")
    f.text(50, 480, "Medical conditions or allergies the coaches should know about:")
    f.box(50, 490, 495, 70, "medical_conditions", "multiline", "Medical conditions or allergies")
    f.labelled_line(50, 545, 590, "Emergency contact name", "emergency_contact_name")
    f.labelled_line(50, 545, 618, "Emergency contact phone", "emergency_contact_phone", "phone")

    f.section(660, "4. Declaration")
    f.text(50, 684, "I confirm that the information above is correct and I agree to follow the club safety rules.", 9.5)
    f.line_field(50, 300, 740, "signature", "signature", "Signature")
    f.text(50, 754, "Signature", 8.5)
    f.line_field(360, 545, 740, "signature_date", "date", "Date")
    f.text(360, 754, "Date", 8.5)
    f.save()


# --------------------------------------------------------------------------- form 2: word-processor table

def table(f: Form, top: float, cols: list[float], rows: int, row_h: float, cells: dict[tuple[int, int], tuple]) -> None:
    """Draw a grid. `cells` maps (row, col) to ("label", text) or ("field", name, type, label)."""
    f.c.setLineWidth(0.7)
    xs = [cols[0]]
    for w in cols[1:]:
        xs.append(xs[-1] + w)
    width = xs[-1] - xs[0]
    for r in range(rows + 1):
        f.c.line(xs[0], H - (top + r * row_h), xs[0] + width, H - (top + r * row_h))
    for x in xs:
        f.c.line(x, H - top, x, H - (top + rows * row_h))
    for (r, c), cell in cells.items():
        x0, x1 = xs[c], xs[c + 1]
        y0 = top + r * row_h
        if cell[0] == "label":
            bold = len(cell) > 2 and cell[2] == "bold"
            f.text(x0 + 5, y0 + row_h / 2 + 3.5, cell[1], 9.5, "Helvetica-Bold" if bold else "Helvetica")
        else:
            _, name, ftype, label = cell
            f.add(name, ftype, label, (x0, y0, x1, y0 + row_h))


def equipment_loan_request() -> None:
    f = Form("equipment-loan-request", "Equipment Loan Request", "Ferncombe Media Lab")
    f.header("Form ML-04")
    f.section(118, "Borrower")
    rows = [("Name", "borrower_name", "text"), ("Department", "department", "text"),
            ("Staff ID", "staff_id", "text"), ("Email", "borrower_email", "email"),
            ("Phone extension", "phone_extension", "phone")]
    cells = {}
    for r, (label, name, ftype) in enumerate(rows):
        cells[(r, 0)] = ("label", label)
        cells[(r, 1)] = ("field", name, ftype, label)
    table(f, 128, [50, 150, 345], len(rows), 24, cells)

    f.section(278, "Equipment")
    cells = {(0, 0): ("label", "Item", "bold"), (0, 1): ("label", "Serial number", "bold"), (0, 2): ("label", "Quantity", "bold")}
    for r in range(1, 4):
        cells[(r, 0)] = ("field", f"item_{r}", "text", f"Item, row {r}")
        cells[(r, 1)] = ("field", f"serial_number_{r}", "text", f"Serial number, row {r}")
        cells[(r, 2)] = ("field", f"quantity_{r}", "number", f"Quantity, row {r}")
    table(f, 288, [50, 245, 200, 50], 4, 24, cells)

    f.section(408, "Loan period")
    cells = {(0, 0): ("label", "Start date"), (0, 1): ("field", "loan_start_date", "date", "Start date"),
             (0, 2): ("label", "Return date"), (0, 3): ("field", "return_date", "date", "Return date")}
    table(f, 418, [50, 110, 162.5, 110, 112.5], 1, 24, cells)

    f.section(474, "Purpose of the loan")
    x = 50
    for option in ["Teaching", "Research", "Event", "Other"]:
        x = f.checkbox(x, 500, f"purpose_{option.lower()}", f"Purpose: {option}", option) + 30
    f.labelled_line(50, 545, 528, "If other, please describe", "purpose_other_details")

    f.section(572, "Approval")
    cells = {(0, 0): ("label", "Manager name"), (0, 1): ("field", "manager_name", "text", "Manager name"),
             (1, 0): ("label", "Manager signature"), (1, 1): ("field", "manager_signature", "signature", "Manager signature"),
             (2, 0): ("label", "Date approved"), (2, 1): ("field", "date_approved", "date", "Date approved")}
    table(f, 582, [50, 150, 345], 3, 30, cells)
    f.text(50, 700, "Return the equipment to the media lab desk by the return date. Late returns are reported to your manager.", 8.5)
    f.save()


# --------------------------------------------------------------------------- form 3: labels above boxes, two pages

def labelled_box(f: Form, x: float, top: float, w: float, label: str, name: str, ftype: str = "text", h: float = 22) -> None:
    f.text(x, top, label, 8.5, "Helvetica")
    f.box(x, top + 5, w, h, name, ftype, label)


def supplier_registration() -> None:
    f = Form("supplier-registration", "Supplier Registration Form", "Halden Water Services")
    f.header("All fields are required unless marked optional")
    f.section(120, "1. Company details")
    labelled_box(f, 50, 145, 495, "Registered company name", "company_name")
    labelled_box(f, 50, 190, 240, "Company registration number", "company_registration_number")
    labelled_box(f, 305, 190, 240, "VAT number (optional)", "vat_number")
    labelled_box(f, 50, 235, 495, "Street address", "street_address")
    labelled_box(f, 50, 280, 240, "Town or city", "town_or_city")
    labelled_box(f, 305, 280, 110, "Postcode", "postcode")
    labelled_box(f, 430, 280, 115, "Country", "country")

    f.section(340, "2. Main contact")
    labelled_box(f, 50, 365, 240, "Contact name", "contact_name")
    labelled_box(f, 305, 365, 240, "Job title", "job_title")
    labelled_box(f, 50, 410, 240, "Email address", "contact_email", "email")
    labelled_box(f, 305, 410, 240, "Telephone", "contact_telephone", "phone")

    f.section(470, "3. Type of business")
    for i, option in enumerate(["Sole trader", "Partnership", "Limited company", "Charity or non-profit"]):
        col, row = i % 2, i // 2
        f.checkbox(50 + col * 250, 496 + row * 24, f"business_type_{option.split()[0].lower()}",
                   f"Type of business: {option}", option)
    f.section(570, "4. Goods and services")
    labelled_box(f, 50, 595, 495, "Describe the goods or services you supply", "goods_and_services", "multiline", h=90)
    f.new_page()

    f.header("Page 2 of 2")
    f.section(120, "5. Bank details")
    labelled_box(f, 50, 145, 495, "Bank name", "bank_name")
    labelled_box(f, 50, 190, 495, "Account holder name", "account_holder_name")
    labelled_box(f, 50, 235, 150, "Sort code", "sort_code", "number")
    labelled_box(f, 215, 235, 200, "Account number", "account_number", "number")

    f.section(300, "6. Declaration")
    f.text(50, 322, "I declare that the information given on this form is correct and complete, and that I am authorised", 9.5)
    f.text(50, 336, "to register this company as a supplier.", 9.5)
    labelled_box(f, 50, 366, 300, "Signature", "declaration_signature", "signature", h=45)
    labelled_box(f, 365, 366, 180, "Date", "declaration_date", "date")
    labelled_box(f, 50, 436, 300, "Name in capitals", "declaration_name")
    labelled_box(f, 365, 436, 180, "Position in the company", "declaration_position")
    f.save()


# --------------------------------------------------------------------------- form 4: typed underscores and dots

def incident_report() -> None:
    f = Form("incident-report", "Workplace Incident Report", "Pellmoor Logistics")
    f.header("Complete within 24 hours of the incident")
    f.section(120, "About you")
    end = f.text(50, 148, "Reported by: ")
    end = f.typed_run(end, 148, "_", 30, "reported_by", "text", "Reported by")
    end = f.text(end + 10, 148, "Job title: ")
    f.typed_run(end, 148, "_", 22, "job_title", "text", "Job title")
    end = f.text(50, 176, "Phone: ")
    f.typed_run(end, 176, "_", 28, "phone", "phone", "Phone")

    f.section(218, "The incident")
    end = f.text(50, 246, "Date of incident: ")
    end = f.typed_run(end, 246, "_", 18, "incident_date", "date", "Date of incident")
    end = f.text(end + 10, 246, "Time: ")
    f.typed_run(end, 246, "_", 12, "incident_time", "text", "Time")
    end = f.text(50, 274, "Location: ")
    f.typed_run(end, 274, "_", 74, "location", "text", "Location")

    f.text(50, 302, "Type of incident (tick one):")
    x = 50
    for option in ["Injury", "Near miss", "Property damage", "Vehicle", "Other"]:
        x = f.checkbox(x, 324, f"incident_type_{option.split()[0].lower()}", f"Type of incident: {option}", option) + 20

    f.text(50, 356, "Describe what happened:")
    # Four ruled lines that together form one answer area.
    c = f.c
    c.setLineWidth(0.6)
    ys = [384, 408, 432, 456]
    for y in ys:
        c.line(50, H - y, 545, H - y)
    f.add("description", "multiline", "Describe what happened", (50, ys[0] - LINE_FIELD_HEIGHT, 545, ys[-1]))

    x = f.text(50, 488, "Was first aid given?") + 14
    x = f.checkbox(x, 488, "first_aid_yes", "Was first aid given? Yes", "Yes", option_left=True) + 18
    f.checkbox(x, 488, "first_aid_no", "Was first aid given? No", "No", option_left=True)
    end = f.text(50, 516, "Witness name: ")
    f.typed_run(end, 516, ".", 70, "witness_name", "text", "Witness name")
    end = f.text(50, 544, "Witness phone: ")
    f.typed_run(end, 544, ".", 50, "witness_phone", "phone", "Witness phone")

    f.section(590, "Sign-off")
    end = f.text(50, 620, "Signature of person reporting: ")
    f.typed_run(end, 620, "_", 34, "reporter_signature", "signature", "Signature of person reporting")
    end = f.text(50, 648, "Date: ")
    f.typed_run(end, 648, "_", 20, "report_date", "date", "Date")
    end = f.text(50, 690, "Reviewed by (manager): ")
    f.typed_run(end, 690, ".", 60, "reviewed_by", "text", "Reviewed by (manager)")
    f.save()


# --------------------------------------------------------------------------- form 5: captions inside boxes (held out)

def caption_box(f: Form, x: float, top: float, w: float, h: float, caption: str, name: str, ftype: str, hint: str = "") -> None:
    """A box with its caption printed small in the top left corner, as on many printed application forms."""
    f.box(x, top, w, h, name, ftype, caption)
    f.text(x + 3, top + 8, caption, 7, "Helvetica")
    if hint:
        f.c.setFillGray(0.55)
        f.text(x + w - stringWidth(hint, "Helvetica", 7) - 4, top + h - 4, hint, 7, "Helvetica")
        f.c.setFillGray(0)


def volunteer_registration() -> None:
    """Written after the detection and naming rules, and not used to tune them: it measures how they generalise."""
    f = Form("volunteer-registration", "Volunteer Registration", "Larkspur Community Garden", held_out=True)
    f.header("Section A must be completed by every volunteer")
    f.section(118, "A. About you")
    caption_box(f, 50, 128, 240, 32, "Surname", "surname", "text")
    caption_box(f, 295, 128, 250, 32, "First name(s)", "first_names", "text")
    caption_box(f, 50, 165, 160, 32, "D.O.B.", "date_of_birth", "date", "DD / MM / YYYY")
    caption_box(f, 215, 165, 160, 32, "Mobile", "mobile", "phone")
    caption_box(f, 380, 165, 165, 32, "E-mail", "email", "email")
    caption_box(f, 50, 202, 495, 32, "Address", "address", "text")
    f.text(50, 252, "Postcode", 9)
    # A comb: one box per character, meant as one field.
    for i in range(8):
        f.c.setLineWidth(0.8)
        f.c.rect(100 + i * 16, H - 258, 16, 18)
    f.add("postcode", "text", "Postcode", (100, 240, 228, 258))

    f.section(292, "B. When can you help?")
    f.text(50, 312, "Tick all that apply", 9)
    days = ["Weekday mornings", "Weekday afternoons", "Saturdays", "Sundays"]
    for i, day in enumerate(days):
        col, row = i % 2, i // 2
        x = 50 + col * 250
        y = 336 + row * 22
        f.text(x, y, day)
        f.c.setLineWidth(0.8)
        f.c.rect(x + 150, H - y - 1, 10, 10)
        f.add(f"available_{day.lower().replace(' ', '_')}", "checkbox", f"When can you help: {day}", (x + 150, y + 1 - 10, x + 160, y + 1))
    caption_box(f, 50, 384, 495, 60, "Skills or experience you would like to share", "skills", "multiline")

    f.section(478, "C. Consent")
    f.text(50, 498, "I agree that the garden may contact me about volunteering. I can withdraw at any time.", 9)
    caption_box(f, 50, 510, 300, 46, "Signature", "signature", "signature")
    caption_box(f, 355, 510, 190, 46, "Date", "consent_date", "date", "DD / MM / YYYY")
    f.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    membership_application()
    equipment_loan_request()
    supplier_registration()
    incident_report()
    volunteer_registration()


if __name__ == "__main__":
    main()
