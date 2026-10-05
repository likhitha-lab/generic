
import React from 'react';
import { ResumeData, ResumeTone, Education, Experience, Project } from '../types';
import { generateProfessionalResume } from "../services/geminiService";

interface ResumeFormProps {
  onSubmit: (response: any) => void; // or a proper interface
  isLoading: boolean;
}


const ResumeForm: React.FC<ResumeFormProps> = ({ onSubmit, isLoading }) => {
  const [formData, setFormData] = React.useState<ResumeData>({
    fullName: '',
    email: '',
    phone: '',
    location: '',
    links: '',
    tone: ResumeTone.FRESHER,
    education: [{ degree: '', institution: '', graduationYear: '', cgpa: '' }],
    skills: { technical: '', soft: '' },
    experience: [{ company: '', role: '', duration: '', responsibilities: '' }],
    projects: [{ title: '', description: '', tools: '' }]
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleNestedChange = (section: 'education' | 'experience' | 'projects', index: number, field: string, value: string) => {
    setFormData(prev => {
      const newArray = [...prev[section]];
      (newArray[index] as any)[field] = value;
      return { ...prev, [section]: newArray };
    });
  };

  const addItem = (section: 'education' | 'experience' | 'projects') => {
    const defaultItems = {
      education: { degree: '', institution: '', graduationYear: '', cgpa: '' },
      experience: { company: '', role: '', duration: '', responsibilities: '' },
      projects: { title: '', description: '', tools: '' }
    };
    setFormData(prev => ({
      ...prev,
      [section]: [...prev[section], { ...defaultItems[section] }]
    }));
  };

  const removeItem = (section: 'education' | 'experience' | 'projects', index: number) => {
    setFormData(prev => ({
      ...prev,
      [section]: prev[section].filter((_, i) => i !== index)
    }));
  };
  const handleSubmit = async (e: React.FormEvent) => {
  e.preventDefault();

  try {
    console.log("Submitting form data:", formData);

    const apiResponse = await generateProfessionalResume(formData);
    console.log("Backend response:", apiResponse);

    const mappedData = {
      fullName: formData.fullName,
      email: formData.email,
      phone: formData.phone,
      location: formData.location,
      links: formData.links,
      tone: formData.tone,

      summary: apiResponse.resumeData.summary,

      skills: {
        technical: apiResponse.resumeData.skills.technical.join(", "),
        soft: apiResponse.resumeData.skills.soft.join(", "),
      },

      education: apiResponse.resumeData.educationItems.map((e: any) => ({
        degree: e.degree,
        institution: e.institution,
        graduationYear: e.graduationYear,
        cgpa: e.cgpa || "",
      })),

      experience: apiResponse.resumeData.experienceItems.map((exp: any) => ({
        company: exp.company,
        role: exp.role,
        duration: exp.duration,
        responsibilities: exp.bullets.join("\n"),
      })),

      projects: apiResponse.resumeData.projectItems.map((p: any) => ({
        title: p.title,
        description: p.description,
        tools: p.tools.join(", "),
      })),
    };

    onSubmit(mappedData);
  } catch (error: any) {
    console.error("Generation failed:", error);
    alert("Failed to generate resume");
  }
};



  

  const inputClass = "w-full px-4 py-2 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all";
  const labelClass = "block text-sm font-semibold text-slate-700 mb-1";
  const sectionHeader = "text-xl font-bold text-slate-800 mb-4 pb-2 border-b border-slate-100 flex items-center justify-between";

  return (
    <form onSubmit={handleSubmit} className="space-y-8 bg-white p-8 rounded-2xl shadow-sm border border-slate-100">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="col-span-2">
          <h2 className={sectionHeader}>Personal Information</h2>
        </div>
        <div>
          <label className={labelClass}>Full Name *</label>
          <input required name="fullName" value={formData.fullName} onChange={handleChange} className={inputClass} placeholder="John Doe" />
        </div>
        <div>
          <label className={labelClass}>Email *</label>
          <input required type="email" name="email" value={formData.email} onChange={handleChange} className={inputClass} placeholder="john@example.com" />
        </div>
        <div>
          <label className={labelClass}>Phone Number *</label>
          <input required name="phone" value={formData.phone} onChange={handleChange} className={inputClass} placeholder="+1 234 567 890" />
        </div>
        <div>
          <label className={labelClass}>City / Country *</label>
          <input required name="location" value={formData.location} onChange={handleChange} className={inputClass} placeholder="San Francisco, USA" />
        </div>
        <div className="col-span-2">
          <label className={labelClass}>LinkedIn / GitHub (Optional)</label>
          <input name="links" value={formData.links} onChange={handleChange} className={inputClass} placeholder="linkedin.com/in/johndoe" />
        </div>
      </div>

      <div>
        <h2 className={sectionHeader}>
          Education
          <button type="button" onClick={() => addItem('education')} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">+ Add</button>
        </h2>
        {formData.education.map((edu, idx) => (
          <div key={idx} className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4 p-4 bg-slate-50 rounded-xl relative">
            <div className="col-span-2 flex justify-end">
              {formData.education.length > 1 && (
                <button type="button" onClick={() => removeItem('education', idx)} className="text-red-500 text-sm">Remove</button>
              )}
            </div>
            <div>
              <label className={labelClass}>Degree</label>
              <input required value={edu.degree} onChange={(e) => handleNestedChange('education', idx, 'degree', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Institution</label>
              <input required value={edu.institution} onChange={(e) => handleNestedChange('education', idx, 'institution', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Year of Graduation</label>
              <input required value={edu.graduationYear} onChange={(e) => handleNestedChange('education', idx, 'graduationYear', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>CGPA / Percentage (Optional)</label>
              <input value={edu.cgpa} onChange={(e) => handleNestedChange('education', idx, 'cgpa', e.target.value)} className={inputClass} />
            </div>
          </div>
        ))}
      </div>

      <div>
        <h2 className={sectionHeader}>Skills</h2>
        <div className="grid grid-cols-1 gap-4">
          <div>
            <label className={labelClass}>Technical Skills (comma-separated) *</label>
            <textarea required value={formData.skills.technical} onChange={(e) => setFormData(p => ({...p, skills: {...p.skills, technical: e.target.value}}))} className={`${inputClass} h-24`} placeholder="React, Node.js, Python, AWS..." />
          </div>
          <div>
            <label className={labelClass}>Soft Skills *</label>
            <textarea required value={formData.skills.soft} onChange={(e) => setFormData(p => ({...p, skills: {...p.skills, soft: e.target.value}}))} className={`${inputClass} h-20`} placeholder="Communication, Leadership, Problem Solving..." />
          </div>
        </div>
      </div>

      <div>
        <h2 className={sectionHeader}>
          Experience (Optional)
          <button type="button" onClick={() => addItem('experience')} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">+ Add</button>
        </h2>
        {formData.experience.map((exp, idx) => (
          <div key={idx} className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4 p-4 bg-slate-50 rounded-xl">
             <div className="col-span-2 flex justify-end">
                <button type="button" onClick={() => removeItem('experience', idx)} className="text-red-500 text-sm">Remove</button>
            </div>
            <div>
              <label className={labelClass}>Company Name</label>
              <input value={exp.company} onChange={(e) => handleNestedChange('experience', idx, 'company', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Role</label>
              <input value={exp.role} onChange={(e) => handleNestedChange('experience', idx, 'role', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Duration</label>
              <input value={exp.duration} onChange={(e) => handleNestedChange('experience', idx, 'duration', e.target.value)} className={inputClass} placeholder="e.g. June 2021 - Present" />
            </div>
            <div className="col-span-2">
              <label className={labelClass}>Key Responsibilities</label>
              <textarea value={exp.responsibilities} onChange={(e) => handleNestedChange('experience', idx, 'responsibilities', e.target.value)} className={`${inputClass} h-32`} placeholder="List your key contributions and tasks..." />
            </div>
          </div>
        ))}
      </div>

      <div>
        <h2 className={sectionHeader}>
          Projects / Internships
          <button type="button" onClick={() => addItem('projects')} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">+ Add</button>
        </h2>
        {formData.projects.map((proj, idx) => (
          <div key={idx} className="grid grid-cols-1 gap-4 mb-4 p-4 bg-slate-50 rounded-xl">
            <div className="flex justify-end">
                <button type="button" onClick={() => removeItem('projects', idx)} className="text-red-500 text-sm">Remove</button>
            </div>
            <div>
              <label className={labelClass}>Project Title</label>
              <input value={proj.title} onChange={(e) => handleNestedChange('projects', idx, 'title', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Tools or Technologies Used</label>
              <input value={proj.tools} onChange={(e) => handleNestedChange('projects', idx, 'tools', e.target.value)} className={inputClass} placeholder="e.g. React, Firebase, Tailwind" />
            </div>
            <div>
              <label className={labelClass}>Short Description</label>
              <textarea value={proj.description} onChange={(e) => handleNestedChange('projects', idx, 'description', e.target.value)} className={`${inputClass} h-24`} />
            </div>
          </div>
        ))}
      </div>

      <div className="pt-6 border-t border-slate-100 flex flex-col md:flex-row items-center gap-6">
        <div className="w-full md:w-1/3">
          <label className={labelClass}>Resume Tone</label>
          <select name="tone" value={formData.tone} onChange={handleChange} className={inputClass}>
            {Object.values(ResumeTone).map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <button 
          disabled={isLoading}
          type="submit" 
          className="w-full md:flex-1 bg-indigo-600 text-white font-bold py-4 rounded-xl hover:bg-indigo-700 transition-colors shadow-lg shadow-indigo-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {isLoading ? (
             <>
               <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
               Architecting Your Resume...
             </>
          ) : "Generate Professional Resume"}
        </button>
      </div>
    </form>
  );
};

export default ResumeForm;
