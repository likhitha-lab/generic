/**
 * Home page: upload-or-build flow. On success the backend has already
 * persisted the resume + first version (see app/services/resume_service.py),
 * so all this page does is fetch that version's structured JSON for the
 * on-screen preview and offer PDF/DOCX downloads for it.
 */
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import UploadResume from "../components/upload/UploadResume";
import DownloadButtons from "../components/upload/DownloadButtons";
import ResumeForm from "../components/form/ResumeForm";
import ResumePreview from "../components/preview/ResumePreview";
import VisibilityToggles from "../components/visibility/VisibilityToggles";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { generateResume, getVersionJson, updateVersionVisibility } from "../api/resumes";
import type { ResumeData, ResumeGenerateInput } from "../types/resume";
import { DEFAULT_VISIBILITY, type ResumeDetail, type ResumeVisibilitySettings } from "../types/history";

const MOCK_RESUME: ResumeData = {
  name: "Alex Thompson",
  email: "alex.thompson@example.com",
  phone: "+1 555 123 4567",
  location: "Austin, TX",
  links: "linkedin.com/in/alexthompson",
  summary:
    "Senior Software Engineer with 8+ years of experience building scalable web applications. Expert in React, Node.js, and Cloud Architecture. Proven track record of leading cross-functional teams and delivering high-impact features that improve user engagement by 40%.",
  experience: [
    {
      company: "TechFlow Solutions",
      role: "Senior Frontend Engineer",
      duration: "Jan 2020 - Present",
      points: [
        "Led the migration of a legacy monolith to a modern micro-frontend architecture using React and Module Federation.",
        "Implemented a comprehensive design system that reduced UI development time by 30%.",
        "Optimized application performance, improving Core Web Vitals scores from 'Needs Improvement' to 'Good'.",
      ],
    },
    {
      company: "Innovate AI",
      role: "Software Engineer",
      duration: "Jun 2017 - Dec 2019",
      points: [
        "Developed and maintained real-time data visualization dashboards using D3.js and WebSocket.",
        "Collaborated with data scientists to integrate machine learning models into the customer-facing platform.",
        "Reduced API latency by 50% through strategic caching and query optimization.",
      ],
    },
  ],
  education: [
    "Master of Science in Computer Science - Stanford University - 2017",
    "Bachelor of Science in Software Engineering - University of Texas - 2015",
  ],
  skills: ["React", "TypeScript", "Node.js", "AWS", "GraphQL", "Tailwind CSS"],
  tools: ["Docker", "Kubernetes", "PostgreSQL", "Git", "Jira"],
  projects: [
    {
      title: "OpenSource Analytics",
      description: "A privacy-focused web analytics tool with real-time tracking and custom reporting.",
    },
  ],
  certifications: ["AWS Certified Solutions Architect - Associate", "Google Professional Cloud Developer"],
};

type Tab = "upload" | "manual";

export default function BuilderPage() {
  const [tab, setTab] = useState<Tab>("upload");
  const [resumeData, setResumeData] = useState<ResumeData | null>(null);
  const [activeResume, setActiveResume] = useState<ResumeDetail | null>(null);
  const [isViewingMock, setIsViewingMock] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<ResumeVisibilitySettings>(DEFAULT_VISIBILITY);
  const [savingVisibility, setSavingVisibility] = useState(false);

  const applyResult = async (detail: ResumeDetail) => {
    setActiveResume(detail);
    setIsViewingMock(false);

    const latest = detail.latest_version;
    if (latest) {
      setVisibility(latest.visibility);
      const versionDetail = await getVersionJson(detail.id, latest.id);
      setResumeData(versionDetail.content);
    }
  };

  const handleVisibilityChange = async (next: ResumeVisibilitySettings) => {
    // Update the on-screen preview instantly, then persist to the backend
    // (which re-renders the stored PDF/DOCX to match) - a failed save
    // reverts the preview back rather than leaving it showing something
    // the downloaded files don't actually reflect.
    const previous = visibility;
    setVisibility(next);
    if (!activeResume?.latest_version) return;

    setSavingVisibility(true);
    try {
      await updateVersionVisibility(activeResume.id, activeResume.latest_version.id, next);
    } catch (err) {
      console.error("Failed to save visibility settings:", err);
      setVisibility(previous);
    } finally {
      setSavingVisibility(false);
    }
  };

  const handleManualSubmit = async (payload: ResumeGenerateInput) => {
    setIsGenerating(true);
    setFormError(null);
    try {
      const detail = await generateResume(payload);
      await applyResult(detail);
    } catch (err) {
      console.error(err);
      setFormError("Failed to generate resume. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  };

  const reset = () => {
    setResumeData(null);
    setActiveResume(null);
    setIsViewingMock(false);
    setFormError(null);
    setVisibility(DEFAULT_VISIBILITY);
  };

  const showMock = () => {
    setResumeData(MOCK_RESUME);
    setActiveResume(null);
    setIsViewingMock(true);
    setVisibility(DEFAULT_VISIBILITY);
  };

  return (
    <div className="flex-grow flex flex-col items-center py-12 px-4">
      {!resumeData ? (
        <div className="max-w-4xl w-full">
          <header className="text-center mb-10">
            <p className="text-sm font-semibold tracking-wide text-brand-600 uppercase mb-3">
              Dataflix AI Resume Management
            </p>
            <h1 className="text-4xl font-bold text-ink tracking-tight sm:text-5xl mb-4">
              Recruiter-Ready Resumes, Standardized at Scale
            </h1>
            <p className="text-lg text-ink-muted max-w-2xl mx-auto leading-relaxed">
              Generate recruiter-ready, ATS-optimized resumes aligned with Dataflix standards
              using AI-powered resume extraction, intelligent skill normalization, and
              standardized formatting.
            </p>
          </header>

          <div className="flex justify-center gap-2 mb-8">
            <button onClick={() => setTab("upload")} className={tabClass(tab === "upload")}>
              Upload Existing Resume
            </button>
            <button onClick={() => setTab("manual")} className={tabClass(tab === "manual")}>
              Build From Scratch
            </button>
          </div>

          {tab === "upload" ? (
            <UploadResume onUploadSuccess={applyResult} />
          ) : (
            <ResumeForm onSubmit={handleManualSubmit} isSubmitting={isGenerating} error={formError} />
          )}

          <div className="mt-12 text-center">
            <button
              onClick={showMock}
              className="text-ink-muted hover:text-brand-400 text-sm font-medium transition-colors duration-150"
            >
              Don&apos;t have a resume? View a sample template
            </button>
          </div>
        </div>
      ) : (
        <div className="w-full">
          <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
            <div className="lg:col-span-8">
              <ResumePreview data={resumeData} visibility={visibility} />
            </div>

            <div className="lg:col-span-4 space-y-6 sticky top-24">
              <Card className="p-6">
                <h3 className="font-semibold text-ink mb-4 flex items-center gap-2">
                  <RefreshCw className="w-5 h-5 text-brand-400" />
                  AI Analysis Complete
                </h3>
                <p className="text-sm text-ink-muted mb-6 leading-relaxed">
                  Your resume has been saved. Download it below, or find it any time under History.
                </p>

                {activeResume && activeResume.latest_version ? (
                  <DownloadButtons
                    resumeId={activeResume.id}
                    versionId={activeResume.latest_version.id}
                    filenameBase={activeResume.title || "resume"}
                  />
                ) : (
                  <p className="text-sm text-ink-muted">
                    Downloads are unavailable for the sample resume.
                  </p>
                )}
              </Card>

              {activeResume && activeResume.latest_version && (
                <VisibilityToggles value={visibility} onChange={handleVisibilityChange} saving={savingVisibility} />
              )}

              <Button variant="secondary" onClick={reset} className="w-full">
                <RefreshCw className="w-4 h-4" />
                Start Over
              </Button>

              {isViewingMock && (
                <div className="bg-brand-500/10 p-6 rounded-2xl border border-brand-500/20">
                  <p className="text-sm text-brand-300 font-medium leading-relaxed">
                    You are currently viewing a sample resume. Upload your own or build one from
                    scratch to see the AI in action!
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function tabClass(active: boolean): string {
  return `px-5 py-2.5 rounded-lg text-sm font-semibold transition-all duration-150 ${
    active
      ? "bg-brand-600 text-white shadow-sm shadow-brand-900/30"
      : "bg-transparent text-ink-muted border border-white/[0.1] hover:bg-white/[0.04] hover:border-white/[0.2]"
  }`;
}
