/**
 * Recruiter-controlled DISPLAY visibility switches for one resume version -
 * Contact Information (email/phone/LinkedIn/address) and Experience
 * (employment dates). Purely a presentation toggle: turning a switch off
 * only changes what the NEXT preview/PDF/DOCX render shows - the
 * underlying resume data is never deleted (see app/services/
 * file_generator.py's `visibility` parameter and resume_service.
 * update_version_visibility, the backend's own guarantee of this).
 *
 * Controlled component - `value`/`onChange` only, no internal state - so
 * the parent (BuilderPage) can update the live preview instantly on
 * toggle AND persist the change to the backend (which re-renders the
 * stored PDF/DOCX) without this component needing to know about either.
 */
import { Loader2 } from "lucide-react";
import type { ResumeVisibilitySettings } from "../../types/history";
import { Card } from "../ui/Card";

interface VisibilityTogglesProps {
  value: ResumeVisibilitySettings;
  onChange: (next: ResumeVisibilitySettings) => void;
  saving?: boolean;
  className?: string;
}

interface ToggleField {
  key: keyof ResumeVisibilitySettings;
  label: string;
}

const CONTACT_FIELDS: ToggleField[] = [
  { key: "show_email", label: "Show Email" },
  { key: "show_phone", label: "Show Phone" },
  { key: "show_linkedin", label: "Show LinkedIn" },
  { key: "show_address", label: "Show Address" },
];

const EXPERIENCE_FIELDS: ToggleField[] = [{ key: "show_employment_dates", label: "Show Employment Dates" }];

function ToggleRow({
  field,
  checked,
  onToggle,
}: {
  field: ToggleField;
  checked: boolean;
  onToggle: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 py-1.5 cursor-pointer select-none">
      <span className="text-sm text-ink-muted">{field.label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(e.target.checked)}
        className="h-4 w-4 rounded border-white/[0.2] bg-white/[0.05] text-brand-600 focus:ring-brand-500 cursor-pointer"
      />
    </label>
  );
}

export default function VisibilityToggles({ value, onChange, saving = false, className = "" }: VisibilityTogglesProps) {
  const set = (key: keyof ResumeVisibilitySettings, checked: boolean) => {
    onChange({ ...value, [key]: checked });
  };

  return (
    <Card className={`p-6 ${className}`}>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-ink">Visibility</h3>
        {saving && <Loader2 className="w-4 h-4 animate-spin text-ink-muted" />}
      </div>

      <div className="mb-5">
        <h4 className="text-xs font-semibold text-ink-muted uppercase tracking-wide mb-2">Contact Information</h4>
        <div className="divide-y divide-white/[0.08]">
          {CONTACT_FIELDS.map((field) => (
            <ToggleRow key={field.key} field={field} checked={value[field.key]} onToggle={(checked) => set(field.key, checked)} />
          ))}
        </div>
      </div>

      <div>
        <h4 className="text-xs font-semibold text-ink-muted uppercase tracking-wide mb-2">Experience</h4>
        <div className="divide-y divide-white/[0.08]">
          {EXPERIENCE_FIELDS.map((field) => (
            <ToggleRow key={field.key} field={field} checked={value[field.key]} onToggle={(checked) => set(field.key, checked)} />
          ))}
        </div>
      </div>

      <p className="text-xs text-ink-muted mt-4 leading-relaxed">
        Hiding a field only affects how this resume is displayed and downloaded - nothing is ever deleted from your
        saved data.
      </p>
    </Card>
  );
}
