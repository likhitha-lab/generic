
export enum ResumeTone {
  FRESHER = 'Fresher',
  EXPERIENCED = 'Experienced Professional',
  TECHNICAL = 'Technical / IT Role'
}

export interface Education {
  degree: string;
  institution: string;
  graduationYear: string;
  cgpa?: string;
}

export interface Experience {
  company: string;
  role: string;
  duration: string;
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
  links?: string;
  education: Education[];
  skills: {
    technical: string;
    soft: string;
  };
  experience: Experience[];
  projects: Project[];
  tone: ResumeTone;
}

export interface GeneratedResume {
  summary: string;
  experienceItems: {
    company: string;
    role: string;
    duration: string;
    bullets: string[];
  }[];
  educationItems: Education[];
  skills: {
    technical: string[];
    soft: string[];
  };
  projectItems: {
    title: string;
    description: string;
    tools: string[];
  }[];
}
