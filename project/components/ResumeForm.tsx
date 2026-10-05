import React, { useState, useEffect } from "react";
import { ResumeData, ResumeTone } from "../types";

interface ResumeFormProps {
  onSubmit: (data: ResumeData) => void;
  onUpload: (file: File) => void;
  isLoading: boolean;
  initialData?: ResumeData | null;
}

const ResumeForm: React.FC<ResumeFormProps> = ({
  onSubmit,
  onUpload,
  isLoading,
  initialData
}) => {

  const [step, setStep] = useState(1);

  const [formData, setFormData] = useState<ResumeData>(
    initialData || {
      fullName: "",
      email: "",
      phone: "",
      location: "",
      links: "",
      tone: ResumeTone.FRESHER,
      education: [{ degree: "", institution: "", graduationYear: "", cgpa: "" }],
      skills: { technical: "", soft: "" },
      experience: [{ company: "", role: "", duration: "", responsibilities: "" }],
      projects: [{ title: "", tools: "", description: "" }]
    }
  );

  useEffect(() => {
    if (initialData) setFormData(initialData);
  }, [initialData]);

  const inputClass =
    "w-full px-4 py-3 border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(formData);
  };

  return (
    <div className="max-w-5xl mx-auto bg-white p-10 rounded-2xl shadow-lg">

      <h2 className="text-3xl font-bold text-center mb-10">
        Build Your Career-Defining Resume
      </h2>

      <div className="flex justify-center gap-6 mb-8 text-sm font-semibold">
        <span className={step === 1 ? "text-indigo-600" : "text-gray-400"}>Step 1</span>
        <span className={step === 2 ? "text-indigo-600" : "text-gray-400"}>Step 2</span>
        <span className={step === 3 ? "text-indigo-600" : "text-gray-400"}>Step 3</span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-8">

        {/* STEP 1 */}
        {step === 1 && (
          <>
            <input required placeholder="Full Name" className={inputClass}
              value={formData.fullName}
              onChange={(e)=>setFormData({...formData, fullName:e.target.value})} />

            <input required placeholder="Email" className={inputClass}
              value={formData.email}
              onChange={(e)=>setFormData({...formData, email:e.target.value})} />

            <input required placeholder="Phone" className={inputClass}
              value={formData.phone}
              onChange={(e)=>setFormData({...formData, phone:e.target.value})} />

            <textarea required placeholder="Technical Skills"
              className={`${inputClass} h-24`}
              value={formData.skills.technical}
              onChange={(e)=>setFormData({
                ...formData,
                skills:{...formData.skills, technical:e.target.value}
              })} />

            <textarea required placeholder="Soft Skills"
              className={`${inputClass} h-20`}
              value={formData.skills.soft}
              onChange={(e)=>setFormData({
                ...formData,
                skills:{...formData.skills, soft:e.target.value}
              })} />

            <button type="button"
              onClick={()=>setStep(2)}
              className="bg-indigo-600 text-white px-8 py-3 rounded-lg">
              Next →
            </button>
          </>
        )}

        {/* STEP 2 */}
        {step === 2 && (
          <>
            <input placeholder="Degree" className={inputClass}
              value={formData.education[0].degree}
              onChange={(e)=>setFormData({
                ...formData,
                education:[{...formData.education[0], degree:e.target.value}]
              })} />

            <input placeholder="Institution" className={inputClass}
              value={formData.education[0].institution}
              onChange={(e)=>setFormData({
                ...formData,
                education:[{...formData.education[0], institution:e.target.value}]
              })} />

            <input placeholder="Graduation Year" className={inputClass}
              value={formData.education[0].graduationYear}
              onChange={(e)=>setFormData({
                ...formData,
                education:[{...formData.education[0], graduationYear:e.target.value}]
              })} />

            <input placeholder="Company" className={inputClass}
              value={formData.experience[0].company}
              onChange={(e)=>setFormData({
                ...formData,
                experience:[{...formData.experience[0], company:e.target.value}]
              })} />

            <input placeholder="Role" className={inputClass}
              value={formData.experience[0].role}
              onChange={(e)=>setFormData({
                ...formData,
                experience:[{...formData.experience[0], role:e.target.value}]
              })} />

            <textarea placeholder="Responsibilities"
              className={`${inputClass} h-24`}
              value={formData.experience[0].responsibilities}
              onChange={(e)=>setFormData({
                ...formData,
                experience:[{...formData.experience[0], responsibilities:e.target.value}]
              })} />

            <div className="flex justify-between">
              <button type="button" onClick={()=>setStep(1)} className="border px-6 py-3 rounded-lg">
                ← Back
              </button>

              <button type="button" onClick={()=>setStep(3)} className="bg-indigo-600 text-white px-6 py-3 rounded-lg">
                Next →
              </button>
            </div>
          </>
        )}

        {/* STEP 3 */}
        {step === 3 && (
          <>
            <input placeholder="Project Title" className={inputClass}
              value={formData.projects[0]?.title || ""}
              onChange={(e)=>setFormData({
                ...formData,
                projects:[{...formData.projects[0], title:e.target.value}]
              })} />

            <input placeholder="Tools Used" className={inputClass}
              value={formData.projects[0]?.tools || ""}
              onChange={(e)=>setFormData({
                ...formData,
                projects:[{...formData.projects[0], tools:e.target.value}]
              })} />

            <textarea placeholder="Project Description"
              className={`${inputClass} h-24`}
              value={formData.projects[0]?.description || ""}
              onChange={(e)=>setFormData({
                ...formData,
                projects:[{...formData.projects[0], description:e.target.value}]
              })} />

            <div className="flex justify-between mt-6">
              <button type="button" onClick={()=>setStep(2)} className="border px-6 py-3 rounded-lg">
                ← Back
              </button>

              <button type="submit"
                disabled={isLoading}
                className="bg-gradient-to-r from-indigo-600 to-purple-600 text-white px-10 py-3 rounded-lg">
                {isLoading ? "Generating..." : "Generate Resume"}
              </button>
            </div>

            <div className="text-center mt-6 border-t pt-6">
              <p>Or upload your existing resume</p>
              <input type="file" accept=".pdf,.docx"
                onChange={(e)=>{
                  const file=e.target.files?.[0];
                  if(file) onUpload(file);
                }} />
            </div>
          </>
        )}

      </form>
    </div>
  );
};

export default ResumeForm;