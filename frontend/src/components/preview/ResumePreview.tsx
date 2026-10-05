/**
 * Ported from the original ResumePreview.tsx almost unchanged (it was
 * already a clean, responsive design). Added: a contact-info line in the
 * header, since the original never rendered email/phone/location/links
 * anywhere - a real functional gap fixed on the backend (see
 * app/services/file_generator.py) and mirrored here for on-screen preview.
 *
 * skills/tools/experience/education/certifications/projects are all run
 * through normalizeSkillGroups()/toSafeArray() (src/utils/resumeFields.ts)
 * before being rendered, rather than calling .map()/.length on them
 * directly - the backend can send skills/tools as either a flat string[]
 * (old format, still what the manual Resume Generator flow produces) or a
 * category -> string[] object (new format, from the upload/convert flow's
 * Stage 2 polish - see backend/app/services/resume_polish.py), and any
 * field can in principle be missing/null on a partially-loaded or older
 * stored version. Going through those helpers means a shape this
 * component doesn't recognize renders as "nothing for this section"
 * instead of crashing the whole preview with "X.map is not a function".
 */
import {
  Award,
  BookOpen,
  Briefcase,
  Code2,
  ExternalLink,
} from "lucide-react";
import type { ResumeData } from "../../types/resume";
import { DEFAULT_VISIBILITY, type ResumeVisibilitySettings } from "../../types/history";
import { normalizeSkillGroups, toSafeArray, type SkillGroup } from "../../utils/resumeFields";

interface ResumePreviewProps {
  data: ResumeData;
  /** Recruiter-controlled display visibility - defaults to "show
   * everything" so every existing caller that doesn't pass this (the mock
   * preview, any not-yet-updated usage) renders exactly as it always has. */
  visibility?: ResumeVisibilitySettings;
}

/** Shared rendering for a skills/tools group, covering both formats the
 * backend can send (see CategorizedList in types/resume.ts):
 *   - old flat-array shape -> one group with category: null -> rendered
 *     as the original pill/tag-cloud style, unchanged
 *   - new categorized-object shape -> one group per category -> rendered
 *     as a heading with its items as a bulleted list underneath
 * Two different layouts by design (not just two colors of the same pill
 * list) - a flat tag cloud and a categorized, headed section read
 * differently on purpose, matching how each format is meant to be used. */
function SkillGroupList({
  groups,
  pillClassName,
  bulletDotClassName,
}: {
  groups: SkillGroup[];
  pillClassName: string;
  bulletDotClassName: string;
}) {
  if (groups.length === 0) {
    return null;
  }
  return (
    <div className="space-y-4">
      {groups.map((group, groupIndex) =>
        group.category ? (
          <div key={group.category}>
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-2">{group.category}</h3>
            <ul className="space-y-1.5">
              {group.items.map((item, itemIndex) => (
                <li key={itemIndex} className="text-sm text-slate-600 flex items-start gap-2">
                  <div className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${bulletDotClassName}`} />
                  {item}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div key={`flat-${groupIndex}`} className="flex flex-wrap gap-2">
            {group.items.map((item, itemIndex) => (
              <span key={itemIndex} className={pillClassName}>
                {item}
              </span>
            ))}
          </div>
        )
      )}
    </div>
  );
}

export default function ResumePreview({ data, visibility = DEFAULT_VISIBILITY }: ResumePreviewProps) {
  const { name, email, phone, location, links, summary, experience, education, skills, tools, projects, certifications } =
    data;

  const contactParts = [
    visibility.show_email && email,
    visibility.show_phone && phone,
    visibility.show_address && location,
    visibility.show_linkedin && links,
  ].filter(Boolean);

  const skillGroups = normalizeSkillGroups(skills);
  const toolGroups = normalizeSkillGroups(tools);
  const safeEducation = toSafeArray<string>(education);
  const safeCertifications = toSafeArray<string>(certifications);
  const safeExperience = toSafeArray<ResumeData["experience"][number]>(experience);
  const safeProjects = toSafeArray<ResumeData["projects"][number]>(projects);

  return (
    <div className="w-full max-w-[210mm] mx-auto bg-white shadow-2xl rounded-sm overflow-hidden text-slate-700 font-sans">
      <header className="bg-slate-900 text-white p-10">
        <h1 className="text-4xl font-bold tracking-tight mb-2">{name || "Your Name"}</h1>
        {contactParts.length > 0 && (
          <p className="text-slate-300 text-sm mb-4">{contactParts.join("   |   ")}</p>
        )}
        <div className="h-1 w-20 bg-brand-600 rounded-full" />
      </header>

      <div className="p-10 space-y-10">
        <section>
          <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-brand-600" />
            Professional Summary
          </h2>
          <p className="text-slate-600 leading-relaxed">{summary}</p>
        </section>

        <section>
          <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
            <Code2 className="w-5 h-5 text-brand-600" />
            Technical Skills
          </h2>
          <SkillGroupList
            groups={skillGroups}
            pillClassName="px-3 py-1 bg-brand-50 text-brand-700 text-sm font-medium rounded-full border border-brand-100"
            bulletDotClassName="bg-brand-600"
          />
        </section>

        <section>
          <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-brand-600" />
            Educational Qualifications
          </h2>
          <div className="space-y-4">
            {safeEducation.map((edu, index) => (
              <div key={index} className="bg-slate-50 p-4 rounded-lg border border-slate-100">
                <p className="text-slate-700 font-medium">{edu}</p>
              </div>
            ))}
          </div>
        </section>

        {safeCertifications.length > 0 && (
          <section>
            <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
              <Award className="w-5 h-5 text-brand-600" />
              Certifications
            </h2>
            <ul className="space-y-3">
              {safeCertifications.map((cert, index) => (
                <li key={index} className="text-sm text-slate-600 flex items-start gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-brand-600 mt-1.5 flex-shrink-0" />
                  {cert}
                </li>
              ))}
            </ul>
          </section>
        )}

        {toolGroups.length > 0 && (
          <section>
            <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
              <Code2 className="w-5 h-5 text-brand-600" />
              Tools
            </h2>
            <SkillGroupList
              groups={toolGroups}
              pillClassName="px-3 py-1 bg-slate-50 text-slate-700 text-sm font-medium rounded-full border border-slate-100"
              bulletDotClassName="bg-slate-500"
            />
          </section>
        )}

        <section>
          <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-brand-600" />
            Professional Experience
          </h2>
          <div className="space-y-8">
            {safeExperience.map((exp, index) => (
              <div key={index} className="relative pl-4 border-l-2 border-slate-100">
                <div className="absolute -left-[9px] top-1 w-4 h-4 rounded-full bg-white border-2 border-brand-600" />
                <div className="flex justify-between items-start mb-1">
                  <h3 className="font-bold text-slate-900 text-lg">{exp.role}</h3>
                  {visibility.show_employment_dates && (
                    <span className="text-sm font-medium text-slate-500 bg-slate-50 px-2 py-1 rounded">
                      {exp.duration}
                    </span>
                  )}
                </div>
                <div className="text-brand-600 font-medium mb-3">{exp.company}</div>
                <ul className="list-disc list-outside ml-4 space-y-2 text-slate-600 text-sm">
                  {toSafeArray<string>(exp.points).map((bullet, i) => (
                    <li key={i}>{bullet}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {safeProjects.length > 0 && (
          <section>
            <h2 className="text-lg font-bold text-slate-900 uppercase tracking-wider border-b-2 border-slate-100 pb-2 mb-4 flex items-center gap-2">
              <ExternalLink className="w-5 h-5 text-brand-600" />
              Projects Handled
            </h2>
            <div className="space-y-6">
              {safeProjects.map((project, index) => (
                <div key={index}>
                  <h3 className="font-bold text-slate-900 mb-1">{project.title}</h3>
                  <p className="text-sm text-slate-600">{project.description}</p>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      <footer className="bg-slate-50 p-6 border-t border-slate-100 text-center">
        <p className="text-xs text-slate-500">
          Generated with Dataflix AI Resume Management • {new Date().toLocaleDateString()}
        </p>
      </footer>
    </div>
  );
}
