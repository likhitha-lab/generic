import React from "react";

interface Props {
  data: any;
  onEdit: () => void;
}

const ResumePreview: React.FC<Props> = ({ data, onEdit }) => {
  if (!data) return null;

  return (
    <div className="max-w-4xl mx-auto bg-white p-10 shadow">
      <button
        onClick={onEdit}
        className="mb-6 px-4 py-2 bg-gray-200 rounded"
      >
        Back
      </button>

      <h1 className="text-3xl font-bold text-center">
        {data.header.fullName}
      </h1>

      <p className="text-center">
        {data.header.email} | {data.header.phone} | {data.header.location}
      </p>

      <section className="mt-6">
        <h2 className="font-bold border-b">Professional Summary</h2>
        <p>{data.summary}</p>
      </section>

      <section className="mt-6">
        <h2 className="font-bold border-b">Technical Skills</h2>
        <p><b>Languages:</b> {data.technicalSkills.languages?.join(", ")}</p>
        <p><b>Frameworks:</b> {data.technicalSkills.frameworks?.join(", ")}</p>
        <p><b>Cloud:</b> {data.technicalSkills.cloud?.join(", ")}</p>
        <p><b>Tools:</b> {data.technicalSkills.tools?.join(", ")}</p>
      </section>

      <section className="mt-6">
        <h2 className="font-bold border-b">Experience</h2>
        {data.experience?.map((exp: any, i: number) => (
          <div key={i} className="mb-4">
            <b>{exp.role}</b> — {exp.company} ({exp.duration})
            <ul className="list-disc ml-6">
              {exp.achievements?.map((a: string, idx: number) => (
                <li key={idx}>{a}</li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section className="mt-6">
        <h2 className="font-bold border-b">Projects</h2>
        {data.projects?.map((p: any, i: number) => (
          <div key={i} className="mb-4">
            <b>{p.title}</b>
            <p>{p.description}</p>
            <p><i>Tools:</i> {p.tools?.join(", ")}</p>
          </div>
        ))}
      </section>

      <section className="mt-6">
        <h2 className="font-bold border-b">Education</h2>
        {data.education?.map((e: any, i: number) => (
          <p key={i}>
            {e.degree} — {e.institution} ({e.year})
          </p>
        ))}
      </section>

      {data.certifications?.length > 0 && (
        <section className="mt-6">
          <h2 className="font-bold border-b">Certifications</h2>
          <ul className="list-disc ml-6">
            {data.certifications.map((c: string, i: number) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

export default ResumePreview;
