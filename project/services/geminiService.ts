import { ResumeData, GeneratedResume } from "../types";

const API_URL =
  "https://resume-backend-v2-1069915675190.us-central1.run.app";

// ================= GENERATE =================
export const generateProfessionalResume = async (
  data: ResumeData
): Promise<GeneratedResume> => {

  const payload = {
    name: data.fullName,
    tone: data.tone,

    summary: data.summary
      ? data.summary.split("\n").map(s => s.trim()).filter(Boolean)
      : [],

    skills: [
      ...data.skills.technical.split(",").map(s => s.trim()).filter(Boolean),
      ...data.skills.soft.split(",").map(s => s.trim()).filter(Boolean)
    ],

    education: data.education.map(edu => ({
      degree: edu.degree,
      institution: edu.institution,
      year: edu.graduationYear || ""
    })),

    experience: data.experience.map(exp => ({
      role: exp.role,
      company: exp.company,
      duration: exp.duration || "",
      points: exp.responsibilities
        ? exp.responsibilities.split("\n").map(p => p.trim()).filter(Boolean)
        : []
    })),

    projects: data.projects.map(proj => ({
      title: proj.title,
      description: proj.description
    })),

    certifications: data.certifications
      ? data.certifications.split("\n").map(c => c.trim()).filter(Boolean)
      : [],

    tools: data.tools
      ? data.tools.split(",").map(t => t.trim()).filter(Boolean)
      : []
  };

  const response = await fetch(`${API_URL}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  // 🔥 IMPROVED ERROR HANDLING
  if (!response.ok) {
    let errorMsg = "Failed to generate resume";
    try {
      const errJson = await response.json();
      errorMsg = errJson.error || JSON.stringify(errJson);
    } catch {
      errorMsg = await response.text();
    }
    throw new Error(errorMsg);
  }

  const dataRes = await response.json();

  // 🔥 HANDLE BACKEND ERROR RESPONSE
  if ((dataRes as any).error) {
    throw new Error((dataRes as any).error);
  }

  return dataRes;
};

// ================= CONVERT =================
export const convertResume = async (
  file: File
): Promise<GeneratedResume> => {

  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_URL}/convert`, {
    method: "POST",
    body: formData
  });

  // 🔥 IMPROVED ERROR HANDLING
  if (!response.ok) {
    let errorMsg = "Failed to convert resume";
    try {
      const errJson = await response.json();
      errorMsg = errJson.error || JSON.stringify(errJson);
    } catch {
      errorMsg = await response.text();
    }
    throw new Error(errorMsg);
  }

  const dataRes = await response.json();

  // 🔥 HANDLE BACKEND ERROR RESPONSE
  if ((dataRes as any).error) {
    throw new Error((dataRes as any).error);
  }

  return dataRes;
};