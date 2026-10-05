from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import google.auth
from docx import Document
from docx.shared import Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pdfplumber

from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    ListFlowable,
    ListItem
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4

import os
import json
import re
import tempfile

# =====================================================
# APP CONFIG
# =====================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY not set")

client = genai.Client(api_key=api_key)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "static", "logo.png")

# =====================================================
# REQUEST MODEL
# =====================================================

class ResumeRequest(BaseModel):
    name: str
    tone: str
    summary: list | None = None
    skills: list | None = None
    experience: list | None = None
    education: list | None = None
    certifications: list | None = None
    tools: list | None = None
    projects: list | None = None

# =====================================================
# GEMINI CALL
# =====================================================

def call_gemini(prompt: str):
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        text = response.text

        if not text:
            raise HTTPException(status_code=500, detail="Empty Gemini response")

        cleaned = re.sub(r"```json|```", "", text).strip()

        return json.loads(cleaned)

    except Exception as e:
        print("GEMINI ERROR:", str(e))
        raise HTTPException(status_code=500, detail=f"Gemini failed: {str(e)}")

# =====================================================
# NAME CLEANER
# =====================================================

def clean_name(raw_name: str):
    if not raw_name:
        return None

    raw_name = raw_name.strip()
    raw_name = re.sub(r'[^a-zA-Z\s]', '', raw_name)

    words = raw_name.split()

    if len(words) > 4:
        words = words[:4]

    return " ".join(words)

# =====================================================
# PDF EXTRACTION
# =====================================================

def extract_pdf_data(file_path):
    structured = {"raw_text": ""}
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                structured["raw_text"] += text.strip() + "\n"
    return structured

# =====================================================
# DOCX EXTRACTION (ADDED)
# =====================================================

def extract_docx_data(file_path):
    structured = {"raw_text": ""}
    doc = Document(file_path)

    full_text = []

    for para in doc.paragraphs:
        if para.text.strip():
            full_text.append(para.text.strip())

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    full_text.append(cell.text.strip())

    structured["raw_text"] = "\n".join(full_text)
    return structured

# =====================================================
# NORMALIZATION
# =====================================================

# ================= FIXED NORMALIZATION =================
def normalize_parsed(parsed: dict):

    for field in ["summary", "skills", "tools"]:
        if parsed.get(field) and isinstance(parsed[field], str):
            parsed[field] = [parsed[field]]

    # EDUCATION
    if parsed.get("education"):
        formatted = []
        for edu in parsed["education"]:
            if isinstance(edu, dict):
                degree = edu.get("degree", "")
                institution = edu.get("institution", "")
                year = edu.get("year", "")
                line = degree
                if institution:
                    line += f" - {institution}"
                if year:
                    line += f" - {year}"
                formatted.append(line.strip())
            else:
                formatted.append(str(edu))
        parsed["education"] = formatted

    # CERTIFICATIONS
    if parsed.get("certifications"):
        formatted = []
        for cert in parsed["certifications"]:
            if isinstance(cert, dict):
                name = cert.get("name", "")
                dates = cert.get("dates", "") or cert.get("credential_id", "")
                line = name
                if dates:
                    line += f" - {dates}"
                formatted.append(line.strip())
            else:
                formatted.append(str(cert))
        parsed["certifications"] = formatted

    # EXPERIENCE CLEAN
    if parsed.get("experience"):
        if isinstance(parsed["experience"], dict):
            parsed["experience"] = [parsed["experience"]]

        clean_exp = []
        for exp in parsed["experience"]:
            if not isinstance(exp, dict):
                continue

            company = exp.get("company", "").strip()
            role = exp.get("role", "").strip()
            duration = exp.get("duration", "").strip()
            points = exp.get("points", [])

            if not company and not role and not points:
                continue

            clean_exp.append({
                "company": company,
                "role": role,
                "duration": duration,
                "points": points if points else []
            })

        parsed["experience"] = clean_exp

    if isinstance(parsed.get("projects"), dict):
        parsed["projects"] = [parsed["projects"]]

    return parsed


# ================= ENFORCE LIMITS =================
def enforce_limits(parsed):

    # SUMMARY
    if isinstance(parsed.get("summary"), list):
        parsed["summary"] = " ".join(parsed["summary"])

    if isinstance(parsed.get("summary"), str):
        parsed["summary"] = ". ".join(parsed["summary"].split(".")[:3]).strip()

    # SKILLS
    if parsed.get("skills"):
        parsed["skills"] = list(dict.fromkeys(parsed["skills"]))[:10]

    # TOOLS
    if parsed.get("tools"):
        parsed["tools"] = list(dict.fromkeys(parsed["tools"]))[:12]

    # EXPERIENCE (DEDUP + LIMIT)
    if parsed.get("experience"):
        seen = set()
        clean_exp = []

        for exp in parsed["experience"]:
            key = (exp.get("company"), exp.get("role"))
            if key not in seen:
                seen.add(key)
                exp["points"] = exp.get("points", [])[:3]
                clean_exp.append(exp)

        parsed["experience"] = clean_exp

    # PROJECTS (2 lines)
    if parsed.get("projects"):
        for proj in parsed["projects"]:
            desc = proj.get("description", "")
            proj["description"] = ". ".join(desc.split(".")[:2])

    return parsed
# =====================================================
# GOOGLE DRIVE UPLOAD
# =====================================================

def upload_to_drive(file_path, file_name):
    credentials, _ = google.auth.default()
    service = build('drive', 'v3', credentials=credentials)

    file_metadata = {
        'name': file_name,
        'parents': ['0ADqkO5jsc0BrUk9PVA']
    }

    media = MediaFileUpload(file_path)

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        supportsAllDrives=True,
        fields='id'
    ).execute()

    file_id = file.get('id')
    return f"https://drive.google.com/uc?id={file_id}&export=download"

# =====================================================
# FILE GENERATION
# =====================================================

def generate_files(parsed, name):

    pdf_path = os.path.join(tempfile.gettempdir(), f"{name.replace(' ', '_')}.pdf")
    doc = BaseDocTemplate(pdf_path, pagesize=A4)
    frame = Frame(40, 40, 515, 750, id="normal")

    def header(canvas, doc):
        width, height = A4

        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(40, height - 40, name)
        canvas.line(40, height - 55, width - 40, height - 55)

        if os.path.exists(LOGO_PATH):
            canvas.drawImage(
                LOGO_PATH,
                width - 130,
                height - 60,
                width=90,
                height=35,
                preserveAspectRatio=True,
                mask='auto'
            )

    doc.addPageTemplates([PageTemplate(id="Resume", frames=frame, onPage=header)])

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    elements = [Spacer(1, 60)]

    def add_section(title, items):
        if items:
            elements.append(Paragraph(title, styles["Heading2"]))
            elements.append(Spacer(1, 8))
            bullets = [ListItem(Paragraph(str(i), normal)) for i in items]
            elements.append(ListFlowable(bullets, bulletType="bullet"))
            elements.append(Spacer(1, 14))

    if parsed.get("summary"):
        elements.append(Paragraph("Professional Summary", styles["Heading2"]))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(parsed["summary"], normal))
        elements.append(Spacer(1, 14))
    if parsed.get("skills"):
        elements.append(Paragraph("Technical Skills", styles["Heading2"]))
        elements.append(Spacer(1, 8))

        bullets = [
            ListItem(Paragraph(skill, normal))
            for skill in parsed.get("skills", [])
        ]

        elements.append(ListFlowable(bullets, bulletType="bullet"))
        elements.append(Spacer(1, 14))
    add_section("Education Qualifications", parsed.get("education"))
    elements.append(Spacer(1, 10))

    add_section("Certification", parsed.get("certifications"))
    elements.append(Spacer(1, 10))

    add_section("Tools", parsed.get("tools"))
    elements.append(Spacer(1, 10))

    if parsed.get("experience"):
        elements.append(Paragraph("Professional Experience", styles["Heading2"]))
        elements.append(Spacer(1, 10))

        for exp in parsed["experience"]:
            elements.append(Paragraph(exp.get("company", ""), styles["Heading3"]))
            elements.append(Spacer(1, 4))

            role = exp.get("role", "")
            duration = exp.get("duration", "")

            if role and duration:
                role_line = f"{role} ({duration})"
            elif role:
                role_line = role
            elif duration:
                role_line = duration
            else:
                role_line = ""

            if role_line:
                elements.append(Paragraph(role_line, styles["Normal"]))
                elements.append(Spacer(1, 6))

            for p in exp.get("points", []):
                elements.append(Paragraph(f"• {p}", normal))

            elements.append(Spacer(1, 14))  

    if parsed.get("projects"):
        elements.append(Paragraph("Project Handled", styles["Heading2"]))
        elements.append(Spacer(1, 8))

        for proj in parsed["projects"]:
            elements.append(Paragraph(proj.get("title", ""), styles["Heading3"]))
            elements.append(Spacer(1, 4))
            elements.append(Paragraph(proj.get("description", ""), normal))
            elements.append(Spacer(1, 10))

    doc.build(elements)
    safe_name = name.replace(" ", "_")
    pdf_link = upload_to_drive(pdf_path, f"{safe_name}.pdf")  

    docx_path = tempfile.NamedTemporaryFile(delete=False, suffix=".docx").name
    d = Document()

    header = d.sections[0].header
    header_para = header.paragraphs[0]

    if os.path.exists(LOGO_PATH):
        run = header_para.add_run()
        run.add_picture(LOGO_PATH, width=Inches(1.2))
        header_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    d.add_heading(name, level=1)

    ordered = [
        ("Professional Summary", "summary"),
        ("Technical Skills", "skills"),
        ("Education Qualifications", "education"),
        ("Certification", "certifications"),
        ("Tools", "tools"),
    ]

    for title, key in ordered:
        value = parsed.get(key)

        # ❌ skip empty data
        if not value:
            continue

        d.add_heading(title, level=2)

        # ✅ SUMMARY (STRING)
        if key == "summary":
            d.add_paragraph(value)
            continue

        # ✅ SKILLS INLINE (like PDF)
        if key == "skills":
            for skill in value:
                d.add_paragraph(skill, style="List Bullet")
            continue

        # ✅ ALL OTHER SECTIONS (BULLETS)
        if isinstance(value, list):
            for item in value:
                if item:
                    d.add_paragraph(str(item), style="List Bullet")
    if parsed.get("experience"):
        d.add_heading("Professional Experience", level=2)
        for exp in parsed.get("experience", []):
            d.add_paragraph(exp.get("company", ""))

            role = exp.get("role", "")
            duration = exp.get("duration", "")

            if role and duration:
                d.add_paragraph(f"{role} ({duration})")
            elif role:
                d.add_paragraph(role)
            elif duration:
                d.add_paragraph(duration)

            for p in exp.get("points", []):
                d.add_paragraph(p, style="List Bullet")

    if parsed.get("projects"):
        d.add_heading("Project Handled", level=2)
        for proj in parsed["projects"]:
            d.add_paragraph(proj.get("title", ""))
            d.add_paragraph(proj.get("description", ""))

    d.save(docx_path)
    docx_link = upload_to_drive(docx_path, f"{safe_name}.docx")

    return {
        "pdf_link": pdf_link,
        "docx_link": docx_link,
        "preview": parsed
    }

# =====================================================
# ROUTES
# =====================================================

@app.post("/generate")
async def generate_resume(data: ResumeRequest):

    formatted = call_gemini(f"""
You are a senior ATS resume expert.

STRICT RULES:

1. Professional Summary:
- No bullet points
- Exactly 3 lines
- Include experience + domain + strengths

2. Technical Skills:
- Only top 8–12 relevant skills

3. Experience:
- Each job must include EXACTLY 3 bullet points

4. Projects:
- Title + description (2 lines only)

5. Tools:
- Only professional tools

6. Certifications:
- Include certificates, badges

Return STRICT JSON:

{{
  "summary": "",
  "skills": [],
  "tools": [],
  "experience": [],
  "projects": [],
  "education": [],
  "certifications": []
}}

Input:
{data.model_dump()}
""")

    formatted = normalize_parsed(formatted)
    formatted = enforce_limits(formatted)

    return generate_files(formatted, data.name)


@app.post("/convert")
async def convert_resume(file: UploadFile = File(...)):

    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.write(await file.read())
    tmp.close()

    if file.filename.lower().endswith(".pdf"):
        extracted = extract_pdf_data(tmp.name)
    elif file.filename.lower().endswith(".docx"):
        extracted = extract_docx_data(tmp.name)
    else:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported")

    structured = call_gemini(f"""
    You are a senior ATS resume expert.

    STRICT RULES:

    1. Professional Summary:
    - No bullets
    - Exactly 3 lines
    - Include experience + domain + strengths

    2. Technical Skills:
    - Only top 8–12 relevant skills

    3. Experience:
    - Each job:
    - company
    - role
    - duration
    - EXACTLY 3 bullet points

    4. Education:
    - Degree + Institution + Year

    5. Certifications:
    - Include certificates, badges, achievements

    6. Tools:
    - Only relevant tools

    7. Projects:
    - Title + description (2 lines only)

    Return JSON:

    {{
    "name": "",
    "summary": "",
    "skills": [],
    "tools": [],
    "experience": [],
    "projects": [],
    "education": [],
    "certifications": []
    }}

    Resume Text:
    {extracted["raw_text"]}
    """)

    structured = normalize_parsed(structured)
    structured = enforce_limits(structured)
    name = clean_name(structured.get("name"))

    if not name:
        first_line = extracted["raw_text"].split("\n")[0]
        name = clean_name(first_line)

    if not name:
        name = "Converted_Resume"

    return generate_files(structured, name)