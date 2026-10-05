import { Readable } from "stream";

import "dotenv/config";
import express from "express";
import cors from "cors";
import { GoogleGenAI } from "@google/genai";
import puppeteer from "puppeteer-core";
import { google } from "googleapis";

const app = express();
app.use(cors());
app.use(express.json());

/* =========================
   GEMINI SETUP
========================= */

const apiKey = process.env.GEMINI_API_KEY;
if (!apiKey) {
  throw new Error("GEMINI_API_KEY is missing");
}

const ai = new GoogleGenAI({ apiKey });

/* =========================
   GOOGLE DRIVE AUTH
========================= */

const auth = new google.auth.GoogleAuth({
  scopes: [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
  ],
});

const drive = google.drive({ version: "v3", auth });

/* =========================
   HTML TEMPLATE
========================= */

function generateResumeHTML(data) {
  return `
  <html>
    <head>
      <meta charset="UTF-8" />
      <style>
        body {
          font-family: Arial, sans-serif;
          margin: 40px;
          line-height: 1.6;
        }
        h1 {
          text-align: center;
          margin-bottom: 10px;
        }
        h2 {
          border-bottom: 1px solid #ccc;
          padding-bottom: 4px;
          margin-top: 25px;
        }
        ul {
          margin-left: 20px;
        }
      </style>
    </head>
    <body>
      <h1>${data.name || "Resume"}</h1>

      <h2>Summary</h2>
      <p>${data.summary || ""}</p>

      <h2>Skills</h2>
      <b>Technical</b>
      <ul>
        ${(data.skills?.technical || []).map(s => `<li>${s}</li>`).join("")}
      </ul>

      <b>Soft</b>
      <ul>
        ${(data.skills?.soft || []).map(s => `<li>${s}</li>`).join("")}
      </ul>

      <h2>Experience</h2>
      ${(data.experienceItems || []).map(exp => `
        <p><b>${exp.role}</b> — ${exp.company} (${exp.duration})</p>
        <ul>
          ${exp.bullets.map(b => `<li>${b}</li>`).join("")}
        </ul>
      `).join("")}

      <h2>Projects</h2>
      ${(data.projectItems || []).map(p => `
        <p><b>${p.title}</b></p>
        <p>${p.description}</p>
        <p><i>Tools:</i> ${p.tools.join(", ")}</p>
      `).join("")}

      <h2>Education</h2>
      ${(data.educationItems || []).map(e => `
        <p>${e.degree} — ${e.institution} (${e.graduationYear})</p>
      `).join("")}
    </body>
  </html>
  `;
}

/* =========================
   MAIN API
========================= */

app.post("/generate-resume", async (req, res) => {
  try {
    const data = req.body;

    const prompt = `
You are a professional resume writer.

STRICT RULES:
- Use ONLY the provided data
- Do NOT fabricate achievements
- Output MUST be valid JSON only

Return JSON in EXACT structure:

{
  "summary": string,
  "skills": {
    "technical": string[],
    "soft": string[]
  },
  "experienceItems": {
    "company": string,
    "role": string,
    "duration": string,
    "bullets": string[]
  }[],
  "projectItems": {
    "title": string,
    "description": string,
    "tools": string[]
  }[],
  "educationItems": {
    "degree": string,
    "institution": string,
    "graduationYear": string,
    "cgpa": string | null
  }[]
}

Tone: ${data.tone}

INPUT DATA:
${JSON.stringify(data, null, 2)}
`;

    const response = await ai.models.generateContent({
      model: "gemini-2.5-flash",
      contents: [{ parts: [{ text: prompt }] }],
    });

    const text = response?.candidates?.[0]?.content?.parts?.[0]?.text;
    if (!text) throw new Error("Empty Gemini response");

    const cleanedText = text
      .replace(/```json/gi, "")
      .replace(/```/g, "")
      .trim();

    const parsed = JSON.parse(cleanedText);

    /* ===== HTML → PDF ===== */

    const html = generateResumeHTML({
      ...parsed,
      name: data.name
    });

    const browser = await puppeteer.launch({
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage"
  ],
  executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
  headless: true,
});




    const page = await browser.newPage();
await page.setContent(html, { waitUntil: "networkidle0" });

// CREATE PDF (returns Uint8Array in Cloud Run)
const pdfUint8 = await page.pdf({ format: "A4" });

// FORCE Buffer (this fixes the crash)
const pdfBuffer = Buffer.from(pdfUint8);

// STREAM for Google Drive
const pdfStream = Readable.from(pdfBuffer);

await browser.close();



    /* ===== PDF → DRIVE ===== */

    const SHARED_DRIVE_ID = "0ADqkO5jsc0BrUk9PVA";

    const pdfFile = await drive.files.create({
      requestBody: {
        name: "Resume.pdf",
        mimeType: "application/pdf",
        parents: [SHARED_DRIVE_ID],
      },
      media: {
        mimeType: "application/pdf",
        body: pdfStream,
      },
      supportsAllDrives: true,
    });


    /* ===== PDF → GOOGLE DOCS ===== */

    const docFile = await drive.files.copy({
      fileId: pdfFile.data.id,
      requestBody: {
        mimeType: "application/vnd.google-apps.document",
      },
      supportsAllDrives: true,
    });


    res.json({
      resumeData: parsed,
      pdfUrl: `https://drive.google.com/file/d/${pdfFile.data.id}/view`,
      docUrl: `https://docs.google.com/document/d/${docFile.data.id}/edit`,
    });

  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err.message });
  }
});

/* =========================
   HEALTH CHECK
========================= */

app.get("/", (req, res) => {
  res.send("Resume Builder Backend is running");
});

const PORT = process.env.PORT || 8080;

app.listen(PORT, () => {
  console.log(`Backend running on http://localhost:${PORT}`);
});

