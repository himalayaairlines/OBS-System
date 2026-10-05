"""Definitions of the cabin report forms the crew complete after a sector.

Each form mirrors a controlled paper form from the airline's manuals, heading
for heading, so a printed copy and a screen copy carry the same information in
the same order. The schema is served to the client, which renders the form from
it; nothing about a form's layout is written into the application.

Field types the client renders:
    text      single line
    textarea  multi-line, `lines` gives the printed line count
    date      calendar entry
    time      clock entry
    check     one tick box
    checks    several tick boxes, any number may be ticked
    choice    several options, one may be chosen
    checklist tick boxes arranged under group headings
    rows      a fixed number of numbered rows with named columns
    note      static text reproduced from the form (distribution lines, etc.)

`prefill` names a value the system already knows, so the crew do not key it
again: flight_number, flight_date, sector, destination, origin, aircraft_type,
aircraft, staff_name, staff_no.
"""

# --------------------------------------------------------------------------- #
# Lost and Found — Cabin Safety Procedure Manual, Section 12, Appendix 12
# --------------------------------------------------------------------------- #
LOST_AND_FOUND = {
    "type": "LOST_AND_FOUND",
    "title": "Lost and Found",
    "manual": "Cabin Safety Procedure Manual",
    "manual_section": "Section 12",
    "appendix": "12.12 Appendix 12 Lost And Found",
    "form_code": "H9/CCD/L&F/19",
    "sections": [
        {
            "title": "Item(s) Description",
            "fields": [
                {
                    "type": "rows", "key": "items", "label": "Item(s) Description",
                    "columns": [{"key": "description", "label": "Item(s) Description"}],
                    "count": 4,
                },
            ],
        },
        {
            "title": "Found (Location): On-board",
            "fields": [
                {"type": "text", "key": "location_at_station", "label": "Location at Station"},
                {"type": "text", "key": "flight_no", "label": "Flight No.", "prefill": "flight_number"},
                {"type": "text", "key": "seat_no", "label": "Seat No."},
                {"type": "text", "key": "sector", "label": "Sector", "prefill": "sector"},
            ],
        },
        {
            "title": "Located by Department (Items found by)",
            "fields": [
                {"type": "text", "key": "located_department", "label": "Located by Department"},
                {"type": "text", "key": "located_staff_name", "label": "Staff Name"},
                {"type": "text", "key": "located_staff_no", "label": "Staff No."},
                {"type": "text", "key": "located_signature", "label": "Signature"},
            ],
        },
        {
            "title": "Handed over to Department",
            "fields": [
                {"type": "text", "key": "handed_department", "label": "Handed over to Department"},
                {"type": "text", "key": "handed_staff_name", "label": "Staff Name"},
                {"type": "text", "key": "handed_staff_no", "label": "Staff No."},
                {"type": "text", "key": "handed_signature", "label": "Signature"},
            ],
        },
        {
            "title": "Distribution",
            "fields": [
                {"type": "note", "text": "White: Aviation Security Department"},
                {"type": "note", "text": "Yellow: CCD"},
                {"type": "note", "text": "Blue: Ground Staff"},
            ],
        },
    ],
}

# --------------------------------------------------------------------------- #
# Cabin Safety Report — Cabin Crew Manual, Section 13, Appendix 8
# --------------------------------------------------------------------------- #
CABIN_SAFETY = {
    "type": "CABIN_SAFETY",
    "title": "Cabin Safety Report",
    "manual": "Cabin Crew Manual",
    "manual_section": "Section 13",
    "appendix": "13.8 Appendix 8 Cabin Safety Report",
    "form_code": "H9/CCD/CSR/08",
    "sections": [
        {
            "title": "Flight",
            "fields": [
                {"type": "text", "key": "flight_no", "label": "Flight No.", "prefill": "flight_number"},
                {"type": "date", "key": "date", "label": "Date", "prefill": "flight_date"},
                {"type": "text", "key": "sector", "label": "Sector", "prefill": "sector"},
                {"type": "text", "key": "purser", "label": "Purser"},
                {"type": "text", "key": "captain", "label": "Captain"},
                {"type": "text", "key": "ac_type", "label": "A / C Type", "prefill": "aircraft_type"},
                {"type": "text", "key": "ac_reg", "label": "A / C Reg", "prefill": "aircraft"},
            ],
        },
        {
            "title": "Phases of Flight",
            "description": "Please tick as appropriate",
            "fields": [
                {
                    "type": "checks", "key": "phase", "label": "Phases of Flight",
                    "options": ["Parked", "Pushed-back", "Taxi", "Take-off",
                                "Climb", "Cruise", "Descent", "Landing"],
                },
            ],
        },
        {
            "title": "Incident",
            "description": "Please tick as appropriate. * If applicable. # In case of injury",
            "fields": [
                {
                    "type": "checks", "key": "incident", "label": "Incident",
                    "options": ["Slide Deployment", "Smoking", "Electronic Devices",
                                "Crew Incapacitation", "Unruly Pax", "Baggage"],
                },
                {
                    "type": "checks", "key": "injury_flags", "label": "Crew / Pax Injury",
                    "options": ["Crew / Pax Injury", "* Crew Ceased Duty",
                                "* Medical Assistance on Arrival"],
                },
                {
                    "type": "choice", "key": "turbulence", "label": "Turbulence",
                    "options": ["Expected", "Unexpected"],
                },
                {
                    "type": "choice", "key": "seatbelt_sign_on",
                    "label": "Seat-belt sign was “ON”", "options": ["Yes", "No"],
                },
                {
                    "type": "choice", "key": "crew_told_to_be_seated",
                    "label": "Cabin crew were told to be seated", "options": ["Yes", "No"],
                },
                {
                    "type": "choice", "key": "severity", "label": "Severity",
                    "options": ["Mild", "Severe"],
                },
                {"type": "text", "key": "others_specify", "label": "Others Specify"},
            ],
        },
        {
            "title": "Place of Incident",
            "description": "Please tick as appropriate",
            "fields": [
                {
                    "type": "checks", "key": "place", "label": "Place of Incident",
                    "options": ["A/C Door", "Cabin", "Toilet", "Pax Seat",
                                "Aisle", "Galley", "O/H Bin", "Crew Seat"],
                },
            ],
        },
        {
            "title": "For Injuries",
            "description": "Please tick",
            "fields": [
                {
                    "type": "checks", "key": "injury_area", "label": "For Injuries",
                    "options": ["Head / Face / Body", "Arm / Elbow", "Hand / Finger",
                                "Leg / Ankle", "Foot / Toe"],
                },
            ],
        },
        {
            "title": "For Contacts",
            "fields": [
                {"type": "text", "key": "pax_name", "label": "Passenger"},
                {"type": "text", "key": "pax_passport_no", "label": "Passport No."},
                {"type": "text", "key": "pax_contact", "label": "Contact"},
                {"type": "textarea", "key": "pax_address", "label": "Address", "lines": 2},
                {"type": "text", "key": "crew_staff_no", "label": "Crew: Staff No."},
                {"type": "text", "key": "witness_contact", "label": "Witness: Contact"},
                {"type": "textarea", "key": "witness_address", "label": "Witness: Address", "lines": 2},
            ],
        },
        {
            "title": "Description of Incident",
            "fields": [
                {
                    "type": "textarea", "key": "description",
                    "label": "Description of Incident", "lines": 8,
                },
            ],
        },
        {
            "title": "Declaration",
            "fields": [
                {
                    "type": "choice", "key": "caan_mandatory",
                    "label": "CAAN Mandatory (MOR)", "options": ["Yes", "No"],
                },
                {"type": "text", "key": "purser_signature", "label": "Purser Signature"},
                {"type": "text", "key": "captain_signature", "label": "Captain Signature"},
            ],
        },
    ],
}

# --------------------------------------------------------------------------- #
# Voyage Report Details — Cabin Crew Manual, Section 13, Appendix 5
# --------------------------------------------------------------------------- #
VOYAGE_REPORT = {
    "type": "VOYAGE_REPORT",
    "title": "Voyage Report Details",
    "manual": "Cabin Crew Manual",
    "manual_section": "Section 13",
    "appendix": "13.5 Appendix 5 - Voyage Report Details",
    "form_code": "H9/CCD/VRD/05",
    "sections": [
        {
            "title": "Flight",
            "fields": [
                {"type": "text", "key": "flt_no", "label": "FLT NO", "prefill": "flight_number"},
                {"type": "text", "key": "destination", "label": "DESTINATION", "prefill": "destination"},
                {"type": "date", "key": "date", "label": "DATE", "prefill": "flight_date"},
                {"type": "text", "key": "purser_name", "label": "PURSER NAME"},
                {"type": "text", "key": "staff_no", "label": "STAFF NO"},
            ],
        },
        {
            "title": "Matters reported",
            "fields": [
                {
                    "type": "checks", "key": "matters", "label": "Matters",
                    "options": ["PASSENGER ISSUES", "CATERING", "GROUND HANDLING",
                                "ENGINEERING", "DUTY FREE", "OTHER MATTERS"],
                },
            ],
        },
        {
            "title": "Passengers Details",
            "fields": [
                {"type": "text", "key": "pax_name", "label": "PAX NAME"},
                {"type": "text", "key": "seat_number", "label": "SEAT NUMBER"},
                {"type": "text", "key": "ff_number", "label": "FF NUMBER"},
            ],
        },
        {
            "title": "Report",
            "fields": [
                {"type": "textarea", "key": "facts", "label": "FACTS", "lines": 8},
                {"type": "textarea", "key": "action", "label": "ACTION", "lines": 8},
                {"type": "textarea", "key": "result", "label": "RESULT", "lines": 8},
            ],
        },
        {
            "title": "Signatures",
            "fields": [
                {"type": "text", "key": "purser_signature", "label": "PURSER SIGNATURE"},
                {"type": "text", "key": "crew_name", "label": "CREW NAME"},
                {"type": "text", "key": "crew_staff_no", "label": "STAFF NO"},
                {
                    "type": "text", "key": "cabin_crew_signature",
                    "label": "CABIN CREW SIGNATURE (if mentioned)",
                },
            ],
        },
    ],
}

# --------------------------------------------------------------------------- #
# Aircraft Security Check List — Aircraft Operators Security Programme, App. J
# --------------------------------------------------------------------------- #
SECURITY_CHECK = {
    "type": "SECURITY_CHECK",
    "title": "Aircraft Security Check List",
    "manual": "Aircraft Operators Security Programme (Restricted)",
    "manual_section": "Appendix – J",
    "appendix": "Appendix – J  Aircraft Security Check, Check List",
    "form_code": "HA-ASD-J-111",
    "statement": ("This Aircraft has been searched in accordance with the requirement of "
                  "Himalaya Airlines Security Department"),
    "sections": [
        {
            "title": "Flight",
            "fields": [
                {"type": "text", "key": "flight_no", "label": "Flight No", "prefill": "flight_number"},
                {"type": "date", "key": "date", "label": "Date", "prefill": "flight_date"},
                {"type": "text", "key": "acft_reg", "label": "Acft. Reg", "prefill": "aircraft"},
                {"type": "text", "key": "inbound_from", "label": "Inbound From"},
                {"type": "text", "key": "station", "label": "Station", "prefill": "origin"},
                {"type": "text", "key": "destination", "label": "Destination", "prefill": "destination"},
                {"type": "time", "key": "start_time", "label": "Start Time"},
                {"type": "time", "key": "end_time", "label": "End Time"},
                {
                    "type": "note",
                    "text": ("PURSER must fill this form and provide it to the "
                             "Pilot-in-Command for his Signature"),
                },
            ],
        },
        {
            "title": "Areas searched",
            "fields": [
                {
                    "type": "checklist", "key": "checks", "label": "Areas searched",
                    "groups": [
                        {
                            "title": "AREA",
                            "items": [
                                "Flight Deck (if left unattended)",
                                "Floor Area",
                                "Seats, Life Jackets, Pockets",
                                "Underneath Seats",
                                "Storage Compartments",
                            ],
                        },
                        {
                            "title": "GALLEYS",
                            "items": [
                                "Waste Bins & Trash Compactors (Inside & Behind)",
                                "Storage Compartments and Cupboard",
                                "Sinks / Ovens / Chillers / Refrigerators",
                                "Service Equipment (Bun warmer, Coffee maker, Jugs, etc)",
                                "Jump seats & Surrounding area",
                            ],
                        },
                        {
                            "title": "PASSENGER CABIN",
                            "items": [
                                "Seat back (including tray tables and literature pockets)",
                                "Seats (including under seats and between window seats and fuselage)",
                                "Life jackets compartment",
                                "Overhead lockers",
                                "Closets, cupboards and storage compartments",
                                "Floor Area (including area between fuselage and window seats)",
                                "Coat storage and other storage compartments",
                                "Jump Seats (Including life jacket storage area)",
                                "Emergency Equipment Storage Area",
                            ],
                        },
                        {
                            "title": "LAVATORIES",
                            "items": [
                                "W/C Bowl",
                                "Under Sink",
                                "Storage Cabinets",
                                "Waste Bin (Inside & Behind)",
                            ],
                        },
                        {
                            "title": "CARGO HOLDS",
                            "items": [
                                "Cargo Holds checked by Ground Staff as confirmed by signature",
                            ],
                        },
                    ],
                },
            ],
        },
        {
            "title": "Signatures",
            "fields": [
                {"type": "text", "key": "purser_name", "label": "PURSER NAME"},
                {"type": "text", "key": "purser_signature", "label": "PURSER Signature"},
                {"type": "text", "key": "purser_staff_no", "label": "Staff No"},
                {"type": "text", "key": "pic_name", "label": "PIC's NAME"},
                {"type": "text", "key": "pic_signature", "label": "PIC's Signature"},
                {"type": "text", "key": "pic_staff_no", "label": "PIC's Staff No"},
            ],
        },
        {
            "title": "Distribution",
            "fields": [
                {
                    "type": "note",
                    "text": ("Cabin Crew uses the same Checklist for the Aircraft Security "
                             "Search in Case of In-Flight Bomb threat"),
                },
                {"type": "note", "text": "1. White – Pilot in Command"},
                {"type": "note", "text": "2. Yellow - Cabin Crew"},
                {"type": "note", "text": "3. Blue - Ground Operation"},
            ],
        },
    ],
}

# Order is the order the crew work through them after a sector.
REPORT_FORMS = [SECURITY_CHECK, CABIN_SAFETY, VOYAGE_REPORT, LOST_AND_FOUND]

FORMS_BY_TYPE = {form["type"]: form for form in REPORT_FORMS}
REPORT_TYPES = [form["type"] for form in REPORT_FORMS]


def form_for(report_type: str):
    return FORMS_BY_TYPE.get((report_type or "").strip().upper())


def field_index(report_type: str):
    """Every field of a form, keyed by its key, for validating a submission."""
    form = form_for(report_type)
    if not form:
        return {}
    index = {}
    for section in form["sections"]:
        for field in section["fields"]:
            if field["type"] != "note":
                index[field["key"]] = field
    return index
