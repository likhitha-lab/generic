import React from "react";
import { ResumeData, GeneratedResume } from "../types";

interface ResumePreviewProps {
  data: ResumeData;
  generated: GeneratedResume;
  onEdit: () => void;
}

const ResumePreview: React.FC<ResumePreviewProps> = ({
  data,
  generated,
  onEdit
}) => {

  const preview = generated?.preview;

  if (!preview) {
    return <div>No preview available</div>;
  }

  return (
    <div className="space-y-6">

      <div className="flex justify-between items-center bg-white p-6 rounded-xl shadow border">
        <h2 className="text-xl font-bold">
          Resume Generated Successfully 🎉
        </h2>

        <div className="flex gap-3">
          <button onClick={onEdit} className="px-4 py-2 border rounded">
            Edit
          </button>

          {generated.docx_link && (
            <a href={generated.docx_link} target="_blank" className="px-4 py-2 bg-black text-white rounded">
              DOCX
            </a>
          )}

          {generated.pdf_link && (
            <a href={generated.pdf_link} target="_blank" className="px-4 py-2 bg-indigo-600 text-white rounded">
              PDF
            </a>
          )}
        </div>
      </div>

      <div className="bg-white p-10 shadow border rounded space-y-6">

        <h1 className="text-2xl font-bold border-b pb-3">
          {data.fullName}
        </h1>

        {preview.summary?.length > 0 && (
          <Section title="Summary" items={preview.summary} />
        )}

        {preview.skills?.length > 0 && (
          <Section title="Skills" items={preview.skills} />
        )}

        {preview.education?.length > 0 && (
          <Section title="Education" items={preview.education} />
        )}

        {preview.experience?.length > 0 && (
          <div>
            <h3 className="font-semibold text-indigo-600 mb-2">
              Experience
            </h3>

            {preview.experience.map((exp: any, i: number) => (
              <div key={i} className="mb-4">
                <p className="font-medium">
                  {[exp.role, exp.company].filter(Boolean).join(" - ")}
                  {exp.duration && ` (${exp.duration})`}
                </p>

                <ul className="list-disc pl-6 mt-2">
                  {exp.points?.map((p: string, j: number) => (
                    <li key={j}>{p}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}

      </div>
    </div>
  );
};

const Section = ({ title, items }: { title: string; items: string[] }) => (
  <div>
    <h3 className="font-semibold text-indigo-600 mb-2">{title}</h3>
    <ul className="list-disc pl-6">
      {items.map((i, idx) => <li key={idx}>{i}</li>)}
    </ul>
  </div>
);

export default ResumePreview;