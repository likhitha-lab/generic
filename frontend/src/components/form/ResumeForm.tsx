/**
 * Manual resume-entry form. This is NEW functionality - the working
 * frontend (Project 2) only supported uploading an existing resume; the
 * only manual-entry UI in the three original projects lived in the
 * abandoned Project 1 (project/components/ResumeForm.tsx), which only
 * supported a single education/experience/project entry and had no
 * shared styling with the rest of the app.
 *
 * This is a ground-up rebuild in the same Tailwind design language as the
 * rest of the app, with real "add another" support for education,
 * experience, and projects, plus contact fields (email/phone/location/
 * links) so generated resumes actually include contact information.
 */
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  RESUME_TONES,
  type EducationInput,
  type ExperienceInput,
  type ProjectInput,
  type ResumeGenerateInput,
  type ResumeTone,
} from "../../types/resume";

interface ResumeFormProps {
  onSubmit: (data: ResumeGenerateInput) => void;
  isSubmitting: boolean;
  error?: string | null;
}

const emptyEducation: EducationInput = { degree: "", institution: "", year: "" };
const emptyExperience = { company: "", role: "", duration: "", responsibilities: "" };
const emptyProject = { title: "", description: "", tools: "" };

const inputClass =
  "w-full px-4 py-2.5 bg-white/[0.03] border border-white/[0.1] rounded-lg text-sm text-ink placeholder:text-ink-muted/60 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 focus:outline-none transition-colors duration-150";
const labelClass = "block text-sm font-medium text-ink-muted mb-1.5";
const cardClass = "bg-surface-2 border border-white/[0.08] rounded-xl p-5 space-y-4 relative";

export default function ResumeForm({ onSubmit, isSubmitting, error }: ResumeFormProps) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [location, setLocation] = useState("");
  const [links, setLinks] = useState("");
  const [tone, setTone] = useState<ResumeTone>("Experienced");

  const [skills, setSkills] = useState("");
  const [tools, setTools] = useState("");
  const [certifications, setCertifications] = useState("");

  const [education, setEducation] = useState([{ ...emptyEducation }]);
  const [experience, setExperience] = useState([{ ...emptyExperience }]);
  const [projects, setProjects] = useState([{ ...emptyProject }]);

  const updateAt = <T,>(list: T[], index: number, patch: Partial<T>) =>
    list.map((item, i) => (i === index ? { ...item, ...patch } : item));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const payload: ResumeGenerateInput = {
      name,
      email: email || undefined,
      phone: phone || undefined,
      location: location || undefined,
      links: links || undefined,
      tone,
      skills: splitList(skills),
      tools: splitList(tools),
      certifications: splitLines(certifications),
      education: education.filter((e) => e.degree || e.institution),
      experience: experience
        .filter((e) => e.company || e.role)
        .map<ExperienceInput>((e) => ({
          company: e.company,
          role: e.role,
          duration: e.duration,
          points: splitLines(e.responsibilities),
        })),
      projects: projects
        .filter((p) => p.title)
        .map<ProjectInput>((p) => ({ title: p.title, description: p.description, tools: p.tools })),
    };

    onSubmit(payload);
  };

  return (
    <form onSubmit={handleSubmit} className="max-w-4xl w-full mx-auto bg-surface p-8 sm:p-10 rounded-2xl shadow-xl shadow-black/30 border border-white/[0.08] space-y-10">
      {/* Contact */}
      <section className="space-y-4">
        <h3 className="text-lg font-bold text-ink">Contact Details</h3>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={labelClass}>Full Name *</label>
            <input required className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" />
          </div>
          <div>
            <label className={labelClass}>Email</label>
            <input type="email" className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="jane@example.com" />
          </div>
          <div>
            <label className={labelClass}>Phone</label>
            <input className={inputClass} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 555 000 1234" />
          </div>
          <div>
            <label className={labelClass}>Location</label>
            <input className={inputClass} value={location} onChange={(e) => setLocation(e.target.value)} placeholder="San Francisco, CA" />
          </div>
          <div className="sm:col-span-2">
            <label className={labelClass}>Links (LinkedIn, GitHub, Portfolio)</label>
            <input className={inputClass} value={links} onChange={(e) => setLinks(e.target.value)} placeholder="linkedin.com/in/janedoe" />
          </div>
          <div className="sm:col-span-2">
            <label className={labelClass}>Resume Tone</label>
            <select className={inputClass} value={tone} onChange={(e) => setTone(e.target.value as ResumeTone)}>
              {RESUME_TONES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {/* Skills */}
      <section className="space-y-4">
        <h3 className="text-lg font-bold text-ink">Skills &amp; Tools</h3>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={labelClass}>Skills (comma-separated)</label>
            <textarea className={`${inputClass} h-20`} value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="React, TypeScript, Node.js" />
          </div>
          <div>
            <label className={labelClass}>Tools (comma-separated)</label>
            <textarea className={`${inputClass} h-20`} value={tools} onChange={(e) => setTools(e.target.value)} placeholder="Docker, Git, Jira" />
          </div>
        </div>
        <div>
          <label className={labelClass}>Certifications (one per line)</label>
          <textarea className={`${inputClass} h-20`} value={certifications} onChange={(e) => setCertifications(e.target.value)} placeholder="AWS Certified Solutions Architect" />
        </div>
      </section>

      {/* Education */}
      <RepeatableSection
        title="Education"
        items={education}
        onAdd={() => setEducation([...education, { ...emptyEducation }])}
        onRemove={(i) => setEducation(education.filter((_, idx) => idx !== i))}
        renderItem={(edu, i) => (
          <div className="grid sm:grid-cols-3 gap-4">
            <input className={inputClass} placeholder="Degree" value={edu.degree} onChange={(e) => setEducation(updateAt(education, i, { degree: e.target.value }))} />
            <input className={inputClass} placeholder="Institution" value={edu.institution} onChange={(e) => setEducation(updateAt(education, i, { institution: e.target.value }))} />
            <input className={inputClass} placeholder="Year" value={edu.year} onChange={(e) => setEducation(updateAt(education, i, { year: e.target.value }))} />
          </div>
        )}
      />

      {/* Experience */}
      <RepeatableSection
        title="Work Experience"
        items={experience}
        onAdd={() => setExperience([...experience, { ...emptyExperience }])}
        onRemove={(i) => setExperience(experience.filter((_, idx) => idx !== i))}
        renderItem={(exp, i) => (
          <div className="space-y-3">
            <div className="grid sm:grid-cols-3 gap-4">
              <input className={inputClass} placeholder="Company" value={exp.company} onChange={(e) => setExperience(updateAt(experience, i, { company: e.target.value }))} />
              <input className={inputClass} placeholder="Role" value={exp.role} onChange={(e) => setExperience(updateAt(experience, i, { role: e.target.value }))} />
              <input className={inputClass} placeholder="Duration (e.g. Jan 2022 - Present)" value={exp.duration} onChange={(e) => setExperience(updateAt(experience, i, { duration: e.target.value }))} />
            </div>
            <textarea
              className={`${inputClass} h-24`}
              placeholder="Responsibilities / achievements - one per line"
              value={exp.responsibilities}
              onChange={(e) => setExperience(updateAt(experience, i, { responsibilities: e.target.value }))}
            />
          </div>
        )}
      />

      {/* Projects */}
      <RepeatableSection
        title="Projects"
        items={projects}
        onAdd={() => setProjects([...projects, { ...emptyProject }])}
        onRemove={(i) => setProjects(projects.filter((_, idx) => idx !== i))}
        renderItem={(proj, i) => (
          <div className="space-y-3">
            <div className="grid sm:grid-cols-2 gap-4">
              <input className={inputClass} placeholder="Project Title" value={proj.title} onChange={(e) => setProjects(updateAt(projects, i, { title: e.target.value }))} />
              <input className={inputClass} placeholder="Tools Used" value={proj.tools} onChange={(e) => setProjects(updateAt(projects, i, { tools: e.target.value }))} />
            </div>
            <textarea
              className={`${inputClass} h-20`}
              placeholder="Project description"
              value={proj.description}
              onChange={(e) => setProjects(updateAt(projects, i, { description: e.target.value }))}
            />
          </div>
        )}
      />

      {error && (
        <div className="p-4 bg-danger-500/10 border border-danger-500/25 rounded-lg text-sm text-danger-400">{error}</div>
      )}

      <button
        type="submit"
        disabled={isSubmitting || !name}
        className={`w-full py-4 px-6 rounded-xl font-semibold transition-all duration-150
          ${isSubmitting || !name ? "bg-white/[0.06] text-ink-muted cursor-not-allowed" : "bg-gradient-to-r from-brand-600 to-accent-600 text-white hover:opacity-90 shadow-lg shadow-brand-900/30 active:scale-[0.98]"}
        `}
      >
        {isSubmitting ? "Generating..." : "Generate Resume"}
      </button>
    </form>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

interface RepeatableSectionProps<T> {
  title: string;
  items: T[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  renderItem: (item: T, index: number) => React.ReactNode;
}

function RepeatableSection<T>({ title, items, onAdd, onRemove, renderItem }: RepeatableSectionProps<T>) {
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-ink">{title}</h3>
        <button
          type="button"
          onClick={onAdd}
          className="flex items-center gap-1.5 text-sm font-semibold text-brand-400 hover:text-brand-300 transition-colors duration-150"
        >
          <Plus className="w-4 h-4" /> Add another
        </button>
      </div>

      <div className="space-y-4">
        {items.map((item, i) => (
          <div key={i} className={cardClass}>
            {items.length > 1 && (
              <button
                type="button"
                onClick={() => onRemove(i)}
                className="absolute top-3 right-3 text-ink-muted hover:text-danger-500"
                aria-label="Remove"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}
            {renderItem(item, i)}
          </div>
        ))}
      </div>
    </section>
  );
}
