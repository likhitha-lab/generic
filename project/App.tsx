import React, { useState } from "react";
import ResumeForm from "./components/ResumeForm";
import ResumePreview from "./components/ResumePreview";
import { ResumeData, GeneratedResume, ResumeTone } from "./types";
import { generateProfessionalResume, convertResume } from "./services/geminiService";

const App: React.FC = () => {

  const [data, setData] = useState<ResumeData | null>(null);
  const [generated, setGenerated] = useState<GeneratedResume | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"form"|"preview">("form");

  // ================= GENERATE =================
  const handleGenerate = async (d: ResumeData) => {
    try {
      setLoading(true);

      const res = await generateProfessionalResume(d);

      setData(d);
      setGenerated(res);
      setView("preview");

    } catch (err: any) {
      console.error("Generate error:", err);
      alert(err.message || "Failed to generate resume");
    } finally {
      setLoading(false);
    }
  };

  // ================= UPLOAD =================
  const handleUpload = async (file: File) => {
    try {
      setLoading(true);

      const res = await convertResume(file);

      const mapped: ResumeData = {
        fullName: "",
        email: "",
        phone: "",
        location: "",
        links: "",
        tone: ResumeTone.FRESHER,
        education: [],
        skills: { technical:"", soft:"" },
        experience: [],
        projects: []
      };

      setData(mapped);
      setView("form");

    } catch (err: any) {
      console.error("Upload error:", err);
      alert(err.message || "Upload failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      {view==="form" &&
        <ResumeForm
          onSubmit={handleGenerate}
          onUpload={handleUpload}
          isLoading={loading}
          initialData={data}
        />
      }

      {view==="preview" && data && generated &&
        <ResumePreview
          data={data}
          generated={generated}
          onEdit={()=>setView("form")}
        />
      }
    </div>
  );
};

export default App;