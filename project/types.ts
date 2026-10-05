export enum ResumeTone {
  FRESHER = "Fresher",
  EXPERIENCED = "Experienced",
  TECHNICAL = "Technical"
}

export interface Education {
  degree: string;
  institution: string;
  graduationYear?: string;
  cgpa?: string;
}

export interface Experience {
  company: string;
  role: string;
  duration?: string;
  responsibilities: string;
}

export interface Project {
  title: string;
  description: string;
  tools: string;
}

export interface ResumeData {
  fullName: string;
  email: string;
  phone: string;
  location: string;
  links: string;
  tone: ResumeTone;

  education: Education[];

  skills: {
    technical: string;
    soft: string;
  };

  experience: Experience[];
  projects: Project[];

  summary?: string;
  certifications?: string;
  tools?: string;
}

export interface GeneratedResume {
  pdf_link?: string;
  docx_link?: string;
  preview?: {
    summary?: string;
    skills?: string[];
    education?: string[];
    certifications?: string[];
    tools?: string[];
    experience?: {
      role?: string;
      company?: string;
      duration?: string;
      points?: string[];
    }[];
    projects?: {
      title?: string;
      description?: string;
    }[];
  };
}