import type { ResumeData, ResumeFileFormat } from "./resume";

/** Recruiter-controlled DISPLAY visibility for one resume version's
 * rendered PDF/DOCX/preview - matches app/schemas/resume_history.py's
 * ResumeVisibilitySettings exactly (same field names, same all-true
 * default). Turning a field off never deletes the underlying data (the
 * backend never touches content_json for this) - it only omits that
 * field from the NEXT render. */
export interface ResumeVisibilitySettings {
  show_email: boolean;
  show_phone: boolean;
  show_linkedin: boolean;
  show_address: boolean;
  show_employment_dates: boolean;
}

export const DEFAULT_VISIBILITY: ResumeVisibilitySettings = {
  show_email: true,
  show_phone: true,
  show_linkedin: true,
  show_address: true,
  show_employment_dates: true,
};

export interface ResumeVersionSummary {
  id: number;
  version_number: number;
  tone: string;
  created_at: string;
  created_by_id: number;
  visibility: ResumeVisibilitySettings;
}

export interface ResumeVersionDetail extends ResumeVersionSummary {
  content: ResumeData;
}

export interface ResumeSummary {
  id: number;
  title: string;
  source_type: "upload" | "manual";
  created_at: string;
  updated_at: string;
  latest_version: ResumeVersionSummary | null;
  version_count: number;
}

export interface ResumeDetail extends ResumeSummary {
  versions: ResumeVersionSummary[];
}

export type { ResumeFileFormat };
