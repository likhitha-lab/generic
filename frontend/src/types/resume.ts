/**
 * Shared resume content types. These mirror `backend/app/schemas/resume.py`'s
 * `ResumeContent` - keeping the two in sync is what stops frontend/backend
 * schema drift.
 */

/**
 * The backend's resume-generation pipeline can now return skills/tools in
 * either shape (see app/services/resume_polish.py):
 *   - a flat list (old format, and still what the manual-entry
 *     Resume Generator flow produces)
 *   - a category-name -> item-list object (new format, from the
 *     upload/convert flow's Stage 2 polish, which groups technologies into
 *     named categories like "Cloud Platforms")
 * Components must not assume either shape - see
 * src/utils/resumeFields.ts's normalizeSkillGroups()/toSafeArray(), which
 * every consumer of this field should go through rather than calling
 * .map()/.length directly on skills/tools.
 */
export type CategorizedList = string[] | Record<string, string[]>;

export interface ResumeData {
  name?: string;
  email?: string;
  phone?: string;
  location?: string;
  links?: string;
  summary: string;
  skills: CategorizedList;
  tools: CategorizedList;
  experience: Array<{
    company: string;
    role: string;
    duration: string;
    points: string[];
  }>;
  education: string[];
  certifications: string[];
  projects: Array<{
    title: string;
    description: string;
  }>;
}

export type ResumeFileFormat = "pdf" | "docx";

// ---- Payload sent to /api/resumes/generate (the manual-entry flow) ----
export interface EducationInput {
  degree: string;
  institution: string;
  year: string;
}

export interface ExperienceInput {
  company: string;
  role: string;
  duration: string;
  points: string[];
}

export interface ProjectInput {
  title: string;
  description: string;
  tools?: string;
}

export const RESUME_TONES = ["Fresher", "Experienced", "Technical", "Executive"] as const;
export type ResumeTone = (typeof RESUME_TONES)[number];

export interface ResumeGenerateInput {
  name: string;
  email?: string;
  phone?: string;
  location?: string;
  links?: string;
  tone: ResumeTone;
  skills?: string[];
  tools?: string[];
  certifications?: string[];
  education?: EducationInput[];
  experience?: ExperienceInput[];
  projects?: ProjectInput[];
}
