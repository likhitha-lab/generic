/**
 * Drag/drop upload UI. On submit, calls uploadResume() (api/resumes.ts),
 * which persists the original file + a generated version and returns a
 * ResumeDetail - actual file bytes are fetched on demand via
 * downloadVersion(), not returned inline.
 */
import { useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileText, Loader2, Upload, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { uploadResume } from "../../api/resumes";
import type { ResumeDetail } from "../../types/history";
import { Button } from "../ui/Button";

interface UploadResumeProps {
  onUploadSuccess: (detail: ResumeDetail) => void;
}

const ALLOWED_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword",
];

export default function UploadResume({ onUploadSuccess }: UploadResumeProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateAndSetFile = (selectedFile?: File) => {
    setError(null);
    setSuccess(false);

    if (!selectedFile) return;

    if (!ALLOWED_TYPES.includes(selectedFile.type)) {
      setError("Please upload a PDF or DOCX file.");
      return;
    }

    if (selectedFile.size > 5 * 1024 * 1024) {
      setError("File is too large. Max size is 5MB.");
      return;
    }

    setFile(selectedFile);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    validateAndSetFile(e.dataTransfer.files?.[0]);
  };

  const handleUpload = async () => {
    if (!file) return;

    setIsLoading(true);
    setError(null);

    try {
      const detail = await uploadResume(file);
      onUploadSuccess(detail);
      setSuccess(true);
    } catch (err) {
      console.error(err);
      setError("Upload failed. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  const removeFile = () => {
    setFile(null);
    setError(null);
    setSuccess(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="w-full max-w-2xl mx-auto p-6">
      <div className="bg-surface rounded-2xl shadow-xl shadow-black/30 border border-white/[0.08] overflow-hidden">
        <div className="p-8">
          <div className="text-center mb-8">
            <h2 className="text-2xl font-semibold text-ink mb-2 tracking-tight">Upload Your Resume</h2>
            <p className="text-ink-muted">
              We&apos;ll extract your information to help you build a professional profile.
            </p>
          </div>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={`relative group cursor-pointer transition-all duration-300 ease-in-out
              border-2 border-dashed rounded-xl p-12 text-center
              ${isDragging ? "border-brand-500 bg-brand-500/10" : "border-white/[0.12] hover:border-brand-400/60 hover:bg-white/[0.03]"}
              ${file ? "border-success-500/60 bg-success-500/[0.07]" : ""}
            `}
            onClick={() => !file && fileInputRef.current?.click()}
          >
            <input
              type="file"
              ref={fileInputRef}
              onChange={(e) => validateAndSetFile(e.target.files?.[0])}
              accept=".pdf,.docx,.doc"
              className="hidden"
            />

            <AnimatePresence mode="wait">
              {!file ? (
                <motion.div
                  key="empty"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="flex flex-col items-center"
                >
                  <div className="w-16 h-16 bg-brand-500/15 rounded-full flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-200">
                    <Upload className="w-8 h-8 text-brand-400" />
                  </div>
                  <p className="text-lg font-medium text-ink">
                    Click to upload or drag and drop
                  </p>
                  <p className="text-sm text-ink-muted mt-1">PDF or DOCX (Max 5MB)</p>
                </motion.div>
              ) : (
                <motion.div
                  key="selected"
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="flex flex-col items-center"
                >
                  <div className="w-16 h-16 bg-success-500/15 rounded-full flex items-center justify-center mb-4">
                    <FileText className="w-8 h-8 text-success-500" />
                  </div>
                  <p className="text-lg font-medium text-ink truncate max-w-xs">
                    {file.name}
                  </p>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFile();
                    }}
                    disabled={isLoading}
                    className={`mt-2 text-sm flex items-center gap-1 transition-colors duration-150 ${
                      isLoading
                        ? "text-ink-muted cursor-not-allowed"
                        : "text-danger-500 hover:text-danger-400"
                    }`}
                  >
                    <X className="w-4 h-4" /> Remove
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-4 p-4 bg-danger-500/10 border border-danger-500/25 rounded-lg flex items-start gap-3 text-danger-400"
              >
                <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                <p className="text-sm">{error}</p>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="mt-8">
            <Button
              onClick={handleUpload}
              disabled={!file || isLoading || success}
              className="w-full py-4 text-base"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" /> Processing Resume...
                </>
              ) : success ? (
                <>
                  <CheckCircle2 className="w-5 h-5" /> Upload Successful!
                </>
              ) : (
                "Analyze Resume"
              )}
            </Button>
          </div>
        </div>

        <div className="bg-surface-2 px-8 py-4 border-t border-white/[0.08]">
          <p className="text-xs text-center text-ink-muted">
            By uploading, you agree to our terms of service. Your data is processed securely.
          </p>
        </div>
      </div>
    </div>
  );
}
