import { apiClient } from "./client";
import type { ResumeFileFormat } from "../types/resume";
import type { AdminResume, AdminUser, DashboardStats } from "../types/admin";

export async function listAllUsers(): Promise<AdminUser[]> {
  const { data } = await apiClient.get<AdminUser[]>("/api/admin/users");
  return data;
}

export async function listAllResumes(search?: string): Promise<AdminResume[]> {
  const { data } = await apiClient.get<AdminResume[]>("/api/admin/resumes", { params: search ? { search } : {} });
  return data;
}

export async function adminDownloadVersion(resumeId: number, versionId: number, format: ResumeFileFormat): Promise<Blob> {
  const { data } = await apiClient.get(`/api/admin/resumes/${resumeId}/versions/${versionId}/download`, {
    params: { format },
    responseType: "blob",
  });
  return data as Blob;
}

export async function adminDeleteResume(resumeId: number): Promise<void> {
  await apiClient.delete(`/api/admin/resumes/${resumeId}`);
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const { data } = await apiClient.get<DashboardStats>("/api/admin/stats");
  return data;
}
