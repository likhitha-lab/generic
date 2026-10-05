/**
 * Lists the current user's resumes and lets them drill into a resume to see
 * every version ever generated (versions are immutable and additive - see
 * ResumeVersion in the DB schema), view a version's structured JSON,
 * download PDF/DOCX for any version, regenerate a new version, or soft-delete
 * the whole resume.
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Code2, FileJson, Loader2, RefreshCw, Trash2 } from "lucide-react";
import DownloadButtons from "../components/upload/DownloadButtons";
import { Card } from "../components/ui/Card";
import { deleteResume, getResume, getVersionJson, listResumes, regenerateResume } from "../api/resumes";
import type { ResumeDetail, ResumeVersionSummary } from "../types/history";
import type { ResumeSummary } from "../types/history";

export default function HistoryPage() {
  const [resumes, setResumes] = useState<ResumeSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ResumeDetail | null>(null);
  const [viewJson, setViewJson] = useState<{ resumeId: number; content: unknown } | null>(null);
  const [busyResumeId, setBusyResumeId] = useState<number | null>(null);

  const load = async () => {
    setIsLoading(true);
    setError(null);
    try {
      setResumes(await listResumes());
    } catch (err) {
      console.error(err);
      setError("Could not load your resumes.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const toggleExpand = async (resumeId: number) => {
    if (expandedId === resumeId) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(resumeId);
    setDetail(null);
    try {
      setDetail(await getResume(resumeId));
    } catch (err) {
      console.error(err);
      setError("Could not load resume versions.");
    }
  };

  const handleRegenerate = async (resumeId: number) => {
    setBusyResumeId(resumeId);
    try {
      await regenerateResume(resumeId);
      await load();
      if (expandedId === resumeId) setDetail(await getResume(resumeId));
    } catch (err) {
      console.error(err);
      setError("Could not regenerate this resume.");
    } finally {
      setBusyResumeId(null);
    }
  };

  const handleDelete = async (resumeId: number) => {
    if (!window.confirm("Delete this resume and all its versions? This cannot be undone.")) return;
    setBusyResumeId(resumeId);
    try {
      await deleteResume(resumeId);
      if (expandedId === resumeId) {
        setExpandedId(null);
        setDetail(null);
      }
      await load();
    } catch (err) {
      console.error(err);
      setError("Could not delete this resume.");
    } finally {
      setBusyResumeId(null);
    }
  };

  const handleViewJson = async (resumeId: number, version: ResumeVersionSummary) => {
    try {
      const versionDetail = await getVersionJson(resumeId, version.id);
      setViewJson({ resumeId, content: versionDetail.content });
    } catch (err) {
      console.error(err);
      setError("Could not load version JSON.");
    }
  };

  return (
    <div className="max-w-5xl w-full mx-auto py-12 px-4">
      <h1 className="text-2xl font-bold text-ink tracking-tight mb-1">Resume History</h1>
      <p className="text-sm text-ink-muted mb-8">Every resume you&apos;ve generated, with every version kept on record.</p>

      {error && <div className="mb-6 p-4 bg-danger-500/10 border border-danger-500/25 rounded-lg text-sm text-danger-400">{error}</div>}

      {isLoading ? (
        <div className="flex items-center justify-center py-20 text-ink-muted">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : resumes.length === 0 ? (
        <p className="text-ink-muted">You haven&apos;t generated any resumes yet.</p>
      ) : (
        <div className="space-y-4">
          {resumes.map((resume) => {
            const isExpanded = expandedId === resume.id;
            const isBusy = busyResumeId === resume.id;

            return (
              <Card key={resume.id} className="overflow-hidden">
                <button
                  onClick={() => toggleExpand(resume.id)}
                  className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-white/[0.03] transition-colors duration-150"
                >
                  <div className="flex items-center gap-3">
                    {isExpanded ? <ChevronDown className="w-4 h-4 text-ink-muted" /> : <ChevronRight className="w-4 h-4 text-ink-muted" />}
                    <div>
                      <p className="font-semibold text-ink">{resume.title}</p>
                      <p className="text-xs text-ink-muted mt-0.5">
                        {resume.source_type === "upload" ? "Uploaded" : "Built from scratch"} &middot; {resume.version_count} version
                        {resume.version_count === 1 ? "" : "s"} &middot; updated {new Date(resume.updated_at).toLocaleString()}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => handleRegenerate(resume.id)}
                      disabled={isBusy}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-brand-400 hover:bg-brand-500/10 rounded-lg transition-colors duration-150 disabled:opacity-50"
                    >
                      {isBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                      Regenerate
                    </button>
                    <button
                      onClick={() => handleDelete(resume.id)}
                      disabled={isBusy}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-danger-500 hover:bg-danger-500/10 rounded-lg transition-colors duration-150 disabled:opacity-50"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Delete
                    </button>
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t border-white/[0.08] px-6 py-5 space-y-4 bg-surface-2">
                    {!detail ? (
                      <div className="flex justify-center py-6 text-ink-muted">
                        <Loader2 className="w-5 h-5 animate-spin" />
                      </div>
                    ) : (
                      detail.versions.map((version) => (
                        <div key={version.id} className="bg-surface rounded-xl border border-white/[0.08] p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                          <div>
                            <p className="text-sm font-semibold text-ink">
                              Version {version.version_number} &middot; {version.tone}
                            </p>
                            <p className="text-xs text-ink-muted mt-0.5">{new Date(version.created_at).toLocaleString()}</p>
                          </div>

                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              onClick={() => handleViewJson(resume.id, version)}
                              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-ink-muted border border-white/[0.1] rounded-lg hover:bg-white/[0.04] hover:border-white/[0.2] transition-colors duration-150"
                            >
                              <FileJson className="w-3.5 h-3.5" /> View JSON
                            </button>
                            <div className="w-40">
                              <DownloadButtons resumeId={resume.id} versionId={version.id} filenameBase={`${resume.title}-v${version.version_number}`} />
                            </div>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {viewJson && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={() => setViewJson(null)}>
          <div
            className="bg-surface rounded-2xl shadow-2xl shadow-black/50 border border-white/[0.08] max-w-2xl w-full max-h-[80vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08]">
              <h3 className="font-semibold text-ink flex items-center gap-2">
                <Code2 className="w-5 h-5 text-brand-400" /> Generated JSON
              </h3>
              <button onClick={() => setViewJson(null)} className="text-ink-muted hover:text-ink text-sm font-medium transition-colors duration-150">
                Close
              </button>
            </div>
            <pre className="p-6 overflow-auto text-xs text-ink-muted bg-surface-2 flex-grow">
              {JSON.stringify(viewJson.content, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
