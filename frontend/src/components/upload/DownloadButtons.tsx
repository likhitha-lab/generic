/**
 * Fetches the PDF/DOCX for a specific persisted resume version on demand via
 * GET /api/resumes/{id}/versions/{id}/download (or the admin equivalent) and
 * triggers a browser download. Nothing is cached across formats since the
 * backend now streams from Blob Storage / local disk rather than
 * regenerating with Gemini each time.
 */
import { useState } from "react";
import { CheckCircle2, Download, FileText, Loader2 } from "lucide-react";
import { downloadVersion } from "../../api/resumes";
import { adminDownloadVersion } from "../../api/admin";
import { downloadBlob } from "../../lib/downloadFile";
import type { ResumeFileFormat } from "../../types/resume";

interface DownloadButtonsProps {
  resumeId: number;
  versionId: number;
  filenameBase?: string;
  asAdmin?: boolean;
  className?: string;
}

export default function DownloadButtons({
  resumeId,
  versionId,
  filenameBase = "resume",
  asAdmin = false,
  className = "",
}: DownloadButtonsProps) {
  const [loading, setLoading] = useState<ResumeFileFormat | null>(null);
  const [success, setSuccess] = useState<ResumeFileFormat | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async (format: ResumeFileFormat) => {
    setError(null);
    setLoading(format);
    try {
      const blob = asAdmin
        ? await adminDownloadVersion(resumeId, versionId, format)
        : await downloadVersion(resumeId, versionId, format);
      downloadBlob(blob, `${filenameBase}.${format}`);
      setSuccess(format);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      console.error("Download error:", err);
      setError(`Could not download the ${format.toUpperCase()}. Please try again.`);
    } finally {
      setLoading(null);
    }
  };

  const renderButton = (format: ResumeFileFormat, label: string, primary: boolean) => {
    const isLoading = loading === format;
    const isSuccess = success === format;

    return (
      <button
        onClick={() => handleDownload(format)}
        disabled={isLoading}
        className={`flex-1 py-3 px-4 rounded-xl font-semibold transition-all duration-150 flex items-center justify-center gap-2
          ${
            isSuccess
              ? "bg-success-500 text-white shadow-lg shadow-success-900/30"
              : primary
                ? "bg-brand-600 text-white hover:bg-brand-500 shadow-lg shadow-brand-900/30 active:scale-[0.98]"
                : "bg-transparent border border-white/[0.12] text-ink hover:bg-white/[0.04] hover:border-white/[0.2] active:scale-[0.98]"
          }
          ${isLoading ? "opacity-70 cursor-wait" : ""}
        `}
      >
        {isLoading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : isSuccess ? (
          <CheckCircle2 className="w-4 h-4" />
        ) : primary ? (
          <Download className="w-4 h-4" />
        ) : (
          <FileText className="w-4 h-4" />
        )}
        {isLoading ? "Preparing..." : isSuccess ? "Downloaded" : label}
      </button>
    );
  };

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      <div className="flex flex-col sm:flex-row gap-3">
        {renderButton("pdf", "Export as PDF", true)}
        {renderButton("docx", "Export as DOCX", false)}
      </div>
      {error && <p className="text-sm text-danger-400">{error}</p>}
    </div>
  );
}
