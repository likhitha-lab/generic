import type { ResumeVersionSummary } from "./history";

export interface AdminUser {
  id: number;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  created_at: string;
  resume_count: number;
}

export interface AdminResume {
  id: number;
  title: string;
  source_type: string;
  owner_id: number;
  owner_email: string;
  created_at: string;
  updated_at: string;
  latest_version: ResumeVersionSummary | null;
  version_count: number;
}

export interface DashboardStats {
  total_users: number;
  total_admins: number;
  total_regular_users: number;
  total_resumes: number;
  total_versions: number;
  resumes_created_last_7_days: number;
  resumes_created_today: number;
  recent_actions: Array<{
    action: string;
    entity_type: string | null;
    entity_id: number | null;
    user_id: number | null;
    created_at: string;
  }>;
}
