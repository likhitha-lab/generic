import { apiClient } from "./client";
import type { ResumeFileFormat, ResumeGenerateInput } from "../types/resume";
import type {
  ResumeDetail,
  ResumeSummary,
  ResumeVersionDetail,
  ResumeVersionSummary,
  ResumeVisibilitySettings,
} from "../types/history";

export async function listResumes(): Promise<ResumeSummary[]> {
  const { data } = await apiClient.get<ResumeSummary[]>("/api/resumes");
  return data;
}

export async function getResume(resumeId: number): Promise<ResumeDetail> {
  const { data } = await apiClient.get<ResumeDetail>(`/api/resumes/${resumeId}`);
  return data;
}

export async function generateResume(payload: ResumeGenerateInput): Promise<ResumeDetail> {
  const { data } = await apiClient.post<ResumeDetail>("/api/resumes/generate", payload);
  return data;
}

export async function uploadResume(file: File): Promise<ResumeDetail> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await apiClient.post<ResumeDetail>("/api/resumes/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function regenerateResume(resumeId: number, tone?: string): Promise<ResumeDetail> {
  const { data } = await apiClient.post<ResumeDetail>(`/api/resumes/${resumeId}/regenerate`, { tone });
  return data;
}

export async function deleteResume(resumeId: number): Promise<void> {
  await apiClient.delete(`/api/resumes/${resumeId}`);
}

export async function getVersionJson(resumeId: number, versionId: number): Promise<ResumeVersionDetail> {
  const { data } = await apiClient.get<ResumeVersionDetail>(`/api/resumes/${resumeId}/versions/${versionId}/json`);
  return data;
}

export async function downloadVersion(resumeId: number, versionId: number, format: ResumeFileFormat): Promise<Blob> {
  const { data } = await apiClient.get(`/api/resumes/${resumeId}/versions/${versionId}/download`, {
    params: { format },
    responseType: "blob",
  });
  return data as Blob;
}

/** Updates a version's recruiter-controlled display visibility (show/hide
 * email, phone, LinkedIn, address, employment dates) - re-renders that
 * version's stored PDF/DOCX to match immediately. Display-only: never
 * deletes the underlying resume data. */
export async function updateVersionVisibility(
  resumeId: number,
  versionId: number,
  settings: ResumeVisibilitySettings
): Promise<ResumeVersionSummary> {
  const { data } = await apiClient.put<ResumeVersionSummary>(
    `/api/resumes/${resumeId}/versions/${versionId}/visibility`,
    settings
  );
  return data;
}
