
import random
import math 
import re
from collections import deque
from flask import Flask, render_template, request, make_response
import io
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import cm

app = Flask(__name__)

data_store = []

# ── NEW: persistent timetable store (set after generation) ──────────────────
generated_timetable = {}   # timetable[class][day][slot] = "Subject (Teacher)"

batch_config = {
    "FY": 3,
    "SY": 4,
    "TY": 4
}

PRIORITY_CONFIG = {
    # Priority 1 — highest
    "P&S": 1, "M-II": 1, "ML": 1,
    # Priority 2
    "DAA": 2, "EC": 2, "CN": 2,
    # Priority 3
    "CAO": 3, "EM": 3, "IOT": 3,
    # Priority 4
    "MDM": 4, "PPS": 4, "CS": 4, "CD": 4,
    # Priority 5 — lowest (subjects missing from original config, now included)
    "ESD": 5, "CP": 5, "CSD": 5, "OE-II": 5,
    "MIL": 5, "PYTHON": 5, "COI": 5, "LBRBA": 5,
    "DBMS": 5, "AI": 5, "DS": 5
}

teachers = [
    "Prof.R.M.Pawar","Prof.J.I.Nandalwar","Prof.P.I.Swami",
    "Prof.S.S.Patil","Prof.P.L.Naikal","Prof.P.A.Katare",
    "Prof.P.S.Pandhare","Prof.V.C.Lad","Prof.S.A.Kadam",
    "Prof.D.B.Parse","Prof.S.M.Gungewale","Prof.G.R.Kulkarni",
    "Prof.A.S.Nimbane"
]

subjects = {
    "FY": ["M-II", "Chemistry", "EM", "Workshop Practice", "PPS", "CS","Yoga"],
    "SY": ["MDM", "DAA", "CAO", "OE", "PYTHON", "MIL","COI", "LBRBA","CSD","P&S"],
    "TY": ["CD", "ML", "ESD", "CSD", "CN", "CS", "CP", "IOT","NPTEL"]
}

# ---------------- ROUTES ----------------

# REPLACE both route bodies — add subjects_json:
@app.route("/")
def admin():
    return render_template("generate.html", data=data_store,
        teachers=teachers, subjects=subjects, subjects_json=subjects)

@app.route('/generate-page')
def generate_page():
    return render_template('generate.html', data=data_store,
        teachers=teachers, subjects=subjects, subjects_json=subjects)

@app.route("/add-entry", methods=["POST"])
def add_entry():
    year = request.form["year"]
    subject = request.form["subject"]

    # Reject if subject not valid for selected year
    if subject not in subjects.get(year, []):
        return render_template("generate.html", data=data_store,
            teachers=teachers, subjects=subjects, subjects_json=subjects,
            error=f"Subject '{subject}' is not valid for {year}.")

    entry = {
        "year": year,
        "division": request.form["division"],
        "subject": subject,
        "teacher": request.form["teacher"],
        "theory": int(request.form["theory"] or 0),
        "practical": int(request.form["practical"] or 0)
    }
    data_store.append(entry)
    return render_template("generate.html", data=data_store,
        teachers=teachers, subjects=subjects, subjects_json=subjects)


@app.route("/delete-entry", methods=["POST"])
def delete_entry():
    try:
        index = int(request.form["index"])
        if 0 <= index < len(data_store):
            data_store.pop(index)
    except (ValueError, KeyError):
        pass
    return render_template("generate.html", data=data_store,
        teachers=teachers, subjects=subjects, subjects_json=subjects)

@app.route("/generate-timetable")
def generate_timetable():
    global generated_timetable

    timetable    = initialize_timetable()
    teacher_busy = initialize_trackers()

    teacher_map                   = get_teacher_map(data_store)
    theory_tasks, practical_tasks = prepare_tasks(data_store)
    required_map                  = build_required_map(theory_tasks)

    assigned_map = {
        cls: {sub: 0 for sub in required_map.get(cls, {})}
        for cls in timetable
    }

    schedule_practicals(timetable, teacher_busy, practical_tasks, batch_config)
    schedule_theory_tasks(timetable, teacher_busy, teacher_map, required_map, assigned_map)
    fill_remaining_slots(timetable, teacher_busy, teacher_map, required_map)

    generated_timetable = timetable

    return render_template(
        "generate.html",
        data=data_store,
        teachers=teachers,
        subjects=subjects,
        subjects_json=subjects,
        timetable=timetable
    )

@app.route("/class-pdf/<class_name>")
def class_pdf(class_name):
    """Download timetable PDF for a single class (e.g. SY-A)."""
    if not generated_timetable:
        return "Timetable not generated yet.", 400
    if class_name not in generated_timetable:
        return f"Class '{class_name}' not found.", 404

    pdf_bytes = _build_class_pdf(class_name, generated_timetable[class_name])

    response = make_response(pdf_bytes)
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = (
        f"attachment; filename={class_name}_timetable.pdf"
    )
    return response

@app.route("/manage-data")
def manage_data():
    return render_template("manage_data.html", teachers=teachers,
        subjects=subjects, batch_config=batch_config, data=data_store)




#-----SET BATCH CONFIG-----Through user


@app.route("/set-batches", methods=["POST"])
def set_batches():
    try:
        batch_config["FY"] = int(request.form.get("FY", batch_config["FY"]))
        batch_config["SY"] = int(request.form.get("SY", batch_config["SY"]))
        batch_config["TY"] = int(request.form.get("TY", batch_config["TY"]))
    except ValueError:
        pass
    return render_template("manage_data.html", teachers=teachers,
        subjects=subjects, batch_config=batch_config, data=data_store)

# ═══════════════════════════════════════════════════════════════════════════
#  MANAGE TEACHERS & SUBJECTS
# ═══════════════════════════════════════════════════════════════════════════
@app.route("/add-teacher", methods=["POST"])
def add_teacher():
    name = request.form.get("teacher", "").strip()
    if name and name not in teachers:
        teachers.append(name)
    return render_template("manage_data.html", teachers=teachers,
        subjects=subjects, batch_config=batch_config, data=data_store)

@app.route("/delete-teacher", methods=["POST"])
def delete_teacher():
    name = request.form.get("teacher", "").strip()
    if name in teachers:
        teachers.remove(name)
    return render_template("manage_data.html", teachers=teachers,
        subjects=subjects, batch_config=batch_config, data=data_store)


@app.route("/add-subject", methods=["POST"])
def add_subject():
    name = request.form.get("subject", "").strip().upper()
    year = request.form.get("year", "").strip()
    if name and year in subjects and name not in subjects[year]:
        subjects[year].append(name)
    return render_template("manage_data.html", teachers=teachers,
        subjects=subjects, batch_config=batch_config, data=data_store)

@app.route("/delete-subject", methods=["POST"])
def delete_subject():
    name = request.form.get("subject", "").strip()
    year = request.form.get("year", "").strip()
    if year in subjects and name in subjects[year]:
        subjects[year].remove(name)
    return render_template("manage_data.html", teachers=teachers,
        subjects=subjects, batch_config=batch_config, data=data_store)
# ═══════════════════════════════════════════════════════════════════════════════
#  FEATURE 1 — STAFF PANEL
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/staff-view", methods=["GET", "POST"])
def staff_view():
    """
    GET  → show dropdown to select teacher
    POST → show that teacher's full-week timetable
    """
    selected_teacher = None
    week_schedule = None   # { day: { slot_idx: {"subject":…,"class":…} } }

    if request.method == "POST":
        selected_teacher = request.form.get("teacher")
        if selected_teacher and generated_timetable:
            week_schedule = extract_teacher_schedule(
                generated_timetable, selected_teacher
            )

    return render_template(
        "staff.html",
        teachers=teachers,
        selected_teacher=selected_teacher,
        week_schedule=week_schedule,
        days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        max_slots=7,         # Mon–Fri = 7 slots, Sat = 4 (handled in template)
        timetable_ready=bool(generated_timetable)
    )

@app.route("/staff-pdf/<path:teacher_name>")
def staff_pdf(teacher_name):
    """Download full-week PDF for one teacher using -> ReportLab."""

    if not generated_timetable:
        return "Timetable not generated yet.", 400

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    max_slots = 7

    # Get teacher schedule
    week_schedule = extract_teacher_schedule(
        generated_timetable, teacher_name
    )

    # Generate PDF bytes
    pdf_bytes = _build_week_pdf(
        teacher_name, week_schedule, days, max_slots
    )

    
    safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', teacher_name).strip("_")

    # Create response
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{safe_name}_week_timetable.pdf"'
    )

    return response

def _build_class_pdf(class_name, day_map):
    """
    ReportLab PDF: full week timetable for one class.
    Columns: Slot | Mon | Tue | Wed | Thu | Fri | Sat
    Colour coding:
      - PRAC cells → light blue
      - Theory cells → light green
      - FREE → grey text
      - Out-of-range (Sat slots 5-7) → light grey dash
    """

    import io
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet
    from flask import make_response

    days      = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    max_slots = 7
    sat_limit = 4

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=0.5*cm, rightMargin=0.5*cm,
        topMargin=1.5*cm,  bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()
    story  = []

    # ✅ Cell style for wrapping text
    cell_style = styles["BodyText"]
    cell_style.fontSize = 8
    cell_style.leading = 9
    cell_style.alignment = 1  # center

    story.append(Paragraph(f"Timetable — {class_name}", styles["Title"]))
    story.append(Spacer(1, 0.4*cm))

    # Build rows
    header = ["Slot"] + days
    rows   = [header]

    for s in range(max_slots):
        row = [Paragraph(str(s + 1), cell_style)]

        for day in days:
            if day == "Sat" and s >= sat_limit:
                row.append(Paragraph("—", cell_style))
            else:
                cell = day_map.get(day, [None]*max_slots)
                val  = cell[s] if s < len(cell) else None

                if not val:
                    row.append(Paragraph("—", cell_style))
                elif val == "FREE":
                    row.append(Paragraph("FREE", cell_style))
                else:
                    row.append(Paragraph(val, cell_style))

        rows.append(row)

    # Slightly wider columns for better wrapping
    col_widths = [1.2*cm] + [4.2*cm] * len(days)

    tbl   = Table(rows, colWidths=col_widths, repeatRows=1)
    style = _base_table_style()

    # ✅ Colour logic (fixed for Paragraph objects)
    for r in range(1, len(rows)):
        for c in range(1, len(days) + 1):
            cell_obj = rows[r][c]

            # Extract text safely
            val = cell_obj.text if hasattr(cell_obj, "text") else str(cell_obj)

            if val == "—":
                style.append(("BACKGROUND", (c, r), (c, r),
                              colors.HexColor("#f8f9fa")))
                style.append(("TEXTCOLOR",  (c, r), (c, r),
                              colors.HexColor("#cccccc")))

            elif val == "FREE":
                style.append(("TEXTCOLOR",  (c, r), (c, r),
                              colors.HexColor("#aaaaaa")))

            elif val.startswith("PRAC"):
                style.append(("BACKGROUND", (c, r), (c, r),
                              colors.HexColor("#cce5ff")))
                style.append(("TEXTCOLOR",  (c, r), (c, r),
                              colors.HexColor("#004085")))

            else:
                # Theory
                style.append(("BACKGROUND", (c, r), (c, r),
                              colors.HexColor("#d4edda")))
                style.append(("TEXTCOLOR",  (c, r), (c, r),
                              colors.HexColor("#155724")))

    tbl.setStyle(TableStyle(style))
    story.append(tbl)

    doc.build(story)

    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{class_name}_week_timetable.pdf"'
    )

    return response.get_data()
# ═══════════════════════════════════════════════════════════════════════════════
#  FEATURE 2 — MANAGE ABSENTEE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/changes", methods=["GET", "POST"])
def changes():
    """
    GET  → show day selector + absent teacher multi-select
    POST → compute replacements, show per-teacher day schedules
    """
    result = None          # { teacher_name: [ {slot, subject, class, is_replacement} ] }
    selected_day = None
    absent_teachers = []

    if request.method == "POST":
        selected_day    = request.form.get("day")
        absent_teachers = request.form.getlist("absent_teachers")

        if selected_day and absent_teachers and generated_timetable:
            result = generate_replacements(
                generated_timetable, selected_day, absent_teachers
            )

    return render_template(
        "changes.html",
        teachers=teachers,
        days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        selected_day=selected_day,
        absent_teachers=absent_teachers,
        result=result,
        timetable_ready=bool(generated_timetable)
    )


@app.route("/absentee-pdf/<day>/<path:teacher_name>")
def absentee_pdf(day, teacher_name):
    """Download one teacher's day schedule as PDF — ReportLab."""
    if not generated_timetable:
        return "Timetable not generated yet.", 400

    absent_raw    = request.args.get("absent", "")
    absent_list   = [t.strip() for t in absent_raw.split(",") if t.strip()]

    full_result   = generate_replacements(generated_timetable, day, absent_list)
    teacher_sched = full_result.get(teacher_name, [])

    pdf_bytes = _build_day_pdf(teacher_name, day, teacher_sched)

    response  = make_response(pdf_bytes)
    safe      = teacher_name.replace(" ", "_").replace(".", "")
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = (
        f"attachment; filename={safe}_{day}_schedule.pdf"
    )
    return response

# ---------------- CORE ----------------

def build_required_map(theory_tasks):
    required_map = {}
    for task in theory_tasks:
        cls = task["class"]
        sub = task["subject"]
        required_map.setdefault(cls, {})
        required_map[cls][sub] = required_map[cls].get(sub, 0) + 1
    return required_map

def get_teacher_map(data_store):
    teacher_map = {}
    for entry in data_store:
        classes = (
            [f"{entry['year']}-A", f"{entry['year']}-B"]
            if entry["division"] == "BOTH"
            else [f"{entry['year']}-{entry['division']}"]
        )
        for class_name in classes:
            teacher_map[(class_name, entry["subject"])] = entry["teacher"]
    return teacher_map

def prepare_tasks(data_store):
    theory_tasks, practical_tasks = [], []

    for entry in data_store:
        classes = (
            [f"{entry['year']}-A", f"{entry['year']}-B"]
            if entry["division"] == "BOTH"
            else [f"{entry['year']}-{entry['division']}"]
        )

        for class_name in classes:
            for _ in range(entry["theory"]):
                theory_tasks.append({
                    "class": class_name,
                    "year": entry["year"],
                    "subject": entry["subject"],
                    "teacher": entry["teacher"]
                })
            if entry["practical"] > 0:
    # practical = frequency (times per week) 
             for _ in range(entry["practical"]):
                 practical_tasks.append({
            "class": class_name,
            "year": entry["year"],
            "subject": entry["subject"],
            "teacher": entry["teacher"],
            "slots": 2   # always 2 consecutive slots
        })

    return theory_tasks, practical_tasks

def initialize_timetable():
    timetable = {}
    days = {"Mon":7,"Tue":7,"Wed":7,"Thu":7,"Fri":7,"Sat":4}
    for year in ["FY","SY","TY"]:
        for div in ["A","B"]:
            timetable[f"{year}-{div}"] = {
                d: [None]*slots for d, slots in days.items()
            }
    return timetable

def initialize_trackers():
    teacher_busy = {}
    days = {"Mon":7,"Tue":7,"Wed":7,"Thu":7,"Fri":7,"Sat":4}
    for d, slots in days.items():
        for s in range(slots):
            teacher_busy[(d, s)] = set()
    return teacher_busy


# ---------------- PRACTICAL ----------------
                                                            

def build_batch_rotation(class_name, practical_tasks, batch_config):
    year         = class_name.split("-")[0]
    num_batches  = batch_config.get(year, 3)
    prefix       = year[0]
    batch_labels = [f"{prefix}{i+1}" for i in range(num_batches)]

    class_tasks = [t for t in practical_tasks if t["class"] == class_name]
    if not class_tasks:
        return []

    # ---- unique subjects ----
    seen = {}
    for t in class_tasks:
        if t["subject"] not in seen:
            seen[t["subject"]] = {
                "teacher": t["teacher"],
                "slots":   t["slots"]
            }

    subjects = list(seen.keys())

    # ---- split into chunks ----
    chunks = []
    for i in range(0, len(subjects), num_batches):
        chunk = subjects[i:i + num_batches]

        roles = []
        for sub in chunk:
            roles.append({
                "subject": sub,
                "teacher": seen[sub]["teacher"],
                "slots":   seen[sub]["slots"]
            })

        # pad with LIBRARY
        while len(roles) < num_batches:
            roles.append({
                "subject": "LIBRARY",
                "teacher": None,
                "slots":   2
            })

        chunks.append(roles)

    # ---- build rotation for each chunk ----
    rotation_plan = []

    for roles in chunks:
        for block_idx in range(num_batches):

            session = []

            for batch_idx in range(num_batches):
                role_idx = (batch_idx + block_idx) % num_batches
                role     = roles[role_idx]

                session.append({
                    "batch":   batch_labels[batch_idx],
                    "subject": role["subject"],
                    "teacher": role["teacher"],
                    "slots":   role["slots"]
                })

            rotation_plan.append(session)

    return rotation_plan



def schedule_practicals(timetable, teacher_busy, practical_tasks, batch_config):
    """
    Place practical blocks for every class using batch rotation.

    Rules enforced:
    - practical_count slots consecutive on the same day
    - Max 2 practical blocks per class per day
    - Each batch gets a different subject per block (rotation)
    - Extra batches get LIBRARY
    - Teacher checked individually per batch (only that batch's teacher)
    - Days are shuffled to scatter practicals across the week
    """
    days_slots = {"Mon":7,"Tue":7,"Wed":7,"Thu":7,"Fri":7,"Sat":4}
    day_list = list(days_slots.keys())

    # Group tasks per class
    classes_with_practicals = list({t["class"] for t in practical_tasks})

    for class_name in classes_with_practicals:

        class_tasks = [t for t in practical_tasks if t["class"] == class_name]
        if not class_tasks:
            continue

        rotation_plan = build_batch_rotation(class_name, practical_tasks, batch_config)

        # Track practical blocks placed per day for this class
        daily_practical_count = {d: 0 for d in day_list}

        for block_idx, session in enumerate(rotation_plan):

            # How many consecutive slots does this block need?
            # Use the slots value from the corresponding task
            block_slots = session[0]["slots"] if block_idx < len(class_tasks) else 2

            placed = False

            # Shuffle days for scatter — prefer days with fewer practicals
            sorted_days = sorted(
                day_list,
                key=lambda d: daily_practical_count[d] + random.uniform(0, 0.5)
            )

            for day in sorted_days:

                # Max 2 practical blocks per class per day
                if daily_practical_count[day] >= 2:
                    continue

                max_start = days_slots[day] - block_slots
                # Randomize start slot slightly for scatter
                slot_order = list(range(max_start + 1))
                random.shuffle(slot_order)

                for start_slot in slot_order:

                    # Check all slots in the block are free for the class
                    slots_range = range(start_slot, start_slot + block_slots)
                    class_slots_free = all(
                        timetable[class_name][day][s] is None
                        for s in slots_range
                    )
                    if not class_slots_free:
                        continue

                    # Check each batch's teacher is free for all slots in block
                    # (only teacher of that batch's subject — LIBRARY has no teacher)
                    teacher_conflict = False
                    for batch_info in session:
                        t = batch_info["teacher"]
                        if t is None:
                            continue  # LIBRARY — no teacher to check
                        for s in slots_range:
                            if t in teacher_busy[(day, s)]:
                                teacher_conflict = True
                                break
                        if teacher_conflict:
                            break

                    if teacher_conflict:
                        continue

                    # ✅ All checks passed — place the block
                    label = _build_practical_label(session)

                    for s in slots_range:
                        timetable[class_name][day][s] = label

                    # Mark each batch's teacher as busy
                    for batch_info in session:
                        t = batch_info["teacher"]
                        if t is None:
                            continue
                        for s in slots_range:
                            teacher_busy[(day, s)].add(t)

                    daily_practical_count[day] += 1
                    placed = True
                    break

                if placed:
                    break

            # If not placed after all days tried — log and continue
            # (never block — deadlock prevention)
            if not placed:
                print(f"[WARN] Could not place practical block {block_idx} for {class_name}")


def _build_practical_label(session):
    """
    Build display string for a practical block.

    Format: PRAC [S1:DBMS (Prof.X) | S2:CN (Prof.Y) | S3:LIBRARY]

    Teacher name is embedded per batch so staff timetable
    can extract batch-specific info per teacher.
    LIBRARY batches have no teacher — shown without parentheses.
    """
    parts = []
    for b in session:
        if b["teacher"]:
            parts.append(f"{b['batch']}:{b['subject']} ({b['teacher']})")
        else:
            parts.append(f"{b['batch']}:{b['subject']}")
    return "PRAC [" + " | ".join(parts) + "]"


# ---------------- THEORY ----------------

def schedule_theory_tasks(
    timetable, teacher_busy, teacher_map,
    required_map, assigned_map
):
    """
    Assign required theory sessions.

    Strategy:
    - Iterate SLOT-first across all classes to avoid Monday clustering
    - No same subject in consecutive slots (strict, relaxed only when stuck)
    - Max 2 of same subject per day
    - Teacher conflict checked
    - Deadlock fallback: if required sessions unmet after strict pass,
      relax consecutive rule and retry
    """
    days_slots = {"Mon":7,"Tue":7,"Wed":7,"Thu":7,"Fri":7,"Sat":4}
    day_list = list(days_slots.keys())

    def try_assign(class_name, day, slot, strict=True):
        """Try to assign a subject to this slot. Returns True if assigned."""
        subs = list(required_map.get(class_name, {}).keys())
        random.shuffle(subs)

        # Sort by remaining required count descending — assign most-needed first
        subs.sort(
            key=lambda s: required_map[class_name][s] - assigned_map[class_name].get(s, 0),
            reverse=True
        )

        for sub in subs:
            remaining = required_map[class_name][sub] - assigned_map[class_name].get(sub, 0)
            if remaining <= 0:
                continue

            teacher = teacher_map.get((class_name, sub))
            if not teacher:
                continue

            if teacher in teacher_busy[(day, slot)]:
                continue

            if not is_good_assignment(timetable, class_name, day, slot, sub, strict=strict):
                continue

            timetable[class_name][day][slot] = f"{sub} ({teacher})"
            teacher_busy[(day, slot)].add(teacher)
            assigned_map[class_name][sub] = assigned_map[class_name].get(sub, 0) + 1
            return True

        return False

    # --- PASS 1: Strict (no consecutive same subject) ---
    # Slot-first traversal: for each slot position, go through all classes
    # This distributes theory evenly across the week instead of filling Mon first
    for day in day_list:
        for slot in range(days_slots[day]):
            # Shuffle class order per slot for fairness
            class_order = list(timetable.keys())
            random.shuffle(class_order)
            for class_name in class_order:
                if timetable[class_name][day][slot] is not None:
                    continue
                try_assign(class_name, day, slot, strict=True)

    # --- PASS 2: Deadlock recovery — relax consecutive rule for unmet required ---
    # Check which classes still have unmet required sessions
    for class_name in timetable:
        unmet = {
            sub: required_map.get(class_name, {}).get(sub, 0) - assigned_map[class_name].get(sub, 0)
            for sub in required_map.get(class_name, {})
            if required_map.get(class_name, {}).get(sub, 0) - assigned_map[class_name].get(sub, 0) > 0
        }

        if not unmet:
            continue

        print(f"[WARN] {class_name} has unmet theory: {unmet} — running relaxed pass")

        for day in day_list:
            for slot in range(days_slots[day]):
                if timetable[class_name][day][slot] is not None:
                    continue
                # Check again if still unmet
                still_unmet = any(
                    required_map[class_name].get(sub, 0) - assigned_map[class_name].get(sub, 0) > 0
                    for sub in required_map.get(class_name, {})
                )
                if not still_unmet:
                    break
                try_assign(class_name, day, slot, strict=False)


# ---------------- EXTRA (PRIORITY + ROUND ROBIN) ----------------

def fill_remaining_slots(timetable, teacher_busy, teacher_map, required_map):
    """
    Fill all remaining empty slots after required theory is placed.

    Strategy:
    - Priority-based: lower PRIORITY_CONFIG value = assigned first
    - Subjects not in PRIORITY_CONFIG get priority 99 (still included, not excluded)
    - Round-robin within same priority tier to avoid same subject clustering
    - No consecutive same subject (relaxed — allowed if no other option)
    - Teacher conflict checked
    - Unfillable slots → FREE
    """
    days_slots = {"Mon":7,"Tue":7,"Wed":7,"Thu":7,"Fri":7,"Sat":4}

    for class_name in timetable:

        # Build subject list — ALL subjects for this class, sorted by priority
        # Subjects missing from PRIORITY_CONFIG get lowest priority (99)
        class_subjects = list(required_map.get(class_name, {}).keys())
        class_subjects.sort(
            key=lambda s: PRIORITY_CONFIG.get(s, 99)
        )

        if not class_subjects:
            # No subjects defined — mark all empty slots as FREE
            for day in days_slots:
                for slot in range(days_slots[day]):
                    if timetable[class_name][day][slot] is None:
                        timetable[class_name][day][slot] = "FREE"
            continue

        rr_queue = deque(class_subjects)

        for day in days_slots:
            for slot in range(days_slots[day]):

                if timetable[class_name][day][slot] is not None:
                    continue

                assigned = False

                # Try each subject in priority/round-robin order
                for _ in range(len(rr_queue)):
                    sub = rr_queue[0]
                    rr_queue.rotate(-1)

                    teacher = teacher_map.get((class_name, sub))
                    if not teacher:
                        continue

                    if teacher in teacher_busy[(day, slot)]:
                        continue

                    # Soft check: avoid consecutive same subject
                    # strict=False means daily limit (max 2) still applies
                    # but consecutive adjacency is allowed as fallback
                    if not is_good_assignment(
                        timetable, class_name, day, slot, sub, strict=False
                    ):
                        continue

                    timetable[class_name][day][slot] = f"{sub} ({teacher})"
                    teacher_busy[(day, slot)].add(teacher)
                    assigned = True
                    break

                if not assigned:
                    timetable[class_name][day][slot] = "FREE"


# ---------------- UTILS ----------------

def is_good_assignment(timetable, class_name, day, slot, subject, strict=True):
    """
    Check if placing `subject` at (class, day, slot) is acceptable.

    strict=True  → enforce:
        - Max 2 of same subject per day
        - No same subject in adjacent slots (no consecutive)

    strict=False → enforce only:
        - Max 2 of same subject per day
        (consecutive adjacency relaxed — deadlock prevention)
    """
    day_slots = timetable[class_name][day]

    # Daily limit: max 2 of same subject per day (always enforced)
    count = sum(1 for s in day_slots if s and subject in s)
    if count >= 2:
        return False

    if strict:
        # No same subject in the slot immediately before
        if slot > 0 and day_slots[slot - 1] and subject in day_slots[slot - 1]:
            return False
        # No same subject in the slot immediately after
        # Note: slot+1 may already be assigned (practical or earlier theory)
        if slot < len(day_slots) - 1 and day_slots[slot + 1] and subject in day_slots[slot + 1]:
            return False

    return True

# ═══════════════════════════════════════════════════════════════════════════════
#  STAFF PANEL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def extract_teacher_schedule(timetable, teacher_name):
    """
    Build teacher's full week schedule.

    For theory slots  → shows subject + class
    For PRAC slots    → shows only the batch this teacher handles:
                        e.g. "DBMS (Batch S1)"  class "SY-A"
    Skips slots where teacher is not involved.

    Returns:
        { day: { slot_idx: {"subject": str, "class": str} } }
    """
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    schedule = {d: {} for d in days}

    for class_name, day_map in timetable.items():
        for day, slots in day_map.items():
            for idx, cell in enumerate(slots):
                if not cell or cell == "FREE":
                    continue

                subject, batch = _parse_subject_for_teacher(cell, teacher_name)

                if subject is None:
                    continue   # teacher not in this slot

                if batch:
                    # Practical — show batch-specific label
                    display = f"{subject} (Batch {batch})"
                else:
                    display = subject

                schedule[day][idx] = {
                    "subject": display,
                    "class":   class_name
                }

    return schedule

def _parse_subject(cell):
    """
    Generic: extract subject label from a theory cell.
    Theory: "CN (Prof.X)"  → "CN"
    PRAC:   return full string (used in absentee pass-through)
    FREE:   return "FREE"
    """
    if not cell or cell == "FREE":
        return "FREE"
    if cell.startswith("PRAC"):
        return cell          # caller decides what to do with PRAC
    if "(" in cell:
        return cell.split("(")[0].strip()
    return cell


def _parse_subject_for_teacher(cell, teacher_name):
    """
    Teacher-specific parser.

    Theory  → "CN (Prof.X)"          returns ("CN", None)
    PRAC    → "PRAC [S1:DBMS (Prof.X) | S2:CN (Prof.Y)]"
               for Prof.X            returns ("DBMS", "S1")
               for Prof.Y            returns ("CN",   "S2")
    If teacher not found in cell     returns (None, None)
    """
    if not cell or cell == "FREE":
        return (None, None)

    if cell.startswith("PRAC"):
        # Format: PRAC [S1:DBMS (Prof.X) | S2:CN (Prof.Y) | S3:LIBRARY]
        # Split on " | " to get each batch segment
        inner = cell[cell.index("[") + 1 : cell.rindex("]")]
        for segment in inner.split(" | "):
            segment = segment.strip()
            if teacher_name in segment:
                # segment looks like: "S1:DBMS (Prof.X)"
                colon_idx = segment.index(":")
                batch = segment[:colon_idx].strip()           # "S1"
                rest  = segment[colon_idx + 1:].strip()       # "DBMS (Prof.X)"
                subject = rest.split("(")[0].strip()          # "DBMS"
                return (subject, batch)
        return (None, None)   # teacher not in this PRAC block

    # Theory cell
    if teacher_name in cell:
        return (_parse_subject(cell), None)

    return (None, None)


# ═══════════════════════════════════════════════════════════════════════════════
#  ABSENTEE / REPLACEMENT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_replacements(timetable, day, absent_teachers):
    """
    Replacement engine — theory only.

    Key rules:
    - PRAC slots are NEVER touched (skipped entirely)
    - Replacement selection is workload-aware + fairly distributed
    - Absent teachers' full day is shown (PRAC slots pass through unchanged)
    - Replacement teachers' full day is shown (original + new assignments)

    Returns:
        { teacher_name: [ {slot, subject, class, is_replacement} ] }
    """
    days_slots  = {"Mon":7, "Tue":7, "Wed":7, "Thu":7, "Fri":7, "Sat":4}
    total_slots = days_slots.get(day, 7)
    absent_set  = set(absent_teachers)

    # ── Build teacher_busy for this day ──────────────────────────────────────
    # Only theory slots contribute to busy tracking
    # PRAC slots: teachers are busy but we won't reassign them anyway
    teacher_busy = {s: set() for s in range(total_slots)}

    for class_name, day_map in timetable.items():
        for idx, cell in enumerate(day_map.get(day, [])):
            if not cell or cell == "FREE":
                continue
            for t in _extract_teachers_from_cell(cell):
                teacher_busy[idx].add(t)

    # ── Collect absent teachers' THEORY slots only ────────────────────────────
    absent_slots = {t: [] for t in absent_teachers}

    for class_name, day_map in timetable.items():
        year = class_name.split("-")[0]
        for idx, cell in enumerate(day_map.get(day, [])):
            if not cell or cell == "FREE":
                continue
            if cell.startswith("PRAC"):
                continue   # ← RULE: never replace practicals
            for t in _extract_teachers_from_cell(cell):
                if t in absent_set:
                    absent_slots[t].append({
                        "slot":    idx,
                        "subject": _parse_subject(cell),
                        "class":   class_name,
                        "year":    year
                    })

    # ── Workload trackers ─────────────────────────────────────────────────────
    # day_workload[teacher]     = slots already assigned this day (original)
    # replacement_count[teacher] = how many replacements assigned so far today
    day_workload      = {}
    replacement_count = {}

    # Seed day_workload from existing timetable (theory only — PRAC excluded)
    for class_name, day_map in timetable.items():
        for idx, cell in enumerate(day_map.get(day, [])):
            if not cell or cell == "FREE" or cell.startswith("PRAC"):
                continue
            for t in _extract_teachers_from_cell(cell):
                day_workload[t] = day_workload.get(t, 0) + 1

    # ── Assign replacements ───────────────────────────────────────────────────
    replacement_assignments = {}   # (slot, class_name) → replacement teacher
    replacement_busy        = {s: set() for s in range(total_slots)}

    for absent_t, slot_list in absent_slots.items():
        for item in slot_list:
            slot = item["slot"]
            rep  = _find_replacement_fair(
                timetable, day, slot, item["year"],
                absent_set,
                teacher_busy, replacement_busy,
                day_workload, replacement_count
            )
            replacement_assignments[(slot, item["class"])] = rep
            if rep:
                replacement_busy[slot].add(rep)
                replacement_count[rep] = replacement_count.get(rep, 0) + 1
                # Workload increases for the replacement teacher
                day_workload[rep] = day_workload.get(rep, 0) + 1

    # ── Determine affected teachers ───────────────────────────────────────────
    affected = set(absent_teachers)
    for rep in replacement_assignments.values():
        if rep:
            affected.add(rep)

    return {
        t: build_teacher_day_schedule(
            timetable, day, t,
            absent_set, replacement_assignments, total_slots
        )
        for t in sorted(affected)
    }

def _extract_teachers_from_cell(cell):
    """
    Extract all teacher names from a timetable cell.

    Theory: "CN (Prof.X)"
        → ["Prof.X"]
    PRAC: "PRAC [S1:DBMS (Prof.X) | S2:CN (Prof.Y) | S3:LIBRARY]"
        → ["Prof.X", "Prof.Y"]
        (LIBRARY batches have no teacher — skipped)
    """
    if not cell or cell == "FREE":
        return []

    if cell.startswith("PRAC"):
        result = []
        if "[" not in cell:
            return []
        inner = cell[cell.index("[") + 1 : cell.rindex("]")]
        for segment in inner.split(" | "):
            segment = segment.strip()
            # Segment format: "S1:DBMS (Prof.X)" or "S3:LIBRARY"
            if "(" in segment and ")" in segment:
                t = segment[segment.rfind("(") + 1 : segment.rfind(")")].strip()
                if t:
                    result.append(t)
        return result

    # Theory cell: "CN (Prof.X)"
    if "(" in cell and ")" in cell:
        t = cell[cell.rfind("(") + 1 : cell.rfind(")")].strip()
        return [t] if t else []

    return []


def _find_replacement_fair(
    timetable, day, slot, year,
    absent_set, teacher_busy, replacement_busy,
    day_workload, replacement_count
):
    """
    Workload-aware, fair replacement selection.

    Steps:
    1. Determine occupied teachers for this slot
    2. Split candidates into Priority 1 (same year) and Priority 2 (all)
    3. From each priority group:
       a. Filter: not absent, not busy this slot
       b. Sort by (day_workload, replacement_count) ascending
       c. Return the first (lowest burden) teacher
    4. If nobody → return None

    This avoids overloading a single free teacher when multiple
    replacements are needed across the same day.
    """
    occupied = (
        teacher_busy.get(slot, set()) |
        replacement_busy.get(slot, set())
    )

    def score(t):
        return (day_workload.get(t, 0), replacement_count.get(t, 0))

    # Build same-year teacher set from timetable
    same_year = set()
    for cls, day_map in timetable.items():
        if not cls.startswith(year):
            continue
        for slots in day_map.values():
            for cell in slots:
                for t in _extract_teachers_from_cell(cell):
                    same_year.add(t)

    # Priority 1: same year, not absent, not busy
    p1_candidates = [
        t for t in same_year
        if t not in absent_set and t not in occupied
    ]
    if p1_candidates:
        return min(p1_candidates, key=score)

    # Priority 2: any teacher, not absent, not busy
    p2_candidates = [
        t for t in teachers       # global teachers list
        if t not in absent_set and t not in occupied
    ]
    if p2_candidates:
        return min(p2_candidates, key=score)

    return None


def build_teacher_day_schedule(
    timetable, day, teacher_name,
    absent_set, replacement_assignments, total_slots
):
    """
    Full day schedule for one teacher.

    Includes:
    - Original theory lectures         → is_replacement = False
    - Original PRAC blocks             → is_replacement = False, subject = parsed batch view
    - Replacement theory lectures      → is_replacement = True
    - FREE for unoccupied slots

    PRAC slots are NEVER replaced — shown as-is using batch-specific parsing.
    """
    schedule = {}

    # ── Original lectures (theory + PRAC) ─────────────────────────────────
    for class_name, day_map in timetable.items():
        for idx, cell in enumerate(day_map.get(day, [])):
            if not cell or cell == "FREE":
                continue

            subject, batch = _parse_subject_for_teacher(cell, teacher_name)
            if subject is None:
                continue

            display = f"{subject} (Batch {batch})" if batch else subject

            schedule[idx] = {
                "slot":           idx,
                "subject":        display,
                "class":          class_name,
                "is_replacement": False,
                "is_practical":   bool(batch)    # flag for template styling
            }

    # ── Replacement theory lectures ────────────────────────────────────────
    for (slot, cls), rep in replacement_assignments.items():
        if rep != teacher_name:
            continue
        slots_list = timetable.get(cls, {}).get(day, [])
        cell       = slots_list[slot] if slot < len(slots_list) else None
        if cell and cell.startswith("PRAC"):
            continue   # safety guard — never mark PRAC as replacement
        subject = _parse_subject(cell) if cell else "—"
        schedule[slot] = {
            "slot":           slot,
            "subject":        subject,
            "class":          cls,
            "is_replacement": True,
            "is_practical":   False
        }

    # ── Fill FREE slots ────────────────────────────────────────────────────
    return [
        schedule.get(s, {
            "slot": s, "subject": "FREE",
            "class": "—", "is_replacement": False, "is_practical": False
        })
        for s in range(total_slots)
    ]

def _base_table_style():
    return [
        ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#212529")),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0),  9),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE",    (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                        [colors.white, colors.HexColor("#f8f9fa")]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.grey),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0,0), (-1, -1), 4),
    ]

def _build_week_pdf(teacher_name, week_schedule, days, max_slots):
    """ReportLab PDF: full week timetable for one teacher."""
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=landscape(A4),
                               leftMargin=1.5*cm, rightMargin=1.5*cm,
                               topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story  = []

    story.append(Paragraph(f"Week Timetable — {teacher_name}", styles["Title"]))
    story.append(Spacer(1, 0.4*cm))

    # Header row
    header = ["Slot"] + days
    rows   = [header]

    for s in range(max_slots):
        row = [str(s + 1)]
        for day in days:
            if day == "Sat" and s >= 4:
                row.append("—")
            else:
                entry = week_schedule[day].get(s)
                row.append(f"{entry['subject']}\n{entry['class']}"
                           if entry else "FREE")
        rows.append(row)

    col_widths = [1.2*cm] + [3.8*cm] * len(days)
    tbl        = Table(rows, colWidths=col_widths, repeatRows=1)
    style      = _base_table_style()

    # Highlight occupied cells green
    for r in range(1, len(rows)):
        for c in range(1, len(days) + 1):
            val = rows[r][c]
            if val not in ("FREE", "—"):
                style.append(("BACKGROUND", (c, r), (c, r),
                              colors.HexColor("#d4edda")))
            elif val == "FREE":
                style.append(("TEXTCOLOR", (c, r), (c, r),
                              colors.HexColor("#aaaaaa")))

    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    doc.build(story)
    return buf.getvalue()

def _build_day_pdf(teacher_name, day, schedule):
    """ReportLab PDF: one teacher's full day schedule."""
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                               leftMargin=2*cm, rightMargin=2*cm,
                               topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story  = []

    story.append(Paragraph(f"Day Schedule — {teacher_name}", styles["Title"]))
    story.append(Paragraph(f"Day: {day}", styles["Normal"]))
    story.append(Spacer(1, 0.5*cm))

    header = ["Slot", "Subject", "Class", "Status"]
    rows   = [header]

    for row in schedule:
        if row["is_replacement"]:
            status = "Replacement"
        elif row["subject"] == "FREE":
            status = "Free"
        else:
            status = "Original"
        rows.append([str(row["slot"] + 1), row["subject"],
                     row["class"], status])

    col_widths = [2*cm, 8*cm, 4*cm, 4*cm]
    tbl        = Table(rows, colWidths=col_widths, repeatRows=1)
    style      = _base_table_style()

    for r in range(1, len(rows)):
        status = rows[r][3]
        if status == "Replacement":
            style.append(("BACKGROUND", (0, r), (-1, r),
                          colors.HexColor("#fff3cd")))
        elif status == "Original":
            style.append(("BACKGROUND", (0, r), (-1, r),
                          colors.HexColor("#d4edda")))
        elif status == "Free":
            style.append(("TEXTCOLOR", (0, r), (-1, r),
                          colors.HexColor("#aaaaaa")))

    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    doc.build(story)
    return buf.getvalue()

# ---------------- HTML OUTPUT ----------------

def generate_html_table(timetable):
    html = ""
    for class_name in timetable:
        html += f"<h2>{class_name}</h2><table border='1'><tr><th>Slot</th>"
        days = list(timetable[class_name].keys())

        for d in days:
            html += f"<th>{d}</th>"
        html += "</tr>"

        max_slots = max(len(timetable[class_name][d]) for d in days)

        for i in range(max_slots):
            html += f"<tr><td>{i+1}</td>"
            for d in days:
                val = timetable[class_name][d][i] if i < len(timetable[class_name][d]) else "-"
                html += f"<td>{val or '-'}</td>"
            html += "</tr>"

        html += "</table><br><br>"

    return html


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5001)
